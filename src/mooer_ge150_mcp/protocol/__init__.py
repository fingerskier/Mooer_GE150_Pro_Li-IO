"""Protocol layer: message framing, CRC, command builders, and response parsing."""

from .framing import build_frame, parse_frame
from .commands import Command, build_command
