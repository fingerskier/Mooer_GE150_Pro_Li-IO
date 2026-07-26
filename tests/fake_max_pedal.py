"""A fake pedal that speaks the capture-confirmed GE150 Max protocol.

``tests/fake_device.py`` emulates the older, pre-capture protocol model and
the legacy tools still exercise it. This one implements only what the two
USB captures actually prove, so tests written against it fail if the code
drifts from observed device behaviour.

Implemented exchanges (all confirmed):

===================  ==========================================
Request              Response
===================  ==========================================
``0x31`` HELLO       none
``0xA0`` DUMP        200 x ``0x20`` preset records
``0xC1`` IR LIST     ``0x41``, 40 x 16-byte names
``0xB0`` ACTIVE      ``0x30``, active preset state
``0x82``-``0x8A``    echoes the block back as ``0x02``-``0x0A``
``0x97`` SAVE        ``0x17`` notification
===================  ==========================================
"""

from __future__ import annotations

from collections import deque

from mooer_ge150_mcp.protocol.commands import (
    IR_SLOT_COUNT,
    MODULE_CHAIN,
    PRESET_NAME_LENGTH,
    PRESET_TAIL_SIZE,
    Command,
    ModuleBlock,
    PresetRecord,
    encode_module_block,
    encode_preset_record,
    response_command,
)
from mooer_ge150_mcp.protocol.framing import (
    HID_REPORT_SIZE,
    build_chunked_frames,
    message_total_size,
    parse_message,
)
from mooer_ge150_mcp.transport.usb_connection import USBConnection

NUM_SLOTS = 200


def _default_record(slot: int) -> PresetRecord:
    """A preset shaped like the factory ones in the capture."""
    modules = {
        command: ModuleBlock(
            enabled=(index % 2 == 0),
            effect_type=index,
            params=[50, 50, 50],
        )
        for index, command in enumerate(MODULE_CHAIN)
    }
    name = f"Preset {slot}".encode("ascii").ljust(PRESET_NAME_LENGTH, b"\x00")
    return PresetRecord(
        slot=slot,
        name_raw=name,
        modules=modules,
        tail=bytes(PRESET_TAIL_SIZE),
    )


class FakeMaxPedal:
    """In-memory GE150 Max speaking the confirmed protocol.

    Mimics the subset of the ``hid.device`` API that ``USBConnection``
    uses (``write(data)`` and ``read(size, timeout_ms)``).
    """

    def __init__(self) -> None:
        #: Slots are 1-based on the wire, so index 0 is unused.
        self.records: list[PresetRecord | None] = [None] + [
            _default_record(slot) for slot in range(1, NUM_SLOTS + 1)
        ]
        self.ir_names: list[str] = ["EMPTY"] * IR_SLOT_COUNT
        self.active_slot = 1
        self.written_blocks: list[tuple[int, ModuleBlock]] = []
        self.selected: list[int] = []
        self.saves: list[tuple[int, bytes]] = []
        self.uploaded: list[int] = []
        self.restore_brackets: list[str] = []
        self.settings: dict[Command, list[int]] = {}
        self.ctrl_flags: list[list[bool]] = [
            [False] * len(MODULE_CHAIN) for _ in range(NUM_SLOTS)
        ]
        self._rx_buffer = b""
        self._rx_expected: int | None = None
        self._tx_reports: deque[bytes] = deque()

    # ── hid.device interface ─────────────────────────────────────────

    def write(self, data: bytes) -> int:
        data = bytes(data)
        # hidapi semantics: the first byte of hid_write() is the report
        # ID, 0x00 for unnumbered reports. Real hardware ignores writes
        # without it, so the fake requires it too.
        assert len(data) == HID_REPORT_SIZE + 1, (
            f"hid_write takes report-ID + 64 bytes, got {len(data)}"
        )
        assert data[0] == 0x00, "report ID must be 0x00 (unnumbered reports)"
        data = data[1:]
        chunk = data[0]
        self._rx_buffer += bytes(data[1 : 1 + chunk])

        if self._rx_expected is None:
            self._rx_expected = message_total_size(self._rx_buffer)
            assert self._rx_expected is not None, "host sent invalid message header"

        if len(self._rx_buffer) >= self._rx_expected:
            message = self._rx_buffer[: self._rx_expected]
            self._rx_buffer = b""
            self._rx_expected = None
            frame = parse_message(message)
            assert frame is not None, "host sent message with bad checksum"
            self._handle(frame.command, frame.payload)
        return len(data)

    def read(self, size: int, timeout_ms: int = 0) -> bytes:
        if not self._tx_reports:
            return b""
        return self._tx_reports.popleft()

    def close(self) -> None:
        pass

    # ── device behaviour ─────────────────────────────────────────────

    def _respond(self, command: int, payload: bytes) -> None:
        for report in build_chunked_frames(command, payload):
            self._tx_reports.append(report)

    def _active_state_payload(self) -> bytes:
        record = self.records[self.active_slot]
        blocks = b"".join(
            encode_module_block(record.modules[c]) for c in MODULE_CHAIN
        )
        return bytes([self.active_slot, 0x01]) + blocks + record.tail

    def _handle(self, command: int, payload: bytes) -> None:
        if command == Command.HELLO:
            return  # the pedal does not answer the hello

        if command == Command.DUMP_PRESETS:
            for slot in range(1, NUM_SLOTS + 1):
                self._respond(
                    Command.PRESET_RECORD,
                    encode_preset_record(self.records[slot]),
                )

        elif command == Command.READ_IR_LIST:
            payload_out = b"".join(
                n.encode("ascii").ljust(PRESET_NAME_LENGTH, b"\x00")
                for n in self.ir_names
            )
            self._respond(Command.IR_LIST, payload_out)

        elif command == Command.READ_ACTIVE:
            self._respond(Command.ACTIVE_STATE, self._active_state_payload())

        elif Command.FX <= command <= Command.REVERB:
            from mooer_ge150_mcp.protocol.commands import decode_module_block

            block = decode_module_block(payload)
            self.written_blocks.append((command, block))
            self.records[self.active_slot].modules[Command(command)] = block
            self._respond(response_command(command), payload)

        elif command == Command.SELECT_PRESET:
            slot = payload[0]
            if 1 <= slot <= NUM_SLOTS:
                self.active_slot = slot
                self.selected.append(slot)
            self._respond(0x2A, bytes(9))
            self._respond(0x29, bytes([slot - 1]) + bytes(9))
            self._respond(Command.PRESET_CHANGED, b"")

        elif command == Command.SAVE_PRESET:
            # Commits the live edit state to the slot, under this name.
            slot = payload[0]
            name = payload[1 : 1 + PRESET_NAME_LENGTH]
            if 1 <= slot <= NUM_SLOTS:
                live = self.records[self.active_slot]
                self.records[slot] = PresetRecord(
                    slot=slot,
                    name_raw=name,
                    modules=dict(live.modules),
                    tail=live.tail,
                )
                self.saves.append((slot, name))
            self._respond(Command.PRESET_NAME_NOTIFY, payload)

        elif command == Command.RESTORE_BEGIN:
            self.restore_brackets.append("begin")

        elif command == Command.RESTORE_END:
            self.restore_brackets.append("end")

        elif command == Command.WRITE_PRESET:
            from mooer_ge150_mcp.protocol.commands import decode_preset_record

            record = decode_preset_record(payload)
            self.records[record.slot] = record
            self.uploaded.append(record.slot)
            self._respond(Command.WRITE_PRESET_ACK, bytes([record.slot]))

        elif command == Command.READ_CTRL_CONFIG:
            slot = payload[0]
            flags = self.ctrl_flags[slot]
            self._respond(
                Command.CTRL_CONFIG,
                bytes([slot]) + bytes(1 if f else 0 for f in flags),
            )

        elif command == Command.WRITE_CTRL_CONFIG:
            self.ctrl_flags[payload[0]] = [bool(b) for b in payload[1:]]

        elif command in (
            Command.INPUT_LEVEL,
            Command.OTG_LEVEL,
            Command.SCREEN_BRIGHTNESS,
            Command.CAB_SIM_THRU,
            Command.SPILLOVER,
        ):
            self.settings[Command(command)] = [
                int.from_bytes(payload[i : i + 2], "little")
                for i in range(0, len(payload), 2)
            ]

        # Anything else is deliberately unanswered: the captures do not
        # show a reply, so tests must not depend on one.


def make_max_connection(
    pedal: FakeMaxPedal | None = None,
) -> tuple[USBConnection, FakeMaxPedal]:
    """Return a real USBConnection wired to a FakeMaxPedal."""
    pedal = pedal or FakeMaxPedal()
    conn = USBConnection()
    conn._device = pedal
    conn._backend = "hidapi"
    conn._connected = True
    return conn, pedal
