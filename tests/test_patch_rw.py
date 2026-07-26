"""Preset read/write round-trips through the capture-confirmed protocol.

These are the contracts the legacy patch-R/W suite pinned — merge-over-
existing, byte-exact copy/swap, export/import and backup/restore — now
exercised end-to-end against ``FakeMaxPedal``: bulk dump (0xA0/0x20) for
reads and bracketed direct writes (0xBA, 0xC3→0x43, 0xBB) for writes.

The old suite drove a fake speaking the pre-capture protocol (0x96 reads,
0xA8 stores); both of those turned out to be other commands entirely, so
the fake and the tests were replaced rather than ported.
"""

from __future__ import annotations

import json

from unittest.mock import patch

import pytest

from mooer_ge150_mcp.protocol.commands import (
    Command,
    decode_module_block,
    encode_module_block,
)


def _canonical(block):
    """A module block as it looks after a wire round-trip (10 params)."""
    return decode_module_block(encode_module_block(block))

from .fake_max_pedal import FakeMaxPedal, make_max_connection
from .test_restore_overwrite import _get_server_module


@pytest.fixture
def wired():
    server = _get_server_module()
    conn, pedal = make_max_connection()
    server._record_cache = {}
    with patch.object(server, "_get_connection", return_value=conn):
        yield server, conn, pedal


class TestSetGetRoundTrip:
    def test_set_then_get_preset(self, wired):
        server, _, _ = wired
        result = server.set_preset(
            5,
            name="Tone Five",
            effects={
                "amp": {"effect_type": 9, "params": [111, 50, 50]},
                "delay": {"enabled": True, "params": [50, 50, 480]},
            },
        )
        assert result["stored"] is True
        assert result["name"] == "Tone Five"

        read_back = server.get_preset(5)
        assert read_back["name"] == "Tone Five"
        assert read_back["modules"]["amp"]["effect_type"] == 9
        assert read_back["modules"]["amp"]["params"][0] == 111
        assert read_back["modules"]["delay"]["params"][2] == 480
        json.dumps(read_back)  # tool results must be JSON-serializable

    def test_name_only_update_preserves_effects(self, wired):
        """A rename must not wipe the modules already in the slot."""
        server, _, pedal = wired
        before = _canonical(pedal.records[3].modules[Command.AMP])

        server.set_preset(2, name="Renamed")

        stored = pedal.records[3]
        assert stored.name == "Renamed"
        assert stored.modules[Command.AMP] == before

    def test_direct_writes_are_bracketed_like_a_restore(self, wired):
        """0xC3 has only been observed inside 0xBA...0xBB, so every
        direct write must use the same bracket."""
        server, _, pedal = wired
        server.set_preset(0, name="X")
        assert pedal.restore_brackets == ["begin", "end"]

    def test_legacy_param_names_are_rejected_with_guidance(self, wired):
        """The old per-param names (amp_gain, ...) came from a speculative
        model; the error should steer callers to the real schema."""
        server, _, _ = wired
        result = server.set_preset(0, effects={"amp": {"amp_gain": 100}})
        assert "error" in result
        assert "effect_type" in result["error"]


class TestCopyAndSwap:
    def test_copy_preserves_every_byte(self, wired):
        """Unmodeled data — raw name padding and the 12-byte tail — must
        survive a copy."""
        server, _, pedal = wired
        source = pedal.records[1]
        source.tail = bytes(range(12))
        source.name_raw = b"Padded  \x00\x00\x00\x00\x00\x00\x00\x00"

        result = server.copy_preset(0, 9)

        assert result["copied"] is True
        target = pedal.records[10]
        assert target.tail == bytes(range(12))
        assert target.name_raw == source.name_raw
        assert target.modules == {
            c: _canonical(b) for c, b in source.modules.items()
        }
        assert target.slot == 10  # re-slotted, not cloned verbatim

    def test_copy_does_not_alias_records(self, wired):
        """Editing the copy afterwards must not change the source."""
        server, _, pedal = wired
        server.copy_preset(0, 9)
        server.set_preset(9, effects={"amp": {"effect_type": 42}})
        assert pedal.records[1].modules[Command.AMP].effect_type != 42

    def test_swap_exchanges_slots_byte_exactly(self, wired):
        server, _, pedal = wired
        pedal.records[1].tail = b"A" * 12
        pedal.records[8].tail = b"B" * 12
        name_a, name_b = pedal.records[1].name, pedal.records[8].name

        result = server.swap_presets(0, 7)

        assert result["swapped"] is True
        assert pedal.records[1].name == name_b
        assert pedal.records[8].name == name_a
        assert pedal.records[1].tail == b"B" * 12
        assert pedal.records[8].tail == b"A" * 12


class TestExportImport:
    def test_export_import_round_trip(self, wired, tmp_path):
        server, _, pedal = wired
        pedal.records[1].tail = bytes(range(12))
        path = tmp_path / "one.json"

        exported = server.export_preset(0, str(path))
        assert exported["name"] == "Preset 1"

        result = server.import_preset(str(path), 19)
        assert result["imported"] is True
        assert result["address"] == "5D"
        assert pedal.records[20].name == "Preset 1"
        assert pedal.records[20].tail == bytes(range(12))
        assert pedal.records[20].slot == 20  # re-slotted on import

    def test_import_rejects_foreign_files(self, wired, tmp_path):
        server, _, _ = wired
        path = tmp_path / "bogus.json"
        path.write_text('{"format": "something-else"}')
        assert "error" in server.import_preset(str(path), 0)

    def test_import_rejects_truncated_records(self, wired, tmp_path):
        server, _, _ = wired
        path = tmp_path / "short.json"
        path.write_text(json.dumps(
            {"format": "mooer-ge150-preset", "slot": 0, "record": "aa55"}
        ))
        assert "error" in server.import_preset(str(path), 0)


class TestBackupRestore:
    def test_backup_restore_round_trip(self, wired, tmp_path):
        server, _, pedal = wired
        pedal.records[7].tail = b"\x07" * 12
        path = tmp_path / "backup.json"

        result = server.backup_all(str(path))
        assert result["preset_count"] == 200

        # Wreck a preset, then restore over it.
        server.set_preset(6, name="Wrecked",
                          effects={"amp": {"effect_type": 63}})
        restored = server.restore_backup(str(path), overwrite=True)

        assert restored["restored"] is True
        assert pedal.records[7].name == "Preset 7"
        assert pedal.records[7].tail == b"\x07" * 12

    def test_backup_file_is_json_with_hex_records(self, wired, tmp_path):
        server, _, _ = wired
        path = tmp_path / "backup.json"
        server.backup_all(str(path))

        payload = json.loads(path.read_text())
        assert payload["format"] == "mooer-ge150-backup"
        assert len(payload["presets"]) == 200
        first = payload["presets"][0]
        assert first["address"] == "1A"
        assert len(bytes.fromhex(first["record"])) == 245
