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

from dataclasses import dataclass, field
from enum import IntEnum

from .framing import build_frame, build_chunked_frames

#: Device replies reuse the request ID with the high bit cleared.
RESPONSE_MASK = 0x7F

#: Effect module blocks are a fixed 12 little-endian u16 words.
MODULE_BLOCK_WORDS = 12
MODULE_BLOCK_SIZE = MODULE_BLOCK_WORDS * 2
#: Words 0 and 1 are the enable flag and model index; the rest are params.
MAX_MODULE_PARAMS = MODULE_BLOCK_WORDS - 2


class Command(IntEnum):
    """Command group identifiers."""

    # --- effect module blocks, in signal-chain order ---------------------
    # 0x84 (amp) and 0x85 (cab) are confirmed: editing the amp block makes
    # the device push an updated cab block back as command 0x05. The rest
    # follow the pedal's chain order; 0x81 and 0x87 were never touched
    # during the capture and are inferred from their position.
    NS = 0x81  # unverified
    FX = 0x82  # confirmed (4 params)
    DRIVE = 0x83  # confirmed (3 params)
    AMP = 0x84  # confirmed (6 params)
    CAB = 0x85  # confirmed
    EQ = 0x86  # confirmed as a block; identity inferred (models 0-2)
    MOD = 0x87  # unverified
    DELAY = 0x88  # confirmed (7 params)
    REVERB = 0x89  # confirmed

    # --- preset operations ----------------------------------------------
    READ_PRESET = 0x96  # confirmed: 1-byte slot argument
    PRESET_NAME = 0x97  # confirmed: slot byte + 16-byte ASCII name

    # --- miscellaneous, shape confirmed but meaning not yet pinned down --
    SETTING_A4 = 0xA4  # confirmed: single u16
    SETTING_A5 = 0xA5  # confirmed: single u16
    SETTING_A6 = 0xA6  # confirmed: two u16 flags
    SETTING_A7 = 0xA7  # confirmed: single u16
    SETTING_AC = 0xAC  # confirmed: 9-byte struct
    POLL = 0xB4  # confirmed: u16 selector, acked by 0x34
    ASSIGNMENT = 0xD1  # confirmed: 18-byte assignment block

    # --- not observed in the capture -------------------------------------
    IDENTIFY = 0x10  # unverified
    SYSTEM = 0xA1  # unverified
    VOLUME = 0xA2  # unverified
    PEDAL_ASSIGNMENT = 0xA3  # unverified
    STORE_PATCH = 0xA8  # unverified
    CABINET_UPLOAD = 0xE1  # unverified
    AMP_UPLOAD = 0xE2  # unverified
    AMP_MODELS = 0xE3  # unverified


#: Mapping from human-readable module names to command IDs.
MODULE_COMMAND_MAP: dict[str, Command] = {
    "ns": Command.NS,
    "fx": Command.FX,
    "od": Command.DRIVE,
    "amp": Command.AMP,
    "cab": Command.CAB,
    "eq": Command.EQ,
    "mod": Command.MOD,
    "delay": Command.DELAY,
    "reverb": Command.REVERB,
}

#: Length of the ASCII name field in a PRESET_NAME message.
PRESET_NAME_LENGTH = 16


def response_command(command: int) -> int:
    """Return the reply ID the device uses for *command*."""
    return int(command) & RESPONSE_MASK


def build_command(command: Command, payload: bytes = b"") -> bytes:
    """Build a single 64-byte HID report for a command."""
    return build_frame(command.value, payload)


# ---------------------------------------------------------------------------
# Effect module blocks
# ---------------------------------------------------------------------------


@dataclass
class ModuleBlock:
    """The state of one effect module.

    A module block is always 12 little-endian u16 words: an enable flag,
    a model index, then up to 10 parameter values. Unused parameter slots
    are zero-filled.
    """

    enabled: bool
    model: int
    params: list[int] = field(default_factory=list)


def encode_module_block(block: ModuleBlock) -> bytes:
    """Serialise a :class:`ModuleBlock` to its 24-byte payload."""
    if len(block.params) > MAX_MODULE_PARAMS:
        raise ValueError(
            f"A module block holds at most {MAX_MODULE_PARAMS} parameters, "
            f"got {len(block.params)}"
        )

    words = [1 if block.enabled else 0, block.model, *block.params]
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
    return ModuleBlock(enabled=bool(words[0]), model=words[1], params=words[2:])


def _module_command(module: str) -> Command:
    if module not in MODULE_COMMAND_MAP:
        raise ValueError(
            f"Unknown module '{module}'. Valid: {list(MODULE_COMMAND_MAP)}"
        )
    return MODULE_COMMAND_MAP[module]


def build_module_block(module: str, block: ModuleBlock) -> bytes:
    """Build a command that writes a module's complete state.

    This is how MOOER Studio edits effects: every knob turn resends the
    whole block for that module rather than a single-parameter delta.
    """
    return build_command(_module_command(module), encode_module_block(block))


# ---------------------------------------------------------------------------
# Preset operations
# ---------------------------------------------------------------------------


def build_read_preset(slot: int) -> bytes:
    """Build a preset read request.

    Args:
        slot: Preset index 0-199.
    """
    if not 0 <= slot <= 199:
        raise ValueError(f"Preset slot must be 0-199, got {slot}")
    return build_command(Command.READ_PRESET, bytes([slot]))


def build_set_preset_name(slot: int, name: str) -> bytes:
    """Build a command that names a preset slot.

    Args:
        slot: Preset index 0-199.
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

    Unverified -- the capture contains no preset write, so neither the
    command ID nor the chunking scheme is confirmed.

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
