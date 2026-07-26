"""Overwrite guards on restore_backup.

The contracts from commit bcf8938, re-pinned against the confirmed
protocol: an occupied slot is never clobbered by an empty backup entry,
``overwrite=False`` skips occupied slots entirely, and empty device
slots are always filled.
"""

from __future__ import annotations

import json
import sys

from unittest.mock import MagicMock, patch

import pytest

from mooer_ge150_mcp.protocol.commands import PRESET_NAME_LENGTH

from .fake_max_pedal import make_max_connection


def _get_server_module():
    """Import server module with FastMCP mocked to avoid init issues."""
    mock_fastmcp_cls = MagicMock()
    mock_fastmcp_instance = MagicMock()
    # Make the @mcp.tool() decorator a no-op that returns the function unchanged
    mock_fastmcp_instance.tool.return_value = lambda fn: fn
    mock_fastmcp_instance.resource.return_value = lambda fn: fn
    mock_fastmcp_instance.prompt.return_value = lambda fn: fn
    mock_fastmcp_cls.return_value = mock_fastmcp_instance

    with patch.dict(sys.modules, {}):
        with patch("mcp.server.fastmcp.FastMCP", mock_fastmcp_cls):
            # Remove cached server module so it re-imports with our mock
            sys.modules.pop("mooer_ge150_mcp.server", None)
            import mooer_ge150_mcp.server as server_mod

    return server_mod


@pytest.fixture
def wired():
    server = _get_server_module()
    conn, pedal = make_max_connection()
    server._record_cache = {}
    with patch.object(server, "_get_connection", return_value=conn):
        yield server, conn, pedal


def _make_backup(server, pedal, path, edits):
    """Write a backup file, apply *edits* to the entries, return path."""
    server.backup_all(str(path))
    payload = json.loads(path.read_text())
    for slot, entry_edit in edits.items():
        for entry in payload["presets"]:
            if entry["slot"] == slot:
                entry.update(entry_edit)
    path.write_text(json.dumps(payload))
    return path


def _blank_entry(entry_record_hex: str) -> dict:
    """Blank out the name inside a raw record, keeping everything else."""
    raw = bytearray(bytes.fromhex(entry_record_hex))
    raw[1 : 1 + PRESET_NAME_LENGTH] = bytes(PRESET_NAME_LENGTH)
    return {"name": "", "record": raw.hex()}


def test_empty_backup_preset_does_not_erase_occupied_slot(wired, tmp_path):
    """An empty backup entry must NOT overwrite a named device preset,
    even with overwrite=True."""
    server, _, pedal = wired
    path = tmp_path / "backup.json"
    server.backup_all(str(path))

    payload = json.loads(path.read_text())
    payload["presets"][0].update(_blank_entry(payload["presets"][0]["record"]))
    path.write_text(json.dumps(payload))

    result = server.restore_backup(str(path), overwrite=True)

    assert pedal.records[1].name == "Preset 1"  # survived
    assert 0 in result.get("skipped_slots", [])


def test_named_backup_skips_occupied_slot(wired, tmp_path):
    """With overwrite=False, a named backup entry must not replace a
    named device preset."""
    server, _, pedal = wired
    path = tmp_path / "backup.json"
    server.backup_all(str(path))

    # Change the device preset after the backup was taken.
    server.set_preset(0, name="Kept On Device")

    result = server.restore_backup(str(path), overwrite=False)

    assert pedal.records[1].name == "Kept On Device"
    assert 0 in result.get("skipped_slots", [])


def test_backup_fills_empty_device_slot(wired, tmp_path):
    """An empty device slot is filled from the backup even without
    overwrite."""
    server, _, pedal = wired
    path = tmp_path / "backup.json"
    server.backup_all(str(path))

    # Empty the device slot after the backup.
    server.set_preset(0, name="")
    server._record_cache = {}
    assert not pedal.records[1].name.strip()

    result = server.restore_backup(str(path), overwrite=False)

    assert pedal.records[1].name == "Preset 1"  # refilled from backup
    assert result["preset_count"] >= 1


def test_restore_rejects_unknown_format(wired, tmp_path):
    server, _, _ = wired
    path = tmp_path / "old.mbf"
    path.write_bytes(b"\x00" * 64)
    assert "error" in server.restore_backup(str(path))
