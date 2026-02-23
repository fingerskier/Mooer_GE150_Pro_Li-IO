"""Tests for message frame building and parsing."""

from mooer_ge150_mcp.protocol.framing import (
    build_frame,
    parse_frame,
    build_chunked_frames,
    Frame,
    HID_REPORT_SIZE,
    PREAMBLE,
)
from mooer_ge150_mcp.utils.crc import crc16


def test_build_frame_size():
    """Every built frame must be exactly 64 bytes."""
    frame = build_frame(0xA6, b"\x02")
    assert len(frame) == HID_REPORT_SIZE


def test_build_frame_preamble():
    """Preamble 0xAA 0x55 should appear at bytes 1-2."""
    frame = build_frame(0x10)
    assert frame[1:3] == PREAMBLE


def test_build_frame_hid_size():
    """First byte should reflect the number of meaningful bytes."""
    frame = build_frame(0xA6, b"\x02")
    hid_size = frame[0]
    # preamble(2) + size(2) + cmd(1) + payload(1) + checksum(2) = 8
    assert hid_size == 8


def test_build_frame_command_byte():
    """Command byte should be at position 5 (after hid_size + preamble + size)."""
    frame = build_frame(0xA6, b"\x02")
    assert frame[5] == 0xA6


def test_build_frame_select_preset_3():
    """Verify frame structure for selecting preset 3 (index 2).

    Structure: [hid_size] AA 55 [size_lo size_hi] [cmd] [payload] [crc_lo crc_hi]
    """
    frame = build_frame(0xA6, b"\x02")
    assert frame[0] == 0x08  # hid_size: 8 meaningful bytes
    assert frame[1] == 0xAA  # preamble
    assert frame[2] == 0x55  # preamble
    assert frame[3] == 0x02  # size low byte (body = 2 bytes)
    assert frame[4] == 0x00  # size high byte
    assert frame[5] == 0xA6  # command (ActivePatch)
    assert frame[6] == 0x02  # payload (preset index 2)
    # Checksum: CRC-16 of [0xA6, 0x02], little-endian
    expected_crc = crc16(bytes([0xA6, 0x02]))
    assert frame[7] == expected_crc & 0xFF          # crc low byte
    assert frame[8] == (expected_crc >> 8) & 0xFF    # crc high byte


def test_roundtrip_parse():
    """Build a frame and parse it back."""
    original_cmd = 0xA6
    original_payload = b"\x05"
    frame = build_frame(original_cmd, original_payload)
    parsed = parse_frame(frame)

    assert parsed is not None
    assert parsed.command == original_cmd
    assert parsed.payload == original_payload


def test_parse_invalid_preamble():
    """Frames with wrong preamble should return None."""
    bad = bytearray(64)
    bad[0] = 8
    bad[1] = 0xBB  # wrong
    bad[2] = 0x55
    assert parse_frame(bytes(bad)) is None


def test_parse_bad_checksum():
    """Frames with corrupt checksum should return None."""
    frame = bytearray(build_frame(0xA6, b"\x02"))
    frame[7] = 0x00  # corrupt checksum
    frame[8] = 0x00
    assert parse_frame(bytes(frame)) is None


def test_roundtrip_empty_payload():
    """Commands with no payload should round-trip."""
    frame = build_frame(0x10)
    parsed = parse_frame(frame)
    assert parsed is not None
    assert parsed.command == 0x10
    assert parsed.payload == b""


def test_roundtrip_large_payload():
    """Payloads near the single-frame limit should round-trip."""
    payload = bytes(range(50))  # 50 bytes, fits in one frame
    frame = build_frame(0x83, payload)
    parsed = parse_frame(frame)
    assert parsed is not None
    assert parsed.command == 0x83
    assert parsed.payload == payload


def test_chunked_small_message():
    """Small messages should produce a single chunk identical to build_frame."""
    single = build_frame(0xA6, b"\x02")
    chunked = build_chunked_frames(0xA6, b"\x02")
    assert len(chunked) == 1
    assert chunked[0] == single


def test_chunked_frames_all_64_bytes():
    """All chunked frames should be 64 bytes."""
    payload = bytes(range(200))  # Large enough to need multiple chunks
    frames = build_chunked_frames(0x83, payload)
    assert len(frames) > 1
    for f in frames:
        assert len(f) == HID_REPORT_SIZE


def test_frame_repr():
    """Frame repr should be readable."""
    f = Frame(command=0xA6, payload=b"\x02")
    r = repr(f)
    assert "0xA6" in r
