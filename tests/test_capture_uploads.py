"""Golden tests from the cab/amp upload capture (log/test4.pcapng).

MOOER Studio uploaded a .gir cab, a .wav cab (converted client-side to
the same wire blob) and a .gnr amp; a failed amp upload produced no
traffic at all. The messages below are verbatim from the capture.
"""

from __future__ import annotations

from mooer_ge150_mcp.protocol.commands import (
    Command,
    build_upload_amp,
    build_upload_cab,
    split_user_model_list,
)
from mooer_ge150_mcp.protocol.framing import parse_chunked_frames


def _payloads(message_reports):
    return [parse_chunked_frames(m).payload for m in message_reports]


class TestCabUploadFraming:
    def test_begin_and_name_match_the_capture(self):
        """Session 1: begin `00 00 00 01`, name `01 C-2X12 FENDER DX`."""
        msgs = _payloads(build_upload_cab(0, "C-2X12 FENDER DX", bytes(1536)))
        assert msgs[0] == bytes.fromhex("00000001")
        assert msgs[1] == bytes([0x01]) + b"C-2X12 FENDER DX"

    def test_second_slot_begin_matches_session_two(self):
        """Session 2 (slot index 1) began `00 01 00 01`."""
        msgs = _payloads(build_upload_cab(1, "34 CT-BogOS412", bytes(1536)))
        assert msgs[0] == bytes.fromhex("00010001")
        # name shorter than 16 is NUL-padded, as captured
        assert msgs[1] == bytes([0x01]) + b"34 CT-BogOS412\x00\x00"

    def test_data_chunks_are_sequenced_from_two(self):
        blob = bytes(range(256)) * 6
        msgs = _payloads(build_upload_cab(0, "X", blob))
        assert [m[0] for m in msgs] == [0x00, 0x01, 0x02, 0x03, 0x04]
        assert b"".join(m[1:] for m in msgs[2:]) == blob


class TestAmpUploadFraming:
    def test_chunk_headers_and_final_name_match_the_capture(self):
        """20 chunks `00 <seq>` then `00 14 E-3RD POWER DRAG`."""
        blob = bytes(10240)
        msgs = _payloads(build_upload_amp(0, "E-3RD POWER DRAG", blob))
        assert len(msgs) == 21
        assert msgs[0][:2] == bytes.fromhex("0000")
        assert msgs[19][:2] == bytes.fromhex("0013")
        assert msgs[20] == bytes.fromhex("0014") + b"E-3RD POWER DRAG"

    def test_ack_command_id(self):
        assert Command.UPLOAD_AMP_ACK == 0x13
        assert Command.UPLOAD_AMP == 0xD3


class TestUserModelListSplit:
    def test_capture_final_state_decodes_to_the_pedal_display_numbers(self):
        """The capture's last 0x41: amp at index 0 (shown 56), cabs at
        20/21 (shown 27/28) -- exactly the user's annotations."""
        names = ["EMPTY"] * 40
        names[0] = "E-3RD POWER DRAG"
        names[20] = "C-2X12 FENDER DX"
        names[21] = "34 CT-BogOS412"

        split = split_user_model_list(names)
        used_amps = [a for a in split["amps"] if not a["empty"]]
        used_cabs = [c for c in split["cabs"] if not c["empty"]]

        assert used_amps == [
            {"index": 0, "display": 56, "name": "E-3RD POWER DRAG",
             "empty": False}
        ]
        assert [(c["display"], c["name"]) for c in used_cabs] == [
            (27, "C-2X12 FENDER DX"), (28, "34 CT-BogOS412"),
        ]
