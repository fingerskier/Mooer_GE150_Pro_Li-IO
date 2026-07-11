# Patch (Preset) Read/Write Test Process

This document defines the test process for verifying patch R/W — reading
presets from the pedal, writing them back, and moving them through the
`.mo` / `.mbf` file formats. It has two tiers:

1. **Automated tests** — run on every change, no hardware required.
2. **Hardware-in-the-loop (HIL) procedure** — run against a real
   GE150 Pro Li before a release or after any protocol-layer change.

---

## 1. Automated tests (no hardware)

### Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### Run

```bash
pytest                      # everything
pytest tests/test_patch_rw.py -v   # patch R/W suite only
```

### What is covered, layer by layer

| Layer | Test file | Coverage |
|-------|-----------|----------|
| CRC | `test_crc.py` | Checksum against known vectors |
| Framing | `test_framing.py` | 64-byte report structure, preamble, checksum, chunk splitting |
| Command builders | `test_commands.py` | Slot bounds, store-preset payload layout |
| Preset model | `test_preset.py`, `test_patch_rw.py` | 512-byte serialize/deserialize round-trip, 16-bit delay time, param byte offsets |
| File formats | `test_file_formats.py` | `.mo` / `.mbf` export–import round-trips, `.gnr` header |
| Transport | `test_patch_rw.py` | Chunked TX **and** chunked-RX reassembly over a fake HID device |
| Server tools | `test_patch_rw.py`, `test_restore_overwrite.py` | `get_preset` / `set_preset` / `copy` / `swap` / `export` / `import` / `backup_all` / `restore_backup`, cache coherence, overwrite guard |

The patch R/W suite drives the **real** protocol and transport code
against `tests/fake_device.py` — a fake pedal that speaks the wire
protocol at the 64-byte HID-report level (length prefixes, preamble,
CRC, chunking). Only the USB hardware layer is simulated, so a failure
in framing, checksums, chunk reassembly, or preset serialization will
surface without a device attached.

### Definition of pass

* `pytest` exits 0.
* Any new tool touching preset data must ship with a round-trip test in
  `tests/test_patch_rw.py` (write → read back → byte-identical in
  canonical `Preset` form) and a JSON-serializability check
  (`json.dumps(result)`), since MCP tool results are JSON-encoded.

---

## 2. Hardware-in-the-loop procedure

Run on a real pedal before releases and after any change to
`protocol/` or `transport/`.

### Prerequisites

* GE150 Pro Li connected via USB-C, powered on, **fully charged or on
  mains** (a power loss mid-write can corrupt a slot).
* Mooer Studio software **closed** (it holds the HID interface).
* Linux: udev permission for VID:PID `0483:5703`, or run with sudo.
* An MCP client wired to this server, or a Python REPL importing
  `mooer_ge150_mcp.server` directly.

### Step 0 — Safety backup (mandatory)

```text
connect
backup_all  output_path=./pre_test_backup.mbf
```

Verify the file exists, is non-trivially sized, and the result has no
`failed_slots`/`warning` field. **Do not proceed** if the backup reports
failures. Keep this file until the whole session is verified.

### Step 1 — Identify

```text
get_device_info
```

Pass: model reads `GE150...` and a plausible firmware version — not
`unknown`. If this fails, stop; nothing downstream is trustworthy.

### Step 2 — Read path

1. `get_preset slot=0` — returns a full effects dict, no error.
2. On the pedal screen, open preset 0 and compare the name and 2–3
   parameter values (amp type, gain, delay time) against the tool output.
3. `list_presets start=0 end=9` — names match the pedal display.

Pass: values match the pedal exactly.

### Step 3 — Write path (use a scratch slot, e.g. 199)

1. `get_preset slot=199` — record its current state.
2. `set_preset slot=199 name="TEST RW" effects={"amp": {"amp_gain": 123}}`
3. `get_preset slot=199` — name is `TEST RW`, amp_gain is 123.
4. On the pedal, navigate away and back to slot 199 — the display shows
   the new name.
5. **Persistence:** power-cycle the pedal, reconnect, `get_preset
   slot=199` — the change survived the reboot.

Pass: read-back matches on both the tool side and the pedal display,
including after a power cycle.

### Step 4 — File round-trips

1. `export_preset slot=199 output_path=./t.mo`, then
   `import_preset input_path=./t.mo slot=198`.
   `get_preset slot=198` must equal slot 199 (name + all params).
2. Open `t.mo` in Mooer Studio (if available) — it must load cleanly.
   This cross-checks our format assumptions against the vendor tool.

### Step 5 — Copy / swap

1. `copy_preset from_slot=199 to_slot=197`, verify 197 == 199.
2. `swap_presets slot_a=197 slot_b=196`, verify both moved.

### Step 6 — Backup / restore round-trip

1. `backup_all output_path=./full.mbf` — no `failed_slots`.
2. Modify scratch slot 199 (`set_preset slot=199 name="CHANGED"`).
3. `restore_backup input_path=./full.mbf overwrite=true`.
4. `get_preset slot=199` — name is back to the backed-up value.
5. Spot-check 3 random other slots against the pedal display.

### Step 7 — Cleanup

Restore the original state from Step 0:

```text
restore_backup input_path=./pre_test_backup.mbf overwrite=true
```

Spot-check a few slots, then `disconnect`.

### Recording results

Log each step as PASS/FAIL with firmware version, OS, and backend
(hidapi/pyusb). Any FAIL in steps 1–3 blocks release; capture the raw
frames (set `logging` to DEBUG) and file an issue with the hex dumps.

---

## Known protocol assumptions to verify on hardware

These are encoded in the implementation but derived from SPEC.md rather
than confirmed captures — the HIL run is what validates them:

* Preset read responses (0x83) arrive chunked across multiple 64-byte
  reports and are reassembled by `USBConnection.read_message()`.
* `.mbf` holds **199** preset entries of 0x222 bytes (SPEC.md), while
  the device exposes slots 0–199 (200 slots) — slot 199 is therefore
  not covered by `backup_all`. If Mooer Studio backups prove to contain
  200 entries, update `MBF_PRESET_COUNT` and its tests.
* Real-time delay-time updates send two single-byte writes (offsets 5
  and 6). If the device expects one 16-bit write, adjust
  `set_effect_param`.
