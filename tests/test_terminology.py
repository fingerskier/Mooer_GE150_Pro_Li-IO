"""Terminology conformance, checked against the owner's manual.

The GE150 Max / GE150 Max Li Owner's Manual is the authority for names in
this project; GLOSSARY.md records the decisions and quotes the sources.
These tests keep the code from drifting back to invented vocabulary.
"""

from __future__ import annotations

import pytest

from mooer_ge150_mcp.protocol.commands import (
    LAST_PRESET_SLOT,
    MODULE_CHAIN,
    MODULE_COMMAND_MAP,
    PRESET_POSITIONS,
    PRESETS_PER_BANK,
    Command,
    ModuleBlock,
    address_to_slot,
    build_module_block,
    slot_to_address,
)


class TestModuleNames:
    """The nine modules and their chain order, as the manual prints them."""

    #: "FX (miscellaneous), DS (Overdrive/Distortion), AMP, CAB,
    #: NS (Noise Gate), EQ, MOD (Modulation), DELAY, REVERB"
    MANUAL_ORDER = ["fx", "ds", "amp", "cab", "ns", "eq", "mod", "delay", "reverb"]

    def test_chain_order_matches_the_manual(self):
        assert list(MODULE_COMMAND_MAP) == self.MANUAL_ORDER

    def test_chain_constant_agrees_with_the_name_map(self):
        assert MODULE_CHAIN == [MODULE_COMMAND_MAP[n] for n in self.MANUAL_ORDER]

    def test_there_are_nine_modules(self):
        assert len(MODULE_CHAIN) == 9

    def test_drive_module_is_called_ds(self):
        """The manual calls it DS, not OD or DRIVE."""
        assert Command.DS == 0x83
        assert not hasattr(Command, "DRIVE")

    @pytest.mark.parametrize("alias", ["od", "drive", "OD", "Drive"])
    def test_legacy_drive_aliases_still_resolve(self, alias):
        """Older callers said "od"; keep them working rather than break them."""
        block = ModuleBlock(enabled=True, effect_type=2)
        assert build_module_block(alias, block) == build_module_block("ds", block)

    def test_unknown_module_names_are_rejected(self):
        with pytest.raises(ValueError, match="Unknown module"):
            build_module_block("distortion", ModuleBlock(True, 0))


class TestEffectTypeNaming:
    """Within a module, the chosen effect is its *effect type*."""

    def test_module_block_exposes_effect_type(self):
        block = ModuleBlock(enabled=True, effect_type=17, params=[1, 2])
        assert block.effect_type == 17

    def test_model_is_not_a_module_block_field(self):
        """"Model" is reserved for what AMP models -- an amplifier."""
        assert not hasattr(ModuleBlock(True, 0), "model")


class TestPresetAddressing:
    """Presets are addressed as bank (1-50) + position (A-D)."""

    def test_positions_are_a_through_d(self):
        assert PRESET_POSITIONS == "ABCD"
        assert PRESETS_PER_BANK == 4

    @pytest.mark.parametrize(
        "slot,address",
        [
            (1, "1A"),
            (2, "1B"),
            (4, "1D"),
            (5, "2A"),
            (193, "49A"),
            (200, "50D"),
        ],
    )
    def test_slot_to_address(self, slot, address):
        assert slot_to_address(slot) == address

    @pytest.mark.parametrize(
        "bank,position,slot",
        [(1, "A", 1), (1, "D", 4), (2, "A", 5), (49, "A", 193), (50, "D", 200)],
    )
    def test_address_to_slot(self, bank, position, slot):
        assert address_to_slot(bank, position) == slot

    def test_round_trip_over_every_slot(self):
        for slot in range(1, LAST_PRESET_SLOT + 1):
            address = slot_to_address(slot)
            bank, position = int(address[:-1]), address[-1]
            assert address_to_slot(bank, position) == slot

    def test_fifty_banks_cover_exactly_two_hundred_presets(self):
        assert address_to_slot(50, "D") == LAST_PRESET_SLOT == 200

    @pytest.mark.parametrize("slot", [0, 201])
    def test_out_of_range_slots_rejected(self, slot):
        with pytest.raises(ValueError, match="1-200"):
            slot_to_address(slot)

    def test_bad_bank_rejected(self):
        with pytest.raises(ValueError, match="Bank must be 1-50"):
            address_to_slot(51, "A")

    def test_bad_position_rejected(self):
        """A/B/C/D are preset positions; 'E' is not one."""
        with pytest.raises(ValueError, match="position must be one of ABCD"):
            address_to_slot(1, "E")

    def test_renamed_preset_from_the_capture_resolves_to_its_address(self):
        """Slot 0xC1 -- the one renamed "Asatooo1" on the pedal -- is 49A."""
        assert slot_to_address(0xC1) == "49A"
