"""Golden tests from the connect / save / rename USB capture.

Source: ``log/unplug_plug_saveloadrename.pcapng`` -- the pedal was
unplugged and replugged, MOOER Studio was started, edits were made, and a
preset was saved and renamed *from the pedal itself*, captured 2026-07-26.

Where ``test_capture_golden.py`` pins single-report editing traffic, this
file pins the parts the first capture never showed: the connect handshake,
the 200-preset bulk dump (which is the only confirmed use of multi-report
chunking), the preset record layout, and the notifications the pedal
pushes when it is operated by hand.
"""

from __future__ import annotations

import pytest

from mooer_ge150_mcp.protocol.commands import (
    LAST_PRESET_SLOT,
    MODULE_CHAIN,
    PRESET_RECORD_SIZE,
    Command,
    ModuleBlock,
    build_dump_presets,
    build_hello,
    build_read_active_preset,
    build_read_ir_list,
    build_save_preset,
    decode_preset_record,
    encode_preset_record,
    response_command,
)
from mooer_ge150_mcp.protocol.framing import (
    build_chunked_frames,
    parse_chunked_frames,
    parse_frame,
)

# (command, payload hex) -- verbatim messages, one per distinct command.
MESSAGES: list[tuple[int, str]] = [
    # --- connect sequence, host -> device -------------------------------
    (0x31, "01"),  # frame 2015: hello
    (0xA0, "01"),  # frame 2017: dump all presets
    (0xC1, "01"),  # frame 3975: read user IR list
    (0x8C, "01"),  # frame 3977
    (0xB0, "01"),  # frame 4025: read active preset
    (0x96, "c1"),  # frame 4845: read preset, slot 193
    # --- device -> host --------------------------------------------------
    (0x32, "0a0012000100010008000100"),  # frame 3973
    (0x12, "0100"),  # frame 3981
    (0x11, "000011182b00c3152d0061182800fe005414"),  # frame 3985
    (0x2A, "000000000000000000"),  # frame 3991
    (0x0C, "000000057800"),  # frame 4031
    (0x33, "010000006400"),  # frame 4047
    (0x18, "000000000000"),  # frame 4051
    (0x29, "c0000000000000000000"),  # frame 4851
    (0x34, "05"),  # frame 4853: preset changed
    # frame 6933: the pedal reporting a rename done on the hardware
    (0x17, "c1417361746f6f6f312020202020202020"),
]

# frame 2019: the first record of the 200-preset dump, slot 1 "65 Deluxe".
PRESET_RECORD_HEX = (
    "0136352044656c7578652020202020202001000700620051001c00640000"
    "000000000000000000000000000400320032003200000000000000000000"
    "0000000000010000005c004c001800400032003400000000000000000001"
    "000000040000000b00270012000000000000000000000000000200320032"
    "003200000000000000000000000000000000000000100010001000100010"
    "00100064005802e204000000000900320000003700320013017800320000"
    "000000000000000800320032001004010010040100320000000000000001"
    "0004000000150015004f0041000000000000000000000000000600000001"
    "0000006400"
)


@pytest.mark.parametrize("command,payload_hex", MESSAGES)
def test_captured_message_round_trips(command, payload_hex):
    """Each captured message rebuilds and reparses byte-for-byte."""
    payload = bytes.fromhex(payload_hex)
    frame = parse_frame(build_chunked_frames(command, payload)[0])
    assert frame is not None
    assert frame.command == command
    assert frame.payload == payload


class TestConnectSequence:
    """The editor's opening exchange, in capture order."""

    def test_builders_match_captured_requests(self):
        captured = dict(MESSAGES)
        for builder, command in [
            (build_hello, Command.HELLO),
            (build_dump_presets, Command.DUMP_PRESETS),
            (build_read_ir_list, Command.READ_IR_LIST),
            (build_read_active_preset, Command.READ_ACTIVE),
        ]:
            frame = parse_frame(builder())
            assert frame is not None
            assert frame.command == command
            assert frame.payload == bytes.fromhex(captured[command])

    def test_reply_ids_follow_the_response_mask(self):
        """Confirmed pairs: 0xA0->0x20, 0xC1->0x41, 0x8C->0x0C, 0xB0->0x30."""
        assert response_command(Command.DUMP_PRESETS) == Command.PRESET_RECORD
        assert response_command(Command.READ_IR_LIST) == Command.IR_LIST
        assert response_command(Command.READ_STATE_8C) == 0x0C
        assert response_command(Command.READ_ACTIVE) == Command.ACTIVE_STATE
        assert response_command(Command.PRESET_NAME) == Command.PRESET_NAME_NOTIFY


class TestChunkedTransfer:
    """The preset dump is the only confirmed multi-report traffic.

    Each record is a 252-byte message carried in four 63-byte chunks.
    """

    def test_preset_record_spans_four_reports(self):
        payload = bytes.fromhex(PRESET_RECORD_HEX)
        reports = build_chunked_frames(Command.PRESET_RECORD, payload)

        assert len(reports) == 4
        assert all(len(r) == 64 for r in reports)
        assert [r[0] for r in reports] == [63, 63, 63, 63]

    def test_chunked_record_reassembles(self):
        payload = bytes.fromhex(PRESET_RECORD_HEX)
        reports = build_chunked_frames(Command.PRESET_RECORD, payload)

        frame = parse_chunked_frames(reports)
        assert frame is not None
        assert frame.command == Command.PRESET_RECORD
        assert frame.payload == payload

    def test_a_single_report_does_not_parse_as_a_whole_message(self):
        reports = build_chunked_frames(
            Command.PRESET_RECORD, bytes.fromhex(PRESET_RECORD_HEX)
        )
        assert parse_frame(reports[0]) is None


class TestPresetRecord:
    """Preset record: slot, 16-byte name, 9 module blocks, 12-byte tail."""

    def test_record_size_matches_the_capture(self):
        assert PRESET_RECORD_SIZE == 245
        assert len(bytes.fromhex(PRESET_RECORD_HEX)) == PRESET_RECORD_SIZE

    def test_decode_slot_and_name(self):
        record = decode_preset_record(bytes.fromhex(PRESET_RECORD_HEX))
        assert record.slot == 1
        assert record.name == "65 Deluxe"

    def test_decode_carries_all_nine_modules(self):
        record = decode_preset_record(bytes.fromhex(PRESET_RECORD_HEX))
        assert list(record.modules) == MODULE_CHAIN
        assert len(MODULE_CHAIN) == 9

    def test_amp_block_of_the_65_deluxe_preset(self):
        """Six parameters, as every confirmed amp block has."""
        record = decode_preset_record(bytes.fromhex(PRESET_RECORD_HEX))
        amp = record.modules[Command.AMP]
        assert amp.enabled is True
        assert amp.params[:6] == [92, 76, 24, 64, 50, 52]

    def test_eq_block_carries_bands_and_crossover_frequencies(self):
        """The EQ block is what pins 0x87: six bands at centre (16),
        followed by 100/600/1250 Hz crossover points."""
        record = decode_preset_record(bytes.fromhex(PRESET_RECORD_HEX))
        eq = record.modules[Command.EQ]
        assert eq.params[:6] == [16, 16, 16, 16, 16, 16]
        assert eq.params[7:10] == [600, 1250, 0]

    def test_delay_block_holds_millisecond_times(self):
        record = decode_preset_record(bytes.fromhex(PRESET_RECORD_HEX))
        delay = record.modules[Command.DELAY]
        assert 1040 in delay.params

    def test_encode_decode_round_trip_is_byte_exact(self):
        payload = bytes.fromhex(PRESET_RECORD_HEX)
        assert encode_preset_record(decode_preset_record(payload)) == payload

    def test_tail_is_preserved_verbatim(self):
        payload = bytes.fromhex(PRESET_RECORD_HEX)
        record = decode_preset_record(payload)
        assert record.tail == payload[-12:]

    def test_wrong_size_is_rejected(self):
        with pytest.raises(ValueError, match="245 bytes"):
            decode_preset_record(b"\x00" * 244)

    @pytest.mark.parametrize("slot", [0, LAST_PRESET_SLOT + 1])
    def test_slots_are_one_based(self, slot):
        """Slots run 1-200 in the dump, not 0-199."""
        record = decode_preset_record(bytes.fromhex(PRESET_RECORD_HEX))
        record.slot = slot
        with pytest.raises(ValueError, match="1-200"):
            encode_preset_record(record)


class TestPedalOriginatedNotifications:
    """What the pedal pushes when edited by hand rather than by the editor."""

    def test_rename_notification_carries_slot_and_name(self):
        """Frame 6933: the user renamed slot 193 to "Asatooo1" on the pedal."""
        payload = bytes.fromhex("c1417361746f6f6f312020202020202020")
        assert payload[0] == 0xC1
        assert payload[1:].decode("ascii").rstrip() == "Asatooo1"

    def test_save_builder_matches_the_notification_shape(self):
        """Wire slot 0xC1 = 193 (1-based) = 49A, the renamed preset."""
        frame = parse_frame(build_save_preset(193, "Asatooo1"))
        assert frame is not None
        assert frame.command == Command.PRESET_NAME
        assert frame.payload[0] == 193
        assert frame.payload[1:].rstrip(b"\x00").decode() == "Asatooo1"
        assert len(frame.payload) == 17

    def test_module_blocks_are_pushed_unsolicited(self):
        """Frame 4059: an FX block (0x02) arriving as a device notification.

        Reply IDs are the module command with bit 7 cleared, so a pushed
        0x02 is the FX module's state.
        """
        payload = bytes.fromhex(
            "0000060032003200320032000000000000000000000000"
            "00"
        )
        assert response_command(Command.FX) == 0x02
        block = ModuleBlock(enabled=False, effect_type=6, params=[50, 50, 50, 50])
        from mooer_ge150_mcp.protocol.commands import encode_module_block

        assert encode_module_block(block) == payload
