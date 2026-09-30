---
description: Operating guide for the MOOER GE150 Max pedal through the mooer-ge150 MCP tools. Load before reading, editing, copying, backing up or restoring the pedal's presets, or changing its global settings.
when_to_use: The user mentions their GE150 or Mooer pedal, a preset address such as 5A or 49D, tones, banks, backups, the footswitch/CTRL setup, or the global EQ.
---

# Working with the GE150 Max

The mooer-ge150 MCP server owns the USB connection and enforces the pedal's
timing rules itself. Your job is to pick the right tool, keep the user's
presets safe, and be honest about what is and is not known.

## Presets and addresses

- The pedal shows presets as bank 1–50 plus position A–D: `5A`, `49D`.
  Pass these straight to the tools. Slot numbers 0–199 also work (5A = 16),
  but talk to the user in addresses.
- A preset holds nine modules in a fixed order: FX, DS, AMP, CAB, NS, EQ,
  MOD, DELAY, REVERB. The order cannot be changed.

## Live versus stored — the thing to get right

- `select_preset`, `set_effect_param` and `toggle_effect` change only the
  **live** state. The next preset change discards it, which makes it safe
  for auditioning.
- `save_preset` commits the live state: to the same preset, or to another
  address as a "save as". Pass `name` to rename.
- `set_preset`, `copy_preset`, `swap_presets`, `import_preset` and
  `set_ctrl_config` write **stored** presets directly.
- Never "save" live edits with `set_preset`: it rewrites the preset from
  its stored copy and throws the live edits away.
- No tool changes an effect *type* live. `set_preset` does it stored, so
  save any live edits first.

## Keeping presets safe

- Before bulk or destructive changes, back up to a new file:
  `backup_all output_path=${CLAUDE_PLUGIN_DATA}/backups/<YYYY-MM-DD-HHMM>-<reason>.json`.
  It refuses to overwrite an existing file; pick a new name rather than
  passing `overwrite=true`.
- Before overwriting a preset that is not empty, say what will be lost and
  get a yes: "5C 'Clean-ish' will be replaced".
- These **reboot the pedal**, silencing it for about 10 s before the server
  reconnects: `put_preset`, `restore_backup`, and `copy_preset` or
  `swap_presets` with `byte_exact=true`. Warn the user first. Prefer the
  live-path versions unless the preset's 12-byte tail must move exactly.
- `upload_amp` wedged the pedal in earlier testing when aimed at amp index
  1; only index 0 has been seen to work. Do not use it unless the user
  accepts that risk.

## What the numbers mean

Effect types and parameters are raw numbers, and most of their names are
not known yet. Do not invent names or meanings.

- To learn what an effect-type number is, select a preset that uses it
  and ask the user what the pedal's screen shows.
- The amp, cab and effect catalog resources are unverified guesses; do not
  trust their ids.
- What *is* known about each module's parameters, the global settings and
  the vocabulary is in [reference.md](reference.md), with verified and
  unverified facts marked.

## Tool map

| To | Use |
|---|---|
| See what is on the pedal | `list_presets`, `get_preset`, `get_device_info` |
| Try changes by ear | `select_preset` → `set_effect_param` / `toggle_effect` → `save_preset` |
| Change a stored preset in one step | `set_preset` (name and/or modules, merged over what it holds) |
| Rearrange | `copy_preset`, `swap_presets` |
| Footswitch (CTRL) setup | `get_ctrl_config`, `set_ctrl_config` |
| Global settings | `set_system_settings`, `set_global_eq`, `set_expression_target` |
| Files | `backup_all`, `restore_backup`, `export_preset`, `import_preset` |
| Hand the pedal to MOOER Studio | `disconnect` (the next tool call reconnects) |

For building or refining a sound, use the `tone` skill. For rearranging
banks, use the `organize` skill.
