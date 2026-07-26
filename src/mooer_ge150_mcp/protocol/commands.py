"""Command group constants and high-level command builders.

Each command is identified by a single-byte group ID. Device replies echo
the request ID with bit 7 cleared (0x85 -> 0x05, 0xB4 -> 0x34), so the
0x80 bit reads as a "host is writing" flag.

Evidence
--------
Values marked *confirmed* were observed in ``log/various_tests.pcapng``,
a Wireshark/USBPcap capture of MOOER Studio driving the pedal over USB
OTG. Values marked *unverified* predate that capture, were inferred from
the GE150 (non-Max) editor, and have not been seen on the wire -- treat
them as provisional.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import IntEnum

from .framing import build_frame, build_chunked_frames

#: Device replies reuse the request ID with the high bit cleared.
RESPONSE_MASK = 0x7F

#: Effect module blocks are a fixed 12 little-endian u16 words.
MODULE_BLOCK_WORDS = 12
MODULE_BLOCK_SIZE = MODULE_BLOCK_WORDS * 2
#: Words 0 and 1 are the ON/OFF status and effect type; the rest are parameters.
MAX_MODULE_PARAMS = MODULE_BLOCK_WORDS - 2


class Command(IntEnum):
    """Command group identifiers."""

    # --- effect module blocks, in signal-chain order ---------------------
    # All nine are confirmed by the preset dump: every preset record holds
    # exactly nine 24-byte blocks, and block N corresponds to command
    # 0x82 + N. EQ (0x87) is unmistakable -- six bands sitting at 16 plus
    # crossover frequencies 100/600/1250. AMP/CAB are pinned by the
    # amp-selects-cabinet push (editing 0x84 makes the pedal send 0x05).
    FX = 0x82  # 4 params
    DS = 0x83  # overdrive / distortion; 3 params
    AMP = 0x84  # 6 params
    CAB = 0x85
    NS = 0x86  # 3 params; identity by elimination
    EQ = 0x87  # 6 bands + 3 crossover frequencies
    MOD = 0x88
    DELAY = 0x89  # times in ms (e.g. 1040)
    REVERB = 0x8A

    # --- connect / bulk read sequence ------------------------------------
    HELLO = 0x31  # confirmed: first message the editor sends
    DUMP_PRESETS = 0xA0  # confirmed: replies with 200x 0x20 records
    PRESET_RECORD = 0x20  # confirmed: 245-byte preset record
    READ_IR_LIST = 0xC1  # confirmed: replies 0x41, 40x 16-byte names
    IR_LIST = 0x41
    READ_STATE_8C = 0x8C  # confirmed: replies 0x0C, 6 bytes
    READ_ACTIVE = 0xB0  # confirmed: replies 0x30, active preset state
    ACTIVE_STATE = 0x30

    # --- preset operations ----------------------------------------------
    READ_PRESET = 0x96  # confirmed: 1-byte slot argument
    PRESET_NAME = 0x97  # confirmed: slot byte + 16-byte ASCII name
    PRESET_NAME_NOTIFY = 0x17  # confirmed: same shape, pedal -> host
    PRESET_CHANGED = 0x34  # confirmed: 1-byte slot, pedal -> host

    # --- miscellaneous, shape confirmed but meaning not yet pinned down --
    SETTING_A4 = 0xA4  # confirmed: single u16
    SETTING_A5 = 0xA5  # confirmed: single u16
    SETTING_A6 = 0xA6  # confirmed: two u16 flags
    SETTING_A7 = 0xA7  # confirmed: single u16
    SETTING_AC = 0xAC  # confirmed: 9-byte struct
    POLL = 0xB4  # confirmed: u16 selector, acked by 0x34
    ASSIGNMENT = 0xD1  # confirmed: 18-byte assignment block

    # --- not observed in either capture ----------------------------------
    IDENTIFY = 0x10  # unverified
    SYSTEM = 0xA1  # unverified
    VOLUME = 0xA2  # unverified
    PEDAL_ASSIGNMENT = 0xA3  # unverified
    STORE_PATCH = 0xA8  # unverified
    CABINET_UPLOAD = 0xE1  # unverified
    AMP_UPLOAD = 0xE2  # unverified
    AMP_MODELS = 0xE3  # unverified


#: The nine effect modules, in signal-chain order. A preset record stores
#: its module blocks in exactly this order.
MODULE_CHAIN: list[Command] = [
    Command.FX,
    Command.DS,
    Command.AMP,
    Command.CAB,
    Command.NS,
    Command.EQ,
    Command.MOD,
    Command.DELAY,
    Command.REVERB,
]

#: Lowest and highest effect-module command IDs.
FIRST_MODULE_COMMAND = Command.FX
LAST_MODULE_COMMAND = Command.REVERB

#: Mapping from module name to command ID, in effect-chain order. These are
#: the owner's manual's own module names (see GLOSSARY.md).
MODULE_COMMAND_MAP: dict[str, Command] = {
    "fx": Command.FX,
    "ds": Command.DS,
    "amp": Command.AMP,
    "cab": Command.CAB,
    "ns": Command.NS,
    "eq": Command.EQ,
    "mod": Command.MOD,
    "delay": Command.DELAY,
    "reverb": Command.REVERB,
}

#: Accepted aliases for module names. "od" predates the manual check --
#: the pedal calls the overdrive/distortion module DS.
MODULE_NAME_ALIASES: dict[str, str] = {"od": "ds", "drive": "ds"}

#: Length of the ASCII name field in a PRESET_NAME message.
PRESET_NAME_LENGTH = 16

#: Preset slots are numbered 1-200 in PRESET_RECORD, READ_PRESET and
#: PRESET_NAME. Note that the 0x29 reply reports the same slot 0-based.
FIRST_PRESET_SLOT = 1
LAST_PRESET_SLOT = 200

#: Presets are addressed as a bank (1-50) and a position (A-D) on the
#: pedal itself; "slot" is our flat index behind that. See GLOSSARY.md.
PRESETS_PER_BANK = 4
FIRST_BANK = 1
LAST_BANK = 50
PRESET_POSITIONS = "ABCD"

#: A preset record: slot byte, 16-byte name, nine module blocks, 12-byte tail.
PRESET_TAIL_SIZE = 12
PRESET_RECORD_SIZE = (
    1 + PRESET_NAME_LENGTH + len(MODULE_CHAIN) * MODULE_BLOCK_SIZE
    + PRESET_TAIL_SIZE
)


def response_command(command: int) -> int:
    """Return the reply ID the device uses for *command*."""
    return int(command) & RESPONSE_MASK


def slot_to_address(slot: int) -> str:
    """Render a 1-based slot as the address the pedal displays.

    >>> slot_to_address(1)
    '1A'
    >>> slot_to_address(193)
    '49A'
    """
    if not FIRST_PRESET_SLOT <= slot <= LAST_PRESET_SLOT:
        raise ValueError(
            f"Preset slot must be {FIRST_PRESET_SLOT}-{LAST_PRESET_SLOT}, "
            f"got {slot}"
        )
    index = slot - FIRST_PRESET_SLOT
    bank = index // PRESETS_PER_BANK + FIRST_BANK
    return f"{bank}{PRESET_POSITIONS[index % PRESETS_PER_BANK]}"


def address_to_slot(bank: int, position: str) -> int:
    """Convert a bank (1-50) and position (A-D) to a 1-based slot.

    >>> address_to_slot(49, "A")
    193
    """
    if not FIRST_BANK <= bank <= LAST_BANK:
        raise ValueError(f"Bank must be {FIRST_BANK}-{LAST_BANK}, got {bank}")
    letter = position.upper()
    if letter not in PRESET_POSITIONS:
        raise ValueError(
            f"Preset position must be one of {PRESET_POSITIONS}, got {position!r}"
        )
    return (
        (bank - FIRST_BANK) * PRESETS_PER_BANK
        + PRESET_POSITIONS.index(letter)
        + FIRST_PRESET_SLOT
    )


def build_command(command: Command, payload: bytes = b"") -> bytes:
    """Build a single 64-byte HID report for a command."""
    return build_frame(command.value, payload)


# ---------------------------------------------------------------------------
# Effect module blocks
# ---------------------------------------------------------------------------


@dataclass
class ModuleBlock:
    """The state of one effect module.

    A module block is always 12 little-endian u16 words: the module's
    ON/OFF status, its effect type, then up to 10 parameter values.
    Unused parameter slots are zero-filled.

    Terminology follows the owner's manual (see GLOSSARY.md): the choice
    of effect within a module is its *effect type*, not its "model".
    """

    enabled: bool
    effect_type: int
    params: list[int] = field(default_factory=list)


def encode_module_block(block: ModuleBlock) -> bytes:
    """Serialise a :class:`ModuleBlock` to its 24-byte payload."""
    if len(block.params) > MAX_MODULE_PARAMS:
        raise ValueError(
            f"A module block holds at most {MAX_MODULE_PARAMS} parameters, "
            f"got {len(block.params)}"
        )

    words = [1 if block.enabled else 0, block.effect_type, *block.params]
    for word in words:
        if not 0 <= word <= 0xFFFF:
            raise ValueError(f"Module word must be 0-65535, got {word}")
    words += [0] * (MODULE_BLOCK_WORDS - len(words))

    return b"".join(w.to_bytes(2, "little") for w in words)


def decode_module_block(payload: bytes) -> ModuleBlock:
    """Parse a 24-byte module block payload."""
    if len(payload) != MODULE_BLOCK_SIZE:
        raise ValueError(
            f"Module block must be {MODULE_BLOCK_SIZE} bytes, "
            f"got {len(payload)}"
        )

    words = [
        int.from_bytes(payload[i : i + 2], "little")
        for i in range(0, MODULE_BLOCK_SIZE, 2)
    ]
    return ModuleBlock(
        enabled=bool(words[0]), effect_type=words[1], params=words[2:]
    )


def _module_command(module: str) -> Command:
    name = MODULE_NAME_ALIASES.get(module.lower(), module.lower())
    if name not in MODULE_COMMAND_MAP:
        raise ValueError(
            f"Unknown module '{module}'. Valid: {list(MODULE_COMMAND_MAP)}"
        )
    return MODULE_COMMAND_MAP[name]


def build_module_block(module: str, block: ModuleBlock) -> bytes:
    """Build a command that writes a module's complete state.

    This is how MOOER Studio edits effects: every knob turn resends the
    whole block for that module rather than a single-parameter delta.
    """
    return build_command(_module_command(module), encode_module_block(block))


# ---------------------------------------------------------------------------
# Preset operations
# ---------------------------------------------------------------------------


@dataclass
class PresetRecord:
    """A complete preset as the pedal stores it.

    Wire layout of a ``PRESET_RECORD`` (0x20) payload, 245 bytes::

        +------+-----------+---------------------------+-----------+
        | slot |   name    |  9 x 24-byte module block |   tail    |
        |  1 B |   16 B    |   in MODULE_CHAIN order   |   12 B    |
        +------+-----------+---------------------------+-----------+

    The trailing 12 bytes are preset-level settings whose fields are not
    yet identified; they are preserved verbatim on round-trip.

    The name field is held as raw bytes because the pedal is not
    consistent about padding -- factory presets are space-padded
    ("65 Deluxe       ") while user presets are NUL-padded
    ("Love of God\\x00\\x00\\x00\\x00\\x00"). Read :attr:`name` for the
    cleaned text; assign via :meth:`with_name` to re-pad.
    """

    slot: int
    name_raw: bytes = b"\x00" * PRESET_NAME_LENGTH
    modules: dict[Command, ModuleBlock] = field(default_factory=dict)
    tail: bytes = b"\x00" * PRESET_TAIL_SIZE

    @property
    def name(self) -> str:
        """The preset name with padding removed."""
        text = self.name_raw.split(b"\x00")[0]
        return text.decode("ascii", errors="replace").rstrip()

    def with_name(self, name: str) -> "PresetRecord":
        """Return a copy renamed to *name*, NUL-padded to 16 bytes."""
        encoded = name.encode("ascii", errors="replace")[:PRESET_NAME_LENGTH]
        return replace(self, name_raw=encoded.ljust(PRESET_NAME_LENGTH, b"\x00"))


def decode_preset_record(payload: bytes) -> PresetRecord:
    """Parse a 245-byte preset record payload."""
    if len(payload) != PRESET_RECORD_SIZE:
        raise ValueError(
            f"Preset record must be {PRESET_RECORD_SIZE} bytes, "
            f"got {len(payload)}"
        )

    modules: dict[Command, ModuleBlock] = {}
    offset = 1 + PRESET_NAME_LENGTH
    for command in MODULE_CHAIN:
        modules[command] = decode_module_block(
            payload[offset : offset + MODULE_BLOCK_SIZE]
        )
        offset += MODULE_BLOCK_SIZE

    return PresetRecord(
        slot=payload[0],
        name_raw=payload[1 : 1 + PRESET_NAME_LENGTH],
        modules=modules,
        tail=payload[offset:],
    )


def encode_preset_record(record: PresetRecord) -> bytes:
    """Serialise a :class:`PresetRecord` back to its 245-byte payload."""
    if not FIRST_PRESET_SLOT <= record.slot <= LAST_PRESET_SLOT:
        raise ValueError(
            f"Preset slot must be {FIRST_PRESET_SLOT}-{LAST_PRESET_SLOT}, "
            f"got {record.slot}"
        )
    if len(record.tail) != PRESET_TAIL_SIZE:
        raise ValueError(
            f"Preset tail must be {PRESET_TAIL_SIZE} bytes, "
            f"got {len(record.tail)}"
        )
    if len(record.name_raw) != PRESET_NAME_LENGTH:
        raise ValueError(
            f"Preset name field must be {PRESET_NAME_LENGTH} bytes, "
            f"got {len(record.name_raw)}"
        )

    out = bytes([record.slot]) + record.name_raw
    for command in MODULE_CHAIN:
        block = record.modules.get(command, ModuleBlock(enabled=False, effect_type=0))
        out += encode_module_block(block)
    return out + record.tail


def build_read_preset(slot: int) -> bytes:
    """Build a preset read request.

    Takes a 0-199 slot to match the rest of the server, even though the
    pedal numbers its slots 1-200 on the wire (see
    :data:`FIRST_PRESET_SLOT`). Migrating the server to the device's
    numbering is tracked separately; doing it here alone would leave the
    two halves inconsistent.

    Args:
        slot: Preset slot 0-199.
    """
    if not 0 <= slot <= 199:
        raise ValueError(f"Preset slot must be 0-199, got {slot}")
    return build_command(Command.READ_PRESET, bytes([slot]))


def build_hello() -> bytes:
    """Build the first message the editor sends after connecting."""
    return build_command(Command.HELLO, b"\x01")


def build_dump_presets() -> bytes:
    """Request every preset. The pedal replies with 200 records (0x20).

    Each reply is a 252-byte message split across four 63-byte HID
    reports, so read them with a reassembling reader.
    """
    return build_command(Command.DUMP_PRESETS, b"\x01")


def build_read_ir_list() -> bytes:
    """Request the user IR slot names. Replies 0x41: 40 x 16-byte names."""
    return build_command(Command.READ_IR_LIST, b"\x01")


#: The IR list reply holds this many fixed-width name fields.
IR_SLOT_COUNT = 40
#: An unused IR slot reads back as this literal.
IR_EMPTY_NAME = "EMPTY"


def decode_ir_list(payload: bytes) -> list[str]:
    """Parse an IR_LIST (0x41) payload into slot names.

    Unused slots come back as ``"EMPTY"``; the name is returned as-is so
    callers can decide what counts as empty.
    """
    expected = IR_SLOT_COUNT * PRESET_NAME_LENGTH
    if len(payload) != expected:
        raise ValueError(
            f"IR list must be {expected} bytes, got {len(payload)}"
        )

    names = []
    for i in range(IR_SLOT_COUNT):
        field = payload[i * PRESET_NAME_LENGTH : (i + 1) * PRESET_NAME_LENGTH]
        text = field.split(b"\x00")[0].decode("ascii", errors="replace").rstrip()
        names.append(text)
    return names


def decode_active_state(payload: bytes) -> PresetRecord:
    """Parse an ACTIVE_STATE (0x30) payload.

    Same shape as a preset record but with a slot byte and one flag byte
    where the 16-byte name would be, so the returned record has an empty
    name field.
    """
    expected = PRESET_RECORD_SIZE - PRESET_NAME_LENGTH + 1
    if len(payload) != expected:
        raise ValueError(
            f"Active state must be {expected} bytes, got {len(payload)}"
        )

    modules: dict[Command, ModuleBlock] = {}
    offset = 2
    for command in MODULE_CHAIN:
        modules[command] = decode_module_block(
            payload[offset : offset + MODULE_BLOCK_SIZE]
        )
        offset += MODULE_BLOCK_SIZE

    return PresetRecord(slot=payload[0], modules=modules, tail=payload[offset:])


def build_read_active_preset() -> bytes:
    """Request the currently active preset's state. Replies 0x30.

    The 0x30 payload is a slot byte, one flag byte, then the same nine
    module blocks and 12-byte tail as a preset record.
    """
    return build_command(Command.READ_ACTIVE, b"\x01")


def _check_slot(slot: int) -> None:
    if not FIRST_PRESET_SLOT <= slot <= LAST_PRESET_SLOT:
        raise ValueError(
            f"Preset slot must be {FIRST_PRESET_SLOT}-{LAST_PRESET_SLOT}, "
            f"got {slot}"
        )


def build_set_preset_name(slot: int, name: str) -> bytes:
    """Build a command that names a preset slot.

    The pedal sends the same shape as 0x17 when a preset is renamed from
    the hardware.

    Args:
        slot: Preset slot 0-199, matching the rest of the server.
        name: Preset name; ASCII, truncated to 16 bytes and zero-padded.
    """
    if not 0 <= slot <= 199:
        raise ValueError(f"Preset slot must be 0-199, got {slot}")

    encoded = name.encode("ascii", errors="replace")[:PRESET_NAME_LENGTH]
    padded = encoded.ljust(PRESET_NAME_LENGTH, b"\x00")
    return build_command(Command.PRESET_NAME, bytes([slot]) + padded)


def build_poll(selector: int) -> bytes:
    """Build a status poll. The device acknowledges with command 0x34.

    MOOER Studio issues these between edit batches; the selector appears
    to identify which state group it is asking the pedal to refresh.
    """
    if not 0 <= selector <= 0xFFFF:
        raise ValueError(f"Poll selector must be 0-65535, got {selector}")
    return build_command(Command.POLL, selector.to_bytes(2, "little"))


# ---------------------------------------------------------------------------
# Unverified: retained from the pre-capture GE150 protocol notes
# ---------------------------------------------------------------------------


def build_identify() -> bytes:
    """Build an Identify command (0x10) to handshake with the device.

    Unverified -- no identify exchange appears in the capture.
    """
    return build_command(Command.IDENTIFY)


def build_select_preset(slot: int) -> bytes:
    """Build a command to switch the active preset.

    Unverified. The capture shows 0xA6 carrying two u16 flags rather than
    a slot index, so the pre-capture assumption does not hold; this now
    uses the confirmed preset-read ID as the closest available stand-in.
    """
    if not 0 <= slot <= 199:
        raise ValueError(f"Preset slot must be 0-199, got {slot}")
    return build_command(Command.READ_PRESET, bytes([slot]))


def build_store_preset(slot: int, preset_data: bytes) -> list[bytes]:
    """Build StorePatch command(s) to write preset data to a slot.

    Unverified -- neither capture contains a preset write, so the command
    ID is still a guess. The 63-byte chunking it relies on *is* now
    confirmed (the pedal uses it for the 0x20 preset dump).

    Note the mismatch with what the pedal actually stores: a real preset
    record is 245 bytes and slots are numbered 1-200, whereas this takes
    512 bytes and 0-199. Reconciling that means re-indexing the server's
    slot model, which is deliberately left as separate work.

    Args:
        slot: Target preset slot 0-199.
        preset_data: The serialized 0x200-byte preset structure.
    """
    if not 0 <= slot <= 199:
        raise ValueError(f"Preset slot must be 0-199, got {slot}")
    if len(preset_data) != 0x200:
        raise ValueError(
            f"Preset data must be 512 bytes, got {len(preset_data)}"
        )
    payload = bytes([slot]) + preset_data
    return build_chunked_frames(Command.STORE_PATCH.value, payload)


def build_effect_param(module: str, param_index: int, value: int) -> bytes:
    """Build a command to set a single effect parameter.

    Unverified. MOOER Studio never sends single-parameter deltas -- it
    resends the whole module block. Prefer :func:`build_module_block`.

    Args:
        module: Effect module name (see :data:`MODULE_COMMAND_MAP`).
        param_index: Parameter byte index within the module.
        value: Parameter value (0-255).
    """
    if not 0 <= value <= 255:
        raise ValueError(f"Parameter value must be 0-255, got {value}")
    return build_command(_module_command(module), bytes([param_index, value]))


def build_toggle_effect(module: str, enabled: bool) -> bytes:
    """Build a command to enable or disable an effect module.

    Unverified in this form. In the capture the enable flag is word 0 of
    the full module block, so toggling requires the module's current
    state; use :func:`build_module_block` when that state is known.
    """
    return build_effect_param(module, 1, 1 if enabled else 0)


def build_set_volume(volume: int) -> bytes:
    """Build a Volume command. Unverified.

    Args:
        volume: Volume level 0-100.
    """
    if not 0 <= volume <= 100:
        raise ValueError(f"Volume must be 0-100, got {volume}")
    return build_command(Command.VOLUME, bytes([volume]))


def build_get_volume() -> bytes:
    """Build a Volume read command. Unverified."""
    return build_command(Command.VOLUME)


def build_get_system_settings() -> bytes:
    """Build a System settings read command. Unverified."""
    return build_command(Command.SYSTEM)


def build_set_system_setting(setting_index: int, value: int) -> bytes:
    """Build a command to modify a system setting. Unverified.

    Args:
        setting_index: Setting byte offset.
        value: Setting value.
    """
    return build_command(Command.SYSTEM, bytes([setting_index, value]))
