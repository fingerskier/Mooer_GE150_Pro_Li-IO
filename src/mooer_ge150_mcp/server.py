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
    MODULE_CHAIN,
    MODULE_COMMAND_MAP,
    FIRST_PRESET_SLOT,
    LAST_PRESET_SLOT,
    decode_active_state,
    decode_ir_list,
    decode_preset_record,
    build_dump_presets,
    build_hello,
    build_module_block,
    build_read_active_preset,
    build_read_ir_list,
    slot_to_address,
    IR_EMPTY_NAME,
    MAX_MODULE_PARAMS,
    MODULE_NAME_ALIASES,
    ModuleBlock,
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
)
from .transport.usb_connection import USBConnection
from .models.preset import Preset, MODULE_NAMES, PRESET_SIZE
from .models.effects import MODULE_CLASSES
from .models.system import SystemSettings
from .models.file_formats import (
    export_mo,
    import_mo,
    export_mbf,
    import_mbf,
    parse_gnr_header,
    MBF_PRESET_COUNT,
)

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "mooer-ge150",
    instructions="Control a Mooer GE150 Max guitar effects pedal over USB.",
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


# ─── CAPTURE-DERIVED HELPERS ──────────────────────────────────────────

#: Records from the last bulk dump, keyed by 0-199 server slot.
_record_cache: dict[int, Any] = {}


def _fetch_all_records(refresh: bool = True) -> dict[int, Any]:
    """Pull every preset via the bulk dump, keyed by 0-199 server slot.

    The pedal answers DUMP_PRESETS with one record per slot -- there is
    no confirmed way to read a single preset, so reading one means
    reading all of them. Results are cached; pass ``refresh=False`` to
    reuse the previous dump.
    """
    global _record_cache
    if _record_cache and not refresh:
        return _record_cache

    conn = _get_connection()
    frames = conn.send_and_collect(build_dump_presets(), LAST_PRESET_SLOT)

    records: dict[int, Any] = {}
    for frame in frames:
        if frame.command != Command.PRESET_RECORD:
            continue
        try:
            record = decode_preset_record(frame.payload)
        except ValueError:
            logger.warning("Skipping malformed preset record")
            continue
        records[record.slot - FIRST_PRESET_SLOT] = record

    if records:
        _record_cache = records
    return records


def _record_to_dict(record: Any) -> dict[str, Any]:
    """Render a PresetRecord as JSON-friendly output."""
    names = {command: name for name, command in MODULE_COMMAND_MAP.items()}
    return {
        "slot": record.slot - FIRST_PRESET_SLOT,
        "address": slot_to_address(record.slot),
        "name": record.name,
        "modules": {
            names[command]: {
                "enabled": record.modules[command].enabled,
                "effect_type": record.modules[command].effect_type,
                "params": record.modules[command].params,
            }
            for command in MODULE_CHAIN
        },
    }


def _read_active_modules() -> dict[Any, Any] | None:
    """Read the active preset's nine module blocks, or None on failure."""
    conn = _get_connection()
    response = conn.send_and_receive(build_read_active_preset())
    if response is None or response.command != Command.ACTIVE_STATE:
        return None
    return decode_active_state(response.payload).modules


# ─── CONNECTION TOOLS ─────────────────────────────────────────────────

@mcp.tool()
def connect() -> dict[str, Any]:
    """Establish a USB connection to the pedal.

    Auto-discovers the device by USB vendor/product ID and performs the
    handshake MOOER Studio uses: a hello, then a read of the active
    preset. Model and manufacturer come from the USB descriptors.
    """
    global _connection
    if _connection is not None and _connection.connected:
        return {
            "connected": True,
            "message": "Already connected",
            "model": _connection.device_info.product,
        }

    _connection = USBConnection()
    try:
        info = _connection.open()
    except Exception:
        _connection = None
        raise

    result: dict[str, Any] = {
        "connected": True,
        "model": info.product,
        "manufacturer": info.manufacturer,
    }

    # Hello draws no reply; the active-preset read is what confirms the
    # pedal is actually talking to us.
    _connection.write(build_hello())
    active = _connection.send_and_receive(build_read_active_preset())
    if active is not None and active.command == Command.ACTIVE_STATE:
        state = decode_active_state(active.payload)
        result["active_slot"] = state.slot
        result["active_preset"] = slot_to_address(state.slot)
    else:
        result["warning"] = "Connected, but the pedal did not report its state"

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
    """Report what is known about the connected device.

    Model and manufacturer come from the USB descriptors. Firmware
    version is not reported: no identify exchange appears in either USB
    capture, so there is no verified way to ask for it.
    """
    conn = _get_connection()
    info = conn.device_info

    result: dict[str, Any] = {
        "model": info.product,
        "manufacturer": info.manufacturer,
        "vendor_id": f"0x{info.vendor_id:04X}",
        "product_id": f"0x{info.product_id:04X}",
    }

    active = conn.send_and_receive(build_read_active_preset())
    if active is not None and active.command == Command.ACTIVE_STATE:
        state = decode_active_state(active.payload)
        result["active_slot"] = state.slot
        result["active_preset"] = slot_to_address(state.slot)

    return result


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

    records = _fetch_all_records()
    if not records:
        return {"error": "No response from device"}

    presets = []
    for slot in range(start, end + 1):
        record = records.get(slot)
        if record is None:
            presets.append({"slot": slot, "name": "", "empty": True})
            continue
        presets.append({
            "slot": slot,
            "address": slot_to_address(record.slot),
            "name": record.name,
            "empty": not record.name.strip(),
        })

    return {"presets": presets, "received": len(records)}


@mcp.tool()
def get_preset(slot: int) -> dict[str, Any]:
    """Read the full preset data for a specific slot.

    Args:
        slot: Preset index (0-199).
    """
    if not 0 <= slot <= 199:
        return {"error": "Slot must be 0-199"}

    records = _fetch_all_records()
    record = records.get(slot)
    if record is None:
        return {"error": f"Device did not return a record for slot {slot}"}

    return _record_to_dict(record)


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

    # Copy the raw bytes verbatim so unmodeled preset data is preserved
    data = parsed.data[:PRESET_SIZE].ljust(PRESET_SIZE, b"\x00")
    frames = build_store_preset(to_slot, data)
    conn.send_chunked_and_receive(frames)
    # Cache independent objects so partial updates to one slot can't
    # bleed into the other
    _preset_cache[from_slot] = Preset.from_bytes(data)
    _preset_cache[to_slot] = Preset.from_bytes(data)
    name = _preset_cache[to_slot].name
    return {"copied": True, "from": from_slot, "to": to_slot, "name": name}


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

    # Swap the raw bytes verbatim so unmodeled preset data is preserved
    data_a = parsed_a.data[:PRESET_SIZE].ljust(PRESET_SIZE, b"\x00")
    data_b = parsed_b.data[:PRESET_SIZE].ljust(PRESET_SIZE, b"\x00")

    # Write A->B and B->A
    conn.send_chunked_and_receive(build_store_preset(slot_b, data_a))
    conn.send_chunked_and_receive(build_store_preset(slot_a, data_b))
    _preset_cache[slot_a] = Preset.from_bytes(data_b)
    _preset_cache[slot_b] = Preset.from_bytes(data_a)

    return {"swapped": True, "slot_a": slot_a, "slot_b": slot_b}


# ─── EFFECT PARAMETER TOOLS ──────────────────────────────────────────

@mcp.tool()
def set_effect_param(
    module: str,
    param_index: int,
    value: int,
) -> dict[str, Any]:
    """Modify one parameter of a module on the currently active preset.

    The pedal has no single-parameter write: the editor resends the
    module's whole block on every change. This reads the active preset's
    current state, substitutes the one value, and writes the block back.

    Args:
        module: Effect module (fx, ds, amp, cab, ns, eq, mod, delay, reverb).
        param_index: Position of the parameter within the module, 0-9.
        value: Parameter value, 0-65535.
    """
    try:
        command = MODULE_COMMAND_MAP[
            MODULE_NAME_ALIASES.get(module.lower(), module.lower())
        ]
    except KeyError:
        return {
            "error": f"Unknown module '{module}'. Valid: {list(MODULE_COMMAND_MAP)}"
        }

    if not 0 <= param_index < MAX_MODULE_PARAMS:
        return {
            "error": f"param_index must be 0-{MAX_MODULE_PARAMS - 1}, "
                     f"got {param_index}"
        }
    if not 0 <= value <= 0xFFFF:
        return {"error": f"Value must be 0-65535, got {value}"}

    modules = _read_active_modules()
    if modules is None:
        return {"error": "Could not read the active preset from the device"}

    block = modules[command]
    params = list(block.params)
    params += [0] * (MAX_MODULE_PARAMS - len(params))
    params[param_index] = value
    updated = ModuleBlock(
        enabled=block.enabled, effect_type=block.effect_type, params=params
    )

    _get_connection().write(build_module_block(module, updated))
    return {
        "module": module,
        "param_index": param_index,
        "value": value,
        "effect_type": updated.effect_type,
        "enabled": updated.enabled,
    }


@mcp.tool()
def toggle_effect(module: str, enabled: bool) -> dict[str, Any]:
    """Turn an effect module on or off on the currently active preset.

    Changes the module's ON/OFF status while preserving its effect type
    and parameters, by reading the active preset and rewriting the block.

    Args:
        module: Effect module (fx, ds, amp, cab, ns, eq, mod, delay, reverb).
        enabled: True to turn the module on, False to turn it off.
    """
    try:
        command = MODULE_COMMAND_MAP[
            MODULE_NAME_ALIASES.get(module.lower(), module.lower())
        ]
    except KeyError:
        return {
            "error": f"Unknown module '{module}'. Valid: {list(MODULE_COMMAND_MAP)}"
        }

    modules = _read_active_modules()
    if modules is None:
        return {"error": "Could not read the active preset from the device"}

    block = modules[command]
    updated = ModuleBlock(
        enabled=enabled, effect_type=block.effect_type, params=list(block.params)
    )
    _get_connection().write(build_module_block(module, updated))

    return {"module": module, "enabled": enabled, "effect_type": updated.effect_type}


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
    module_index_map = {name: i for i, name in enumerate(MODULE_NAMES)}
    order_bytes = bytes([module_index_map.get(m, 0) for m in order])
    # Pad to 10 bytes
    order_bytes = order_bytes.ljust(10, b"\x00")

    conn = _get_connection()
    frame = build_command(Command.SETTING_A5, order_bytes)
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
    failed_slots: list[int] = []

    for slot in range(MBF_PRESET_COUNT):
        response = conn.send_and_receive(build_read_preset(slot))
        parsed = parse_preset_response(response) if response else None
        if parsed:
            presets.append(Preset.from_bytes(parsed.data))
        else:
            failed_slots.append(slot)
            presets.append(Preset())

    path = export_mbf(presets, output_path)
    result: dict[str, Any] = {"path": str(path), "preset_count": len(presets)}
    if failed_slots:
        result["failed_slots"] = failed_slots
        result["warning"] = (
            f"{len(failed_slots)} slot(s) could not be read and were "
            "written as empty presets"
        )
    return result


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
        if not overwrite:
            # Read existing to check if slot is occupied
            response = conn.send_and_receive(build_read_preset(slot))
            if response:
                parsed = parse_preset_response(response)
                if parsed:
                    existing = Preset.from_bytes(parsed.data)
                    if existing.name.strip():
                        continue

        frames = build_store_preset(slot, preset.to_bytes())
        conn.send_chunked_and_receive(frames)
        _preset_cache[slot] = preset
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
    _preset_cache[slot] = preset

    return {"imported": True, "slot": slot, "name": preset.name}


# ─── IR / CABINET TOOLS ──────────────────────────────────────────────

@mcp.tool()
def list_ir_slots() -> dict[str, Any]:
    """List the user IR slots and their contents."""
    conn = _get_connection()
    response = conn.send_and_receive(build_read_ir_list())
    if response is None:
        return {"error": "No response from device"}
    if response.command != Command.IR_LIST:
        return {"error": f"Unexpected reply 0x{response.command:02X}"}

    names = decode_ir_list(response.payload)
    slots = [
        {"slot": i, "name": name, "empty": name == IR_EMPTY_NAME or not name}
        for i, name in enumerate(names)
    ]
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
