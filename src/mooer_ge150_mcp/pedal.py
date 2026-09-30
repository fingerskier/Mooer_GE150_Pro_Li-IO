"""A session with the pedal: one open connection and the rules for using it.

Everything that talks to the hardware goes through :class:`Pedal`. It owns
the USB connection and the timing rules measured on a real GE150 Max --
a select must finish loading before anything follows it, CTRL traffic
needs two seconds of quiet, a restore bracket ends in a reboot that has to
be ridden out -- so no caller can get them wrong. The MCP server is a thin
layer over it.

Slots at this API are 0-based (0-199), the server's numbering. The 1-based
wire slot stays inside, as does :attr:`PresetRecord.slot`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from .protocol.commands import (
    Command,
    FIRST_PRESET_SLOT,
    LAST_PRESET_SLOT,
    MODULE_CHAIN,
    ModuleBlock,
    PresetRecord,
    build_command,
    build_dump_presets,
    build_hello,
    build_read_active_preset,
    build_read_ctrl_config,
    build_read_ir_list,
    build_restore_begin,
    build_restore_end,
    build_save_preset,
    build_select_preset_slot,
    build_write_ctrl_config,
    build_write_preset_record,
    decode_active_state,
    decode_ctrl_config,
    decode_ir_list,
    decode_preset_record,
    encode_module_block,
)
from .transport.usb_connection import DeviceInfo, USBConnection

logger = logging.getLogger(__name__)

#: Pacing between HID reports and messages. Unpaced writes are not
#: merely unreliable: back-to-back bracketed records made the pedal
#: watchdog-reboot in live testing (2026-07-26).
WRITE_PACING_SECONDS = 0.02

#: The editor paces successive records of a restore about 100 ms apart;
#: sending them back to back rebooted the pedal in live testing.
RECORD_PACING_SECONDS = 0.1

#: An app-initiated save draws no reply, and it takes effect a moment
#: after it is sent: measured live 2026-09-30, a dump started 0.15 s
#: after a save still returned the old preset, while one started 0.65 s
#: after returned the new one. So a save is followed by this much quiet.
SAVE_SETTLE_SECONDS = 1.0

#: Unrelated messages to skip while waiting for a reply. The pedal can
#: push dozens in a fraction of a second (one CTRL read gave up after 32
#: in 0.4 s in live testing), and every read is time-bounded anyway.
REPLY_MAX_SKIP = 256

#: How long to wait for the pedal to report a selected preset loaded. It
#: normally does so about 0.2 s after the select.
SELECT_SETTLE_TIMEOUT_MS = 3000

#: Quiet time the pedal needs after CTRL traffic before it can take a
#: select. Measured live 2026-09-30: a select 0.5 s after a CTRL read
#: hung the pedal until its watchdog reset it; 1.0 s worked but answered
#: late (0.36 s against the usual 0.24 s); 2.0 s was clean every time.
CTRL_SETTLE_SECONDS = 2.0

#: After RESTORE_END the pedal spends a couple of seconds rebroadcasting
#: its state and ignores a dump request (observed live).
DUMP_RETRY_SECONDS = 2.5

#: Time to give the kernel to re-bind its drivers after a USB port reset.
USB_RESET_SETTLE_SECONDS = 4.0


class Pedal:
    """One pedal, opened on first use and kept open.

    Args:
        connection: An already-open connection to adopt, e.g. one wired
            to a fake device in tests.
        open_connection: Makes a new, unopened connection when the pedal
            has to be (re)opened.
    """

    def __init__(
        self,
        connection: USBConnection | None = None,
        open_connection: Callable[[], USBConnection] = USBConnection,
    ) -> None:
        self._conn = connection
        self._open_connection = open_connection
        #: time.monotonic() before which the pedal is still digesting CTRL
        #: traffic (see CTRL_SETTLE_SECONDS).
        self._ctrl_quiet_until = 0.0
        #: Records from the most recent dump, keyed by slot. For display
        #: only: nothing that writes to the pedal relies on it.
        self.last_dump: dict[int, PresetRecord] = {}

    # ── connection ────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._conn is not None and self._conn.connected

    @property
    def device_info(self) -> DeviceInfo | None:
        return self._conn.device_info if self.connected else None

    @property
    def connection(self) -> USBConnection:
        """The open connection, opening the pedal first if need be.

        Raises:
            ConnectionError: If no pedal can be opened.
        """
        if not self.connected:
            self.connect()
        return self._conn

    def connect(self) -> PresetRecord | None:
        """Open the pedal and do the editor's handshake.

        The hello draws no reply; reading the active preset is what
        confirms the pedal is talking.

        Returns:
            The active preset's state, or None if the pedal did not
            report it.

        Raises:
            ConnectionError: If no pedal can be opened.
        """
        if not self.connected:
            conn = self._open_connection()
            conn.open()
            self._conn = conn
            # A dump from before describes whichever pedal that was.
            self.last_dump = {}
            conn.write(build_hello())
        return self.read_active()

    def disconnect(self) -> None:
        """Close the connection, e.g. to let MOOER Studio have the pedal."""
        self.last_dump = {}
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def reconnect_after_reboot(self) -> bool:
        """Ride through the reboot that ends a restore bracket.

        Reopening the device is not enough: the pedal can come back on the
        bus with its HID interface silent (observed live 2026-09-30, after
        a reopen about a second into its start-up). So this confirms the
        pedal answers, and if it does not, resets its USB port and tries
        again.

        Returns:
            True only if the pedal is answering requests again.
        """
        conn = self._conn
        if conn is None:
            return False
        self.last_dump = {}
        if conn.reconnect() and self._answers():
            return True
        logger.warning("Pedal silent after its reboot; resetting its USB port")
        if not conn.reset_usb():
            return False
        time.sleep(USB_RESET_SETTLE_SECONDS)
        try:
            conn.open()
        except ConnectionError:
            return False
        return self._answers()

    def _answers(self, attempts: int = 3) -> bool:
        """True if the pedal replies to a read of its active preset."""
        for attempt in range(attempts):
            if attempt:
                time.sleep(1.0)
            self._conn.drain()
            reply = self._conn.send_and_expect(
                build_read_active_preset(), Command.ACTIVE_STATE,
                timeout_ms=1500,
            )
            if reply is not None:
                return True
        return False

    # ── CTRL quiet time ───────────────────────────────────────────────

    def _note_ctrl_traffic(self) -> None:
        """Record that a CTRL read or write just went out."""
        self._ctrl_quiet_until = time.monotonic() + CTRL_SETTLE_SECONDS

    def _wait_for_ctrl_quiet(self) -> None:
        """Hold off until the pedal has digested any recent CTRL traffic.

        Called before a select, a save or a restore bracket. Only the
        select is proven to hang the pedal; the other two get the same
        margin because a reset in the middle of a stored write is the
        costlier failure.
        """
        remaining = self._ctrl_quiet_until - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

    # ── reads ─────────────────────────────────────────────────────────

    def read_presets(self) -> dict[int, PresetRecord]:
        """Read every preset, keyed by slot.

        The pedal only answers a bulk dump (about 2.5 s) -- there is no
        confirmed single-preset read -- so this is also how one preset is
        read. Always fresh: presets can change on the pedal itself.
        """
        conn = self.connection
        frames = conn.send_and_collect(
            build_dump_presets(), LAST_PRESET_SLOT,
            command=Command.PRESET_RECORD,
        )
        if not frames:
            logger.debug("Dump returned nothing; retrying after settle")
            time.sleep(DUMP_RETRY_SECONDS)
            frames = conn.send_and_collect(
                build_dump_presets(), LAST_PRESET_SLOT,
                command=Command.PRESET_RECORD,
            )

        records: dict[int, PresetRecord] = {}
        for frame in frames:
            try:
                record = decode_preset_record(frame.payload)
            except ValueError:
                logger.warning("Skipping malformed preset record")
                continue
            records[record.slot - FIRST_PRESET_SLOT] = record
        self.last_dump = records
        return records

    def read_active(self) -> PresetRecord | None:
        """Read the active preset's live state (it has no name field)."""
        reply = self.connection.send_and_expect(
            build_read_active_preset(), Command.ACTIVE_STATE
        )
        return decode_active_state(reply.payload) if reply else None

    def read_ctrl_config(self, slot: int) -> dict[Command, bool] | None:
        """Read which modules a preset's footswitch toggles.

        A preset load pushes a 0x29 shaped exactly like the reply, for the
        slot that loaded; only a reply for *slot* is accepted.
        """
        reply = self.connection.send_and_expect(
            build_read_ctrl_config(slot), Command.CTRL_CONFIG,
            max_skip=REPLY_MAX_SKIP,
            match=lambda frame: frame.payload[:1] == bytes([slot]),
        )
        self._note_ctrl_traffic()
        if reply is None:
            return None
        return decode_ctrl_config(reply.payload)[1]

    def read_user_models(self) -> list[str] | None:
        """Read the 40 user model names: 20 amps, then 20 cabs."""
        reply = self.connection.send_and_expect(
            build_read_ir_list(), Command.IR_LIST
        )
        return decode_ir_list(reply.payload) if reply else None

    # ── the live edit state ───────────────────────────────────────────

    def select(self, slot: int) -> bool:
        """Make a preset active and wait until it has loaded.

        The pedal answers every select -- even of the preset already
        active -- about 0.2 s later with 0x2A and then a 0x29 naming the
        loaded slot. A command that arrives inside that window can hang
        the pedal until its watchdog resets it, so nothing may follow a
        select until that 0x29 has been seen.

        Returns:
            True once the pedal reports the preset loaded, False on
            timeout.
        """
        conn = self.connection
        self._wait_for_ctrl_quiet()
        # A 0x29 for this slot left over from earlier would end the wait
        # before the load has even started.
        conn.drain()
        loaded = conn.send_and_expect(
            build_select_preset_slot(slot + FIRST_PRESET_SLOT),
            Command.CTRL_CONFIG,
            timeout_ms=SELECT_SETTLE_TIMEOUT_MS,
            max_skip=REPLY_MAX_SKIP,
            match=lambda frame: frame.payload[:1] == bytes([slot]),
        )
        return loaded is not None

    def write_module(self, command: Command, block: ModuleBlock) -> None:
        """Replace one module of the live state. Not stored until saved."""
        self.connection.write(build_command(command, encode_module_block(block)))

    def save(self, slot: int, name: str) -> None:
        """Commit the live state to *slot* under *name* (also a rename)."""
        conn = self.connection
        self._wait_for_ctrl_quiet()
        conn.write(build_save_preset(slot + FIRST_PRESET_SLOT, name))
        time.sleep(SAVE_SETTLE_SECONDS)

    def write_live(self, slot: int, record: PresetRecord) -> bool:
        """Store a record's modules and name the way the editor does:
        select the slot, write each module block, then save.

        No reboot, so this is the path for interactive writes. The 12-byte
        preset tail cannot be written this way; the slot keeps its own.

        Returns:
            False if the pedal never confirmed the select, in which case
            nothing was written.
        """
        if not self.select(slot):
            return False
        for command in MODULE_CHAIN:
            block = record.modules.get(command)
            if block is None:
                continue
            self.write_module(command, block)
            time.sleep(WRITE_PACING_SECONDS)
        # An app-initiated save draws no reply (0x17 only accompanies saves
        # made on the pedal itself), so this is fire-and-forget plus pacing.
        self.save(slot, record.name)
        return True

    # ── stored writes that reboot the pedal ───────────────────────────

    def write_records(self, records: list[PresetRecord]) -> int:
        """Store whole records, byte for byte, inside a restore bracket.

        0xC3 has only ever been observed between RESTORE_BEGIN and
        RESTORE_END, and RESTORE_END REBOOTS THE PEDAL by design (MOOER
        Studio's restore does the same). Follow with
        :meth:`reconnect_after_reboot`.

        Returns:
            The number of records the pedal acknowledged.
        """
        conn = self.connection
        acked = 0
        self._wait_for_ctrl_quiet()
        # Messages the pedal pushed earlier (it rebroadcasts its whole
        # state after a reboot) would otherwise be read ahead of the acks;
        # a write that landed was reported unacknowledged that way.
        conn.drain()
        conn.write(build_restore_begin())
        time.sleep(WRITE_PACING_SECONDS)
        try:
            for record in records:
                for report in build_write_preset_record(record):
                    conn.write(report)
                    time.sleep(WRITE_PACING_SECONDS)
                if conn.expect(Command.WRITE_PRESET_ACK) is not None:
                    acked += 1
                time.sleep(RECORD_PACING_SECONDS)
        finally:
            conn.write(build_restore_end())
        self.last_dump = {}
        return acked

    # ── everything else ───────────────────────────────────────────────

    def write_ctrl_config(self, slot: int, flags: list[bool]) -> None:
        """Set which modules a preset's footswitch toggles."""
        self.connection.write(build_write_ctrl_config(slot, flags))
        # Not measured for the write, but it is the same CTRL traffic.
        self._note_ctrl_traffic()

    def send(self, report: bytes) -> None:
        """Send one prepared report that draws no reply (a setting)."""
        self.connection.write(report)
        time.sleep(WRITE_PACING_SECONDS)

    def upload(self, messages: list[list[bytes]], ack: Command) -> bool:
        """Send a user model upload, waiting for *ack* after each message.

        Returns:
            False as soon as a message goes unacknowledged.
        """
        conn = self.connection
        conn.drain()
        for message in messages:
            for report in message:
                conn.write(report)
                time.sleep(WRITE_PACING_SECONDS)
            if conn.expect(ack) is None:
                return False
        return True
