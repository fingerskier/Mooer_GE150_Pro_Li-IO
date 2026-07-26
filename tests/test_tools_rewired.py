"""The MCP tools that now run on capture-confirmed protocol paths.

These drive the real tool functions against ``FakeMaxPedal``, which only
implements exchanges observed in the USB captures. A tool that reverts to
a guessed command will stop getting answers and fail here.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from mooer_ge150_mcp.protocol.commands import Command

from .fake_max_pedal import FakeMaxPedal, make_max_connection
from .test_restore_overwrite import _get_server_module


@pytest.fixture
def wired():
    """Server module with a FakeMaxPedal patched in as the connection."""
    server = _get_server_module()
    conn, pedal = make_max_connection()
    server._record_cache = {}
    with patch.object(server, "_get_connection", return_value=conn):
        yield server, conn, pedal


def test_server_imports_and_registers_its_tools():
    """FastMCP must accept our constructor arguments.

    The rest of the suite mocks FastMCP out, so nothing else would notice
    the server failing to start.
    """
    import asyncio

    import mooer_ge150_mcp.server as real_server

    tools = asyncio.run(real_server.mcp.list_tools())
    assert len(tools) == 22
    assert "list_ir_slots" in {t.name for t in tools}


class TestListIrSlots:
    """Was sending the CAB module-block write; now sends READ_IR_LIST."""

    def test_returns_all_forty_slots(self, wired):
        server, _, _ = wired
        result = server.list_ir_slots()
        assert "error" not in result
        assert len(result["slots"]) == 40

    def test_empty_slots_are_flagged(self, wired):
        server, _, _ = wired
        assert all(s["empty"] for s in server.list_ir_slots()["slots"])

    def test_named_slots_are_reported(self, wired):
        server, _, pedal = wired
        pedal.ir_names[3] = "Marshall 4x12"

        slots = server.list_ir_slots()["slots"]
        assert slots[3] == {"slot": 3, "name": "Marshall 4x12", "empty": False}

    def test_does_not_write_a_cab_module_block(self, wired):
        """The old implementation sent 0x85, which edits the cab module."""
        server, _, pedal = wired
        server.list_ir_slots()
        assert pedal.written_blocks == []


class TestPresetListing:
    """Reads via the confirmed bulk dump rather than a per-slot request."""

    def test_lists_every_slot(self, wired):
        server, _, _ = wired
        result = server.list_presets()
        assert "error" not in result
        assert len(result["presets"]) == 200
        assert result["received"] == 200

    def test_names_come_from_the_device(self, wired):
        server, _, _ = wired
        presets = server.list_presets(0, 2)["presets"]
        assert [p["name"] for p in presets] == [
            "Preset 1", "Preset 2", "Preset 3",
        ]

    def test_reports_the_pedal_s_own_address(self, wired):
        server, _, _ = wired
        presets = server.list_presets(0, 4)["presets"]
        assert [p["address"] for p in presets] == ["1A", "1B", "1C", "1D", "2A"]

    def test_range_is_honoured(self, wired):
        server, _, _ = wired
        result = server.list_presets(10, 12)
        assert [p["slot"] for p in result["presets"]] == [10, 11, 12]

    def test_bad_range_rejected(self, wired):
        server, _, _ = wired
        assert "error" in server.list_presets(0, 200)


class TestGetPreset:
    def test_returns_all_nine_modules(self, wired):
        server, _, _ = wired
        result = server.get_preset(0)
        assert "error" not in result
        assert set(result["modules"]) == {
            "fx", "ds", "amp", "cab", "ns", "eq", "mod", "delay", "reverb",
        }

    def test_reports_slot_name_and_address(self, wired):
        server, _, _ = wired
        result = server.get_preset(192)
        assert result["slot"] == 192
        assert result["address"] == "49A"
        assert result["name"] == "Preset 193"

    def test_module_state_uses_manual_terminology(self, wired):
        server, _, _ = wired
        amp = server.get_preset(0)["modules"]["amp"]
        assert set(amp) == {"enabled", "effect_type", "params"}


class TestEffectEditing:
    """Whole-block writes, as the editor does -- not parameter deltas."""

    def test_toggle_writes_one_whole_block(self, wired):
        server, _, pedal = wired
        result = server.toggle_effect("amp", False)

        assert result["enabled"] is False
        assert len(pedal.written_blocks) == 1
        command, block = pedal.written_blocks[0]
        assert command == Command.AMP
        assert block.enabled is False

    def test_toggle_preserves_effect_type_and_params(self, wired):
        server, _, pedal = wired
        before = pedal.records[pedal.active_slot].modules[Command.AMP]

        server.toggle_effect("amp", False)

        _, written = pedal.written_blocks[0]
        assert written.effect_type == before.effect_type
        assert written.params[:3] == before.params[:3]

    def test_set_param_changes_only_the_target(self, wired):
        server, _, pedal = wired
        before = pedal.records[pedal.active_slot].modules[Command.DELAY]

        server.set_effect_param("delay", param_index=2, value=1040)

        _, written = pedal.written_blocks[0]
        assert written.params[2] == 1040
        assert written.params[0] == before.params[0]
        assert written.enabled is before.enabled
        assert written.effect_type == before.effect_type

    def test_set_param_accepts_sixteen_bit_values(self, wired):
        server, _, pedal = wired
        server.set_effect_param("delay", param_index=4, value=65535)
        assert pedal.written_blocks[0][1].params[4] == 65535

    def test_legacy_od_alias_targets_the_ds_module(self, wired):
        server, _, pedal = wired
        server.toggle_effect("od", True)
        assert pedal.written_blocks[0][0] == Command.DS

    def test_unknown_module_rejected(self, wired):
        server, _, pedal = wired
        assert "error" in server.toggle_effect("distortion", True)
        assert pedal.written_blocks == []

    @pytest.mark.parametrize("index", [-1, 10])
    def test_param_index_out_of_range_rejected(self, wired, index):
        server, _, pedal = wired
        result = server.set_effect_param("amp", param_index=index, value=1)
        assert "error" in result
        assert pedal.written_blocks == []

    def test_out_of_range_value_rejected(self, wired):
        server, _, pedal = wired
        assert "error" in server.set_effect_param("amp", 0, 70000)
        assert pedal.written_blocks == []


class TestDeviceInfo:
    """No identify exchange exists, so we report only what we can know."""

    def test_reports_usb_identity_and_active_preset(self, wired):
        server, _, pedal = wired
        pedal.active_slot = 193

        info = server.get_device_info()
        assert info["vendor_id"] == "0x34DB"
        assert info["product_id"] == "0x000F"
        assert info["active_slot"] == 193
        assert info["active_preset"] == "49A"

    def test_does_not_claim_a_firmware_version(self, wired):
        """Firmware came from a guessed identify command; it is gone."""
        server, _, _ = wired
        assert "firmware" not in server.get_device_info()
