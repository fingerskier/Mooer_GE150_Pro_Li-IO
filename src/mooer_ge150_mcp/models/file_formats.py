"""File format handlers for .mo, .gnr, and .mbf files.

.mo  — Single preset (0x800 bytes / 2048 bytes)
.gnr — IR / cabinet impulse response
.mbf — Full device backup (199 presets)
"""

from __future__ import annotations

import struct
from pathlib import Path

from .preset import Preset, PRESET_SIZE

MO_FILE_SIZE = 0x800  # 2048 bytes
MO_HEADER_SIZE = 0x200
MO_PRESET_OFFSET = 0x200

GNR_MAGIC = b"mooerge\x00"

MBF_MANUFACTURER_SIZE = 8
MBF_MODEL_NAME_SIZE = 32
MBF_PRESET_ENTRY_SIZE = 0x222  # 546 bytes per preset
MBF_PRESET_COUNT = 199


def export_mo(preset: Preset, path: str | Path) -> Path:
    """Export a single preset to a .mo file.

    The .mo format is 0x800 bytes:
      - 0x000-0x1FF: Header (zeroed)
      - 0x200-0x3FF: Preset data (512 bytes)
      - 0x400-0x7FF: Padding (zeroed)

    Args:
        preset: The preset to export.
        path: Output file path.

    Returns:
        The path written to.
    """
    path = Path(path)
    buf = bytearray(MO_FILE_SIZE)
    preset_data = preset.to_bytes()
    buf[MO_PRESET_OFFSET : MO_PRESET_OFFSET + PRESET_SIZE] = preset_data
    path.write_bytes(bytes(buf))
    return path


def import_mo(path: str | Path) -> Preset:
    """Import a preset from a .mo file.

    Args:
        path: Path to the .mo file.

    Returns:
        The parsed Preset.

    Raises:
        ValueError: If the file is too small.
    """
    path = Path(path)
    data = path.read_bytes()
    if len(data) < MO_PRESET_OFFSET + PRESET_SIZE:
        raise ValueError(
            f"File too small for .mo format: {len(data)} bytes "
            f"(need at least {MO_PRESET_OFFSET + PRESET_SIZE})"
        )
    preset_data = data[MO_PRESET_OFFSET : MO_PRESET_OFFSET + PRESET_SIZE]
    return Preset.from_bytes(preset_data)


def parse_gnr_header(data: bytes) -> dict:
    """Parse the header of a .gnr (IR/cabinet) file.

    Returns:
        A dict with 'magic', 'info_size', and 'info' keys.

    Raises:
        ValueError: If the magic header doesn't match.
    """
    if len(data) < 12:
        raise ValueError(f"GNR file too small: {len(data)} bytes")

    magic = data[:8]
    if magic != GNR_MAGIC:
        raise ValueError(
            f"Invalid GNR magic: {magic!r} (expected {GNR_MAGIC!r})"
        )

    info_size = struct.unpack_from("<I", data, 8)[0]
    info = data[12 : 12 + info_size] if len(data) >= 12 + info_size else data[12:]

    return {
        "magic": magic.decode("ascii", errors="replace").rstrip("\x00"),
        "info_size": info_size,
        "info": info,
        "data_offset": 12 + info_size,
    }


def export_mbf(
    presets: list[Preset],
    path: str | Path,
    manufacturer: str = "MOOER",
    model_name: str = "GE150 Pro Li",
) -> Path:
    """Export a full backup to a .mbf file.

    Args:
        presets: List of up to 199 presets.
        path: Output file path.
        manufacturer: 8-byte manufacturer string.
        model_name: 32-byte model name.

    Returns:
        The path written to.
    """
    path = Path(path)

    buf = bytearray()

    # Manufacturer (8 bytes)
    mfg = manufacturer.encode("ascii")[:MBF_MANUFACTURER_SIZE]
    buf.extend(mfg.ljust(MBF_MANUFACTURER_SIZE, b"\x00"))

    # Model name (32 bytes)
    model = model_name.encode("ascii")[:MBF_MODEL_NAME_SIZE]
    buf.extend(model.ljust(MBF_MODEL_NAME_SIZE, b"\x00"))

    # Version placeholder (assume some fixed bytes for now)
    buf.extend(b"\x01\x00\x00\x00")

    # Preset entries (0x222 bytes each, up to 199)
    for i in range(MBF_PRESET_COUNT):
        entry = bytearray(MBF_PRESET_ENTRY_SIZE)
        if i < len(presets):
            preset_data = presets[i].to_bytes()
            entry[:PRESET_SIZE] = preset_data
        buf.extend(entry)

    path.write_bytes(bytes(buf))
    return path


def import_mbf(path: str | Path) -> list[Preset]:
    """Import presets from a .mbf backup file.

    Args:
        path: Path to the .mbf file.

    Returns:
        List of parsed Presets (up to 199).
    """
    path = Path(path)
    data = path.read_bytes()

    # Skip header: manufacturer (8) + model name (32) + version (4)
    header_size = MBF_MANUFACTURER_SIZE + MBF_MODEL_NAME_SIZE + 4
    offset = header_size

    presets: list[Preset] = []
    for _ in range(MBF_PRESET_COUNT):
        if offset + MBF_PRESET_ENTRY_SIZE > len(data):
            break
        entry = data[offset : offset + MBF_PRESET_ENTRY_SIZE]
        preset = Preset.from_bytes(entry[:PRESET_SIZE])
        presets.append(preset)
        offset += MBF_PRESET_ENTRY_SIZE

    return presets
