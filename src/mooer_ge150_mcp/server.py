"""MCP server entry point for the Mooer GE150 Pro Li.

Exposes tools, resources, and prompts via the Model Context Protocol
using the official Python MCP SDK with stdio transport.
"""

from __future__ import annotations

import json
import logging
import time
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
    build_save_preset,
    build_select_preset_slot,
    build_set_exp_assign,
    build_write_preset,
    build_write_preset_record,
    build_read_ctrl_config,
    build_write_ctrl_config,
    build_set_input_level,
    build_set_otg_level,
    build_set_brightness,
    build_set_cab_sim_thru,
    build_set_spillover,
    build_set_global_eq,
    decode_global_eq,
    GlobalEQ,
    build_restore_begin,
    build_restore_end,
    decode_ctrl_config,
    db_to_level,
    level_to_db,
    CTRL_FLAG_COUNT,
    PresetRecord,
    slot_to_address,
    IR_EMPTY_NAME,
    AMP_BLOB_SIZE,
    CAB_BLOB_SIZE,
    build_upload_amp,
    build_upload_cab,
    split_user_model_list,
    MAX_MODULE_PARAMS,
    MODULE_NAME_ALIASES,
    ModuleBlock,
    encode_preset_record,
    PRESET_RECORD_SIZE,
    build_command,
)
from .transport.usb_connection import USBConnection

logger = logging.getLogger(__name__)

#: The editor paces its preset writes roughly this far apart.
WRITE_PACING_SECONDS = 0.02

#: File-format tags for backups and single-preset exports.
BACKUP_FORMAT = "mooer-ge150-backup"
PRESET_FORMAT = "mooer-ge150-preset"

mcp = FastMCP(
    "mooer-ge150",
    instructions="Control a Mooer GE150 Max guitar effects pedal over USB.",
)

# Global connection state
_connection: USBConnection | None = None


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
    frames = conn.send_and_collect(
        build_dump_presets(), LAST_PRESET_SLOT,
        command=Command.PRESET_RECORD,
    )

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
    response = conn.send_and_expect(
        build_read_active_preset(), Command.ACTIVE_STATE
    )
    if response is None:
        return None
    return decode_active_state(response.payload).modules



def _upload_records(conn, records: list) -> int:
    """Upload preset records via WRITE_PRESET inside a restore bracket.

    0xC3 has only ever been observed between RESTORE_BEGIN and
    RESTORE_END, so every direct write uses the same bracket. Returns the
    number of records the pedal acknowledged.
    """
    acked = 0
    conn.write(build_restore_begin())
    try:
        for record in records:
            for report in build_write_preset_record(record):
                conn.write(report)
            for _ in range(8):
                ack = conn.read_message()
                if ack is None:
                    break
                if ack.command == Command.WRITE_PRESET_ACK:
                    acked += 1
                    break
    finally:
        conn.write(build_restore_end())
    _record_cache.clear()
    return acked


def _merge_module_states(
    record, modules: dict[str, dict[str, Any]]
) -> str | None:
    """Apply per-module overrides onto a PresetRecord.

    Returns an error message, or None on success.
    """
    for module_name, state in (modules or {}).items():
        key = MODULE_NAME_ALIASES.get(module_name.lower(), module_name.lower())
        if key not in MODULE_COMMAND_MAP:
            return (
                f"Unknown module '{module_name}'. "
                f"Valid: {list(MODULE_COMMAND_MAP)}"
            )
        unknown = set(state) - {"enabled", "effect_type", "params"}
        if unknown:
            return (
                f"Unknown fields {sorted(unknown)} for module "
                f"'{module_name}'. Modules take enabled / effect_type / "
                f"params (see get_preset output)."
            )
        command = MODULE_COMMAND_MAP[key]
        block = record.modules.get(
            command, ModuleBlock(enabled=False, effect_type=0)
        )
        try:
            record.modules[command] = ModuleBlock(
                enabled=bool(state.get("enabled", block.enabled)),
                effect_type=int(state.get("effect_type", block.effect_type)),
                params=[int(v) for v in state.get("params", block.params)],
            )
        except (TypeError, ValueError) as exc:
            return f"Bad state for module '{module_name}': {exc}"
    return None


def _record_to_file_entry(record) -> dict[str, Any]:
    """A JSON-safe preset entry carrying the byte-exact record."""
    return {
        "slot": record.slot - FIRST_PRESET_SLOT,
        "address": slot_to_address(record.slot),
        "name": record.name,
        "record": encode_preset_record(record).hex(),
    }


def _record_from_file_entry(entry: dict[str, Any], slot: int):
    """Rebuild a PresetRecord from a file entry, re-slotted to *slot*
    (0-199). Raises ValueError on malformed input."""
    raw = bytearray(bytes.fromhex(str(entry["record"])))
    if len(raw) != PRESET_RECORD_SIZE:
        raise ValueError(
            f"Preset record must be {PRESET_RECORD_SIZE} bytes, "
            f"got {len(raw)}"
        )
    raw[0] = slot + FIRST_PRESET_SLOT
    return decode_preset_record(bytes(raw))


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
    active = _connection.send_and_expect(
        build_read_active_preset(), Command.ACTIVE_STATE
    )
    if active is not None:
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

    active = conn.send_and_expect(
        build_read_active_preset(), Command.ACTIVE_STATE
    )
    if active is not None:
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
    """Update a preset in place: merge changes over what the slot holds.

    Reads the slot's current record (via the bulk dump), applies the
    given name and module overrides, and writes the merged record back
    with the confirmed direct-write command (0xC3).

    Args:
        slot: Target slot (0-199).
        name: Optional new preset name (max 16 chars).
        effects: Optional per-module overrides using the same shape
            get_preset returns, e.g.
            ``{"amp": {"enabled": true, "effect_type": 5, "params": [90]}}``.
    """
    if not 0 <= slot <= 199:
        return {"error": "Slot must be 0-199"}

    conn = _get_connection()
    records = _fetch_all_records(refresh=False)
    record = records.get(slot)
    if record is None:
        record = PresetRecord(slot=slot + FIRST_PRESET_SLOT)
        for command in MODULE_CHAIN:
            record.modules[command] = ModuleBlock(enabled=False, effect_type=0)

    if name is not None:
        record = record.with_name(name)

    error = _merge_module_states(record, effects or {})
    if error:
        return {"error": error}

    acked = _upload_records(conn, [record])
    return {
        "stored": acked == 1,
        "slot": slot,
        "address": slot_to_address(slot + FIRST_PRESET_SLOT),
        "name": record.name,
    }


@mcp.tool()
def select_preset(slot: int) -> dict[str, Any]:
    """Switch the pedal's active preset.

    Args:
        slot: Preset index (0-199).
    """
    if not 0 <= slot <= 199:
        return {"error": "Slot must be 0-199"}

    # The wire slot is 1-based; the previous implementation sent the
    # 0-based index and selected the preset one below the one asked for.
    _get_connection().write(build_select_preset_slot(slot + FIRST_PRESET_SLOT))
    return {"active": slot, "address": slot_to_address(slot + FIRST_PRESET_SLOT)}


@mcp.tool()
def copy_preset(from_slot: int, to_slot: int) -> dict[str, Any]:
    """Copy a preset from one slot to another.

    Byte-exact: the raw record (name padding, tail and all) is re-slotted
    and uploaded, so unmodeled data survives the copy.

    Args:
        from_slot: Source slot (0-199).
        to_slot: Destination slot (0-199).
    """
    if not 0 <= from_slot <= 199 or not 0 <= to_slot <= 199:
        return {"error": "Slots must be 0-199"}

    conn = _get_connection()
    records = _fetch_all_records()
    source = records.get(from_slot)
    if source is None:
        return {"error": f"Device did not return a record for slot {from_slot}"}

    raw = bytearray(encode_preset_record(source))
    raw[0] = to_slot + FIRST_PRESET_SLOT
    duplicate = decode_preset_record(bytes(raw))

    acked = _upload_records(conn, [duplicate])
    return {
        "copied": acked == 1,
        "from": from_slot,
        "to": to_slot,
        "name": duplicate.name,
    }


@mcp.tool()
def swap_presets(slot_a: int, slot_b: int) -> dict[str, Any]:
    """Swap two preset slots, byte-exactly.

    Args:
        slot_a: First slot (0-199).
        slot_b: Second slot (0-199).
    """
    if not 0 <= slot_a <= 199 or not 0 <= slot_b <= 199:
        return {"error": "Slots must be 0-199"}

    conn = _get_connection()
    records = _fetch_all_records()
    rec_a, rec_b = records.get(slot_a), records.get(slot_b)
    if rec_a is None or rec_b is None:
        return {"error": "Device did not return both preset records"}

    raw_a = bytearray(encode_preset_record(rec_a))
    raw_b = bytearray(encode_preset_record(rec_b))
    raw_a[0] = slot_b + FIRST_PRESET_SLOT
    raw_b[0] = slot_a + FIRST_PRESET_SLOT

    acked = _upload_records(
        conn,
        [decode_preset_record(bytes(raw_a)), decode_preset_record(bytes(raw_b))],
    )
    return {"swapped": acked == 2, "slot_a": slot_a, "slot_b": slot_b}


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
    """Not supported: this pedal's effect chain order is fixed.

    Every preset carries exactly nine module blocks in the manual's chain
    order (FX, DS, AMP, CAB, NS, EQ, MOD, DELAY, REVERB) and no captured
    traffic reorders them.

    This previously sent its byte array under command 0xA5, which is in
    fact screen brightness -- calling it dimmed the display instead of
    reordering anything.
    """
    return {
        "error": "The effect chain order is fixed on this pedal.",
        "chain": list(MODULE_COMMAND_MAP),
        "requested": order,
    }


# ─── SYSTEM SETTINGS TOOLS ───────────────────────────────────────────

@mcp.tool()
def get_system_settings() -> dict[str, Any]:
    """Not supported: no verified way to read system settings exists.

    The pedal pushes its settings unsolicited after connect and restore,
    but no read request has been observed in any capture. Writes ARE
    supported -- see set_input_level, set_otg_level, set_screen_brightness,
    set_cab_sim_thru and set_spillover.
    """
    return {
        "error": "Reading system settings is not supported: no read "
                 "command has been observed on the wire.",
        "writable_settings": [
            "set_input_level", "set_otg_level", "set_screen_brightness",
            "set_cab_sim_thru", "set_spillover",
        ],
    }


@mcp.tool()
def set_system_setting(setting: str, value: int) -> dict[str, Any]:
    """Not supported: use the specific setting tools instead.

    This previously sent a guessed command (0xA1) that has never been
    observed on the wire. The confirmed settings each have their own
    tool now.
    """
    return {
        "error": f"Refusing to send an unverified command for '{setting}'.",
        "writable_settings": [
            "set_input_level", "set_otg_level", "set_screen_brightness",
            "set_cab_sim_thru", "set_spillover",
        ],
    }


@mcp.tool()
def get_volume() -> dict[str, Any]:
    """Not supported: no volume command has ever been observed.

    The master volume moves in the captures were made on the pedal and
    produced no USB traffic, so the volume may not sync over USB at all.
    """
    return {
        "error": "No volume command has been observed on the wire; "
                 "refusing to send a guessed one."
    }


@mcp.tool()
def set_volume(volume: int) -> dict[str, Any]:
    """Not supported: no volume command has ever been observed.

    Args:
        volume: Ignored.
    """
    return {
        "error": "No volume command has been observed on the wire; "
                 "refusing to send a guessed one."
    }


# ─── BACKUP & RESTORE TOOLS ──────────────────────────────────────────

@mcp.tool()
def backup_all(output_path: str) -> dict[str, Any]:
    """Download every preset to a JSON backup file.

    Reads all 200 slots via the confirmed bulk dump and stores each
    record byte-exactly (hex) alongside its name for readability.

    System settings and CTRL configurations are not yet included.

    Args:
        output_path: File path for the backup.
    """
    records = _fetch_all_records()
    if not records:
        return {"error": "No response from device"}

    payload = {
        "format": BACKUP_FORMAT,
        "version": 1,
        "presets": [
            _record_to_file_entry(records[slot]) for slot in sorted(records)
        ],
    }
    path = Path(output_path)
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")

    result: dict[str, Any] = {
        "path": str(path),
        "preset_count": len(records),
    }
    missing = [slot for slot in range(200) if slot not in records]
    if missing:
        result["missing_slots"] = missing
        result["warning"] = (
            f"{len(missing)} slot(s) were not returned by the device "
            "and are absent from the backup"
        )
    return result


@mcp.tool()
def restore_backup(input_path: str, overwrite: bool = False) -> dict[str, Any]:
    """Restore presets from a backup file made by backup_all.

    Occupied slots are never clobbered by empty backup entries. With
    ``overwrite=False`` occupied slots are skipped entirely; with
    ``overwrite=True`` named backup entries replace them.

    Args:
        input_path: Path to the backup JSON file.
        overwrite: If True, named entries overwrite occupied slots.
    """
    path = Path(input_path)
    if not path.exists():
        return {"error": f"File not found: {input_path}"}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"error": f"Could not read backup file: {exc}"}
    if payload.get("format") != BACKUP_FORMAT:
        return {
            "error": "Unrecognized backup format. Only files written by "
                     "backup_all can be restored."
        }

    conn = _get_connection()
    device = _fetch_all_records()

    to_write = []
    skipped: list[int] = []
    for entry in payload.get("presets", []):
        try:
            slot = int(entry["slot"])
            record = _record_from_file_entry(entry, slot)
        except (KeyError, TypeError, ValueError) as exc:
            return {"error": f"Malformed backup entry: {exc}"}
        if not 0 <= slot <= 199:
            return {"error": f"Backup entry has bad slot {slot}"}

        existing = device.get(slot)
        occupied = existing is not None and existing.name.strip()
        if occupied and not record.name.strip():
            skipped.append(slot)  # never erase a named preset with an empty one
            continue
        if occupied and not overwrite:
            skipped.append(slot)
            continue
        to_write.append(record)

    acked = _upload_records(conn, to_write) if to_write else 0
    result: dict[str, Any] = {"restored": True, "preset_count": acked}
    if skipped:
        result["skipped_slots"] = sorted(skipped)
    if acked != len(to_write):
        result["warning"] = (
            f"Device acknowledged {acked} of {len(to_write)} writes"
        )
    return result


@mcp.tool()
def export_preset(slot: int, output_path: str) -> dict[str, Any]:
    """Export a single preset to a JSON file.

    Args:
        slot: Preset slot (0-199).
        output_path: Output file path.
    """
    if not 0 <= slot <= 199:
        return {"error": "Slot must be 0-199"}

    records = _fetch_all_records(refresh=False)
    record = records.get(slot)
    if record is None:
        return {"error": f"Device did not return a record for slot {slot}"}

    entry = _record_to_file_entry(record)
    entry["format"] = PRESET_FORMAT
    entry["version"] = 1
    path = Path(output_path)
    path.write_text(json.dumps(entry, indent=1), encoding="utf-8")
    return {"path": str(path), "name": record.name}


@mcp.tool()
def import_preset(input_path: str, slot: int) -> dict[str, Any]:
    """Import a preset from a file written by export_preset.

    Args:
        input_path: Path to the preset JSON file.
        slot: Target slot (0-199).
    """
    if not 0 <= slot <= 199:
        return {"error": "Slot must be 0-199"}
    path = Path(input_path)
    if not path.exists():
        return {"error": f"File not found: {input_path}"}

    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"error": f"Could not read preset file: {exc}"}
    if entry.get("format") != PRESET_FORMAT:
        return {
            "error": "Unrecognized preset format. Only files written by "
                     "export_preset can be imported."
        }

    try:
        record = _record_from_file_entry(entry, slot)
    except (KeyError, TypeError, ValueError) as exc:
        return {"error": f"Malformed preset file: {exc}"}

    acked = _upload_records(_get_connection(), [record])
    return {
        "imported": acked == 1,
        "slot": slot,
        "address": slot_to_address(slot + FIRST_PRESET_SLOT),
        "name": record.name,
    }


# ─── IR / CABINET TOOLS ──────────────────────────────────────────────

@mcp.tool()
def list_ir_slots() -> dict[str, Any]:
    """List the user IR slots and their contents."""
    conn = _get_connection()
    response = conn.send_and_expect(build_read_ir_list(), Command.IR_LIST)
    if response is None:
        return {"error": "No IR list reply from device"}

    names = decode_ir_list(response.payload)
    result = split_user_model_list(names)
    # Kept for callers of the old flat shape.
    result["slots"] = [
        {"slot": i, "name": name, "empty": name == IR_EMPTY_NAME or not name}
        for i, name in enumerate(names)
    ]
    return result


@mcp.tool()
def upload_cab(index: int, name: str, blob_hex: str) -> dict[str, Any]:
    """Upload a user cab (IR) blob to a user cab slot.

    Takes the 1536-byte wire blob as hex -- NOT a .gir or .wav file.
    MOOER Studio converts files to this blob client-side and that
    conversion is not yet reverse-engineered, so this tool is for
    blobs captured from the wire or copied between slots.

    Args:
        index: User cab slot 0-19 (the pedal displays these as 27-46).
        name: Cab name, up to 16 ASCII characters.
        blob_hex: 1536 bytes of blob data, hex-encoded.
    """
    try:
        blob = bytes.fromhex(blob_hex)
    except ValueError:
        return {"error": "blob_hex is not valid hex"}

    conn = _get_connection()
    try:
        messages = build_upload_cab(index, name, blob)
    except ValueError as exc:
        return {"error": str(exc)}

    for message in messages:
        for report in message:
            conn.write(report)
        reply = conn.read_message()
        if reply is None or reply.command != Command.UPLOAD_CAB:
            return {"error": "No ack for cab upload message"}

    return {
        "uploaded": True,
        "index": index,
        "display": index + 27,
        "name": name[:16],
    }


@mcp.tool()
def upload_amp(index: int, name: str, blob_hex: str) -> dict[str, Any]:
    """Upload a user amp model blob to a user amp slot.

    Takes the 10240-byte wire blob as hex -- NOT a .gnr file (see
    upload_cab for why).

    Args:
        index: User amp slot 0-19 (the pedal displays these as 56-75).
        name: Amp name, up to 16 ASCII characters.
        blob_hex: 10240 bytes of blob data, hex-encoded.
    """
    try:
        blob = bytes.fromhex(blob_hex)
    except ValueError:
        return {"error": "blob_hex is not valid hex"}

    conn = _get_connection()
    try:
        messages = build_upload_amp(index, name, blob)
    except ValueError as exc:
        return {"error": str(exc)}

    for message in messages:
        for report in message:
            conn.write(report)
        reply = conn.read_message()
        if reply is None or reply.command != Command.UPLOAD_AMP_ACK:
            return {"error": "No ack for amp upload message"}

    return {
        "uploaded": True,
        "index": index,
        "display": index + 56,
        "name": name[:16],
    }


# ─── CAPTURE-DERIVED WRITE TOOLS ──────────────────────────────────────

@mcp.tool()
def select_preset_slot(slot: int) -> dict[str, Any]:
    """Make a preset active on the pedal.

    Args:
        slot: Preset slot 0-199.
    """
    if not 0 <= slot <= 199:
        return {"error": "Slot must be 0-199"}

    _get_connection().write(build_select_preset_slot(slot + FIRST_PRESET_SLOT))
    return {
        "slot": slot,
        "address": slot_to_address(slot + FIRST_PRESET_SLOT),
        "selected": True,
    }


@mcp.tool()
def save_preset(slot: int, name: str) -> dict[str, Any]:
    """Commit the pedal's current live state to a preset slot.

    This is the pedal's only write. Edit modules first, then save.
    Saving also sets the name, so this doubles as rename.

    Args:
        slot: Target preset slot 0-199.
        name: Preset name, up to 16 ASCII characters.
    """
    if not 0 <= slot <= 199:
        return {"error": "Slot must be 0-199"}

    _get_connection().write(build_save_preset(slot + FIRST_PRESET_SLOT, name))
    _record_cache.clear()
    return {
        "slot": slot,
        "address": slot_to_address(slot + FIRST_PRESET_SLOT),
        "name": name[:16],
        "saved": True,
    }


@mcp.tool()
def write_preset(
    slot: int,
    name: str,
    modules: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Write a complete preset to a slot, the way the editor does.

    Selects the slot, writes each supplied module block, then saves.
    Modules left out keep whatever the pedal currently has.

    Args:
        slot: Target preset slot 0-199.
        name: Preset name, up to 16 ASCII characters.
        modules: Per-module state, e.g.
            {"amp": {"enabled": true, "effect_type": 16, "params": [37, 50]}}.
    """
    if not 0 <= slot <= 199:
        return {"error": "Slot must be 0-199"}

    blocks: dict[Any, Any] = {}
    for module_name, state in (modules or {}).items():
        key = MODULE_NAME_ALIASES.get(module_name.lower(), module_name.lower())
        if key not in MODULE_COMMAND_MAP:
            return {
                "error": f"Unknown module '{module_name}'. "
                         f"Valid: {list(MODULE_COMMAND_MAP)}"
            }
        try:
            blocks[MODULE_COMMAND_MAP[key]] = ModuleBlock(
                enabled=bool(state.get("enabled", True)),
                effect_type=int(state.get("effect_type", 0)),
                params=[int(v) for v in state.get("params", [])],
            )
        except (TypeError, ValueError) as exc:
            return {"error": f"Bad state for module '{module_name}': {exc}"}

    conn = _get_connection()
    try:
        reports = build_write_preset(slot + FIRST_PRESET_SLOT, name, blocks)
    except ValueError as exc:
        return {"error": str(exc)}

    for report in reports:
        conn.write(report)
        time.sleep(WRITE_PACING_SECONDS)

    _record_cache.clear()
    return {
        "slot": slot,
        "address": slot_to_address(slot + FIRST_PRESET_SLOT),
        "name": name[:16],
        "modules_written": sorted(
            n for n, c in MODULE_COMMAND_MAP.items() if c in blocks
        ),
        "saved": True,
    }


@mcp.tool()
def set_expression_target(target: int, enabled: int = 1) -> dict[str, Any]:
    """Assign what the expression pedal controls.

    The target enumeration is not fully known: the captures show 10 and
    12 as the editor switched to volume and then to DS. Other values are
    untested.

    Args:
        target: Assignment target ID.
        enabled: Mode/enable flag, normally 1.
    """
    try:
        _get_connection().write(build_set_exp_assign(target, enabled))
    except ValueError as exc:
        return {"error": str(exc)}
    return {"target": target, "enabled": enabled}


# ─── SYSTEM SETTINGS (CAPTURE-CONFIRMED) ──────────────────────────────

@mcp.tool()
def set_input_level(db: float) -> dict[str, Any]:
    """Set the global input level in decibels.

    Applies to all presets. The manual's range is -inf to +6 dB; the
    encoding was read off the wire (9 = 0 dB, half a decibel per step).

    Args:
        db: Level in decibels, e.g. 2.5.
    """
    value = db_to_level(db)
    if not 0 <= value <= 0xFFFF:
        return {"error": f"Level {db} dB is out of range"}
    _get_connection().write(build_set_input_level(value))
    return {"db": level_to_db(value), "raw": value}


@mcp.tool()
def set_otg_level(db: float) -> dict[str, Any]:
    """Set the global OTG output level in decibels.

    Args:
        db: Level in decibels, e.g. 1.0.
    """
    value = db_to_level(db)
    if not 0 <= value <= 0xFFFF:
        return {"error": f"Level {db} dB is out of range"}
    _get_connection().write(build_set_otg_level(value))
    return {"db": level_to_db(value), "raw": value}


@mcp.tool()
def set_screen_brightness(value: int) -> dict[str, Any]:
    """Set the pedal's screen brightness. The editor uses 8-17.

    Args:
        value: Brightness level.
    """
    if not 0 <= value <= 0xFFFF:
        return {"error": f"Brightness must be 0-65535, got {value}"}
    _get_connection().write(build_set_brightness(value))
    return {"brightness": value}


@mcp.tool()
def set_cab_sim_thru(left: bool, right: bool) -> dict[str, Any]:
    """Enable or disable cabinet simulation on each output channel.

    Args:
        left: Cab sim on the left output.
        right: Cab sim on the right output.
    """
    _get_connection().write(build_set_cab_sim_thru(left, right))
    return {"left": left, "right": right}


@mcp.tool()
def set_spillover(enabled: bool) -> dict[str, Any]:
    """Enable or disable delay/reverb spill-over between preset changes.

    Args:
        enabled: True to let trails ring out across a preset change.
    """
    _get_connection().write(build_set_spillover(enabled))
    return {"spillover": enabled}


@mcp.tool()
def set_global_eq(
    enabled: bool,
    low_freq: int = 0, low_gain_db: float = 0.0,
    mid_freq: int = 0, mid_gain_db: float = 0.0,
    high_freq: int = 0, high_gain_db: float = 0.0,
    low_cut: int = 0, high_cut: int = 0,
) -> dict[str, Any]:
    """Set the pedal's global EQ (applies across all presets).

    This is the manual's GLOBAL EQ, distinct from any preset's EQ
    module. The whole block is written on every change, as the editor
    does. Frequencies are raw wire values; the editor's display runs 30
    higher for the three band frequencies. Gains are in dB, half-dB
    steps.
    """
    eq = GlobalEQ(
        enabled=enabled,
        low_freq=low_freq, low_gain_db=low_gain_db,
        mid_freq=mid_freq, mid_gain_db=mid_gain_db,
        high_freq=high_freq, high_gain_db=high_gain_db,
        low_cut=low_cut, high_cut=high_cut,
    )
    try:
        report = build_set_global_eq(eq)
    except ValueError as exc:
        return {"error": str(exc)}
    _get_connection().write(report)
    return {"global_eq": eq.__dict__}


# ─── CTRL CONFIGURATION ───────────────────────────────────────────────

@mcp.tool()
def get_ctrl_config(slot: int) -> dict[str, Any]:
    """Read which modules a preset's footswitch toggles (its CTRL setup).

    Args:
        slot: Preset slot 0-199.
    """
    if not 0 <= slot <= 199:
        return {"error": "Slot must be 0-199"}

    conn = _get_connection()
    response = conn.send_and_expect(
        build_read_ctrl_config(slot), Command.CTRL_CONFIG
    )
    if response is None:
        return {"error": "No CTRL config reply from device"}

    _, flags = decode_ctrl_config(response.payload)
    names = {command: name for name, command in MODULE_COMMAND_MAP.items()}
    return {
        "slot": slot,
        "address": slot_to_address(slot + FIRST_PRESET_SLOT),
        "toggles": {names[c]: v for c, v in flags.items()},
    }


@mcp.tool()
def set_ctrl_config(slot: int, modules: list[str]) -> dict[str, Any]:
    """Choose which modules a preset's footswitch toggles.

    Args:
        slot: Preset slot 0-199.
        modules: Module names the footswitch should toggle, e.g.
            ["delay", "reverb"]. Any not listed are left untouched by it.
    """
    if not 0 <= slot <= 199:
        return {"error": "Slot must be 0-199"}

    wanted = set()
    for name in modules:
        key = MODULE_NAME_ALIASES.get(name.lower(), name.lower())
        if key not in MODULE_COMMAND_MAP:
            return {
                "error": f"Unknown module '{name}'. "
                         f"Valid: {list(MODULE_COMMAND_MAP)}"
            }
        wanted.add(MODULE_COMMAND_MAP[key])

    flags = [c in wanted for c in MODULE_CHAIN]
    _get_connection().write(build_write_ctrl_config(slot, flags))
    return {"slot": slot, "toggles": sorted(m.lower() for m in modules)}


# ─── DIRECT PRESET WRITE ──────────────────────────────────────────────

@mcp.tool()
def put_preset(slot: int, preset: dict[str, Any]) -> dict[str, Any]:
    """Write a complete preset record directly to a slot.

    This is how the editor restores a backup. Unlike write_preset it does
    not select the slot or disturb the active preset -- the whole record
    is uploaded in one message.

    Args:
        slot: Target preset slot 0-199.
        preset: A preset as returned by get_preset -- ``name`` plus
            ``modules``, each with enabled / effect_type / params.
    """
    if not 0 <= slot <= 199:
        return {"error": "Slot must be 0-199"}

    record = PresetRecord(slot=slot + FIRST_PRESET_SLOT)
    record = record.with_name(str(preset.get("name", "")))

    blocks: dict[Any, Any] = {}
    for name, state in (preset.get("modules") or {}).items():
        key = MODULE_NAME_ALIASES.get(name.lower(), name.lower())
        if key not in MODULE_COMMAND_MAP:
            return {"error": f"Unknown module '{name}'"}
        try:
            blocks[MODULE_COMMAND_MAP[key]] = ModuleBlock(
                enabled=bool(state.get("enabled", True)),
                effect_type=int(state.get("effect_type", 0)),
                params=[int(v) for v in state.get("params", [])],
            )
        except (TypeError, ValueError) as exc:
            return {"error": f"Bad state for module '{name}': {exc}"}

    for command in MODULE_CHAIN:
        blocks.setdefault(command, ModuleBlock(enabled=False, effect_type=0))
    record.modules = blocks

    acked = _upload_records(_get_connection(), [record])
    return {
        "slot": slot,
        "address": slot_to_address(slot + FIRST_PRESET_SLOT),
        "name": record.name,
        "acknowledged": acked == 1,
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
    """Summary list of preset names from the last bulk dump."""
    presets = [
        {
            "slot": slot,
            "address": slot_to_address(record.slot),
            "name": record.name,
        }
        for slot, record in sorted(_record_cache.items())
    ]
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
