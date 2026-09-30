---
description: Reorganize and rename presets across the MOOER GE150 Max's 50 banks, such as grouping sounds by style, moving presets between banks or cleaning up names. Use when the user wants their presets sorted, grouped, renamed consistently or moved.
argument-hint: "[what to organize, e.g. 'group by genre' or 'banks 1-10']"
---

# Organize presets

Goal: $ARGUMENTS

If the goal is unclear, ask what order the user wants (by genre, by gig
set list, clean to high gain) and which banks are in scope. If the `guide`
skill is not loaded yet, load it.

## 1. Back up and survey

1. Back up to a new file:
   `backup_all output_path=${CLAUDE_PLUGIN_DATA}/backups/<YYYY-MM-DD-HHMM>-organize.json`.
2. `list_presets` for the banks in scope; each read takes about 2.5 s.
   Group presets by name. Where a name says nothing, `get_preset` it and
   look at which modules are on: for example, DS on with a high amp level
   suggests a drive sound. Say which placements are guesses.

## 2. Plan, and get it approved

Show the plan as a table of current address and name → new address and
name, including which slots end up empty. **Nothing is written until the
user approves the plan.** Edit it as they ask.

## 3. Carry it out

Use the live path; it does not reboot the pedal:

- **Move into an empty slot:** `copy_preset source=<from> destination=<to>`.
  The copy carries modules and the 12-byte tail. The old slot keeps its
  preset until you overwrite or rename it.
- **Exchange two presets:** `swap_presets first=<a> second=<b>`. Names and
  modules swap, but each slot keeps its own 12-byte tail. If the tails
  must move too, `byte_exact=true` does it, at the cost of a pedal reboot
  per swap. Mention this trade-off once, then use the default.
- **Rearrange a whole bank:** use a sequence of swaps.
- **Rename:** `set_preset preset=<address> name="<new name>"` (at most 16
  ASCII characters).

`copy_preset` makes the source the active preset, so tell the user the
active preset will jump around while you work. Keep a log of every step.
If a step reports an error, stop and show the log before going on. A
failed swap returns the displaced preset in `displaced`; write it back
with `put_preset` straight away.

## 4. Check

Run `list_presets` over the banks in scope again and show before and after
side by side. If something is wrong and cannot be fixed step by step,
`restore_backup` from the step 1 backup with `overwrite=true`. It reboots
the pedal.
