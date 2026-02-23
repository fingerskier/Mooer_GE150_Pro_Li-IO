"""Tests for command builders."""

import pytest

from mooer_ge150_mcp.protocol.commands import (
    Command,
    build_command,
    build_identify,
    build_select_preset,
    build_read_preset,
    build_store_preset,
    build_effect_param,
    build_toggle_effect,
    build_set_volume,
    build_get_volume,
)
from mooer_ge150_mcp.protocol.framing import parse_frame, HID_REPORT_SIZE


def test_command_enum_values():
    """Verify key command IDs match the spec."""
    assert Command.IDENTIFY == 0x10
    assert Command.ACTIVE_PATCH == 0xA6
    assert Command.STORE_PATCH == 0xA8
    assert Command.FX == 0x90
    assert Command.AMP == 0x93
    assert Command.REVERB == 0x99
    assert Command.VOLUME == 0xA2
    assert Command.SYSTEM == 0xA1


def test_build_identify():
    """Identify command should be a valid 64-byte frame."""
    frame = build_identify()
    assert len(frame) == HID_REPORT_SIZE
    parsed = parse_frame(frame)
    assert parsed is not None
    assert parsed.command == Command.IDENTIFY


def test_build_select_preset():
    """Select preset should embed the slot index."""
    frame = build_select_preset(42)
    parsed = parse_frame(frame)
    assert parsed is not None
    assert parsed.command == Command.ACTIVE_PATCH
    assert parsed.payload == bytes([42])


def test_select_preset_bounds():
    """Selecting out-of-range slot should raise."""
    with pytest.raises(ValueError):
        build_select_preset(200)
    with pytest.raises(ValueError):
        build_select_preset(-1)


def test_build_read_preset():
    """Read preset command should contain the slot index."""
    frame = build_read_preset(0)
    parsed = parse_frame(frame)
    assert parsed is not None
    assert parsed.command == Command.PRESET
    assert parsed.payload == bytes([0])


def test_build_store_preset():
    """Store preset should produce chunked frames for 512-byte data."""
    preset_data = b"\x00" * 0x200
    frames = build_store_preset(5, preset_data)
    assert isinstance(frames, list)
    assert len(frames) > 0
    for f in frames:
        assert len(f) == HID_REPORT_SIZE


def test_store_preset_wrong_size():
    """Store preset with wrong data size should raise."""
    with pytest.raises(ValueError):
        build_store_preset(0, b"\x00" * 100)


def test_build_effect_param():
    """Effect param command should use the correct module command ID."""
    frame = build_effect_param("amp", 3, 128)
    parsed = parse_frame(frame)
    assert parsed is not None
    assert parsed.command == Command.AMP
    assert parsed.payload == bytes([3, 128])


def test_effect_param_invalid_module():
    """Unknown module should raise."""
    with pytest.raises(ValueError):
        build_effect_param("unknown", 0, 0)


def test_effect_param_invalid_value():
    """Value out of 0-255 should raise."""
    with pytest.raises(ValueError):
        build_effect_param("amp", 0, 256)


def test_build_toggle_effect():
    """Toggle should set param index 1 to 0 or 1."""
    on_frame = build_toggle_effect("amp", True)
    off_frame = build_toggle_effect("amp", False)

    on_parsed = parse_frame(on_frame)
    off_parsed = parse_frame(off_frame)

    assert on_parsed is not None
    assert on_parsed.payload == bytes([1, 1])

    assert off_parsed is not None
    assert off_parsed.payload == bytes([1, 0])


def test_build_set_volume():
    """Volume command should contain the volume level."""
    frame = build_set_volume(75)
    parsed = parse_frame(frame)
    assert parsed is not None
    assert parsed.command == Command.VOLUME
    assert parsed.payload == bytes([75])


def test_volume_bounds():
    """Volume out of 0-100 should raise."""
    with pytest.raises(ValueError):
        build_set_volume(101)
    with pytest.raises(ValueError):
        build_set_volume(-1)


def test_build_get_volume():
    """Get volume command should have no payload."""
    frame = build_get_volume()
    parsed = parse_frame(frame)
    assert parsed is not None
    assert parsed.command == Command.VOLUME
    assert parsed.payload == b""
