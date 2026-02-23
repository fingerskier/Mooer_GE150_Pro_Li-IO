"""Message frame builder and parser for 64-byte USB HID reports.

Frame layout::

    +----------+---------+---------+---------+------------------+----------+---------+
    | HID Size | Preamble|  Size   | Command |     Payload      | Checksum | Padding |
    | 1 byte   | 2 bytes | 2 bytes | 1 byte  |  variable length |  2 bytes | to 64 B |
    +----------+---------+---------+---------+------------------+----------+---------+

- HID Size: number of meaningful bytes that follow (excludes itself)
- Preamble: 0xAA 0x55
- Size: little-endian length of (command byte + payload)
- Checksum: inverted CRC-16 over (command + payload), little-endian
- Padding: zero bytes to fill 64-byte HID report
"""

from __future__ import annotations

from dataclasses import dataclass

from ..utils.crc import crc16

PREAMBLE = b"\xAA\x55"
HID_REPORT_SIZE = 64
MAX_PAYLOAD_PER_FRAME = 58  # 64 - 1(hid_size) - 2(preamble) - 2(size) - 1(cmd)


@dataclass
class Frame:
    """A parsed protocol frame."""

    command: int
    payload: bytes

    def __repr__(self) -> str:
        return (
            f"Frame(command=0x{self.command:02X}, "
            f"payload={self.payload.hex(' ') if self.payload else '(empty)'})"
        )


def build_frame(command: int, payload: bytes = b"") -> bytes:
    """Build a 64-byte HID report containing a single protocol frame.

    Args:
        command: Single-byte command group ID.
        payload: Command-specific payload bytes.

    Returns:
        A 64-byte ``bytes`` object ready to send via USB HID interrupt transfer.
    """
    body = bytes([command]) + payload
    size = len(body).to_bytes(2, "little")
    checksum = crc16(body).to_bytes(2, "little")
    frame = PREAMBLE + size + body + checksum
    hid_size = len(frame)
    # HID report: 1-byte size prefix + frame + zero padding to 64 bytes
    report = bytes([hid_size]) + frame + b"\x00" * (HID_REPORT_SIZE - 1 - len(frame))
    return report


def build_chunked_frames(command: int, payload: bytes) -> list[bytes]:
    """Build multiple 64-byte HID reports for payloads that exceed one frame.

    Large messages are split across multiple USB HID packets. Each chunk is
    a raw 64-byte report with a 1-byte length prefix followed by up to 63
    bytes of data.

    For small messages that fit in a single frame, this returns a list with
    one element identical to :func:`build_frame`.
    """
    body = bytes([command]) + payload
    size_bytes = len(body).to_bytes(2, "little")
    checksum = crc16(body).to_bytes(2, "little")
    full_message = PREAMBLE + size_bytes + body + checksum

    # If it fits in one report (with the 1-byte hid_size prefix)
    if len(full_message) <= HID_REPORT_SIZE - 1:
        return [build_frame(command, payload)]

    # Split into 63-byte chunks (first byte of each report is chunk length)
    frames: list[bytes] = []
    offset = 0
    while offset < len(full_message):
        chunk = full_message[offset : offset + 63]
        report = bytes([len(chunk)]) + chunk + b"\x00" * (63 - len(chunk))
        frames.append(report)
        offset += 63

    return frames


def parse_frame(data: bytes) -> Frame | None:
    """Parse a 64-byte HID report into a Frame.

    Args:
        data: A 64-byte USB HID report.

    Returns:
        A ``Frame`` if the report contains a valid protocol message,
        or ``None`` if the preamble is missing or the checksum fails.
    """
    if len(data) < 8:
        return None

    hid_size = data[0]
    if hid_size < 7:
        return None

    # Check preamble
    if data[1:3] != PREAMBLE:
        return None

    # Parse size (little-endian)
    body_size = int.from_bytes(data[3:5], "little")
    if body_size < 1:
        return None

    command = data[5]
    payload = data[6 : 5 + body_size]

    # Verify checksum
    body = data[5 : 5 + body_size]
    expected_checksum = int.from_bytes(
        data[5 + body_size : 5 + body_size + 2], "little"
    )
    actual_checksum = crc16(body)
    if actual_checksum != expected_checksum:
        return None

    return Frame(command=command, payload=payload)


def parse_chunked_frames(reports: list[bytes]) -> Frame | None:
    """Reassemble a multi-report message and parse it.

    Args:
        reports: List of 64-byte HID reports forming a single message.

    Returns:
        A ``Frame`` if reassembly and checksum pass, else ``None``.
    """
    # Concatenate the meaningful bytes from each report
    assembled = b""
    for report in reports:
        if len(report) < 1:
            continue
        chunk_size = report[0]
        assembled += report[1 : 1 + chunk_size]

    if len(assembled) < 7:
        return None

    # Verify preamble
    if assembled[0:2] != PREAMBLE:
        return None

    body_size = int.from_bytes(assembled[2:4], "little")
    if body_size < 1:
        return None

    command = assembled[4]
    payload = assembled[5 : 4 + body_size]

    # Verify checksum
    body = assembled[4 : 4 + body_size]
    expected_checksum = int.from_bytes(
        assembled[4 + body_size : 4 + body_size + 2], "little"
    )
    actual_checksum = crc16(body)
    if actual_checksum != expected_checksum:
        return None

    return Frame(command=command, payload=payload)
