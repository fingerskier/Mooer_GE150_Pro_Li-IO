"""Tests for the surviving command builders.

The pre-capture builders (identify, store-patch, byte-delta effect
params, volume, system settings) were deleted rather than kept: several
of their guessed command IDs turned out to collide with real commands
(0xA5 is screen brightness, 0xA8 a real setting), so an unverified
builder is a hazard, not a convenience. What remains here is only what
the captures back.
"""

import pytest

from mooer_ge150_mcp.protocol.commands import (
    Command,
    build_save_preset,
    build_select_preset_slot,
    build_set_cab_sim_thru,
    build_set_input_level,
    build_set_spillover,
    build_write_ctrl_config,
    db_to_level,
    level_to_db,
)
from mooer_ge150_mcp.protocol.framing import parse_frame, HID_REPORT_SIZE


def test_command_enum_values():
    """Key command IDs, as confirmed across the six captures."""
    assert Command.FX == 0x82
    assert Command.AMP == 0x84
    assert Command.CAB == 0x85
    assert Command.DELAY == 0x89
    assert Command.REVERB == 0x8A
    assert Command.SELECT_PRESET == 0x96
    assert Command.SAVE_PRESET == 0x97
    assert Command.WRITE_PRESET == 0xC3
    assert Command.INPUT_LEVEL == 0xA4
    assert Command.SCREEN_BRIGHTNESS == 0xA5
    assert Command.CAB_SIM_THRU == 0xA6
    assert Command.POLL == 0xB4

    # Never observed; retained as documentation only, with no builders.
    assert Command.IDENTIFY == 0x10
    assert Command.VOLUME == 0xA2
    assert Command.SYSTEM == 0xA1


def test_no_store_patch_alias():
    """0xA8 is a real setting; the old STORE_PATCH alias invited misuse."""
    assert not hasattr(Command, "STORE_PATCH")


def test_build_select_preset_slot_is_one_based():
    frame = parse_frame(build_select_preset_slot(200))
    assert frame is not None
    assert frame.command == Command.SELECT_PRESET
    assert frame.payload == bytes([200])

    with pytest.raises(ValueError):
        build_select_preset_slot(0)
    with pytest.raises(ValueError):
        build_select_preset_slot(201)


def test_build_save_preset_carries_slot_and_padded_name():
    frame = parse_frame(build_save_preset(200, "Dual Lead"))
    assert frame is not None
    assert frame.command == Command.SAVE_PRESET
    assert frame.payload[0] == 200
    assert frame.payload[1:] == b"Dual Lead".ljust(16, b"\x00")


def test_level_encoding_matches_both_observed_points():
    """2.5 dB -> 14 (input level) and 1.0 dB -> 11 (OTG level)."""
    assert db_to_level(2.5) == 14
    assert db_to_level(1.0) == 11
    assert db_to_level(0.0) == 9
    assert level_to_db(14) == 2.5
    assert level_to_db(9) == 0.0


def test_settings_builders_produce_valid_frames():
    for report in (
        build_set_input_level(14),
        build_set_cab_sim_thru(True, False),
        build_set_spillover(True),
        build_write_ctrl_config(0, [True] + [False] * 8),
    ):
        assert len(report) == HID_REPORT_SIZE
        assert parse_frame(report) is not None


def test_ctrl_config_needs_exactly_nine_flags():
    with pytest.raises(ValueError):
        build_write_ctrl_config(0, [True] * 8)
