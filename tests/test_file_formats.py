"""Tests for .mo, .gnr, and .mbf file format handlers."""

import tempfile
from pathlib import Path

from mooer_ge150_mcp.models.preset import Preset
from mooer_ge150_mcp.models.effects import AmpModule, ReverbModule
from mooer_ge150_mcp.models.file_formats import (
    export_mo,
    import_mo,
    export_mbf,
    import_mbf,
    parse_gnr_header,
    MO_FILE_SIZE,
    MO_PRESET_OFFSET,
    GNR_MAGIC,
)


def test_mo_export_size():
    """Exported .mo file should be exactly 0x800 bytes."""
    preset = Preset(name="MO Test")
    with tempfile.NamedTemporaryFile(suffix=".mo", delete=False) as f:
        path = export_mo(preset, f.name)
    data = Path(path).read_bytes()
    assert len(data) == MO_FILE_SIZE
    Path(path).unlink()


def test_mo_roundtrip():
    """Export and re-import a .mo file."""
    preset = Preset(
        name="Roundtrip",
        amp=AmpModule(enabled=1, type=10, amp_gain=200),
        reverb=ReverbModule(enabled=1, type=5, level=80),
    )
    with tempfile.NamedTemporaryFile(suffix=".mo", delete=False) as f:
        path = export_mo(preset, f.name)

    restored = import_mo(path)
    assert restored.name == "Roundtrip"
    assert restored.amp.type == 10
    assert restored.amp.amp_gain == 200
    assert restored.reverb.level == 80
    Path(path).unlink()


def test_mo_preset_offset():
    """Preset data in .mo file should start at offset 0x200."""
    preset = Preset(name="Offset Test")
    with tempfile.NamedTemporaryFile(suffix=".mo", delete=False) as f:
        path = export_mo(preset, f.name)
    data = Path(path).read_bytes()
    # First 0x200 bytes should be zeroed header
    assert data[:MO_PRESET_OFFSET] == b"\x00" * MO_PRESET_OFFSET
    # Preset name should appear at offset 0x200 + 0x0C
    name_offset = MO_PRESET_OFFSET + 0x0C
    assert data[name_offset:name_offset + 11] == b"Offset Test"
    Path(path).unlink()


def test_mbf_roundtrip():
    """Export and re-import a .mbf backup file."""
    presets = [
        Preset(name=f"Preset {i}") for i in range(5)
    ]
    with tempfile.NamedTemporaryFile(suffix=".mbf", delete=False) as f:
        path = export_mbf(presets, f.name)

    restored = import_mbf(path)
    assert len(restored) > 0
    assert restored[0].name == "Preset 0"
    assert restored[4].name == "Preset 4"
    Path(path).unlink()


def test_gnr_header_valid():
    """Parse a valid GNR header."""
    data = GNR_MAGIC + b"\x08\x00\x00\x00" + b"testinfo" + b"\x00" * 100
    header = parse_gnr_header(data)
    assert header["magic"] == "mooerge"
    assert header["info_size"] == 8
    assert header["data_offset"] == 20  # 8 + 4 + 8


def test_gnr_header_invalid():
    """Invalid magic should raise ValueError."""
    try:
        parse_gnr_header(b"badmagic\x00\x00\x00\x00")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_gnr_too_small():
    """GNR data too small should raise ValueError."""
    try:
        parse_gnr_header(b"short")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
