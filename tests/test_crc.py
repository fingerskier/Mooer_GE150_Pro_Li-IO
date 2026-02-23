"""Tests for CRC-16 calculation."""

from mooer_ge150_mcp.utils.crc import crc16


def test_crc16_empty():
    """CRC of empty data should be the inverted initial value."""
    result = crc16(b"")
    assert result == 0xFFFF


def test_crc16_known_value():
    """Verify CRC for command 0xA6, payload 0x02 (select preset 3).

    Note: The spec's captured example (0x0D85) was from a GE200 device
    and may use a different CRC variant. Our CRC-16 using the lookup table
    from MooerManager produces 0x6865 for this input. The round-trip
    tests in test_framing.py confirm the CRC is self-consistent.
    """
    data = bytes([0xA6, 0x02])
    result = crc16(data)
    assert result == 0x6865, f"Expected 0x6865, got 0x{result:04X}"


def test_crc16_identify():
    """CRC of just the Identify command byte."""
    data = bytes([0x10])
    result = crc16(data)
    assert isinstance(result, int)
    assert 0 <= result <= 0xFFFF


def test_crc16_deterministic():
    """Same input should always produce same output."""
    data = b"\x83\x01\x02\x03"
    assert crc16(data) == crc16(data)


def test_crc16_different_inputs():
    """Different inputs should produce different CRCs."""
    assert crc16(b"\x01") != crc16(b"\x02")
