# GE150 Max reference

What is known, and how well. **Verified** means seen on the wire or tested
on the pedal; everything else is marked.

## Vocabulary (from the owner's manual)

- **Preset**: one stored sound, addressed by **bank** (1–50) and
  **position** (A–D), such as `12C`.
- **Effect module**: one stage of the chain. **Effect type**: the effect
  chosen within a module. **Parameter**: one knob of that effect.
  **ON/OFF status**: whether the module is active.
- **CTRL function**: the footswitch of the *active* preset toggles a chosen
  set of modules between two **A/B configurations** (blue/purple LED). A/B
  here has nothing to do with positions A–D.
- **GLOBAL EQ** applies to every preset. The **EQ module** is stage 6 of a
  preset's chain. Never let one name stand for both.

## A preset (verified)

Each preset has a 16-character name, nine module blocks and a 12-byte
**tail** of preset-level settings nobody has decoded yet. Each module
block has `enabled`, `effect_type` and up to ten raw parameter values
(0–65535).

## Module parameters

The parameter count comes from the captures. The meanings are marked
where known.

| Module | Params | Known |
|---|---|---|
| FX | 4 | — |
| DS | 3 | Level, tone and gain (the order is **not** confirmed) |
| AMP | 6 | — |
| CAB | — | — |
| NS | 3 | — |
| EQ | 9 | Words 0–5 are six band gains with 16 = centre (verified in factory presets; step size unknown). Words 6–8 are crossover frequencies in Hz, 100 / 600 / 1250 (verified). |
| MOD | — | — |
| DELAY | — | Some values are times in ms, e.g. 1040 (verified). In stored presets, param 2 varies like a delay time (360–560) (**unconfirmed**). |
| REVERB | — | — |

In stored presets, most knob-like parameters sit between 0 and 100, with
many at 50. That suggests a 0–100 knob with 50 in the middle, but it is
**not** confirmed parameter by parameter. Check real presets before
assuming a range.

## Global settings (verified encodings)

| Setting | Tool argument | Notes |
|---|---|---|
| Input level | `input_level_db` | Half-dB steps; manual range −∞ to +6 dB |
| OTG output level | `otg_level_db` | Half-dB steps |
| Screen brightness | `screen_brightness` | The editor uses 8–17 |
| Cab sim thru | `cab_sim_left` + `cab_sim_right` | Always sent together |
| Spill-over | `spillover` | Delay/reverb trails ring on across preset changes |
| Global EQ | `set_global_eq` | Gains in half-dB steps. Frequencies are raw, and the editor displays the three band frequencies 30 higher. |

Settings cannot be read back: no read command has been observed.

## CTRL (footswitch)

- `get_ctrl_config` and `set_ctrl_config` read and write which modules the
  footswitch toggles. Both are verified on the wire.
- The manual says to SAVE a preset after changing its CTRL setup on the
  pedal. Whether `set_ctrl_config` persists without a save is **not**
  verified. Check with `get_ctrl_config` after a preset change.

## User models

- User amp slots display as 56–75, and user cab/IR slots as 27–46
  (`list_user_models`).
- Uploads take the pedal's internal wire blob, **not** `.gir`, `.wav` or
  `.gnr` files. The file-to-blob conversion is not reverse-engineered.
- `upload_amp` hung the pedal twice at amp index 1; only index 0 is known
  to work.

## Timing (handled by the server; here so you can explain delays)

| Fact | Consequence |
|---|---|
| A select finishes loading about 0.2 s later | `select_preset` returns once the preset has loaded |
| After a CTRL read the pedal is busy about 1 s | A select, save or restore waits up to 2 s after CTRL traffic |
| A save takes effect up to about 0.65 s after it is sent | Every save waits 1 s, so an immediate read-back is correct |
| Reading presets means dumping all 200 | Each `get_preset` or `list_presets` takes about 2.5 s |
| A restore-style write reboots the pedal | Those tools take about 10 s and reconnect on their own |
