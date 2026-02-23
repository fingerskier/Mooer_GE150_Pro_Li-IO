"""Data models for effect modules within a preset.

Each module has a fixed-size binary representation. All parameter
values are unsigned 8-bit integers (0-255) unless noted otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import ClassVar


@dataclass
class EffectModule:
    """Base class for effect modules."""

    SIZE: ClassVar[int] = 0
    header: int = 0
    enabled: int = 0
    type: int = 0

    def to_bytes(self) -> bytes:
        raise NotImplementedError

    @classmethod
    def from_bytes(cls, data: bytes):
        raise NotImplementedError

    def to_dict(self) -> dict:
        d = asdict(self)
        d["enabled"] = bool(d["enabled"])
        return d


@dataclass
class FXModule(EffectModule):
    """FX / Compressor module (13 bytes)."""

    SIZE: ClassVar[int] = 13
    q: int = 0
    position: int = 0
    peak: int = 0
    level: int = 0
    reserved: bytes = field(default_factory=lambda: b"\x00" * 6)

    def to_bytes(self) -> bytes:
        return bytes([
            self.header, self.enabled, self.type,
            self.q, self.position, self.peak, self.level,
        ]) + self.reserved[:6]

    @classmethod
    def from_bytes(cls, data: bytes) -> FXModule:
        if len(data) < cls.SIZE:
            data = data + b"\x00" * (cls.SIZE - len(data))
        return cls(
            header=data[0], enabled=data[1], type=data[2],
            q=data[3], position=data[4], peak=data[5], level=data[6],
            reserved=data[7:13],
        )


@dataclass
class DistortionModule(EffectModule):
    """Distortion / Overdrive module (11 bytes)."""

    SIZE: ClassVar[int] = 11
    volume: int = 0
    tone: int = 0
    gain: int = 0
    reserved: bytes = field(default_factory=lambda: b"\x00" * 5)

    def to_bytes(self) -> bytes:
        return bytes([
            self.header, self.enabled, self.type,
            self.volume, self.tone, self.gain,
        ]) + self.reserved[:5]

    @classmethod
    def from_bytes(cls, data: bytes) -> DistortionModule:
        if len(data) < cls.SIZE:
            data = data + b"\x00" * (cls.SIZE - len(data))
        return cls(
            header=data[0], enabled=data[1], type=data[2],
            volume=data[3], tone=data[4], gain=data[5],
            reserved=data[6:11],
        )


@dataclass
class AmpModule(EffectModule):
    """Amp model module (17 bytes)."""

    SIZE: ClassVar[int] = 17
    amp_gain: int = 0
    bass: int = 0
    mid: int = 0
    treble: int = 0
    presence: int = 0
    master: int = 0
    reserved: bytes = field(default_factory=lambda: b"\x00" * 8)

    def to_bytes(self) -> bytes:
        return bytes([
            self.header, self.enabled, self.type,
            self.amp_gain, self.bass, self.mid, self.treble,
            self.presence, self.master,
        ]) + self.reserved[:8]

    @classmethod
    def from_bytes(cls, data: bytes) -> AmpModule:
        if len(data) < cls.SIZE:
            data = data + b"\x00" * (cls.SIZE - len(data))
        return cls(
            header=data[0], enabled=data[1], type=data[2],
            amp_gain=data[3], bass=data[4], mid=data[5], treble=data[6],
            presence=data[7], master=data[8],
            reserved=data[9:17],
        )


@dataclass
class CabModule(EffectModule):
    """Cabinet simulation module (13 bytes)."""

    SIZE: ClassVar[int] = 13
    mic: int = 0
    center: int = 0
    distance: int = 0
    tube: int = 0
    reserved: bytes = field(default_factory=lambda: b"\x00" * 6)

    def to_bytes(self) -> bytes:
        return bytes([
            self.header, self.enabled, self.type,
            self.mic, self.center, self.distance, self.tube,
        ]) + self.reserved[:6]

    @classmethod
    def from_bytes(cls, data: bytes) -> CabModule:
        if len(data) < cls.SIZE:
            data = data + b"\x00" * (cls.SIZE - len(data))
        return cls(
            header=data[0], enabled=data[1], type=data[2],
            mic=data[3], center=data[4], distance=data[5], tube=data[6],
            reserved=data[7:13],
        )


@dataclass
class NoiseGateModule(EffectModule):
    """Noise gate module (11 bytes)."""

    SIZE: ClassVar[int] = 11
    attack: int = 0
    release: int = 0
    threshold: int = 0
    reserved: bytes = field(default_factory=lambda: b"\x00" * 5)

    def to_bytes(self) -> bytes:
        return bytes([
            self.header, self.enabled, self.type,
            self.attack, self.release, self.threshold,
        ]) + self.reserved[:5]

    @classmethod
    def from_bytes(cls, data: bytes) -> NoiseGateModule:
        if len(data) < cls.SIZE:
            data = data + b"\x00" * (cls.SIZE - len(data))
        return cls(
            header=data[0], enabled=data[1], type=data[2],
            attack=data[3], release=data[4], threshold=data[5],
            reserved=data[6:11],
        )


@dataclass
class EQModule(EffectModule):
    """Equalizer module (23 bytes)."""

    SIZE: ClassVar[int] = 23
    bands: list[int] = field(default_factory=lambda: [0] * 6)
    bands_extra: list[int] = field(default_factory=lambda: [0] * 6)
    reserved: bytes = field(default_factory=lambda: b"\x00" * 8)

    def to_bytes(self) -> bytes:
        return bytes([
            self.header, self.enabled, self.type,
        ] + self.bands[:6] + self.bands_extra[:6]) + self.reserved[:8]

    @classmethod
    def from_bytes(cls, data: bytes) -> EQModule:
        if len(data) < cls.SIZE:
            data = data + b"\x00" * (cls.SIZE - len(data))
        return cls(
            header=data[0], enabled=data[1], type=data[2],
            bands=list(data[3:9]),
            bands_extra=list(data[9:15]),
            reserved=data[15:23],
        )


@dataclass
class ModulationModule(EffectModule):
    """Modulation module (15 bytes)."""

    SIZE: ClassVar[int] = 15
    rate: int = 0
    level: int = 0
    depth: int = 0
    param4: int = 0
    param5: int = 0
    reserved: bytes = field(default_factory=lambda: b"\x00" * 7)

    def to_bytes(self) -> bytes:
        return bytes([
            self.header, self.enabled, self.type,
            self.rate, self.level, self.depth,
            self.param4, self.param5,
        ]) + self.reserved[:7]

    @classmethod
    def from_bytes(cls, data: bytes) -> ModulationModule:
        if len(data) < cls.SIZE:
            data = data + b"\x00" * (cls.SIZE - len(data))
        return cls(
            header=data[0], enabled=data[1], type=data[2],
            rate=data[3], level=data[4], depth=data[5],
            param4=data[6], param5=data[7],
            reserved=data[8:15],
        )


@dataclass
class DelayModule(EffectModule):
    """Delay module (17 bytes).

    Delay time is a 16-bit value at bytes 5-6.
    """

    SIZE: ClassVar[int] = 17
    level: int = 0
    feedback: int = 0
    time_ms: int = 0  # 16-bit, stored as bytes 5-6
    subdivision: int = 0
    param5: int = 0
    param6: int = 0
    reserved: bytes = field(default_factory=lambda: b"\x00" * 7)

    def to_bytes(self) -> bytes:
        time_lo = self.time_ms & 0xFF
        time_hi = (self.time_ms >> 8) & 0xFF
        return bytes([
            self.header, self.enabled, self.type,
            self.level, self.feedback, time_lo, time_hi,
            self.subdivision, self.param5, self.param6,
        ]) + self.reserved[:7]

    @classmethod
    def from_bytes(cls, data: bytes) -> DelayModule:
        if len(data) < cls.SIZE:
            data = data + b"\x00" * (cls.SIZE - len(data))
        time_ms = data[5] | (data[6] << 8)
        return cls(
            header=data[0], enabled=data[1], type=data[2],
            level=data[3], feedback=data[4],
            time_ms=time_ms,
            subdivision=data[7], param5=data[8], param6=data[9],
            reserved=data[10:17],
        )

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["time_ms"] = self.time_ms
        return d


@dataclass
class ReverbModule(EffectModule):
    """Reverb module (13 bytes)."""

    SIZE: ClassVar[int] = 13
    pre_delay: int = 0
    level: int = 0
    decay: int = 0
    tone: int = 0
    reserved: bytes = field(default_factory=lambda: b"\x00" * 6)

    def to_bytes(self) -> bytes:
        return bytes([
            self.header, self.enabled, self.type,
            self.pre_delay, self.level, self.decay, self.tone,
        ]) + self.reserved[:6]

    @classmethod
    def from_bytes(cls, data: bytes) -> ReverbModule:
        if len(data) < cls.SIZE:
            data = data + b"\x00" * (cls.SIZE - len(data))
        return cls(
            header=data[0], enabled=data[1], type=data[2],
            pre_delay=data[3], level=data[4], decay=data[5], tone=data[6],
            reserved=data[7:13],
        )


# Map module names to classes for factory use
MODULE_CLASSES: dict[str, type[EffectModule]] = {
    "fx": FXModule,
    "od": DistortionModule,
    "amp": AmpModule,
    "cab": CabModule,
    "ns": NoiseGateModule,
    "eq": EQModule,
    "mod": ModulationModule,
    "delay": DelayModule,
    "reverb": ReverbModule,
}
