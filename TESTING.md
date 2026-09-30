# Testing

Two tiers:

1. **Automated tests** — run on every change, no hardware required.
2. **Hardware-in-the-loop (HIL)** — run against a real pedal before a
   release and after any change to `pedal.py`, `protocol/` or
   `transport/`.

---

## 1. Automated tests (no hardware)

```bash
uv run --no-project --with-editable . --with pytest --with pytest-asyncio \
    python -m pytest -q
```

or, in a virtualenv, `pip install -e ".[dev]"` and then `pytest`.

| Layer | Test file | Coverage |
|-------|-----------|----------|
| CRC, framing | `test_crc.py`, `test_framing.py` | Checksum vectors, 64-byte reports, chunking |
| Captures | `test_capture_*.py` | Golden frames from the USB captures in `log/` |
| Command builders | `test_commands.py`, `test_terminology.py` | Payload layouts, slot/address numbering, `parse_preset` |
| Tools on the fake pedal | `test_tools_rewired.py`, `test_patch_rw.py`, `test_restore_overwrite.py` | Every MCP tool, the timing guards, reconnect after reboot, restore overwrite rules |
| Legacy model | `test_preset.py`, `test_file_formats.py` | The pre-capture 512-byte model in `models/`, which the server no longer uses |

The tool tests drive the real `Pedal`, protocol and transport code
against `tests/fake_max_pedal.py`, which speaks only the exchanges seen
in the captures, at the 64-byte report level. The fake can also ignore a
select or go silent, to exercise the failure paths. Its fixtures skip
the pacing sleeps and can never reach real hardware.

Definition of pass: `pytest` exits 0, and a new tool that changes a
preset ships with a test that reads the change back.

---

## 2. Hardware-in-the-loop procedure

### Prerequisites

* The pedal on USB and powered, with MOOER Studio closed.
* Linux: the udev rule from `udev/` installed (see the README).
* The MCP server connected (in this repo, Claude Code starts it from
  `.mcp.json`), or a Python script using `mooer_ge150_mcp.server`.
* A **scratch bank** whose presets may be overwritten. The examples use
  5A–5D.

### Step 0 — Safety backup

`backup_all output_path=./pre_test.json`. It must report
`preset_count: 200` and no `missing_slots`. Do not continue otherwise, and
keep the file until the run is verified.

### Step 1 — Reads

* `get_device_info` connects and reports `GE150Max` and the active preset.
* `list_presets start=5A end=5D` shows the names the pedal displays.
* `get_preset preset=5A` returns nine modules and a 12-byte `tail`.
* `get_ctrl_config preset=5A` matches the preset's CTRL setup.

### Step 2 — Live edits are not stored

1. `select_preset preset=5A` reports `confirmed: true`.
2. `toggle_effect module=reverb enabled=false`, then select 5A again.
3. `get_preset preset=5A` still shows reverb in its original state.

### Step 3 — Stored writes, no reboot

1. `set_preset preset=5C name="HIL Temp"`. Straight after it,
   `get_preset preset=5C` must show the new name.
2. `copy_preset source=5B destination=5C`. 5C now matches 5B, tail
   included.
3. `swap_presets first=5C second=5D`. Names and modules swap, but each
   slot keeps its own tail.

### Step 4 — Timing guards

Select 5B, read 5C's CTRL config, then select 5A, with no pauses between
the calls. Every select must be confirmed, and the pedal must not drop
off USB (`journalctl -k | grep "USB disconnect"`). This exact sequence
hung the pedal before the guards existed.

### Step 5 — Writes that reboot the pedal

Each of these reboots the pedal by design and should reconnect within
about 10 s, reporting `reconnected: true`:

* `put_preset preset=5D contents=<get_preset of 5A>`. 5D matches 5A,
  tail included.
* `swap_presets first=5A second=5B byte_exact=true`. Both raw records
  swap, byte for byte.
* `copy_preset source=5C destination=5D byte_exact=true`.

### Step 6 — Restore and compare

`restore_backup input_path=<backup with only the scratch bank>
overwrite=true`, then `backup_all` again. All 200 records must be
byte-identical to Step 0's file. Compare against the Step 0 file, not a
backup taken partway through the run.

### Recording results

Log each step as PASS/FAIL with the date, OS and hidapi backend. Any
failure in Steps 0–3 blocks a release. For a failure, set the
`mooer_ge150_mcp` loggers to DEBUG and keep the log.

---

## Hardware timing facts

Measured on a GE150 Max; `pedal.py` enforces all of them.

| Fact | Consequence |
|---|---|
| A select is answered about 0.2 s later by `0x2A`, then a `0x29` naming the loaded slot | Nothing is sent after a select until that `0x29` arrives |
| After a CTRL read the pedal is busy about 1 s; a select 0.5 s later hung it | 2 s of quiet after CTRL traffic before a select, save or restore |
| A save takes effect 0.15–0.65 s after it is sent | 1 s of quiet after every save |
| Unpaced records in a restore bracket rebooted it | 20 ms between reports, 100 ms between records |
| `RESTORE_END` always reboots it; reopened about 1 s into start-up, its HID interface went silent | Reconnect waits for it to settle, confirms it answers, falls back to a USB port reset |
