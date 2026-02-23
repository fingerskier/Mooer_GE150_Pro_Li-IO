"""System settings model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SystemSettings:
    """Global system settings from the device."""

    raw: bytes = b""

    def to_dict(self) -> dict:
        return {
            "raw_hex": self.raw.hex(" ") if self.raw else "",
            "raw_length": len(self.raw),
        }

    @classmethod
    def from_bytes(cls, data: bytes) -> SystemSettings:
        return cls(raw=data)
