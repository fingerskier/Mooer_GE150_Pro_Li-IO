"""Tests for restore_backup overwrite guard logic."""

from __future__ import annotations

import sys
import tempfile
from unittest.mock import MagicMock, patch

from mooer_ge150_mcp.models.preset import Preset
from mooer_ge150_mcp.models.file_formats import export_mbf
from mooer_ge150_mcp.protocol.commands import Command
from mooer_ge150_mcp.protocol.framing import Frame


def _make_preset_frame(slot: int, preset: Preset) -> Frame:
    """Build a Frame that parse_preset_response can parse."""
    return Frame(command=Command.PRESET, payload=bytes([slot]) + preset.to_bytes())


def _create_backup(presets: list[Preset]) -> str:
    """Export presets to a temporary .mbf file and return the path."""
    f = tempfile.NamedTemporaryFile(suffix=".mbf", delete=False)
    path = export_mbf(presets, f.name)
    f.close()
    return path


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


def test_empty_backup_preset_does_not_erase_occupied_slot():
    """An empty backup entry must NOT overwrite a named device preset
    when overwrite=False."""
    server = _get_server_module()

    # Device has a named preset in slot 0
    device_preset = Preset(name="My Tone")
    device_frame = _make_preset_frame(0, device_preset)

    # Backup has an empty preset in slot 0
    backup_presets = [Preset()]  # empty name
    backup_path = _create_backup(backup_presets)

    mock_conn = MagicMock()
    mock_conn.send_and_receive.return_value = device_frame

    with patch.object(server, "_get_connection", return_value=mock_conn):
        result = server.restore_backup(backup_path, overwrite=False)

    # The occupied slot should have been skipped
    assert result["preset_count"] == 0
    mock_conn.send_chunked_and_receive.assert_not_called()


def test_empty_backup_preset_can_overwrite_when_flag_set():
    """When overwrite=True, even empty backup presets should be written."""
    server = _get_server_module()

    backup_presets = [Preset()]  # empty name
    backup_path = _create_backup(backup_presets)

    mock_conn = MagicMock()

    with patch.object(server, "_get_connection", return_value=mock_conn):
        result = server.restore_backup(backup_path, overwrite=True)

    # mbf format pads to 199 entries; all should be written with overwrite=True
    assert result["preset_count"] == 199
    assert mock_conn.send_chunked_and_receive.call_count == 199


def test_named_backup_skips_occupied_slot():
    """A named backup preset should not overwrite a named device preset
    when overwrite=False."""
    server = _get_server_module()

    device_preset = Preset(name="User Preset")
    device_frame = _make_preset_frame(0, device_preset)

    backup_presets = [Preset(name="Backup Tone")]
    backup_path = _create_backup(backup_presets)

    mock_conn = MagicMock()
    mock_conn.send_and_receive.return_value = device_frame

    with patch.object(server, "_get_connection", return_value=mock_conn):
        result = server.restore_backup(backup_path, overwrite=False)

    assert result["preset_count"] == 0
    mock_conn.send_chunked_and_receive.assert_not_called()


def test_backup_fills_empty_device_slot():
    """A named backup preset should be written to an empty device slot
    when overwrite=False."""
    server = _get_server_module()

    device_preset = Preset()  # empty slot on device
    device_frame = _make_preset_frame(0, device_preset)

    backup_presets = [Preset(name="New Tone")]
    backup_path = _create_backup(backup_presets)

    mock_conn = MagicMock()
    mock_conn.send_and_receive.return_value = device_frame

    with patch.object(server, "_get_connection", return_value=mock_conn):
        result = server.restore_backup(backup_path, overwrite=False)

    # All 199 slots are empty on the device, so all 199 entries get written
    assert result["preset_count"] == 199
    assert mock_conn.send_chunked_and_receive.call_count == 199
