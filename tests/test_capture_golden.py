"""Golden-frame tests derived from a real USB capture.

Source: ``log/various_tests.pcapng`` -- MOOER Studio driving a GE150 Pro Li
over USB OTG, captured with Wireshark/USBPcap on 2026-07-26.

Every ``MESSAGES`` entry below is a verbatim protocol message lifted from
that capture (HID length prefix stripped, trailing report padding removed).
They pin down the framing, the checksum, and the payload shapes so that
future changes cannot silently drift away from what the pedal actually
speaks.
"""

from __future__ import annotations

import pytest

from mooer_ge150_mcp.protocol.framing import (
    HID_REPORT_SIZE,
    build_frame,
    frame_checksum,
    parse_frame,
    parse_message,
)
from mooer_ge150_mcp.protocol.commands import (
    Command,
    ModuleBlock,
    decode_module_block,
    encode_module_block,
    response_command,
)

# (command, payload hex, full message hex) -- one per distinct command seen.
MESSAGES: list[tuple[int, str, str]] = [
    # --- host -> device -------------------------------------------------
    # frame 403: FX/comp module block
    (0x82, "000006006100320032003200000000000000000000000000",
     "aa551900820000060061003200320032000000000000000000000000000fc7"),
    # frame 2205: drive module block
    (0x83, "010013003c00410033000000000000000000000000000000",
     "aa55190083010013003c004100330000000000000000000000000000007b44"),
    # frame 4059: amp module block (6 parameters)
    (0x84, "010011005a001e0017004c00500041000000000000000000",
     "aa55190084010011005a001e0017004c00500041000000000000000000ba9a"),
    # frame 5627: cab module block
    (0x85, "010008000200000005003700120000000000000000000000",
     "aa55190085010008000200000005003700120000000000000000000000aa39"),
    # frame 7847: NS module block
    (0x86, "010002003200320032000000000000000000000000000000",
     "aa55190086010002003200320032000000000000000000000000000000bc65"),
    # frame 10295: MOD module block
    (0x88, "00000a000d0028001c003200c80078003200000000000000",
     "aa5519008800000a000d0028001c003200c800780032000000000000002411"),
    # frame 10849: DELAY module block (note the 1040 ms times)
    (0x89, "000003003200320010040100100401003200000000000000",
     "aa5519008900000300320032001004010010040100320000000000000005f7"),
    # frame 17837: read preset
    (0x96, "c6", "aa55020096c61950"),
    # frame 1731: set preset name "Crazy Diamond"
    (0x97, "c54372617a79204469616d6f6e64000000",
     "aa55120097c54372617a79204469616d6f6e64000000a3c5"),
    # frame 12531 / 12979 / 12775: single u16 argument commands
    (0xA4, "0a00", "aa550300a40a009fba"),
    (0xA5, "1300", "aa550300a513001161"),
    (0xA7, "0900", "aa550300a7090093b9"),
    # frame 6819: paired u16 flags
    (0xA6, "00000100", "aa550500a6000001005288"),
    # frame 9515
    (0xAC, "010000000000000000", "aa550a00ac01000000000000000033d9"),
    # frame 2089: status poll
    (0xB4, "0700", "aa550300b40700aa85"),
    # frame 13537: assignment block
    (0xD1, "0100192320001923200019231d0000000000",
     "aa551300d10100192320001923200019231d0000000000699c"),
    # --- device -> host -------------------------------------------------
    # frame 4061: cab block echoed back after an amp change
    (0x05, "010008000300000005003700120000000000000000000000",
     "aa5519000501000800030000000500370012000000000000000000000041aa"),
    # frame 11343: delay block echoed back
    (0x09, "0000030012004c000a050600100401003200000000000000",
     "aa551900090000030012004c000a050600100401003200000000000000ac59"),
    # frame 17851 / 17853: preset read replies
    (0x2A, "000000000000000000", "aa550a002a0000000000000000004f71"),
    (0x29, "c5000000000000000000", "aa550b0029c5000000000000000000bfe3"),
    # frame 443: poll acknowledgement
    (0x34, "06", "aa5502003406bb00"),
]


@pytest.mark.parametrize("command,payload_hex,message_hex", MESSAGES)
def test_captured_message_parses(command, payload_hex, message_hex):
    """Every captured message parses with a valid checksum."""
    frame = parse_message(bytes.fromhex(message_hex))
    assert frame is not None, "checksum or framing rejected a real message"
    assert frame.command == command
    assert frame.payload == bytes.fromhex(payload_hex)


@pytest.mark.parametrize("command,payload_hex,message_hex", MESSAGES)
def test_build_frame_reproduces_capture(command, payload_hex, message_hex):
    """Building a frame yields the exact bytes MOOER Studio sent."""
    message = bytes.fromhex(message_hex)
    report = build_frame(command, bytes.fromhex(payload_hex))

    assert len(report) == HID_REPORT_SIZE
    assert report[0] == len(message)
    assert report[1 : 1 + len(message)] == message
    assert report[1 + len(message) :] == b"\x00" * (
        HID_REPORT_SIZE - 1 - len(message)
    )


@pytest.mark.parametrize("command,payload_hex,message_hex", MESSAGES)
def test_parse_frame_round_trip(command, payload_hex, message_hex):
    """A built report parses back to the same command and payload."""
    payload = bytes.fromhex(payload_hex)
    frame = parse_frame(build_frame(command, payload))
    assert frame is not None
    assert frame.command == command
    assert frame.payload == payload


def test_checksum_covers_size_field_and_is_big_endian():
    """The checksum spans the size field, not just the body, and is BE.

    Verified against all 146 messages in the capture; frame 403 is the
    representative case. Truncating the range to the body alone -- or
    storing the result little-endian -- reproduces neither.
    """
    message = bytes.fromhex(MESSAGES[0][2])
    body = message[2:-2]  # size field + command + payload
    assert frame_checksum(body).to_bytes(2, "big") == message[-2:]


def test_a_corrupted_checksum_is_rejected():
    message = bytearray(bytes.fromhex(MESSAGES[0][2]))
    message[-1] ^= 0xFF
    assert parse_message(bytes(message)) is None


def test_response_command_clears_the_request_bit():
    """Device replies echo the request ID with bit 7 cleared.

    Capture evidence: 0x85 -> 0x05 (frames 5627/4061),
    0x89 -> 0x09 (frames 10849/11343), 0xB4 -> 0x34 (frames 2089/2091).
    """
    assert response_command(Command.CAB) == 0x05
    assert response_command(Command.DELAY) == 0x09
    assert response_command(Command.POLL) == 0x34


class TestModuleBlock:
    """The 0x82-0x8A module blocks are 12 little-endian u16 words."""

    def test_decode_amp_block_from_capture(self):
        """Frame 4059: amp enabled, model 17, six parameters."""
        block = decode_module_block(
            bytes.fromhex("010011005a001e0017004c00500041000000000000000000")
        )
        assert block.enabled is True
        assert block.model == 17
        assert block.params[:6] == [90, 30, 23, 76, 80, 65]

    def test_decode_disabled_block_from_capture(self):
        """Frame 3233: the same drive block with the module bypassed."""
        block = decode_module_block(
            bytes.fromhex("000013003700280046000000000000000000000000000000")
        )
        assert block.enabled is False
        assert block.model == 19

    def test_encode_reproduces_captured_payload(self):
        payload = bytes.fromhex(
            "010011005a001e0017004c00500041000000000000000000"
        )
        block = ModuleBlock(
            enabled=True, model=17, params=[90, 30, 23, 76, 80, 65]
        )
        assert encode_module_block(block) == payload

    def test_encode_decode_round_trip(self):
        block = ModuleBlock(enabled=False, model=3, params=[50, 50, 1040, 1])
        decoded = decode_module_block(encode_module_block(block))
        assert decoded.enabled is False
        assert decoded.model == 3
        assert decoded.params[:4] == [50, 50, 1040, 1]

    def test_too_many_params_is_rejected(self):
        with pytest.raises(ValueError, match="at most 10"):
            encode_module_block(ModuleBlock(True, 0, [0] * 11))

    def test_out_of_range_value_is_rejected(self):
        with pytest.raises(ValueError, match="0-65535"):
            encode_module_block(ModuleBlock(True, 0, [70000]))
