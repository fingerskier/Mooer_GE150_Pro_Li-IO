# Terminology

The vocabulary in this repo follows the **GE150 Max / GE150 Max Li Owner's
Manual**. Where our earlier code invented a term, the manual wins and the
old name is noted so existing notes stay readable.

Quotations below are verbatim from the manual unless marked otherwise.

## Presets

> "The number indicates the bank (1 - 50) and the letter (A - D) indicates
> the preset position within the bank."

- **Preset** — one stored sound. The manual uses *preset* throughout and
  never says "patch". Our code says `PresetRecord`; older notes and some
  MCP tool names still say *patch*, which means the same thing.
- **Bank** — a group of four presets, numbered **1–50**.
- **Preset position** — the letter **A–D** within a bank.
- **Preset address** — bank + letter, e.g. `49A`. This is what the pedal
  shows and what a player says out loud.
- **Slot** — *our term*, not the manual's, for the flat index behind the
  address. On the wire the pedal numbers slots **1–200**; the server
  currently uses **0–199** internally (see `log/CAPTURE_ANALYSIS.md`).

```
slot (1-based) = (bank - 1) * 4 + position + 1     # position: A=0 … D=3
```

Saving is explicit, and the manual is emphatic about it:

> "All changes must be stored in the Preset using the SAVE button, before
> you switch presets. Otherwise your changes will be lost."

## Effect chain

> "you can use the footswitch to change the switching status of one or
> several effect modules in the effect chain"

- **Effect chain** — the ordered series of modules a signal passes
  through. Not "signal chain", not "routing".
- **Effect module** — one stage of the chain. There are **nine**, and
  every preset carries all nine:

  | # | Module | Full name |
  |---|---|---|
  | 1 | **FX** | miscellaneous effects |
  | 2 | **DS** | overdrive / distortion |
  | 3 | **AMP** | amplifier model |
  | 4 | **CAB** | cabinet simulation |
  | 5 | **NS** | noise gate |
  | 6 | **EQ** | equaliser |
  | 7 | **MOD** | modulation |
  | 8 | **DELAY** | delay |
  | 9 | **REVERB** | reverb |

  This order is the chain order, and it is also the order the modules
  appear in a preset record on the wire.

- **Effect type** — the specific effect selected within a module.

  > "Rotate the SELECT knob to select (highlight) the name of the effect
  > type (top row of the menu)."

  Our code called this `model`; it is now `effect_type`. *Model* is
  reserved for what it plainly means in the AMP module (an amp model).

- **Parameter** — one adjustable value within a module, shown as a dial.

  > "Rotate the SELECT knob to select (highlight) one of the parameter
  > dials."

- **ON/OFF status** — whether a module is active.

  > "The ON/OFF status of the module is indicated in the upper right
  > corner of the menu."

  In the CTRL context the manual also calls this the **switching
  status**. Our code exposes it as `enabled`. Avoid "bypass" — the manual
  does not use it for modules.

## CTRL and footswitch toggling

Pressing the footswitch of the *currently active* preset does not change
preset; it flips that preset between two saved states.

> "The GE150 Max supports configuring the footswitch of the currently
> active preset for performing a CTRL function. In this mode, you can use
> the footswitch to change the switching status of one or several effect
> modules in the effect chain."

> "After selecting the effect module to be controlled, step on the
> footswitch of the currently active preset to change the switching
> status of the selected effect module. The LED ring around the footswitch
> indicates the A/B status in blue / purple."

- **CTRL function** (mode: **CTRL Mode**) — the feature.
- **A/B configurations** — the two states the footswitch toggles between.
  Call this *toggling the A/B configuration*, not "stomping", "scene", or
  "snapshot" (those are other manufacturers' words).
- **A/B status** — which of the two is live; blue vs purple LED ring.

The CTRL setup belongs to the preset and is not live until stored:

> "Note: You need to SAVE the preset after you have completed the CTRL
> function settings."

Careful: **A/B here is unrelated to preset positions A–D.** A preset at
address `12C` still has its own A/B configurations.

## Scope of settings

Three distinct scopes. Getting these confused is the main terminology
hazard in this project.

| Scope | Belongs to | Examples | Persistence |
|---|---|---|---|
| **Parameter** | one effect module in one preset | gain, delay time, EQ band | saved with the preset |
| **Preset** | one preset | effect types, ON/OFF states, CTRL A/B configuration, preset name | saved with the preset via SAVE |
| **System / global** | the whole pedal | INPUT LEVEL, SCREEN, CAB SIM THRU, GLOBAL EQ | independent of presets |

The manual's own headings are **"Parameter Editing"**, **"Saving
Presets"**, and **"SYSTEM SETTINGS"**.

On global scope it is explicit — of INPUT LEVEL:

> "This setting is global and applies to all presets."

And of GLOBAL EQ:

> "Use this setting for quick overall adjustment of your system to
> different venues or amplification equipment, thus avoiding tedious
> settings changes in every single preset."

**GLOBAL EQ is not the EQ module.** GLOBAL EQ sits outside the presets and
applies to all of them; the EQ module is stage 6 of the effect chain and
is stored per preset. Never let one name serve both.

## Operating modes

The manual names these modes: **Preset**, **Tuner**, **Drum Machine**
(also called Rhythm), **Looper**, **Expression**, and **CTRL Mode**.
**TAP TEMPO** is a function rather than a mode.

## Naming rules for this repo

1. Prefer the manual's word. *Preset*, *effect chain*, *effect module*,
   *effect type*, *parameter*, *ON/OFF status*, *CTRL function*,
   *A/B configuration*, *bank*, *system settings*, *global EQ*.
2. Where we need a term the manual lacks — *slot*, *module block*,
   *record*, *frame* — define it here and use it consistently.
3. Never reuse a manual term for something else. The specific offenders
   to watch are *EQ module* vs *GLOBAL EQ*, *A/B configuration* vs
   *preset position A–D*, and *effect type* vs *amp model*.
4. Say *patch* only when quoting an existing MCP tool name.

## Sources

- [GE150 Max / GE150 Max Li Owner's Manual (ManualsLib)](https://www.manualslib.com/manual/4097455/Mooer-Ge150-Max.html)
- [MOOER GE150 Max downloads (official)](https://www.mooeraudio.com/companyfile/GE150-Downloads-146.html)
