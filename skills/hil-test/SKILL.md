---
description: Hardware-in-the-loop test of the mooer-ge150 tools against a real GE150 Max. Runs on one scratch bank whose four presets may be overwritten, restores them at the end, and checks all 200 presets against a backup.
argument-hint: "<scratch bank 1-50>"
arguments: bank
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/backups.py *) Bash(journalctl -k *)
---

# Hardware test on a scratch bank

Scratch bank: **$bank**. Below, `N` stands for that bank, so `NA`–`ND` are
its four presets: for bank 5, `5A`–`5D`. If no bank was given, ask for one.

## Before anything

Confirm all of these with the user and wait for a yes:

- The four presets in bank N may be overwritten during the test. They are
  restored at the end.
- The pedal is connected and powered, and MOOER Studio is closed.
- The pedal will reboot four times, silencing it for about 10 s each time.

## Rules

- Compare backups only with `python3 ${CLAUDE_SKILL_DIR}/backups.py`.
  Never read a backup file into the conversation; each is about 115 KB.
- Name files `${CLAUDE_PLUGIN_DATA}/backups/<YYYY-MM-DD-HHMM>-hil-<step>.json`.
- Record every step as PASS or FAIL with a one-line note. Stop at the
  first FAIL in steps 0–3; after that, go straight to step 6 to restore.

## Steps

**0. Safety backup.** Run `backup_all` to `…-hil-before.json`. It must
report `preset_count: 200` and no `missing_slots`. Then save the bank on
its own:
`python3 ${CLAUDE_SKILL_DIR}/backups.py subset <before file> <…-hil-bank.json> $bank`.

**1. Reads.**
- `get_device_info` reports GE150Max and the active preset.
- `list_presets start=NA end=ND` shows four names.
- `get_preset preset=NA` has nine modules and a 24-hex-digit `tail`.
- `get_ctrl_config preset=NA` answers.

**2. Live edits are not stored.** Run `select_preset preset=NA` (it must
be `confirmed: true`). Flip reverb with `toggle_effect`, run
`select_preset preset=NA` again, then `get_preset preset=NA`: reverb must
be back to its stored state.

**3. Stored writes, no reboot.**
- `set_preset preset=NC name="HIL Temp"`, then straight away
  `get_preset preset=NC`. It must show the new name.
- `copy_preset source=NB destination=NC`. NC's modules and `tail` must
  now equal NB's.
- `swap_presets first=NC second=ND`. Names and modules swap; each slot
  keeps its own `tail`.

**4. Timing guards.** Run `select_preset preset=NB`,
`get_ctrl_config preset=NC` and `select_preset preset=NA` back to back.
All must succeed. On Linux, check
`journalctl -k --since "-2min"` for `USB disconnect` lines: there must be
none.

**5. Writes that reboot the pedal.** Each call must report
`reconnected: true`.
- `put_preset preset=ND contents=<NA's get_preset result>`. ND's modules
  and `tail` must equal NA's.
- `get_preset` NA and NB, then `swap_presets first=NA second=NB
  byte_exact=true`. NA must now read back as NB did, tail included, and
  the other way round.
- `copy_preset source=NC destination=ND byte_exact=true`.

**6. Restore and compare.** Run
`restore_backup input_path=<…-hil-bank.json> overwrite=true`: it must
report `restored: true` and `reconnected: true`. Then `backup_all` to
`…-hil-after.json`, and run
`python3 ${CLAUDE_SKILL_DIR}/backups.py compare <before file> <after file>`.
It must print `identical`. Finish with `select_preset preset=NA`.

## Report

Give a table of steps with PASS/FAIL and notes, then the date, the OS and
the paths of the two backups. If step 6 is not identical, list the presets
that differ and do not call the run a pass.
