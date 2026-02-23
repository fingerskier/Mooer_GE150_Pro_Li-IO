"""Tests for preset data model serialization/deserialization."""

from mooer_ge150_mcp.models.preset import Preset, PRESET_SIZE
from mooer_ge150_mcp.models.effects import (
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


def test_preset_roundtrip():
    """Serialize and deserialize a preset."""
    preset = Preset(
        name="Test Preset",
        effect_order=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
        amp=AmpModule(enabled=1, type=5, amp_gain=128, bass=64, mid=100,
                       treble=80, presence=90, master=200),
        od=DistortionModule(enabled=1, type=2, volume=100, tone=64, gain=200),
        reverb=ReverbModule(enabled=1, type=3, pre_delay=20, level=80,
                            decay=150, tone=100),
    )

    data = preset.to_bytes()
    assert len(data) == PRESET_SIZE

    restored = Preset.from_bytes(data)
    assert restored.name == "Test Preset"
    assert restored.amp.type == 5
    assert restored.amp.amp_gain == 128
    assert restored.amp.bass == 64
    assert restored.od.gain == 200
    assert restored.reverb.decay == 150


def test_preset_empty():
    """Default preset should serialize to 512 bytes."""
    preset = Preset()
    data = preset.to_bytes()
    assert len(data) == PRESET_SIZE


def test_preset_name_truncation():
    """Names longer than 14 chars should be truncated."""
    preset = Preset(name="This Is A Very Long Preset Name")
    data = preset.to_bytes()
    restored = Preset.from_bytes(data)
    assert len(restored.name) <= 14


def test_preset_to_dict():
    """to_dict should produce a JSON-serializable structure."""
    preset = Preset(name="Dict Test", amp=AmpModule(enabled=1, type=3))
    d = preset.to_dict()
    assert d["name"] == "Dict Test"
    assert "effects" in d
    assert d["effects"]["amp"]["enabled"] is True
    assert d["effects"]["amp"]["type"] == 3


def test_effect_order_preserved():
    """Effect order should survive round-trip."""
    order = [8, 7, 6, 5, 4, 3, 2, 1, 0, 9]
    preset = Preset(effect_order=order)
    data = preset.to_bytes()
    restored = Preset.from_bytes(data)
    assert restored.effect_order == order


def test_delay_time_16bit():
    """Delay time (16-bit) should round-trip correctly."""
    preset = Preset(delay=DelayModule(enabled=1, type=1, time_ms=1500))
    data = preset.to_bytes()
    restored = Preset.from_bytes(data)
    assert restored.delay.time_ms == 1500


def test_eq_bands():
    """EQ band values should round-trip."""
    bands = [10, 20, 30, 40, 50, 60]
    preset = Preset(eq=EQModule(enabled=1, type=0, bands=bands))
    data = preset.to_bytes()
    restored = Preset.from_bytes(data)
    assert restored.eq.bands == bands


def test_get_module():
    """get_module should return the correct module."""
    preset = Preset(amp=AmpModule(type=42))
    assert preset.get_module("amp").type == 42


def test_get_module_invalid():
    """get_module with invalid name should raise."""
    preset = Preset()
    try:
        preset.get_module("invalid")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_fx_module_roundtrip():
    """FX module bytes round-trip."""
    module = FXModule(header=1, enabled=1, type=3, q=50, position=2, peak=100, level=80)
    data = module.to_bytes()
    assert len(data) == FXModule.SIZE
    restored = FXModule.from_bytes(data)
    assert restored.type == 3
    assert restored.level == 80


def test_all_modules_size():
    """All module to_bytes should produce the correct size."""
    assert len(FXModule().to_bytes()) == 13
    assert len(DistortionModule().to_bytes()) == 11
    assert len(AmpModule().to_bytes()) == 17
    assert len(CabModule().to_bytes()) == 13
    assert len(NoiseGateModule().to_bytes()) == 11
    assert len(EQModule().to_bytes()) == 23
    assert len(ModulationModule().to_bytes()) == 15
    assert len(DelayModule().to_bytes()) == 17
    assert len(ReverbModule().to_bytes()) == 13
