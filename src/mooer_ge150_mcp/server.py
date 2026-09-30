"""MCP server entry point for the MOOER GE150 Max.

A thin layer over :class:`~mooer_ge150_mcp.pedal.Pedal`, which owns the
USB connection and the pedal's timing rules. This module parses tool
arguments, shapes JSON results and reads/writes backup files; it never
talks to the transport directly.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .pedal import Pedal
from .protocol.commands import (
    Command,
    FIRST_PRESET_SLOT,
    GlobalEQ,
    MAX_MODULE_PARAMS,
    MODULE_CHAIN,
    MODULE_COMMAND_MAP,
    MODULE_NAME_ALIASES,
    ModuleBlock,
    PRESET_RECORD_SIZE,
    PRESET_TAIL_SIZE,
    PresetRecord,
    build_set_brightness,
    build_set_cab_sim_thru,
    build_set_exp_assign,
    build_set_global_eq,
    build_set_input_level,
    build_set_otg_level,
    build_set_spillover,
    build_upload_amp,
    build_upload_cab,
    db_to_level,
    decode_preset_record,
    encode_preset_record,
    level_to_db,
    parse_preset,
    slot_to_address,
    split_user_model_list,
)

logger = logging.getLogger(__name__)

#: Reported when a live write is abandoned because the select it depends
#: on was never confirmed.
SELECT_UNCONFIRMED = (
    "The pedal did not confirm the preset select, so nothing was written."
)

#: File-format tags for backups and single-preset exports.
BACKUP_FORMAT = "mooer-ge150-backup"
PRESET_FORMAT = "mooer-ge150-preset"

INSTRUCTIONS = """\
Controls a MOOER GE150 Max guitar effects pedal over USB. The connection
opens on first use; disconnect releases it (e.g. for MOOER Studio).

- Presets: pass the address the pedal shows -- bank 1-50 plus position
  A-D, e.g. "5A" -- or a slot number 0-199.
- Live vs stored: select_preset, set_effect_param and toggle_effect change
  only the pedal's live state, which is lost on the next preset change
  unless save_preset commits it. set_preset, copy_preset, swap_presets,
  import_preset and set_ctrl_config write stored presets directly.
- put_preset, restore_backup and byte_exact=True REBOOT the pedal by design
  (about 10 s, reconnected automatically).
- Run backup_all before bulk or destructive changes.
- Effect types and parameters are raw numbers; most of their names are not
  known yet, and the effect chain order is fixed.
- In Claude Code, the plugin's skills carry the details: guide, tone,
  organize and hil-test.
"""

mcp = FastMCP("mooer-ge150", instructions=INSTRUCTIONS)

#: The one pedal this server talks to. Opened on first use.
pedal = Pedal()

# ─── TOOL ANNOTATIONS ─────────────────────────────────────────────────

#: Reads the pedal; changes nothing.
READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
#: Changes the live state or the connection; nothing stored.
LIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True,
    openWorldHint=False,
)
#: Overwrites stored presets or global settings.
STORE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True,
    openWorldHint=False,
)
#: Overwrites stored presets, and repeating it undoes it.
SWAP = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False,
    openWorldHint=False,
)
#: Reads the pedal and writes a local file, which it can overwrite (only
#: when asked to, but the hint describes the tool, not one call).
TO_FILE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True,
    openWorldHint=False,
)

# ─── AMP / EFFECT CATALOGS (UNVERIFIED) ───────────────────────────────

#: Unverified: these names predate the USB captures and have not been
#: matched to the pedal's effect-type numbers.
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


# ─── HELPERS ──────────────────────────────────────────────────────────

def _where(slot: int) -> dict[str, Any]:
    """A slot as tools report it: the number and the pedal's address."""
    return {"slot": slot, "address": slot_to_address(slot + FIRST_PRESET_SLOT)}


def _module_command(name: str) -> Command:
    """Resolve a module name (manual name or alias). Raises ValueError."""
    key = MODULE_NAME_ALIASES.get(name.lower(), name.lower())
    if key not in MODULE_COMMAND_MAP:
        raise ValueError(
            f"Unknown module '{name}'. Valid: {list(MODULE_COMMAND_MAP)}"
        )
    return MODULE_COMMAND_MAP[key]


def _record_to_dict(record: PresetRecord) -> dict[str, Any]:
    """Render a PresetRecord as JSON-friendly output."""
    names = {command: name for name, command in MODULE_COMMAND_MAP.items()}
    return {
        **_where(record.slot - FIRST_PRESET_SLOT),
        "name": record.name,
        "modules": {
            names[command]: {
                "enabled": record.modules[command].enabled,
                "effect_type": record.modules[command].effect_type,
                "params": record.modules[command].params,
            }
            for command in MODULE_CHAIN
        },
        # Undecoded preset-level bytes; put_preset writes them back.
        "tail": record.tail.hex(),
    }


def _merge_module_states(
    record: PresetRecord, modules: dict[str, dict[str, Any]]
) -> str | None:
    """Apply per-module overrides onto a PresetRecord.

    Returns an error message, or None on success.
    """
    for module_name, state in (modules or {}).items():
        try:
            command = _module_command(module_name)
        except ValueError as exc:
            return str(exc)
        unknown = set(state) - {"enabled", "effect_type", "params"}
        if unknown:
            return (
                f"Unknown fields {sorted(unknown)} for module "
                f"'{module_name}'. Modules take enabled / effect_type / "
                f"params (see get_preset output)."
            )
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


def _record_to_file_entry(record: PresetRecord) -> dict[str, Any]:
    """A JSON-safe preset entry carrying the byte-exact record."""
    return {
        **_where(record.slot - FIRST_PRESET_SLOT),
        "name": record.name,
        "record": encode_preset_record(record).hex(),
    }


def _record_from_file_entry(entry: dict[str, Any], slot: int) -> PresetRecord:
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


def _refuse_overwrite(path: Path, overwrite: bool) -> dict[str, Any] | None:
    """An error result if *path* exists and replacing it was not asked for.

    A backup written over the last good backup is exactly the loss a
    backup exists to prevent, so replacing a file takes an explicit yes.
    """
    if path.exists() and not overwrite:
        return {
            "error": f"{path} already exists; pass overwrite=true to "
                     "replace it, or choose another path"
        }
    return None


def _read_live_block(module: str) -> tuple[Command, ModuleBlock] | str:
    """The active preset's current block for *module*, or an error."""
    try:
        command = _module_command(module)
    except ValueError as exc:
        return str(exc)
    active = pedal.read_active()
    if active is None:
        return "Could not read the active preset from the device"
    return command, active.modules[command]


# ─── CONNECTION AND READS ─────────────────────────────────────────────

@mcp.tool(title="Get device info", annotations=READ)
def get_device_info() -> dict[str, Any]:
    """Report the connected pedal and its active preset, connecting first
    if need be.

    Model and manufacturer come from the USB descriptors. Firmware version
    is not reported: no identify exchange has been observed, so there is
    no verified way to ask for it.
    """
    active = pedal.connect()
    info = pedal.device_info
    result: dict[str, Any] = {
        "model": info.product,
        "manufacturer": info.manufacturer,
        "vendor_id": f"0x{info.vendor_id:04X}",
        "product_id": f"0x{info.product_id:04X}",
    }
    if active is not None:
        result["active"] = _where(active.slot - FIRST_PRESET_SLOT)
    else:
        result["warning"] = "The pedal did not report its active preset"
    return result


@mcp.tool(title="Disconnect", annotations=LIVE)
def disconnect() -> dict[str, bool]:
    """Release the USB connection, e.g. so MOOER Studio can use the pedal.

    The next tool call reconnects.
    """
    pedal.disconnect()
    return {"disconnected": True}


@mcp.tool(title="List presets", annotations=READ)
def list_presets(start: int | str = "1A", end: int | str = "50D") -> dict[str, Any]:
    """List preset names.

    Args:
        start: First preset, e.g. "1A" or 0 (default "1A").
        end: Last preset, e.g. "50D" or 199 (default "50D").
    """
    try:
        first, last = sorted((parse_preset(start), parse_preset(end)))
    except ValueError as exc:
        return {"error": str(exc)}

    records = pedal.read_presets()
    if not records:
        return {"error": "No response from device"}

    presets = []
    for slot in range(first, last + 1):
        record = records.get(slot)
        name = record.name if record is not None else ""
        presets.append({**_where(slot), "name": name, "empty": not name.strip()})
    return {"presets": presets, "received": len(records)}


@mcp.tool(title="Get preset", annotations=READ)
def get_preset(preset: int | str) -> dict[str, Any]:
    """Read one preset in full: name, all nine modules and the 12-byte
    tail of settings not yet decoded.

    Args:
        preset: The preset, e.g. "5A" or 16.
    """
    try:
        slot = parse_preset(preset)
    except ValueError as exc:
        return {"error": str(exc)}

    record = pedal.read_presets().get(slot)
    if record is None:
        return {"error": f"Device did not return a record for {preset}"}
    return _record_to_dict(record)


@mcp.tool(title="Get CTRL config", annotations=READ)
def get_ctrl_config(preset: int | str) -> dict[str, Any]:
    """Read which modules a preset's footswitch toggles (its CTRL setup).

    Args:
        preset: The preset, e.g. "5A" or 16.
    """
    try:
        slot = parse_preset(preset)
    except ValueError as exc:
        return {"error": str(exc)}

    flags = pedal.read_ctrl_config(slot)
    if flags is None:
        return {"error": "No CTRL config reply from device"}
    names = {command: name for name, command in MODULE_COMMAND_MAP.items()}
    return {**_where(slot), "toggles": {names[c]: v for c, v in flags.items()}}


@mcp.tool(title="List user models", annotations=READ)
def list_user_models() -> dict[str, Any]:
    """List the user amp slots (displayed 56-75) and user cab/IR slots
    (displayed 27-46), with what each holds."""
    names = pedal.read_user_models()
    if names is None:
        return {"error": "No user model list reply from device"}
    return split_user_model_list(names)


# ─── LIVE EDITS (NOT STORED UNTIL SAVED) ──────────────────────────────

@mcp.tool(title="Select preset", annotations=LIVE)
def select_preset(preset: int | str) -> dict[str, Any]:
    """Make a preset the active one.

    Returns once the pedal reports it loaded (about 0.2 s), so the next
    command cannot land mid-load.

    Args:
        preset: The preset, e.g. "5A" or 16.
    """
    try:
        slot = parse_preset(preset)
    except ValueError as exc:
        return {"error": str(exc)}

    confirmed = pedal.select(slot)
    result: dict[str, Any] = {"active": _where(slot), "confirmed": confirmed}
    if not confirmed:
        result["warning"] = "The pedal did not report the preset loaded"
    return result


@mcp.tool(title="Set effect parameter (live)", annotations=LIVE)
def set_effect_param(module: str, param_index: int, value: int) -> dict[str, Any]:
    """Change one parameter of a module in the active preset.

    Live only: commit with save_preset before switching presets, or the
    change is lost. The pedal has no single-parameter write, so this reads
    the module's current block, substitutes the value and resends it.

    Args:
        module: fx, ds, amp, cab, ns, eq, mod, delay or reverb.
        param_index: Position of the parameter within the module, 0-9.
        value: Raw parameter value, 0-65535.
    """
    if not 0 <= param_index < MAX_MODULE_PARAMS:
        return {
            "error": f"param_index must be 0-{MAX_MODULE_PARAMS - 1}, "
                     f"got {param_index}"
        }
    if not 0 <= value <= 0xFFFF:
        return {"error": f"Value must be 0-65535, got {value}"}

    current = _read_live_block(module)
    if isinstance(current, str):
        return {"error": current}
    command, block = current

    params = list(block.params) + [0] * (MAX_MODULE_PARAMS - len(block.params))
    params[param_index] = value
    pedal.write_module(command, replace(block, params=params))
    return {
        "module": module,
        "param_index": param_index,
        "value": value,
        "effect_type": block.effect_type,
        "enabled": block.enabled,
    }


@mcp.tool(title="Toggle effect module (live)", annotations=LIVE)
def toggle_effect(module: str, enabled: bool) -> dict[str, Any]:
    """Turn a module of the active preset on or off, keeping its effect
    type and parameters.

    Live only, like set_effect_param: commit with save_preset.

    Args:
        module: fx, ds, amp, cab, ns, eq, mod, delay or reverb.
        enabled: True for on, False for off.
    """
    current = _read_live_block(module)
    if isinstance(current, str):
        return {"error": current}
    command, block = current

    pedal.write_module(command, replace(block, enabled=enabled))
    return {"module": module, "enabled": enabled, "effect_type": block.effect_type}


# ─── STORED PRESETS (NO REBOOT) ───────────────────────────────────────

@mcp.tool(title="Save live state to preset", annotations=STORE)
def save_preset(preset: int | str, name: str | None = None) -> dict[str, Any]:
    """Commit the pedal's live state to a preset. Also renames it.

    Edit with select_preset, set_effect_param and toggle_effect first,
    then save.

    Args:
        preset: The preset to store into, e.g. "5A" or 16.
        name: Up to 16 ASCII characters. Defaults to the name the preset
            already has.
    """
    try:
        slot = parse_preset(preset)
    except ValueError as exc:
        return {"error": str(exc)}

    if name is None:
        record = pedal.read_presets().get(slot)
        if record is None:
            return {"error": f"Device did not return a record for {preset}"}
        name = record.name
    pedal.save(slot, name)
    return {**_where(slot), "name": name[:16], "saved": True}


@mcp.tool(title="Set preset", annotations=STORE)
def set_preset(
    preset: int | str,
    name: str | None = None,
    modules: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Change a stored preset: merge a new name and module settings over
    what it holds now.

    Writes through the live path (select, write modules, save), so there
    is no reboot; the preset becomes the active one.

    Args:
        preset: The preset, e.g. "5A" or 16.
        name: New name, up to 16 ASCII characters.
        modules: Per-module changes in get_preset's shape, e.g.
            ``{"amp": {"enabled": true, "effect_type": 5, "params": [90]}}``.
            Fields left out keep their current values.
    """
    try:
        slot = parse_preset(preset)
    except ValueError as exc:
        return {"error": str(exc)}

    record = pedal.read_presets().get(slot)
    if record is None:
        return {"error": f"Device did not return a record for {preset}"}
    error = _merge_module_states(record, modules or {})
    if error:
        return {"error": error}
    if name is not None:
        record = record.with_name(name)

    stored = pedal.write_live(slot, record)
    result: dict[str, Any] = {**_where(slot), "name": record.name, "stored": stored}
    if not stored:
        result["error"] = SELECT_UNCONFIRMED
    return result


@mcp.tool(title="Copy preset", annotations=STORE)
def copy_preset(
    source: int | str, destination: int | str, byte_exact: bool = False
) -> dict[str, Any]:
    """Copy a preset to another slot.

    By default this is the editor's own "save as": select the source so
    its stored state is live, then commit that to the destination. No
    reboot, and the copy carries the modules and the 12-byte preset tail
    (confirmed on hardware). The source becomes the active preset, and
    the name is re-padded with NULs.

    With ``byte_exact=True`` the raw record -- name padding included --
    is re-slotted and uploaded with the restore-style write instead, and
    the active preset is left alone. THE PEDAL REBOOTS afterwards; the
    connection reconnects automatically (about 10 s).

    Args:
        source: The preset to copy, e.g. "5A" or 16.
        destination: The preset to overwrite, e.g. "5B" or 17.
        byte_exact: Upload the raw record (reboots the pedal).
    """
    try:
        src, dst = parse_preset(source), parse_preset(destination)
    except ValueError as exc:
        return {"error": str(exc)}

    record = pedal.read_presets().get(src)
    if record is None:
        return {"error": f"Device did not return a record for {source}"}
    result: dict[str, Any] = {
        "from": _where(src), "to": _where(dst), "name": record.name,
    }

    if byte_exact:
        acked = pedal.write_records(
            [replace(record, slot=dst + FIRST_PRESET_SLOT)]
        )
        result.update(copied=acked == 1, byte_exact=True,
                      reconnected=pedal.reconnect_after_reboot())
        return result

    if not pedal.select(src):
        return {**result, "copied": False, "error": SELECT_UNCONFIRMED}
    pedal.save(dst, record.name)
    return {**result, "copied": True}


@mcp.tool(title="Swap presets", annotations=SWAP)
def swap_presets(
    first: int | str, second: int | str, byte_exact: bool = False
) -> dict[str, Any]:
    """Swap two presets.

    By default each is rewritten through the live path (select, write the
    modules, save): names and modules swap without a reboot, but each slot
    keeps its own 12-byte preset tail (settings not yet decoded), which
    the live path cannot write.

    With ``byte_exact=True`` both raw records are re-slotted and uploaded
    with the restore-style write, so the tails swap too (confirmed on
    hardware) -- but THE PEDAL REBOOTS afterwards; the connection
    reconnects automatically (about 10 s).

    Args:
        first: One preset, e.g. "5A" or 16.
        second: The other, e.g. "5B" or 17.
        byte_exact: Swap the raw records (reboots the pedal).
    """
    try:
        a, b = parse_preset(first), parse_preset(second)
    except ValueError as exc:
        return {"error": str(exc)}

    records = pedal.read_presets()
    rec_a, rec_b = records.get(a), records.get(b)
    if rec_a is None or rec_b is None:
        return {"error": "Device did not return both preset records"}
    result: dict[str, Any] = {"first": _where(a), "second": _where(b)}

    if byte_exact:
        acked = pedal.write_records([
            replace(rec_b, slot=a + FIRST_PRESET_SLOT),
            replace(rec_a, slot=b + FIRST_PRESET_SLOT),
        ])
        result.update(swapped=acked == 2, byte_exact=True,
                      reconnected=pedal.reconnect_after_reboot())
        return result

    if not pedal.write_live(a, rec_b):
        return {**result, "swapped": False, "error": SELECT_UNCONFIRMED}
    time.sleep(0.2)
    if not pedal.write_live(b, rec_a):
        # Half done: the first slot is overwritten and its old preset now
        # exists only here. Hand it back so it can be written somewhere.
        return {
            **result,
            "swapped": False,
            "error": (
                f"{result['first']['address']} now holds "
                f"{result['second']['address']}'s preset, but the pedal did "
                f"not confirm the select of {result['second']['address']}, "
                f"so it was not written. The displaced preset is in "
                f"'displaced'; write it back with put_preset."
            ),
            "displaced": _record_to_dict(rec_a),
        }
    return {**result, "swapped": True}


@mcp.tool(title="Set CTRL config", annotations=STORE)
def set_ctrl_config(preset: int | str, modules: list[str]) -> dict[str, Any]:
    """Choose which modules a preset's footswitch toggles.

    Args:
        preset: The preset, e.g. "5A" or 16.
        modules: The modules the footswitch should toggle, e.g.
            ["delay", "reverb"]. Modules not listed are not toggled.
    """
    try:
        slot = parse_preset(preset)
        wanted = {_module_command(name) for name in modules}
    except ValueError as exc:
        return {"error": str(exc)}

    pedal.write_ctrl_config(slot, [c in wanted for c in MODULE_CHAIN])
    return {**_where(slot), "toggles": sorted(m.lower() for m in modules)}


# ─── STORED PRESETS (REBOOT) ──────────────────────────────────────────

@mcp.tool(title="Put preset (reboots pedal)", annotations=STORE)
def put_preset(preset: int | str, contents: dict[str, Any]) -> dict[str, Any]:
    """Write a complete preset record directly.

    This is the restore-style write: it sets every module AND the 12-byte
    preset tail (settings not yet decoded), which the live path cannot
    write -- but THE PEDAL REBOOTS a moment after (by design, exactly as
    after MOOER Studio's own restore). Prefer set_preset for interactive
    edits; use this when the tail matters and a reboot is acceptable. The
    connection reconnects automatically (about 10 s).

    Args:
        preset: The preset to overwrite, e.g. "5A" or 16.
        contents: A preset as get_preset returns it -- ``name``,
            ``modules`` (each with enabled / effect_type / params) and
            ``tail`` (hex). Without ``tail`` the slot keeps its own.
    """
    try:
        slot = parse_preset(preset)
    except ValueError as exc:
        return {"error": str(exc)}

    record = PresetRecord(slot=slot + FIRST_PRESET_SLOT)
    record = record.with_name(str(contents.get("name", "")))
    blocks: dict[Command, ModuleBlock] = {}
    for name, state in (contents.get("modules") or {}).items():
        try:
            command = _module_command(name)
            blocks[command] = ModuleBlock(
                enabled=bool(state.get("enabled", True)),
                effect_type=int(state.get("effect_type", 0)),
                params=[int(v) for v in state.get("params", [])],
            )
        except (TypeError, ValueError) as exc:
            return {"error": f"Bad state for module '{name}': {exc}"}
    for command in MODULE_CHAIN:
        blocks.setdefault(command, ModuleBlock(enabled=False, effect_type=0))
    record.modules = blocks

    # A fresh record's tail is all zeros, which would silently wipe the
    # undecoded preset-level settings; never write that by default.
    if contents.get("tail") is not None:
        try:
            tail = bytes.fromhex(str(contents["tail"]))
        except ValueError:
            return {"error": "tail must be hex, as get_preset returns it"}
        if len(tail) != PRESET_TAIL_SIZE:
            return {
                "error": f"tail must be {PRESET_TAIL_SIZE} bytes, "
                         f"got {len(tail)}"
            }
    else:
        existing = pedal.read_presets().get(slot)
        if existing is None:
            return {
                "error": f"Device did not return a record for {preset}, so "
                         "its tail cannot be preserved; pass the preset's "
                         "tail explicitly"
            }
        tail = existing.tail
    record.tail = tail

    acked = pedal.write_records([record])
    return {
        **_where(slot),
        "name": record.name,
        "acknowledged": acked == 1,
        "reconnected": pedal.reconnect_after_reboot(),
    }


@mcp.tool(title="Restore backup (reboots pedal)", annotations=STORE)
def restore_backup(input_path: str, overwrite: bool = False) -> dict[str, Any]:
    """Restore presets from a file written by backup_all.

    THE PEDAL REBOOTS when the restore completes (by design -- MOOER
    Studio's restore does the same); the connection reconnects
    automatically afterwards.

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

    device = pedal.read_presets()
    to_write: list[PresetRecord] = []
    skipped: list[int] = []
    for entry in payload.get("presets", []):
        try:
            slot = int(entry["slot"])
            if not 0 <= slot <= 199:
                return {"error": f"Backup entry has bad slot {slot}"}
            record = _record_from_file_entry(entry, slot)
        except (KeyError, TypeError, ValueError) as exc:
            return {"error": f"Malformed backup entry: {exc}"}

        existing = device.get(slot)
        occupied = existing is not None and existing.name.strip()
        if occupied and not record.name.strip():
            skipped.append(slot)  # never erase a named preset with an empty one
            continue
        if occupied and not overwrite:
            skipped.append(slot)
            continue
        to_write.append(record)

    acked = pedal.write_records(to_write) if to_write else 0
    result: dict[str, Any] = {
        "restored": acked == len(to_write),
        "preset_count": acked,
        # RESTORE_END reboots the pedal by design; ride through it.
        "reconnected": pedal.reconnect_after_reboot() if to_write else True,
    }
    if skipped:
        result["skipped_slots"] = sorted(skipped)
    if acked != len(to_write):
        result["warning"] = f"Device acknowledged {acked} of {len(to_write)} writes"
    return result


# ─── FILES ────────────────────────────────────────────────────────────

@mcp.tool(title="Back up all presets", annotations=TO_FILE)
def backup_all(output_path: str, overwrite: bool = False) -> dict[str, Any]:
    """Save every preset to a JSON backup file, each record byte for byte.

    System settings and CTRL configurations are not included yet.

    Args:
        output_path: File path for the backup. Missing folders are
            created.
        overwrite: Replace the file if it already exists. Without it an
            existing file is left alone and an error is returned.
    """
    path = Path(output_path)
    refused = _refuse_overwrite(path, overwrite)
    if refused:
        return refused

    records = pedal.read_presets()
    if not records:
        return {"error": "No response from device"}

    payload = {
        "format": BACKUP_FORMAT,
        "version": 1,
        "presets": [_record_to_file_entry(records[s]) for s in sorted(records)],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")

    result: dict[str, Any] = {"path": str(path), "preset_count": len(records)}
    missing = [slot for slot in range(200) if slot not in records]
    if missing:
        result["missing_slots"] = missing
        result["warning"] = (
            f"{len(missing)} slot(s) were not returned by the device "
            "and are absent from the backup"
        )
    return result


@mcp.tool(title="Export preset", annotations=TO_FILE)
def export_preset(
    preset: int | str, output_path: str, overwrite: bool = False
) -> dict[str, Any]:
    """Save one preset to a JSON file, byte for byte.

    Args:
        preset: The preset, e.g. "5A" or 16.
        output_path: Output file path. Missing folders are created.
        overwrite: Replace the file if it already exists. Without it an
            existing file is left alone and an error is returned.
    """
    try:
        slot = parse_preset(preset)
    except ValueError as exc:
        return {"error": str(exc)}
    path = Path(output_path)
    refused = _refuse_overwrite(path, overwrite)
    if refused:
        return refused

    record = pedal.read_presets().get(slot)
    if record is None:
        return {"error": f"Device did not return a record for {preset}"}
    entry = {**_record_to_file_entry(record), "format": PRESET_FORMAT, "version": 1}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry, indent=1), encoding="utf-8")
    return {"path": str(path), "name": record.name}


@mcp.tool(title="Import preset", annotations=STORE)
def import_preset(input_path: str, preset: int | str) -> dict[str, Any]:
    """Store a preset from a file written by export_preset.

    Writes through the live path (no reboot), so the slot keeps its own
    12-byte tail; use put_preset with the file's contents when the tail
    must come along too.

    Args:
        input_path: Path to the preset JSON file.
        preset: The preset to overwrite, e.g. "5A" or 16.
    """
    try:
        slot = parse_preset(preset)
    except ValueError as exc:
        return {"error": str(exc)}
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

    stored = pedal.write_live(slot, record)
    result: dict[str, Any] = {**_where(slot), "name": record.name, "imported": stored}
    if not stored:
        result["error"] = SELECT_UNCONFIRMED
    return result


# ─── GLOBAL SETTINGS ──────────────────────────────────────────────────

@mcp.tool(title="Set system settings", annotations=STORE)
def set_system_settings(
    input_level_db: float | None = None,
    otg_level_db: float | None = None,
    screen_brightness: int | None = None,
    cab_sim_left: bool | None = None,
    cab_sim_right: bool | None = None,
    spillover: bool | None = None,
) -> dict[str, Any]:
    """Change global settings (they apply to every preset). Only the
    settings given are sent.

    They cannot be read back: no read command has been observed.

    Args:
        input_level_db: Input level in dB, half-dB steps (manual range
            -inf to +6 dB).
        otg_level_db: OTG output level in dB, half-dB steps.
        screen_brightness: Screen brightness; the editor uses 8-17.
        cab_sim_left: Cabinet simulation on the left output. Give both
            cab_sim_left and cab_sim_right: they are sent together.
        cab_sim_right: Cabinet simulation on the right output.
        spillover: Let delay/reverb trails ring on across preset changes.
    """
    reports: list[bytes] = []
    applied: dict[str, Any] = {}

    for key, db, build in (
        ("input_level_db", input_level_db, build_set_input_level),
        ("otg_level_db", otg_level_db, build_set_otg_level),
    ):
        if db is None:
            continue
        raw = db_to_level(db)
        if not 0 <= raw <= 0xFFFF:
            return {"error": f"{key} {db} dB is out of range"}
        reports.append(build(raw))
        applied[key] = level_to_db(raw)

    if screen_brightness is not None:
        if not 0 <= screen_brightness <= 0xFFFF:
            return {"error": f"screen_brightness must be 0-65535, got {screen_brightness}"}
        reports.append(build_set_brightness(screen_brightness))
        applied["screen_brightness"] = screen_brightness

    if (cab_sim_left is None) != (cab_sim_right is None):
        return {"error": "Give both cab_sim_left and cab_sim_right; they are sent together"}
    if cab_sim_left is not None:
        reports.append(build_set_cab_sim_thru(cab_sim_left, cab_sim_right))
        applied.update(cab_sim_left=cab_sim_left, cab_sim_right=cab_sim_right)

    if spillover is not None:
        reports.append(build_set_spillover(spillover))
        applied["spillover"] = spillover

    if not reports:
        return {"error": "No settings given"}
    for report in reports:
        pedal.send(report)
    return {"applied": applied}


@mcp.tool(title="Set global EQ", annotations=STORE)
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
    pedal.send(report)
    return {"global_eq": eq.__dict__}


@mcp.tool(title="Set expression pedal target", annotations=STORE)
def set_expression_target(target: int, enabled: int = 1) -> dict[str, Any]:
    """Assign what the expression pedal controls.

    The target numbering is not fully known: the captures show 10 and 12
    as the editor switched to volume and then to DS. Other values are
    untested.

    Args:
        target: Assignment target ID.
        enabled: Mode/enable flag, normally 1.
    """
    try:
        report = build_set_exp_assign(target, enabled)
    except ValueError as exc:
        return {"error": str(exc)}
    pedal.send(report)
    return {"target": target, "enabled": enabled}


# ─── USER MODEL UPLOADS ───────────────────────────────────────────────

@mcp.tool(title="Upload user cab", annotations=STORE)
def upload_cab(index: int, name: str, blob_hex: str) -> dict[str, Any]:
    """Upload a user cab (IR) blob to a user cab slot.

    Takes the 1536-byte wire blob as hex -- NOT a .gir or .wav file.
    MOOER Studio converts files to this blob client-side and that
    conversion is not yet reverse-engineered, so this tool is for blobs
    captured from the wire or copied between slots.

    Args:
        index: User cab slot 0-19 (the pedal displays these as 27-46).
        name: Cab name, up to 16 ASCII characters.
        blob_hex: 1536 bytes of blob data, hex-encoded.
    """
    try:
        messages = build_upload_cab(index, name, bytes.fromhex(blob_hex))
    except ValueError as exc:
        return {"error": str(exc)}
    if not pedal.upload(messages, Command.UPLOAD_CAB):
        return {"error": "No ack for cab upload message"}
    return {"uploaded": True, "index": index, "display": index + 27, "name": name[:16]}


@mcp.tool(title="Upload user amp", annotations=STORE)
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
        messages = build_upload_amp(index, name, bytes.fromhex(blob_hex))
    except ValueError as exc:
        return {"error": str(exc)}
    if not pedal.upload(messages, Command.UPLOAD_AMP_ACK):
        return {"error": "No ack for amp upload message"}
    return {"uploaded": True, "index": index, "display": index + 56, "name": name[:16]}


# ─── MCP RESOURCES ───────────────────────────────────────────────────

@mcp.resource("mooer://device/info")
def resource_device_info() -> str:
    """Connection state and USB identity of the pedal."""
    info = pedal.device_info
    if info is None:
        return json.dumps({"connected": False})
    return json.dumps({
        "connected": True,
        "manufacturer": info.manufacturer,
        "product": info.product,
        "vendor_id": f"0x{info.vendor_id:04X}",
        "product_id": f"0x{info.product_id:04X}",
    })


@mcp.resource("mooer://presets/list")
def resource_presets_list() -> str:
    """Preset names from the most recent read of the pedal."""
    presets = [
        {**_where(slot), "name": record.name}
        for slot, record in sorted(pedal.last_dump.items())
    ]
    return json.dumps({"presets": presets})


@mcp.resource("mooer://catalog/amps")
def resource_amp_catalog() -> str:
    """Amp model names. UNVERIFIED: not matched to the pedal's numbering."""
    amps = [{"id": i, "name": name} for i, name in enumerate(AMP_MODELS)]
    return json.dumps({"amps": amps, "count": len(amps), "verified": False})


@mcp.resource("mooer://catalog/cabs")
def resource_cab_catalog() -> str:
    """Cabinet names. UNVERIFIED: not matched to the pedal's numbering."""
    cabs = [{"id": i, "name": name} for i, name in enumerate(CAB_MODELS)]
    return json.dumps({"cabs": cabs, "count": len(cabs), "verified": False})


@mcp.resource("mooer://catalog/effects")
def resource_effects_catalog() -> str:
    """Effect names by module. UNVERIFIED: not matched to the pedal's
    numbering."""
    catalog = {
        category: [{"id": i, "name": name} for i, name in enumerate(effects)]
        for category, effects in EFFECT_CATALOG.items()
    }
    return json.dumps({"effects": catalog, "verified": False})


# ─── ENTRY POINT ─────────────────────────────────────────────────────

def main():
    """Run the MCP server with stdio transport."""
    logging.basicConfig(level=logging.INFO)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
