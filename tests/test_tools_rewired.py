"""The MCP tools that now run on capture-confirmed protocol paths.

These drive the real tool functions against ``FakeMaxPedal``, which only
implements exchanges observed in the USB captures. A tool that reverts to
a guessed command will stop getting answers and fail here.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from mooer_ge150_mcp.protocol.commands import Command, MODULE_CHAIN

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
    assert len(tools) == 36
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

    def test_stale_notifications_do_not_truncate_the_dump(self, wired):
        """Observed live: leftover notifications in the HID buffer were
        counted as records and a 200-preset dump came back as 188."""
        server, _, pedal = wired
        for _ in range(12):
            pedal._respond(Command.PRESET_CHANGED, b"")

        result = server.list_presets()
        assert result["received"] == 200

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


class TestWritePath:
    """The pedal's real write model: select, edit blocks, then save.

    Confirmed in log/test0-2.pcapng: MOOER Studio never uploads a preset
    blob. It selects a slot, sends module blocks live, then commits with
    0x97 carrying the slot and name.
    """

    def test_select_uses_one_based_slots_on_the_wire(self, wired):
        server, _, pedal = wired
        result = server.select_preset_slot(199)  # 50D
        assert result["address"] == "50D"
        assert pedal.selected == [200]

    def test_save_commits_live_state_under_a_new_name(self, wired):
        server, _, pedal = wired
        server.select_preset_slot(199)
        server.toggle_effect("amp", False)

        result = server.save_preset(199, "ZZTEST")

        assert result["saved"] is True
        assert result["address"] == "50D"
        assert pedal.records[200].name == "ZZTEST"
        assert pedal.records[200].modules[Command.AMP].enabled is False

    def test_save_doubles_as_rename(self, wired):
        server, _, pedal = wired
        server.save_preset(0, "Renamed")
        assert pedal.records[1].name == "Renamed"

    def test_write_preset_selects_then_writes_then_saves(self, wired):
        server, _, pedal = wired
        result = server.write_preset(
            199,
            "Dual Lead",
            {"amp": {"enabled": True, "effect_type": 16, "params": [37, 50]}},
        )

        assert result["saved"] is True
        assert result["modules_written"] == ["amp"]
        assert pedal.selected == [200]
        assert pedal.saves[-1][0] == 200

        amp = pedal.records[200].modules[Command.AMP]
        assert amp.effect_type == 16
        assert amp.params[0] == 37

    def test_write_preset_leaves_unlisted_modules_alone(self, wired):
        server, _, pedal = wired
        before = pedal.records[1].modules[Command.REVERB]

        server.write_preset(0, "Only Amp", {"amp": {"effect_type": 3}})

        assert [c for c, _ in pedal.written_blocks] == [Command.AMP]
        assert pedal.records[1].modules[Command.REVERB] == before

    def test_write_preset_accepts_several_modules_in_chain_order(self, wired):
        server, _, pedal = wired
        server.write_preset(
            0,
            "Multi",
            {
                "reverb": {"effect_type": 4, "params": [50, 34]},
                "amp": {"effect_type": 16},
            },
        )
        # Blocks go out in chain order regardless of dict order.
        assert [c for c, _ in pedal.written_blocks] == [Command.AMP, Command.REVERB]

    def test_write_preset_rejects_unknown_module(self, wired):
        server, _, pedal = wired
        result = server.write_preset(0, "Bad", {"chorus": {"effect_type": 1}})
        assert "error" in result
        assert pedal.saves == []

    def test_write_preset_rejects_bad_slot(self, wired):
        server, _, pedal = wired
        assert "error" in server.write_preset(200, "Bad", {})
        assert pedal.saves == []

    def test_name_is_truncated_to_sixteen_characters(self, wired):
        server, _, pedal = wired
        server.save_preset(0, "A" * 30)
        assert pedal.records[1].name == "A" * 16

    def test_saving_invalidates_the_dump_cache(self, wired):
        server, _, _ = wired
        server.list_presets(0, 0)
        assert server._record_cache
        server.save_preset(0, "New")
        assert server._record_cache == {}


class TestExpressionAssignment:
    def test_sends_the_observed_shape(self, wired):
        server, conn, _ = wired
        assert server.set_expression_target(10) == {"target": 10, "enabled": 1}

    def test_rejects_out_of_range(self, wired):
        server, _, _ = wired
        assert "error" in server.set_expression_target(70000)


class TestSystemSettings:
    """Global settings, all confirmed in log/test3.pcapng.

    The user set input level 2.5 dB, brightness 10, OTG level 1.0 dB and
    toggled both cab-sim outputs and spill-over, which pinned every one.
    """

    def test_input_level_uses_the_observed_db_encoding(self, wired):
        """2.5 dB went out as raw 14."""
        server, _, pedal = wired
        result = server.set_input_level(2.5)
        assert result == {"db": 2.5, "raw": 14}
        assert pedal.settings[Command.INPUT_LEVEL] == [14]

    def test_otg_level_shares_that_encoding(self, wired):
        """1.0 dB went out as raw 11 -- the same 9-is-zero, 0.5 dB steps."""
        server, _, pedal = wired
        assert server.set_otg_level(1.0) == {"db": 1.0, "raw": 11}
        assert pedal.settings[Command.OTG_LEVEL] == [11]

    def test_zero_db_is_raw_nine(self, wired):
        server, _, _ = wired
        assert server.set_input_level(0)["raw"] == 9

    def test_brightness_is_a_direct_value(self, wired):
        server, _, pedal = wired
        assert server.set_screen_brightness(10) == {"brightness": 10}
        assert pedal.settings[Command.SCREEN_BRIGHTNESS] == [10]

    def test_cab_sim_thru_carries_both_channels(self, wired):
        server, _, pedal = wired
        server.set_cab_sim_thru(left=True, right=False)
        assert pedal.settings[Command.CAB_SIM_THRU] == [1, 0]

    def test_spillover_toggles(self, wired):
        server, _, pedal = wired
        server.set_spillover(True)
        assert pedal.settings[Command.SPILLOVER] == [1]
        server.set_spillover(False)
        assert pedal.settings[Command.SPILLOVER] == [0]


class TestEffectOrderIsFixed:
    def test_reordering_is_refused_rather_than_dimming_the_screen(self, wired):
        """It used to send its bytes under 0xA5, which is brightness."""
        server, _, pedal = wired
        result = server.set_effect_order(["amp", "cab"])
        assert "error" in result
        assert Command.SCREEN_BRIGHTNESS not in pedal.settings


class TestCtrlConfig:
    """Which modules a preset's footswitch toggles (the manual's CTRL)."""

    def test_reads_nine_module_flags(self, wired):
        server, _, _ = wired
        result = server.get_ctrl_config(0)
        assert set(result["toggles"]) == set(
            ["fx", "ds", "amp", "cab", "ns", "eq", "mod", "delay", "reverb"]
        )

    def test_round_trips_a_selection(self, wired):
        server, _, _ = wired
        server.set_ctrl_config(5, ["delay", "reverb"])

        toggles = server.get_ctrl_config(5)["toggles"]
        assert toggles["delay"] is True
        assert toggles["reverb"] is True
        assert toggles["amp"] is False

    def test_ignores_a_pushed_0x29_for_another_slot(self, wired):
        """A preset load makes the pedal push a 0x29 for that slot. One
        queued ahead of the reply used to be taken as the answer."""
        server, _, pedal = wired
        pedal.ctrl_flags[5] = [c in (Command.DELAY, Command.REVERB)
                               for c in MODULE_CHAIN]
        pedal._respond(0x2A, bytes(9))
        pedal._respond(Command.CTRL_CONFIG, bytes([0]) + bytes(9))

        result = server.get_ctrl_config(5)

        assert result["slot"] == 5
        assert {m for m, on in result["toggles"].items() if on} == {
            "delay", "reverb"
        }

    def test_ctrl_slots_are_zero_based_on_the_wire(self, wired):
        server, _, pedal = wired
        server.set_ctrl_config(0, ["amp"])
        assert pedal.ctrl_flags[0][MODULE_CHAIN.index(Command.AMP)] is True

    def test_unknown_module_rejected(self, wired):
        server, _, _ = wired
        assert "error" in server.set_ctrl_config(0, ["chorus"])


class TestPutPreset:
    """Direct whole-record upload, as a backup restore does it."""

    def test_uploads_and_is_acknowledged(self, wired):
        server, _, pedal = wired
        result = server.put_preset(
            4, {"name": "Uploaded", "modules": {"amp": {"effect_type": 7}}}
        )
        assert result["acknowledged"] is True
        assert result["address"] == "2A"
        assert pedal.uploaded == [5]
        assert pedal.records[5].name == "Uploaded"
        assert pedal.records[5].modules[Command.AMP].effect_type == 7

    def test_does_not_disturb_the_active_preset(self, wired):
        server, _, pedal = wired
        pedal.active_slot = 1
        server.put_preset(99, {"name": "Elsewhere", "modules": {}})
        assert pedal.active_slot == 1
        assert pedal.selected == []

    def test_round_trips_through_get_preset(self, wired):
        server, _, _ = wired
        original = server.get_preset(10)
        server.put_preset(11, original)
        assert server.get_preset(11)["name"] == original["name"]

    def test_round_trip_carries_the_tail(self, wired):
        """put_preset(get_preset(x)) used to zero the 12-byte tail."""
        server, _, pedal = wired
        pedal.records[11].tail = bytes(range(1, 13))  # server slot 10

        server.put_preset(10, server.get_preset(10))  # in place
        server.put_preset(11, server.get_preset(10))  # to another slot

        assert pedal.records[11].tail == bytes(range(1, 13))
        assert pedal.records[12].tail == bytes(range(1, 13))

    def test_without_a_tail_the_slot_keeps_its_own(self, wired):
        server, _, pedal = wired
        pedal.records[5].tail = b"\xAB" * 12
        server.put_preset(4, {"name": "No Tail", "modules": {}})
        assert pedal.records[5].tail == b"\xAB" * 12

    def test_rejects_a_malformed_tail(self, wired):
        server, _, pedal = wired
        assert "error" in server.put_preset(4, {"name": "x", "tail": "zz"})
        assert "error" in server.put_preset(4, {"name": "x", "tail": "00"})
        assert pedal.uploaded == []

    def test_rejects_bad_slot_and_module(self, wired):
        server, _, pedal = wired
        assert "error" in server.put_preset(200, {"name": "x"})
        assert "error" in server.put_preset(0, {"modules": {"nope": {}}})
        assert pedal.uploaded == []


class TestUserModelUploads:
    """Cab/amp uploads, decoded from log/test4.pcapng.

    The wire sessions: cab = 0xB5 begin/name/3x512B chunks, each echoed;
    amp = 0xD3 [index, seq, 512B] x20 + name message, acked by 0x13.
    """

    def test_cab_upload_lands_in_a_user_cab_slot(self, wired):
        server, _, pedal = wired
        blob = bytes(range(256)) * 6  # 1536 bytes
        result = server.upload_cab(0, "C-2X12 FENDER DX", blob.hex())

        assert result == {
            "uploaded": True, "index": 0, "display": 27,
            "name": "C-2X12 FENDER DX",
        }
        assert pedal.ir_names[20] == "C-2X12 FENDER DX"
        assert pedal.cab_blobs[0] == blob

    def test_amp_upload_lands_in_a_user_amp_slot(self, wired):
        server, _, pedal = wired
        blob = bytes([7]) * 10240
        result = server.upload_amp(0, "E-3RD POWER DRAG", blob.hex())

        assert result["uploaded"] is True
        assert result["display"] == 56
        assert pedal.ir_names[0] == "E-3RD POWER DRAG"
        assert pedal.amp_blobs[0] == blob

    def test_list_shows_the_amp_cab_split(self, wired):
        server, _, pedal = wired
        server.upload_amp(0, "E-3RD POWER DRAG", (bytes([1]) * 10240).hex())
        server.upload_cab(1, "34 CT-BogOS412", (bytes([2]) * 1536).hex())

        listing = server.list_ir_slots()
        amps = {a["display"]: a["name"] for a in listing["amps"] if not a["empty"]}
        cabs = {c["display"]: c["name"] for c in listing["cabs"] if not c["empty"]}
        assert amps == {56: "E-3RD POWER DRAG"}
        assert cabs == {28: "34 CT-BogOS412"}

    def test_wrong_blob_size_is_rejected_before_sending(self, wired):
        server, _, pedal = wired
        assert "error" in server.upload_cab(0, "X", "00" * 100)
        assert "error" in server.upload_amp(0, "X", "00" * 100)
        assert pedal.cab_blobs == {}
        assert pedal.amp_blobs == {}

    def test_bad_hex_is_rejected(self, wired):
        server, _, _ = wired
        assert "error" in server.upload_cab(0, "X", "zz")

    def test_out_of_range_index_rejected(self, wired):
        server, _, _ = wired
        assert "error" in server.upload_cab(20, "X", "00" * 1536)
        assert "error" in server.upload_amp(20, "X", "00" * 10240)


class TestOptimizePresetPrompt:
    """The prompt used to say "set_effect_param, then set_preset to save",
    which rewrote the slot from its stored copy and lost the edits."""

    def test_prescribes_select_edit_save(self, wired):
        server, _, _ = wired
        text = server.optimize_preset(3, "more clarity")
        assert "select_preset slot=3" in text
        assert "save_preset slot=3" in text
        assert "then set_preset to save" not in text

    def test_the_prescribed_workflow_keeps_the_edit(self, wired):
        server, _, pedal = wired
        name = server.get_preset(3)["name"]

        server.select_preset(3)
        server.set_effect_param("amp", 0, 99)
        server.save_preset(3, name)

        saved = pedal.records[4]
        assert saved.modules[Command.AMP].params[0] == 99
        assert saved.name == name


class TestSelectSettles:
    """Nothing may follow a select until the pedal reports the preset
    loaded: a command landing mid-load hung real hardware (2026-09-30)."""

    def test_select_consumes_the_load_complete_push(self, wired):
        server, conn, pedal = wired
        pedal.ctrl_flags[4] = [c == Command.DS for c in MODULE_CHAIN]

        result = server.select_preset(4)

        assert result["confirmed"] is True
        left = []
        while (frame := conn.read_message()) is not None:
            left.append(frame.command)
        assert Command.CTRL_CONFIG not in left

    def test_select_preset_slot_reports_the_confirmation(self, wired):
        server, _, pedal = wired
        assert server.select_preset_slot(4)["selected"] is True
        pedal.silent_selects = {6}
        assert server.select_preset_slot(5)["selected"] is False

    def test_unanswered_select_is_reported(self, wired):
        server, _, pedal = wired
        pedal.silent_selects = {5}
        result = server.select_preset(4)
        assert result["confirmed"] is False
        assert "warning" in result

    def test_a_stale_0x29_for_the_same_slot_does_not_count(self, wired):
        """Left over from earlier, it would end the wait before the load
        had even begun."""
        server, _, pedal = wired
        pedal._respond(Command.CTRL_CONFIG, bytes([4]) + bytes(9))
        pedal.silent_selects = {5}
        assert server.select_preset(4)["confirmed"] is False

    def test_set_preset_writes_nothing_without_confirmation(self, wired):
        server, _, pedal = wired
        pedal.silent_selects = {6}

        result = server.set_preset(
            5, name="Nope", effects={"amp": {"effect_type": 9}}
        )

        assert result["stored"] is False
        assert "error" in result
        assert pedal.written_blocks == []
        assert pedal.saves == []

    def test_write_preset_writes_nothing_without_confirmation(self, wired):
        server, _, pedal = wired
        pedal.silent_selects = {6}

        result = server.write_preset(5, "Nope", {"amp": {"effect_type": 9}})

        assert "error" in result
        assert pedal.written_blocks == []
        assert pedal.saves == []

    def test_copy_saves_nothing_without_confirmation(self, wired):
        server, _, pedal = wired
        pedal.silent_selects = {1}
        result = server.copy_preset(0, 9)
        assert result["copied"] is False
        assert pedal.saves == []

    def test_half_done_swap_hands_back_the_displaced_preset(self, wired):
        server, _, pedal = wired
        name_a, name_b = pedal.records[1].name, pedal.records[8].name
        pedal.records[1].tail = bytes(range(12))
        pedal.silent_selects = {8}  # the second write's select

        result = server.swap_presets(0, 7)

        assert result["swapped"] is False
        assert pedal.records[1].name == name_b  # first half landed
        assert pedal.records[8].name == name_b  # second half did not
        assert result["displaced"]["name"] == name_a
        assert result["displaced"]["tail"] == bytes(range(12)).hex()

    def test_swap_writes_nothing_if_the_first_select_fails(self, wired):
        server, _, pedal = wired
        pedal.silent_selects = {1}
        result = server.swap_presets(0, 7)
        assert result["swapped"] is False
        assert "displaced" not in result
        assert pedal.saves == []

    def test_drain_discards_queued_input(self, wired):
        _, conn, pedal = wired
        pedal._respond(0x2A, bytes(9))
        pedal._respond(Command.CTRL_CONFIG, bytes(10))
        assert conn.drain() == 2
        assert conn.read_message() is None


class TestCtrlQuietTime:
    """After a CTRL read the pedal is busy for about a second, and a
    select landing in that window hung real hardware (2026-09-30)."""

    def _slept(self, server, call):
        naps = []
        with patch.object(server.time, "sleep", naps.append):
            call()
        return naps

    def test_select_waits_out_a_recent_ctrl_read(self, wired):
        server, _, _ = wired
        server.get_ctrl_config(3)
        naps = self._slept(server, lambda: server.select_preset(4))
        assert len(naps) == 1
        assert 1.5 < naps[0] <= server.CTRL_SETTLE_SECONDS

    def test_no_wait_without_ctrl_traffic(self, wired):
        server, _, _ = wired
        assert self._slept(server, lambda: server.select_preset(4)) == []

    def test_consecutive_ctrl_reads_are_not_delayed(self, wired):
        server, _, _ = wired
        server.get_ctrl_config(3)
        assert self._slept(server, lambda: server.get_ctrl_config(4)) == []

    def test_ctrl_write_starts_the_quiet_time_too(self, wired):
        server, _, _ = wired
        server.set_ctrl_config(3, ["delay"])
        naps = self._slept(server, lambda: server.select_preset(4))
        assert naps and naps[0] > 1.5

    def test_save_and_restore_bracket_wait_too(self, wired):
        server, _, _ = wired
        server.get_ctrl_config(3)
        assert self._slept(server, lambda: server.save_preset(4, "X"))[0] > 1.5
        server.get_ctrl_config(3)
        naps = self._slept(
            server, lambda: server.copy_preset(0, 9, byte_exact=True)
        )
        assert naps[0] > 1.5


class TestRebootReconnect:
    """A restore bracket ends in a reboot. Reopening the device is not
    proof the pedal is back: on hardware it once came up silent until its
    USB port was reset (2026-09-30)."""

    def test_answering_pedal_needs_no_reset(self, wired):
        server, conn, _ = wired
        conn.reset_usb = lambda: pytest.fail("reset not needed")
        assert server._reconnect(conn) is True

    def test_silent_pedal_is_recovered_by_a_usb_reset(self, wired):
        server, conn, pedal = wired
        pedal.mute = True
        resets = []

        def reset_usb():
            resets.append(1)
            pedal.mute = False
            return True

        conn.reset_usb = reset_usb
        conn.open = lambda: None
        with patch.object(server.time, "sleep"):
            assert server._reconnect(conn) is True
        assert resets == [1]

    def test_reports_failure_when_the_pedal_stays_silent(self, wired):
        server, conn, pedal = wired
        conn.reset_usb = lambda: False  # e.g. not on Linux

        def go_mute(timeout_s=20.0):
            pedal.mute = True
            return True

        conn.reconnect = go_mute
        with patch.object(server.time, "sleep"):
            result = server.put_preset(4, {"name": "X", "modules": {}})
        assert result["acknowledged"] is True
        assert result["reconnected"] is False

    def test_ack_is_found_behind_a_queued_broadcast(self, wired):
        """After a reboot the pedal pushes its whole state; a write that
        landed was reported unacknowledged behind it."""
        server, _, pedal = wired
        server._fetch_all_records()  # tail lookup must not flush the queue
        for command in MODULE_CHAIN + MODULE_CHAIN:
            pedal._respond(command & 0x7F, bytes(24))

        result = server.put_preset(4, {"name": "Landed", "modules": {}})

        assert result["acknowledged"] is True
