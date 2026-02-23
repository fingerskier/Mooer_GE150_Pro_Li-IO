"""Response parsing for device messages."""

from __future__ import annotations

from dataclasses import dataclass

from .commands import Command
from .framing import Frame


@dataclass
class IdentifyResponse:
    """Parsed Identify (0x10) response."""

    firmware: str
    device_name: str
    raw: bytes

    def __repr__(self) -> str:
        return (
            f"IdentifyResponse(firmware={self.firmware!r}, "
            f"device_name={self.device_name!r})"
        )


@dataclass
class PresetResponse:
    """Parsed Preset (0x83) response containing raw preset data."""

    slot: int
    data: bytes  # 0x200 bytes of preset data

    def __repr__(self) -> str:
        return f"PresetResponse(slot={self.slot}, data_len={len(self.data)})"


@dataclass
class ActivePatchResponse:
    """Parsed ActivePatch (0xA6) response."""

    slot: int


@dataclass
class VolumeResponse:
    """Parsed Volume (0xA2) response."""

    volume: int


@dataclass
class SystemResponse:
    """Parsed System (0xA1) response."""

    data: bytes


def parse_identify(frame: Frame) -> IdentifyResponse | None:
    """Parse an Identify response frame.

    The response payload contains a 5-byte firmware version followed by
    an 11-byte device name.
    """
    if frame.command != Command.IDENTIFY:
        return None

    payload = frame.payload
    if len(payload) < 16:
        return IdentifyResponse(
            firmware="unknown",
            device_name="unknown",
            raw=payload,
        )

    # Firmware: 5 bytes as version string (e.g., "1.5.0")
    fw_bytes = payload[:5]
    firmware_parts = [str(b) for b in fw_bytes if b != 0]
    firmware = ".".join(firmware_parts) if firmware_parts else "unknown"

    # Device name: next 11 bytes, null-terminated ASCII
    name_bytes = payload[5:16]
    device_name = name_bytes.split(b"\x00")[0].decode("ascii", errors="replace")

    return IdentifyResponse(
        firmware=firmware,
        device_name=device_name,
        raw=payload,
    )


def parse_preset_response(frame: Frame) -> PresetResponse | None:
    """Parse a Preset response frame."""
    if frame.command != Command.PRESET:
        return None

    if len(frame.payload) < 1:
        return None

    slot = frame.payload[0]
    data = frame.payload[1:]
    return PresetResponse(slot=slot, data=data)


def parse_active_patch(frame: Frame) -> ActivePatchResponse | None:
    """Parse an ActivePatch response."""
    if frame.command != Command.ACTIVE_PATCH:
        return None
    if len(frame.payload) < 1:
        return None
    return ActivePatchResponse(slot=frame.payload[0])


def parse_volume(frame: Frame) -> VolumeResponse | None:
    """Parse a Volume response."""
    if frame.command != Command.VOLUME:
        return None
    if len(frame.payload) < 1:
        return None
    return VolumeResponse(volume=frame.payload[0])


def parse_system(frame: Frame) -> SystemResponse | None:
    """Parse a System settings response."""
    if frame.command != Command.SYSTEM:
        return None
    return SystemResponse(data=frame.payload)


def parse_response(frame: Frame):
    """Auto-dispatch a frame to the appropriate response parser.

    Returns the parsed response dataclass, or the raw Frame if no
    specific parser matches.
    """
    parsers = {
        Command.IDENTIFY: parse_identify,
        Command.PRESET: parse_preset_response,
        Command.ACTIVE_PATCH: parse_active_patch,
        Command.VOLUME: parse_volume,
        Command.SYSTEM: parse_system,
    }
    parser = parsers.get(Command(frame.command))
    if parser:
        result = parser(frame)
        if result is not None:
            return result
    return frame
