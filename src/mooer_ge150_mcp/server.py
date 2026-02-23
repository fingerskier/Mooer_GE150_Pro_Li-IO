"""MCP server entry point for the Mooer GE150 Pro Li.

Exposes tools, resources, and prompts via the Model Context Protocol
using the official Python MCP SDK with stdio transport.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .protocol.commands import (
    Command,
    build_identify,
    build_select_preset,
    build_read_preset,
    build_store_preset,
    build_effect_param,
    build_toggle_effect,
    build_set_volume,
    build_get_volume,
    build_get_system_settings,
    build_set_system_setting,
    build_command,
)
from .protocol.parser import (
    parse_identify,
    parse_preset_response,
    parse_active_patch,
    parse_volume,
    parse_system,
    parse_response,
)
from .protocol.framing import parse_frame
from .transport.usb_connection import USBConnection
from .models.preset import Preset
from .models.effects import MODULE_CLASSES
from .models.system import SystemSettings
from .models.file_formats import (
    export_mo,
    import_mo,
    export_mbf,
    import_mbf,
    parse_gnr_header,
)

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "mooer-ge150",
    description="MCP server for Mooer GE150 Pro Li guitar effects pedal",
)

# Global connection state
_connection: USBConnection | None = None
_preset_cache: dict[int, Preset] = {}


def _get_connection() -> USBConnection:
    """Get the active USB connection, raising if not connected."""
    if _connection is None or not _connection.connected:
        raise RuntimeError(
            "Not connected to device. Use the 'connect' tool first."
        )
    return _connection


# ─── AMP / EFFECT CATALOGS ────────────────────────────────────────────

AMP_MODELS = [
    "Deluxe Vib", "Deluxe Tweed", "Brit 800", "Brit 2000",
    "US Hi-Gain", "SLO 100", "Fireman", "Dual Rect",
    "Die VH4", "PV 5150", "BE 100", "Recto Verb",
    "Jazz 120", "AC 15", "AC 30", "Match DC30",
    "Tiny Terror", "Blues Jr", "Plexi 50W", "JTM 45",
    "Super Reverb", "Twin Reverb", "Bassman", "Champ",
    "Princeton", "Hiwatt DR103", "Fender 57", "Orange AD30",
    "Marshall JVM", "Mesa MarkV", "Bogner Ecstasy", "ENGL Savage",
    "Diezel Herbert", "Friedman BE", "Soldano SLO", "EVH 5150III",
    "Peavey 6505", "Randall RG", "Laney IRT", "Blackstar HT",
    "Hughes & Kettner", "Koch", "Egnater", "Rivera",
    "Dr. Z", "BadCat", "Budda", "Vox Night Train",
    "Fender Mustang", "Acoustic", "Clean DI", "Crunch DI",
    "Hi-Gain DI", "Lead DI", "Bass",
]

CAB_MODELS = [
    "1x8 Champ", "1x10 Princeton", "1x12 Deluxe", "1x12 AC15",
    "2x10 Twin", "2x12 AC30", "2x12 Jazz", "2x12 Blue",
    "2x12 Match", "2x12 Recto", "4x10 Bassman", "4x12 1960A",
    "4x12 1960B", "4x12 Recto", "4x12 5150", "4x12 SLO",
    "4x12 Uber", "4x12 V30", "4x12 Green", "4x12 Orange",
    "IR Slot 1", "IR Slot 2", "IR Slot 3", "IR Slot 4",
    "IR Slot 5", "IR Slot 6",
]

EFFECT_CATALOG = {
    "fx": ["Comp", "Red Comp", "T-Comp", "Limiter", "Graphic EQ", "Wah", "Auto Wah",
           "Touch Wah", "Vol Pedal", "Tremolo", "Uni-Vibe", "Octave", "Pitch"],
    "od": ["Blues OD", "TS808", "TS9", "SD-1", "OCD", "Klon", "Rat",
           "Metal Zone", "DS-1", "Fuzz Face", "Big Muff", "Tube Screamer"],
    "mod": ["Chorus", "Flanger", "Phaser", "Vibrato", "Rotary", "Tremolo",
            "Ring Mod", "Uni-Vibe", "Auto Wah", "Envelope", "Pitch Shift",
            "Detune", "Harmonizer"],
    "delay": ["Digital", "Analog", "Tape", "Mod Delay", "Reverse", "Ping Pong",
              "Sweep", "Filter", "Crystal"],
    "reverb": ["Room", "Hall", "Plate", "Spring", "Mod Reverb", "Shimmer",
               "Ambient", "Church", "Arena"],
}


# ─── CONNECTION TOOLS ─────────────────────────────────────────────────

@mcp.tool()
def connect() -> dict[str, Any]:
    """Establish a USB connection to the Mooer GE150 Pro Li pedal.

    Auto-discovers the device by USB vendor/product ID (0x0483:0x5703).
    Sends an Identify command to confirm the device and retrieve
    model and firmware information.
    """
    global _connection
    if _connection is not None and _connection.connected:
        return {
            "connected": True,
            "message": "Already connected",
            "model": _connection.device_info.product,
        }

    _connection = USBConnection()
    info = _connection.open()

    # Send Identify command
    identify_frame = build_identify()
    response = _connection.send_and_receive(identify_frame)

    result: dict[str, Any] = {
        "connected": True,
        "model": info.product,
        "manufacturer": info.manufacturer,
    }

    if response:
        ident = parse_identify(response)
        if ident:
            result["firmware"] = ident.firmware
            result["device_name"] = ident.device_name

    return result


@mcp.tool()
def disconnect() -> dict[str, bool]:
    """Close the USB connection to the pedal."""
    global _connection
    if _connection is None:
        return {"disconnected": True}
    _connection.close()
    _connection = None
    return {"disconnected": True}


@mcp.tool()
def get_device_info() -> dict[str, Any]:
    """Retrieve device identification (model, firmware version).

    Sends the Identify command (0x10) and parses the response.
    """
    conn = _get_connection()
    response = conn.send_and_receive(build_identify())
    if response is None:
        return {"error": "No response from device"}

    ident = parse_identify(response)
    if ident is None:
        return {"error": "Failed to parse identify response"}

    return {
        "model": ident.device_name,
        "firmware": ident.firmware,
        "manufacturer": conn.device_info.manufacturer,
    }


# ─── PRESET MANAGEMENT TOOLS ─────────────────────────────────────────

@mcp.tool()
def list_presets(start: int = 0, end: int = 199) -> dict[str, Any]:
    """List preset slots with names.

    Args:
        start: First slot index (0-199, default 0).
        end: Last slot index (0-199, default 199).
    """
    if not 0 <= start <= 199 or not 0 <= end <= 199:
        return {"error": "Slot range must be 0-199"}
    if start > end:
        start, end = end, start

    conn = _get_connection()
    presets = []
    for slot in range(start, end + 1):
        response = conn.send_and_receive(build_read_preset(slot))
        if response:
            parsed = parse_preset_response(response)
            if parsed:
                preset = Preset.from_bytes(parsed.data)
                _preset_cache[slot] = preset
                presets.append({
                    "slot": slot,
                    "name": preset.name,
                    "empty": not preset.name.strip(),
                })
            else:
                presets.append({"slot": slot, "name": "", "empty": True})
        else:
            presets.append({"slot": slot, "name": "", "empty": True})

    return {"presets": presets}


@mcp.tool()
def get_preset(slot: int) -> dict[str, Any]:
    """Read the full preset data for a specific slot.

    Args:
        slot: Preset index (0-199).
    """
    if not 0 <= slot <= 199:
        return {"error": "Slot must be 0-199"}

    conn = _get_connection()
    response = conn.send_and_receive(build_read_preset(slot))
    if response is None:
        return {"error": "No response from device"}

    parsed = parse_preset_response(response)
    if parsed is None:
        return {"error": "Failed to parse preset response"}

    preset = Preset.from_bytes(parsed.data)
    _preset_cache[slot] = preset

    result = preset.to_dict()
    result["slot"] = slot
    return result


@mcp.tool()
def set_preset(
    slot: int,
    name: str | None = None,
    effects: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Write a complete preset to a slot.

    If a preset already exists in the slot, it is read first and the
    provided fields are merged over it.

    Args:
        slot: Target slot (0-199).
        name: Optional new preset name (max 14 chars).
        effects: Optional dict of module overrides, e.g.
                 {"amp": {"type": 5, "amp_gain": 128}}.
    """
    if not 0 <= slot <= 199:
        return {"error": "Slot must be 0-199"}

    conn = _get_connection()

    # Start from cached or freshly-read preset
    if slot in _preset_cache:
        preset = _preset_cache[slot]
    else:
        response = conn.send_and_receive(build_read_preset(slot))
        if response:
            parsed = parse_preset_response(response)
            preset = Preset.from_bytes(parsed.data) if parsed else Preset()
        else:
            preset = Preset()

    if name is not None:
        preset.name = name[:14]

    if effects:
        for module_name, params in effects.items():
            module = preset.get_module(module_name)
            for param, value in params.items():
                if hasattr(module, param):
                    setattr(module, param, value)

    frames = build_store_preset(slot, preset.to_bytes())
    response = conn.send_chunked_and_receive(frames)
    _preset_cache[slot] = preset

    return {"stored": True, "slot": slot, "name": preset.name}


@mcp.tool()
def select_preset(slot: int) -> dict[str, Any]:
    """Switch the pedal's active preset.

    Args:
        slot: Preset index (0-199).
    """
    if not 0 <= slot <= 199:
        return {"error": "Slot must be 0-199"}

    conn = _get_connection()
    response = conn.send_and_receive(build_select_preset(slot))
    if response:
        parsed = parse_active_patch(response)
        if parsed:
            return {"active": parsed.slot}
    return {"active": slot}


@mcp.tool()
def copy_preset(from_slot: int, to_slot: int) -> dict[str, Any]:
    """Copy a preset from one slot to another.

    Args:
        from_slot: Source slot (0-199).
        to_slot: Destination slot (0-199).
    """
    if not 0 <= from_slot <= 199 or not 0 <= to_slot <= 199:
        return {"error": "Slots must be 0-199"}

    conn = _get_connection()
    response = conn.send_and_receive(build_read_preset(from_slot))
    if response is None:
        return {"error": "Failed to read source preset"}

    parsed = parse_preset_response(response)
    if parsed is None:
        return {"error": "Failed to parse source preset"}

    frames = build_store_preset(to_slot, parsed.data[:0x200].ljust(0x200, b"\x00"))
    conn.send_chunked_and_receive(frames)
    return {"copied": True, "from": from_slot, "to": to_slot}


@mcp.tool()
def swap_presets(slot_a: int, slot_b: int) -> dict[str, Any]:
    """Swap two preset slots.

    Args:
        slot_a: First slot (0-199).
        slot_b: Second slot (0-199).
    """
    if not 0 <= slot_a <= 199 or not 0 <= slot_b <= 199:
        return {"error": "Slots must be 0-199"}

    conn = _get_connection()

    # Read both
    resp_a = conn.send_and_receive(build_read_preset(slot_a))
    resp_b = conn.send_and_receive(build_read_preset(slot_b))
    if resp_a is None or resp_b is None:
        return {"error": "Failed to read one or both presets"}

    parsed_a = parse_preset_response(resp_a)
    parsed_b = parse_preset_response(resp_b)
    if parsed_a is None or parsed_b is None:
        return {"error": "Failed to parse preset data"}

    data_a = parsed_a.data[:0x200].ljust(0x200, b"\x00")
    data_b = parsed_b.data[:0x200].ljust(0x200, b"\x00")

    # Write A->B and B->A
    conn.send_chunked_and_receive(build_store_preset(slot_b, data_a))
    conn.send_chunked_and_receive(build_store_preset(slot_a, data_b))

    return {"swapped": True, "slot_a": slot_a, "slot_b": slot_b}


# ─── EFFECT PARAMETER TOOLS ──────────────────────────────────────────

@mcp.tool()
def set_effect_param(
    module: str,
    param: str,
    value: int,
) -> dict[str, Any]:
    """Modify a single parameter on the currently active preset in real time.

    Args:
        module: Effect module (fx, od, amp, cab, ns, eq, mod, delay, reverb).
        param: Parameter name (e.g. 'gain', 'level', 'type').
        value: Parameter value (0-255, or 0-65535 for delay time).
    """
    if module not in MODULE_CLASSES:
        return {"error": f"Unknown module '{module}'. Valid: {list(MODULE_CLASSES)}"}

    # Map param name to byte index within the module
    cls = MODULE_CLASSES[module]
    dummy = cls()
    fields = [f.name for f in dummy.__dataclass_fields__.values()
              if f.name not in ("reserved", "SIZE")]
    if param not in fields:
        return {"error": f"Unknown param '{param}' for {module}. Valid: {fields}"}

    param_index = fields.index(param)

    conn = _get_connection()
    frame = build_effect_param(module, param_index, value & 0xFF)
    conn.write(frame)

    return {"module": module, "param": param, "value": value}


@mcp.tool()
def toggle_effect(module: str, enabled: bool) -> dict[str, Any]:
    """Enable or disable an effect module.

    Args:
        module: Effect module name.
        enabled: True to enable, False to disable.
    """
    if module not in MODULE_CLASSES:
        return {"error": f"Unknown module '{module}'. Valid: {list(MODULE_CLASSES)}"}

    conn = _get_connection()
    frame = build_toggle_effect(module, enabled)
    conn.write(frame)

    return {"module": module, "enabled": enabled}


@mcp.tool()
def set_effect_order(order: list[str]) -> dict[str, Any]:
    """Change the signal chain order.

    Args:
        order: List of module names in desired order,
               e.g. ["fx", "od", "amp", "cab", "ns", "eq", "mod", "delay", "reverb"].
    """
    valid = set(MODULE_CLASSES.keys())
    for m in order:
        if m not in valid:
            return {"error": f"Unknown module '{m}' in order. Valid: {sorted(valid)}"}

    # Build order byte array (maps position -> module index)
    module_index_map = {name: i for i, name in enumerate(
        ["fx", "od", "amp", "cab", "ns", "eq", "mod", "delay", "reverb"]
    )}
    order_bytes = bytes([module_index_map.get(m, 0) for m in order])
    # Pad to 10 bytes
    order_bytes = order_bytes.ljust(10, b"\x00")

    conn = _get_connection()
    frame = build_command(Command.PATCH_SETTING, order_bytes)
    conn.write(frame)

    return {"order": order}


# ─── SYSTEM SETTINGS TOOLS ───────────────────────────────────────────

@mcp.tool()
def get_system_settings() -> dict[str, Any]:
    """Read global system settings (global EQ, display brightness, auto-off, etc.)."""
    conn = _get_connection()
    response = conn.send_and_receive(build_get_system_settings())
    if response is None:
        return {"error": "No response from device"}

    parsed = parse_system(response)
    if parsed is None:
        return {"error": "Failed to parse system response"}

    settings = SystemSettings.from_bytes(parsed.data)
    return {"settings": settings.to_dict()}


@mcp.tool()
def set_system_setting(setting: str, value: int) -> dict[str, Any]:
    """Modify a global system setting.

    Args:
        setting: Setting name/index.
        value: Setting value.
    """
    # For now, setting is treated as a numeric index
    try:
        setting_index = int(setting)
    except ValueError:
        return {"error": f"Setting must be a numeric index, got '{setting}'"}

    conn = _get_connection()
    frame = build_set_system_setting(setting_index, value)
    conn.write(frame)

    return {"setting": setting, "value": value}


@mcp.tool()
def get_volume() -> dict[str, Any]:
    """Read master volume level."""
    conn = _get_connection()
    response = conn.send_and_receive(build_get_volume())
    if response is None:
        return {"error": "No response from device"}

    parsed = parse_volume(response)
    if parsed is None:
        return {"error": "Failed to parse volume response"}

    return {"volume": parsed.volume}


@mcp.tool()
def set_volume(volume: int) -> dict[str, Any]:
    """Set master volume level.

    Args:
        volume: Volume level (0-100).
    """
    if not 0 <= volume <= 100:
        return {"error": "Volume must be 0-100"}

    conn = _get_connection()
    frame = build_set_volume(volume)
    conn.write(frame)

    return {"volume": volume}


# ─── BACKUP & RESTORE TOOLS ──────────────────────────────────────────

@mcp.tool()
def backup_all(output_path: str) -> dict[str, Any]:
    """Download all presets as a .mbf backup file.

    Args:
        output_path: File path for the backup.
    """
    conn = _get_connection()
    presets: list[Preset] = []

    for slot in range(199):
        response = conn.send_and_receive(build_read_preset(slot))
        if response:
            parsed = parse_preset_response(response)
            if parsed:
                preset = Preset.from_bytes(parsed.data)
                presets.append(preset)
            else:
                presets.append(Preset())
        else:
            presets.append(Preset())

    path = export_mbf(presets, output_path)
    return {"path": str(path), "preset_count": len(presets)}


@mcp.tool()
def restore_backup(input_path: str, overwrite: bool = False) -> dict[str, Any]:
    """Restore presets from a .mbf backup file.

    Args:
        input_path: Path to the .mbf backup file.
        overwrite: If True, overwrite existing presets.
    """
    if not Path(input_path).exists():
        return {"error": f"File not found: {input_path}"}

    conn = _get_connection()
    presets = import_mbf(input_path)

    restored = 0
    for slot, preset in enumerate(presets):
        if not overwrite and preset.name.strip():
            # Read existing to check if non-empty
            response = conn.send_and_receive(build_read_preset(slot))
            if response:
                parsed = parse_preset_response(response)
                if parsed:
                    existing = Preset.from_bytes(parsed.data)
                    if existing.name.strip():
                        continue

        frames = build_store_preset(slot, preset.to_bytes())
        conn.send_chunked_and_receive(frames)
        restored += 1

    return {"restored": True, "preset_count": restored}


@mcp.tool()
def export_preset(slot: int, output_path: str) -> dict[str, Any]:
    """Export a single preset to a .mo file.

    Args:
        slot: Preset slot (0-199).
        output_path: Output .mo file path.
    """
    if not 0 <= slot <= 199:
        return {"error": "Slot must be 0-199"}

    conn = _get_connection()
    response = conn.send_and_receive(build_read_preset(slot))
    if response is None:
        return {"error": "No response from device"}

    parsed = parse_preset_response(response)
    if parsed is None:
        return {"error": "Failed to parse preset response"}

    preset = Preset.from_bytes(parsed.data)
    path = export_mo(preset, output_path)
    return {"path": str(path), "name": preset.name}


@mcp.tool()
def import_preset(input_path: str, slot: int) -> dict[str, Any]:
    """Import a preset from a .mo file into a slot.

    Args:
        input_path: Path to the .mo file.
        slot: Target slot (0-199).
    """
    if not 0 <= slot <= 199:
        return {"error": "Slot must be 0-199"}
    if not Path(input_path).exists():
        return {"error": f"File not found: {input_path}"}

    preset = import_mo(input_path)
    conn = _get_connection()
    frames = build_store_preset(slot, preset.to_bytes())
    conn.send_chunked_and_receive(frames)

    return {"imported": True, "slot": slot, "name": preset.name}


# ─── IR / CABINET TOOLS ──────────────────────────────────────────────

@mcp.tool()
def list_ir_slots() -> dict[str, Any]:
    """List the user IR slots and their contents."""
    conn = _get_connection()
    response = conn.send_and_receive(
        build_command(Command.CAB_MODELS)
    )
    if response is None:
        return {"error": "No response from device"}

    # Parse the cab models list — IR slots are typically slots 20+
    slots = []
    for i in range(10):
        slots.append({
            "slot": i,
            "name": f"IR Slot {i + 1}",
            "empty": True,  # Will be populated from device response
        })

    return {"slots": slots}


@mcp.tool()
def upload_ir(slot: int, file_path: str, name: str | None = None) -> dict[str, Any]:
    """Upload a WAV or GNR impulse response to an IR slot.

    Args:
        slot: IR slot index (0-9).
        file_path: Path to WAV or GNR file.
        name: Optional name for the IR.
    """
    if not 0 <= slot <= 9:
        return {"error": "IR slot must be 0-9"}

    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    data = path.read_bytes()

    conn = _get_connection()

    # For .gnr files, parse and send directly
    if path.suffix.lower() == ".gnr":
        header = parse_gnr_header(data)
        ir_data = data[header["data_offset"]:]
    else:
        # For WAV files, send raw data — the device handles conversion
        ir_data = data

    # Send cabinet upload command with slot and data
    from .protocol.framing import build_chunked_frames
    payload = bytes([slot]) + ir_data
    frames = build_chunked_frames(Command.CABINET_UPLOAD.value, payload)
    conn.send_chunked_and_receive(frames)

    return {
        "uploaded": True,
        "slot": slot,
        "name": name or path.stem,
    }


# ─── MCP RESOURCES ───────────────────────────────────────────────────

@mcp.resource("mooer://device/info")
def resource_device_info() -> str:
    """Device model, firmware, connection state."""
    if _connection is None or not _connection.connected:
        return json.dumps({"connected": False})

    info = _connection.device_info
    return json.dumps({
        "connected": True,
        "manufacturer": info.manufacturer,
        "product": info.product,
        "vendor_id": f"0x{info.vendor_id:04X}",
        "product_id": f"0x{info.product_id:04X}",
    })


@mcp.resource("mooer://device/status")
def resource_device_status() -> str:
    """Connection state and active preset."""
    connected = _connection is not None and _connection.connected
    return json.dumps({"connected": connected})


@mcp.resource("mooer://presets/list")
def resource_presets_list() -> str:
    """Summary list of cached preset names."""
    presets = []
    for slot in sorted(_preset_cache.keys()):
        p = _preset_cache[slot]
        presets.append({"slot": slot, "name": p.name})
    return json.dumps({"presets": presets})


@mcp.resource("mooer://catalog/amps")
def resource_amp_catalog() -> str:
    """List of all amp model names with IDs."""
    amps = [{"id": i, "name": name} for i, name in enumerate(AMP_MODELS)]
    return json.dumps({"amps": amps, "count": len(amps)})


@mcp.resource("mooer://catalog/cabs")
def resource_cab_catalog() -> str:
    """List of all cabinet simulation names with IDs."""
    cabs = [{"id": i, "name": name} for i, name in enumerate(CAB_MODELS)]
    return json.dumps({"cabs": cabs, "count": len(cabs)})


@mcp.resource("mooer://catalog/effects")
def resource_effects_catalog() -> str:
    """List of all effects organized by category."""
    catalog = {}
    for category, effects in EFFECT_CATALOG.items():
        catalog[category] = [
            {"id": i, "name": name} for i, name in enumerate(effects)
        ]
    return json.dumps({"effects": catalog})


@mcp.resource("mooer://catalog/ir-slots")
def resource_ir_slots() -> str:
    """User IR slot status."""
    slots = [{"slot": i, "name": f"IR Slot {i + 1}"} for i in range(10)]
    return json.dumps({"slots": slots})


@mcp.resource("mooer://system/settings")
def resource_system_settings() -> str:
    """Global system settings (cached)."""
    return json.dumps({"settings": {}})


@mcp.resource("mooer://system/footswitch")
def resource_footswitch() -> str:
    """Footswitch assignments."""
    return json.dumps({"footswitch": {}})


@mcp.resource("mooer://system/pedal-assign")
def resource_pedal_assign() -> str:
    """Expression pedal assignments."""
    return json.dumps({"pedal_assign": {}})


# ─── MCP PROMPTS ─────────────────────────────────────────────────────

@mcp.prompt()
def create_tone(style: str) -> str:
    """Guide the AI to build a preset for a specific musical style or reference tone.

    Args:
        style: Genre, artist, or song name.
    """
    return f"""Create a preset for {style} style.
Consider:
- Amp model selection for the right gain structure
- Appropriate drive/overdrive settings
- EQ shaping for the style
- Modulation, delay, and reverb to taste
- Noise gate threshold based on gain level

Available amp models: {', '.join(AMP_MODELS[:20])}...
Available effects: Use the catalog resources for full listings.

Use the set_preset tool to save the result to a slot."""


@mcp.prompt()
def optimize_preset(slot: int, goal: str) -> str:
    """Analyze an existing preset and suggest improvements.

    Args:
        slot: Preset slot to analyze.
        goal: Optimization goal (e.g., "less noise", "more clarity").
    """
    return f"""Read preset {slot} using the get_preset tool and analyze its settings.
Suggest improvements for: {goal}

Consider:
- Current amp settings and whether they suit the goal
- Noise gate threshold relative to gain level
- EQ balance and frequency shaping
- Effect levels and interactions
- Signal chain order optimization

Use set_effect_param to make real-time adjustments, then set_preset to save."""


@mcp.prompt()
def batch_organize() -> str:
    """Help organize and rename presets across the 200 slots."""
    return """Read all presets using list_presets. Group them by style/genre.
Suggest a logical ordering and naming convention.
Consider:
- Clean tones in slots 0-49
- Crunch/overdrive in slots 50-99
- High gain in slots 100-149
- Effects-heavy / ambient in slots 150-199

Use copy_preset and swap_presets to reorganize.
Use set_preset to rename presets."""


# ─── ENTRY POINT ─────────────────────────────────────────────────────

def main():
    """Run the MCP server with stdio transport."""
    logging.basicConfig(level=logging.INFO)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
