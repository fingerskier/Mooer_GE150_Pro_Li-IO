"""End-to-end read/write tests for patches (presets).

These tests run the real protocol and transport code against a
``FakePedal`` (tests/fake_device.py) that emulates the device at the
64-byte HID report level, so every test covers:

    Preset model → store frames → chunked TX → device →
    chunked RX → reassembly → parse → Preset model

See TESTING.md for the full patch R/W test process, including the
hardware-in-the-loop procedure this suite mirrors.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from mooer_ge150_mcp.models.preset import Preset, PRESET_SIZE, OFF_TAIL
from mooer_ge150_mcp.models.effects import (
    AmpModule,
    DistortionModule,
    DelayModule,
    ReverbModule,
    NoiseGateModule,
    MODULE_CLASSES,
)
from mooer_ge150_mcp.protocol.commands import build_read_preset, build_store_preset
from mooer_ge150_mcp.protocol.parser import parse_preset_response

from .fake_device import FakePedal, make_connection
from .test_restore_overwrite import _get_server_module


def _make_rich_preset(name: str = "RW Test") -> Preset:
    """A preset with distinct non-zero values spread across modules."""
    return Preset(
        name=name,
        effect_order=[3, 1, 2, 0, 4, 5, 8, 7, 6, 0],
        amp=AmpModule(enabled=1, type=12, amp_gain=200, bass=90, mid=110,
                      treble=130, presence=70, master=150),
        od=DistortionModule(enabled=1, type=4, volume=95, tone=85, gain=180),
        ns=NoiseGateModule(enabled=1, threshold=42),
        delay=DelayModule(enabled=1, type=2, level=60, feedback=45,
                          time_ms=750, subdivision=3),
        reverb=ReverbModule(enabled=1, type=5, level=80, decay=64, tone=33),
    )


# ─── Preset model: byte-level roundtrip ──────────────────────────────

def test_preset_bytes_roundtrip():
    """to_bytes → from_bytes must preserve every field."""
    original = _make_rich_preset()
    restored = Preset.from_bytes(original.to_bytes())
    assert restored.to_bytes() == original.to_bytes()
    assert restored.name == original.name
    assert restored.effect_order == original.effect_order
    assert restored.amp.amp_gain == 200
    assert restored.delay.time_ms == 750
    assert restored.delay.subdivision == 3


def test_preset_serialized_size():
    assert len(_make_rich_preset().to_bytes()) == PRESET_SIZE


def _with_opaque_tail(preset: Preset) -> bytes:
    """Serialize a preset, then fill the unmodeled tail with a pattern."""
    raw = bytearray(preset.to_bytes())
    raw[OFF_TAIL:PRESET_SIZE] = bytes(
        (i * 7 + 13) & 0xFF for i in range(PRESET_SIZE - OFF_TAIL)
    )
    return bytes(raw)


def test_preset_preserves_opaque_tail_bytes():
    """Unmodeled bytes (0x9F-0x1FF) must survive from_bytes → to_bytes."""
    raw = _with_opaque_tail(_make_rich_preset())
    assert Preset.from_bytes(raw).to_bytes() == raw


def test_delay_16bit_time_roundtrip():
    """time_ms > 255 must survive serialization (16-bit little-endian)."""
    delay = DelayModule(time_ms=1500)
    restored = DelayModule.from_bytes(delay.to_bytes())
    assert restored.time_ms == 1500


def test_param_offsets_account_for_wide_fields():
    """Delay params after the 16-bit time_ms must be shifted by one byte."""
    offsets = DelayModule.param_offsets()
    assert offsets["time_ms"] == 5
    assert offsets["subdivision"] == 7  # not 6: time_ms occupies bytes 5-6
    assert offsets["param5"] == 8


def test_all_modules_to_dict_json_serializable():
    """Tool results are JSON-encoded; no module dict may contain bytes."""
    for name, cls in MODULE_CLASSES.items():
        json.dumps(cls().to_dict())
    json.dumps(_make_rich_preset().to_dict())


# ─── Protocol + transport: chunked store and read ────────────────────

def test_store_and_read_preset_via_transport():
    """Full wire roundtrip: chunked store, then chunked read + reassembly."""
    conn, pedal = make_connection()
    original = _make_rich_preset("Wire Trip")

    frames = build_store_preset(7, original.to_bytes())
    assert len(frames) > 1  # 513-byte payload must be chunked
    ack = conn.send_chunked_and_receive(frames)
    assert ack is not None

    response = conn.send_and_receive(build_read_preset(7))
    assert response is not None
    parsed = parse_preset_response(response)
    assert parsed is not None
    assert parsed.slot == 7

    restored = Preset.from_bytes(parsed.data)
    assert restored.to_bytes() == original.to_bytes()


def test_read_response_spans_multiple_reports():
    """A 512-byte preset response cannot fit one report; reassembly must work."""
    conn, pedal = make_connection()
    pedal.slots[3] = _make_rich_preset("Big Reply").to_bytes()

    conn.write(build_read_preset(3))
    assert len(pedal._tx_reports) > 1  # device queued a chunked response

    frame = conn.read_message()
    assert frame is not None
    parsed = parse_preset_response(frame)
    assert Preset.from_bytes(parsed.data).name == "Big Reply"


def test_read_message_timeout_mid_message_returns_none():
    """Losing reports mid-message must yield None, not a corrupt frame."""
    conn, pedal = make_connection()
    pedal.slots[0] = _make_rich_preset().to_bytes()
    conn.write(build_read_preset(0))
    # Drop everything after the first report
    first = pedal._tx_reports.popleft()
    pedal._tx_reports.clear()
    pedal._tx_reports.append(first)
    assert conn.read_message() is None


# ─── Server tools: get/set/copy/swap/export/import/backup/restore ────

def _server_with_pedal():
    server = _get_server_module()
    conn, pedal = make_connection()
    return server, conn, pedal


def test_server_set_then_get_preset():
    server, conn, pedal = _server_with_pedal()
    with patch.object(server, "_get_connection", return_value=conn):
        result = server.set_preset(
            5,
            name="Tone Five",
            effects={"amp": {"type": 9, "amp_gain": 111},
                     "delay": {"enabled": 1, "time_ms": 480}},
        )
        assert result == {"stored": True, "slot": 5, "name": "Tone Five"}

    # get_preset now reads via the confirmed bulk dump, which this legacy
    # fake does not implement, so verify the store landed on the device.
    stored = Preset.from_bytes(pedal.slots[5])
    assert stored.name == "Tone Five"
    assert stored.amp.type == 9
    assert stored.amp.amp_gain == 111
    assert stored.delay.time_ms == 480
    json.dumps(result)  # tool results must be JSON-serializable


def test_server_set_preset_merges_over_existing():
    """A name-only update must not wipe the effects already in the slot."""
    server, conn, pedal = _server_with_pedal()
    pedal.slots[2] = _make_rich_preset("Original").to_bytes()

    with patch.object(server, "_get_connection", return_value=conn):
        server.set_preset(2, name="Renamed")

    stored = Preset.from_bytes(pedal.slots[2])
    assert stored.name == "Renamed"
    assert stored.amp.amp_gain == 200  # preserved


def test_server_copy_and_swap_preserve_opaque_bytes():
    """copy/swap must be byte-for-byte safe, including unmodeled data."""
    server, conn, pedal = _server_with_pedal()
    raw_a = _with_opaque_tail(_make_rich_preset("Opaque A"))
    raw_b = _with_opaque_tail(_make_rich_preset("Opaque B"))
    pedal.slots[0] = raw_a
    pedal.slots[1] = raw_b

    with patch.object(server, "_get_connection", return_value=conn):
        server.copy_preset(0, 10)
        assert pedal.slots[10] == raw_a

        server.swap_presets(0, 1)
        assert pedal.slots[0] == raw_b
        assert pedal.slots[1] == raw_a


def test_server_set_preset_merge_preserves_opaque_bytes():
    """A partial set_preset must not zero the unmodeled tail bytes."""
    server, conn, pedal = _server_with_pedal()
    raw = _with_opaque_tail(_make_rich_preset("Opaque"))
    pedal.slots[3] = raw

    with patch.object(server, "_get_connection", return_value=conn):
        server.set_preset(3, name="Renamed")

    assert pedal.slots[3][OFF_TAIL:] == raw[OFF_TAIL:]
    assert Preset.from_bytes(pedal.slots[3]).name == "Renamed"


def test_server_copy_cache_entries_not_aliased():
    """Partial updates to a copied slot must not bleed into the source."""
    server, conn, pedal = _server_with_pedal()
    pedal.slots[0] = _make_rich_preset("Tone A").to_bytes()

    with patch.object(server, "_get_connection", return_value=conn):
        server.copy_preset(0, 10)
        assert server._preset_cache[0] is not server._preset_cache[10]

        server.set_preset(10, name="Diverged")

    assert server._preset_cache[0].name == "Tone A"
    assert Preset.from_bytes(pedal.slots[0]).name == "Tone A"
    assert Preset.from_bytes(pedal.slots[10]).name == "Diverged"


def test_server_copy_and_swap_presets():
    server, conn, pedal = _server_with_pedal()
    pedal.slots[0] = _make_rich_preset("Tone A").to_bytes()
    pedal.slots[1] = _make_rich_preset("Tone B").to_bytes()

    with patch.object(server, "_get_connection", return_value=conn):
        copied = server.copy_preset(0, 10)
        assert copied["copied"] and copied["name"] == "Tone A"
        assert Preset.from_bytes(pedal.slots[10]).name == "Tone A"

        server.swap_presets(0, 1)
        assert Preset.from_bytes(pedal.slots[0]).name == "Tone B"
        assert Preset.from_bytes(pedal.slots[1]).name == "Tone A"
        # Cache must track the swap so later merges don't use stale data
        assert server._preset_cache[0].name == "Tone B"
        assert server._preset_cache[1].name == "Tone A"


def test_server_export_import_mo_roundtrip(tmp_path):
    server, conn, pedal = _server_with_pedal()
    pedal.slots[4] = _make_rich_preset("File Trip").to_bytes()
    mo_path = tmp_path / "preset.mo"

    with patch.object(server, "_get_connection", return_value=conn):
        exported = server.export_preset(4, str(mo_path))
        assert exported["name"] == "File Trip"

        imported = server.import_preset(str(mo_path), 20)
        assert imported == {"imported": True, "slot": 20, "name": "File Trip"}

    assert pedal.slots[20] == pedal.slots[4]


def test_server_backup_restore_roundtrip(tmp_path):
    """backup_all → wipe device → restore_backup must restore every patch."""
    server, conn, pedal = _server_with_pedal()
    pedal.slots[0] = _make_rich_preset("Keep Me 0").to_bytes()
    pedal.slots[42] = _make_rich_preset("Keep Me 42").to_bytes()
    original_slots = list(pedal.slots)
    mbf_path = tmp_path / "backup.mbf"

    with patch.object(server, "_get_connection", return_value=conn), \
         patch("mooer_ge150_mcp.transport.usb_connection.time.sleep"):
        backed_up = server.backup_all(str(mbf_path))
        assert backed_up["preset_count"] == 199
        assert "failed_slots" not in backed_up

        # Wipe the device, then restore
        pedal.slots = [bytes(PRESET_SIZE) for _ in pedal.slots]
        restored = server.restore_backup(str(mbf_path), overwrite=True)
        assert restored["restored"]

    assert Preset.from_bytes(pedal.slots[0]).name == "Keep Me 0"
    assert Preset.from_bytes(pedal.slots[42]).name == "Keep Me 42"
    # Every backed-up slot must match the original device state.
    # Compare canonical Preset form: a restored empty slot carries the
    # serialized size field, which raw all-zero flash bytes lack.
    for slot in range(199):
        restored_bytes = Preset.from_bytes(pedal.slots[slot]).to_bytes()
        original_bytes = Preset.from_bytes(original_slots[slot]).to_bytes()
        assert restored_bytes == original_bytes, f"slot {slot} differs"


# Two tests once lived here pinning set_effect_param's byte-offset delta
# protocol (one frame per byte, param addressed by name). Both USB captures
# disprove it: MOOER Studio never sends a parameter delta, it resends the
# module's whole 24-byte block. The tool was rewired accordingly and is
# covered by tests/test_tools_rewired.py::TestEffectEditing.
