---
description: Build a new sound or refine an existing preset on the MOOER GE150 Max by ear, with live edits the user listens to before anything is saved. Use when the user wants a preset to sound different (more gain, less noise, brighter, like a song or an artist) or wants a new sound in a slot.
argument-hint: "[preset] [what it should sound like]"
---

# Shape a tone on the GE150 Max

Request: $ARGUMENTS

If the request is missing the preset or the goal, ask for it. If the
`guide` skill is not loaded yet, load it: the difference between live and
stored changes is the whole method here.

## 1. Set up

1. Back up to a new file:
   `backup_all output_path=${CLAUDE_PLUGIN_DATA}/backups/<YYYY-MM-DD-HHMM>-tone.json`.
2. Settle where the result will live:
   - **Refining** a preset: that preset is both the start and the target.
   - **New sound**: pick a starting point. Run `list_presets`, look for
     names close to the goal, propose one or two, and ask which address
     the result should be saved to. If that address is not empty, confirm
     it may be replaced and name what is there.
3. `get_preset` the starting preset. Summarise it in plain words: which
   modules are on, and which effect types (as numbers) they use.

## 2. Work live, by ear

1. `select_preset` the starting preset. Live edits apply to the active
   preset only.
2. Change one or two things at a time with `set_effect_param` or
   `toggle_effect`. For each change, say what you changed (module,
   parameter index, old → new value) and why in musical terms. Then ask
   the user to play and tell you what they hear.
3. Keep a running list of changes. Undoing one means setting the old value
   back. Selecting the preset again throws away every unsaved edit.
4. Values are raw numbers. Before moving a parameter, look at its value in
   this preset and in similar presets, and move it in small steps
   (about 5–10 on a 0–100 scale). Say plainly when you do not know what
   a parameter controls, and suggest the user watch the pedal's screen
   while you change it.
5. **Changing an effect type** (a different amp, drive or reverb) cannot
   be done live. Save the live edits first (step 3 below), then
   `set_preset preset=<target> modules={"<module>": {"effect_type": N}}`.
   Select the preset again and keep going. Ask the user to read the new
   effect's name off the pedal's screen, since the numbers are not mapped
   to names.

## 3. Commit

- `save_preset preset=<target>` stores the live state. Saving to another
  address is a "save as", and `name=` renames the preset.
- `get_preset` the target afterwards and confirm the changes stuck.
- If the user does not like the result: before saving, select the preset
  again; after saving, `restore_backup` from the step 1 backup (this
  reboots the pedal) or re-apply the old values.

Finish with a short summary: where the sound is stored, what changed from
the starting point, and the backup file's path.
