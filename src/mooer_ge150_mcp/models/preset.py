"""Preset data model: serialize/deserialize the 0x200-byte preset structure.

Layout (offsets relative to preset start)::

    +------------------+--------+------+----+----+-----+-----+----+----+-----+-------+--------+
    | Effect Order     | Size   | Name | FX | OD | AMP | CAB | NS | EQ | MOD | DELAY | REVERB |
    | 10 bytes         | 2 bytes| 14 B |    |    |     |     |    |    |     |       |        |
    +------------------+--------+------+----+----+-----+-----+----+----+-----+-------+--------+
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from .effects import (
    FXModule,
    DistortionModule,
    AmpModule,
    CabModule,
    NoiseGateModule,
    EQModule,
    ModulationModule,
    DelayModule,
    ReverbModule,
)

PRESET_SIZE = 0x200  # 512 bytes

# Offsets within the preset data
OFF_EFFECT_ORDER = 0x00   # 10 bytes
OFF_SIZE = 0x0A           # 2 bytes (big-endian)
OFF_NAME = 0x0C           # 14 bytes ASCII
OFF_FX = 0x1A             # 13 bytes
OFF_OD = 0x27             # 11 bytes
OFF_AMP = 0x32            # 17 bytes
OFF_CAB = 0x43            # 13 bytes
OFF_NS = 0x50             # 11 bytes
OFF_EQ = 0x5B             # 23 bytes
OFF_MOD = 0x72            # 15 bytes
OFF_DELAY = 0x81          # 17 bytes
OFF_REVERB = 0x92         # 13 bytes

MODULE_NAMES = ["fx", "od", "amp", "cab", "ns", "eq", "mod", "delay", "reverb"]


@dataclass
class Preset:
    """A single device preset (512 bytes)."""

    TOTAL_SIZE: ClassVar[int] = PRESET_SIZE

    effect_order: list[int] = field(default_factory=lambda: list(range(10)))
    name: str = ""
    fx: FXModule = field(default_factory=FXModule)
    od: DistortionModule = field(default_factory=DistortionModule)
    amp: AmpModule = field(default_factory=AmpModule)
    cab: CabModule = field(default_factory=CabModule)
    ns: NoiseGateModule = field(default_factory=NoiseGateModule)
    eq: EQModule = field(default_factory=EQModule)
    mod: ModulationModule = field(default_factory=ModulationModule)
    delay: DelayModule = field(default_factory=DelayModule)
    reverb: ReverbModule = field(default_factory=ReverbModule)

    def to_bytes(self) -> bytes:
        """Serialize the preset to a 0x200-byte structure."""
        buf = bytearray(PRESET_SIZE)

        # Effect order (10 bytes)
        for i, val in enumerate(self.effect_order[:10]):
            buf[OFF_EFFECT_ORDER + i] = val & 0xFF

        # Calculate data size (everything from name onward)
        data_size = PRESET_SIZE - OFF_SIZE - 2
        buf[OFF_SIZE] = (data_size >> 8) & 0xFF
        buf[OFF_SIZE + 1] = data_size & 0xFF

        # Name (14 bytes, null-padded ASCII)
        name_bytes = self.name.encode("ascii", errors="replace")[:14]
        buf[OFF_NAME : OFF_NAME + len(name_bytes)] = name_bytes

        # Effect modules
        buf[OFF_FX : OFF_FX + FXModule.SIZE] = self.fx.to_bytes()
        buf[OFF_OD : OFF_OD + DistortionModule.SIZE] = self.od.to_bytes()
        buf[OFF_AMP : OFF_AMP + AmpModule.SIZE] = self.amp.to_bytes()
        buf[OFF_CAB : OFF_CAB + CabModule.SIZE] = self.cab.to_bytes()
        buf[OFF_NS : OFF_NS + NoiseGateModule.SIZE] = self.ns.to_bytes()
        buf[OFF_EQ : OFF_EQ + EQModule.SIZE] = self.eq.to_bytes()
        buf[OFF_MOD : OFF_MOD + ModulationModule.SIZE] = self.mod.to_bytes()
        buf[OFF_DELAY : OFF_DELAY + DelayModule.SIZE] = self.delay.to_bytes()
        buf[OFF_REVERB : OFF_REVERB + ReverbModule.SIZE] = self.reverb.to_bytes()

        return bytes(buf)

    @classmethod
    def from_bytes(cls, data: bytes) -> Preset:
        """Deserialize a preset from a 0x200-byte (or larger) structure."""
        if len(data) < PRESET_SIZE:
            data = data + b"\x00" * (PRESET_SIZE - len(data))

        effect_order = list(data[OFF_EFFECT_ORDER : OFF_EFFECT_ORDER + 10])

        name_bytes = data[OFF_NAME : OFF_NAME + 14]
        name = name_bytes.split(b"\x00")[0].decode("ascii", errors="replace")

        return cls(
            effect_order=effect_order,
            name=name,
            fx=FXModule.from_bytes(data[OFF_FX : OFF_FX + FXModule.SIZE]),
            od=DistortionModule.from_bytes(data[OFF_OD : OFF_OD + DistortionModule.SIZE]),
            amp=AmpModule.from_bytes(data[OFF_AMP : OFF_AMP + AmpModule.SIZE]),
            cab=CabModule.from_bytes(data[OFF_CAB : OFF_CAB + CabModule.SIZE]),
            ns=NoiseGateModule.from_bytes(data[OFF_NS : OFF_NS + NoiseGateModule.SIZE]),
            eq=EQModule.from_bytes(data[OFF_EQ : OFF_EQ + EQModule.SIZE]),
            mod=ModulationModule.from_bytes(data[OFF_MOD : OFF_MOD + ModulationModule.SIZE]),
            delay=DelayModule.from_bytes(data[OFF_DELAY : OFF_DELAY + DelayModule.SIZE]),
            reverb=ReverbModule.from_bytes(data[OFF_REVERB : OFF_REVERB + ReverbModule.SIZE]),
        )

    def to_dict(self) -> dict:
        """Convert preset to a JSON-serializable dictionary."""
        return {
            "name": self.name,
            "effect_order": self.effect_order,
            "effects": {
                "fx": self.fx.to_dict(),
                "od": self.od.to_dict(),
                "amp": self.amp.to_dict(),
                "cab": self.cab.to_dict(),
                "ns": self.ns.to_dict(),
                "eq": self.eq.to_dict(),
                "mod": self.mod.to_dict(),
                "delay": self.delay.to_dict(),
                "reverb": self.reverb.to_dict(),
            },
        }

    def get_module(self, name: str):
        """Get an effect module by name."""
        modules = {
            "fx": self.fx,
            "od": self.od,
            "amp": self.amp,
            "cab": self.cab,
            "ns": self.ns,
            "eq": self.eq,
            "mod": self.mod,
            "delay": self.delay,
            "reverb": self.reverb,
        }
        if name not in modules:
            raise ValueError(f"Unknown module '{name}'. Valid: {list(modules)}")
        return modules[name]

    def __repr__(self) -> str:
        return f"Preset(name={self.name!r})"
