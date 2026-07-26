"""A fake Mooer GE150 pedal that emulates the device at the HID-report level.

``FakePedal`` implements the same ``write``/``read`` interface the hidapi
backend exposes, so it can be wired directly into a real ``USBConnection``.
Every test that goes through it exercises the full stack: frame building,
CRC checksums, chunked transmission, and chunked-response reassembly.
"""

from __future__ import annotations

from collections import deque

from mooer_ge150_mcp.protocol.commands import Command
from mooer_ge150_mcp.protocol.framing import (
    build_chunked_frames,
    parse_message,
    message_total_size,
    HID_REPORT_SIZE,
)
from mooer_ge150_mcp.models.preset import PRESET_SIZE
from mooer_ge150_mcp.transport.usb_connection import USBConnection

NUM_SLOTS = 200


class FakePedal:
    """In-memory pedal: 200 preset slots plus volume/system state.

    Mimics the subset of the ``hid.device`` API that ``USBConnection``
    uses (``write(data)`` and ``read(size, timeout_ms)``).
    """

    def __init__(self) -> None:
        self.slots: list[bytes] = [bytes(PRESET_SIZE) for _ in range(NUM_SLOTS)]
        self.active_slot = 0
        self.volume = 50
        self.firmware = bytes([1, 5, 0, 0, 0])
        self.device_name = b"GE150ProLi\x00"
        self._rx_buffer = b""
        self._rx_expected: int | None = None
        self._tx_reports: deque[bytes] = deque()

    # ── hid.device interface ─────────────────────────────────────────

    def write(self, data: bytes) -> int:
        """Accept one 64-byte HID report from the host."""
        assert len(data) == HID_REPORT_SIZE, f"report must be 64B, got {len(data)}"
        chunk_size = data[0]
        self._rx_buffer += bytes(data[1 : 1 + chunk_size])

        if self._rx_expected is None:
            self._rx_expected = message_total_size(self._rx_buffer)
            assert self._rx_expected is not None, "host sent invalid message header"

        if len(self._rx_buffer) >= self._rx_expected:
            message = self._rx_buffer[: self._rx_expected]
            self._rx_buffer = b""
            self._rx_expected = None
            frame = parse_message(message)
            assert frame is not None, "host sent message with bad checksum"
            self._handle_command(frame.command, frame.payload)

        return len(data)

    def read(self, size: int, timeout_ms: int = 0) -> bytes:
        """Return the next queued 64-byte response report, or b'' on timeout."""
        if not self._tx_reports:
            return b""
        return self._tx_reports.popleft()

    def close(self) -> None:
        pass

    # ── device behavior ──────────────────────────────────────────────

    def _respond(self, command: int, payload: bytes) -> None:
        """Queue a (possibly chunked) response message."""
        for report in build_chunked_frames(command, payload):
            self._tx_reports.append(report)

    def _handle_command(self, command: int, payload: bytes) -> None:
        if command == Command.IDENTIFY:
            self._respond(command, self.firmware + self.device_name)
        elif command == Command.READ_PRESET:
            slot = payload[0]
            if len(payload) == 1:
                # Read request → respond with slot + 512 bytes of preset data
                self._respond(command, bytes([slot]) + self.slots[slot])
            else:
                # Some hosts write via 0x83 as well; store the data
                self.slots[slot] = payload[1 : 1 + PRESET_SIZE].ljust(
                    PRESET_SIZE, b"\x00"
                )
                self._respond(command, bytes([slot]))
        elif command == Command.STORE_PATCH:
            slot = payload[0]
            self.slots[slot] = payload[1 : 1 + PRESET_SIZE].ljust(
                PRESET_SIZE, b"\x00"
            )
            self._respond(command, bytes([slot]))
        elif command == Command.SETTING_A6:
            if len(payload) >= 1:
                self.active_slot = payload[0]
            self._respond(command, bytes([self.active_slot]))
        elif command == Command.VOLUME:
            if len(payload) >= 1:
                self.volume = payload[0]
            self._respond(command, bytes([self.volume]))
        else:
            # Ack unknown commands with an empty payload echo
            self._respond(command, b"")


def make_connection(pedal: FakePedal | None = None) -> tuple[USBConnection, FakePedal]:
    """Return a real USBConnection wired to a FakePedal via the hidapi path."""
    pedal = pedal or FakePedal()
    conn = USBConnection()
    conn._device = pedal
    conn._backend = "hidapi"
    conn._connected = True
    return conn, pedal
