"""Command group constants and high-level command builders.

Each command is identified by a single-byte group ID used for both
host-to-device requests and device-to-host responses.
"""

from __future__ import annotations

from enum import IntEnum

from .framing import build_frame, build_chunked_frames


class Command(IntEnum):
    """Command group identifiers."""

    IDENTIFY = 0x10
    MENU = 0x82
    PRESET = 0x83
    PEDAL_ASSIGN_ALT = 0x84
    CAB_MODELS = 0x85
    FOOT_SWITCH = 0x89
    FX = 0x90
    DS_OD = 0x91
    AMP = 0x93
    CAB = 0x94
    NS_GATE = 0x95
    EQ = 0x96
    MOD = 0x97
    DELAY = 0x98
    REVERB = 0x99
    SYSTEM = 0xA1
    VOLUME = 0xA2
    PEDAL_ASSIGNMENT = 0xA3
    PATCH_ALTERNATE = 0xA4
    PATCH_SETTING = 0xA5
    ACTIVE_PATCH = 0xA6
    STORE_PATCH = 0xA8
    ACTIVE_PATCH_SETTING = 0xA9
    CABINET_UPLOAD = 0xE1
    AMP_UPLOAD = 0xE2
    AMP_MODELS = 0xE3


# Mapping from human-readable module names to command IDs
MODULE_COMMAND_MAP: dict[str, Command] = {
    "fx": Command.FX,
    "od": Command.DS_OD,
    "amp": Command.AMP,
    "cab": Command.CAB,
    "ns": Command.NS_GATE,
    "eq": Command.EQ,
    "mod": Command.MOD,
    "delay": Command.DELAY,
    "reverb": Command.REVERB,
}


def build_command(command: Command, payload: bytes = b"") -> bytes:
    """Build a single 64-byte HID report for a command."""
    return build_frame(command.value, payload)


def build_identify() -> bytes:
    """Build an Identify command (0x10) to handshake with the device."""
    return build_command(Command.IDENTIFY)


def build_select_preset(slot: int) -> bytes:
    """Build an ActivePatch command to switch the active preset.

    Args:
        slot: Preset index 0-199.
    """
    if not 0 <= slot <= 199:
        raise ValueError(f"Preset slot must be 0-199, got {slot}")
    return build_command(Command.ACTIVE_PATCH, bytes([slot]))


def build_read_preset(slot: int) -> bytes:
    """Build a Preset read command for a specific slot.

    Args:
        slot: Preset index 0-199.
    """
    if not 0 <= slot <= 199:
        raise ValueError(f"Preset slot must be 0-199, got {slot}")
    return build_command(Command.PRESET, bytes([slot]))


def build_store_preset(slot: int, preset_data: bytes) -> list[bytes]:
    """Build StorePatch command(s) to write preset data to a slot.

    The preset data may exceed a single 64-byte frame, so this returns
    a list of HID reports.

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


def build_effect_param(
    module: str, param_index: int, value: int
) -> bytes:
    """Build a command to set a single effect parameter.

    Args:
        module: Effect module name (fx, od, amp, cab, ns, eq, mod, delay, reverb).
        param_index: Parameter byte index within the module.
        value: Parameter value (0-255).
    """
    if module not in MODULE_COMMAND_MAP:
        raise ValueError(
            f"Unknown module '{module}'. Valid: {list(MODULE_COMMAND_MAP)}"
        )
    if not 0 <= value <= 255:
        raise ValueError(f"Parameter value must be 0-255, got {value}")
    cmd = MODULE_COMMAND_MAP[module]
    return build_command(cmd, bytes([param_index, value]))


def build_toggle_effect(module: str, enabled: bool) -> bytes:
    """Build a command to enable or disable an effect module.

    The enabled state is typically at param index 1 in each module.
    """
    return build_effect_param(module, 1, 1 if enabled else 0)


def build_set_volume(volume: int) -> bytes:
    """Build a Volume command.

    Args:
        volume: Volume level 0-100.
    """
    if not 0 <= volume <= 100:
        raise ValueError(f"Volume must be 0-100, got {volume}")
    return build_command(Command.VOLUME, bytes([volume]))


def build_get_volume() -> bytes:
    """Build a Volume read command."""
    return build_command(Command.VOLUME)


def build_get_system_settings() -> bytes:
    """Build a System settings read command."""
    return build_command(Command.SYSTEM)


def build_set_system_setting(setting_index: int, value: int) -> bytes:
    """Build a command to modify a system setting.

    Args:
        setting_index: Setting byte offset.
        value: Setting value.
    """
    return build_command(Command.SYSTEM, bytes([setting_index, value]))
