# USB capture analysis — `various_tests.pcapng`

Wireshark/USBPcap capture of MOOER Studio driving a **GE150 Max** over USB
OTG, taken 2026-07-26 while sweeping knobs, toggling modules, renaming
presets and switching patches.

This file is the ground truth behind the protocol constants in
`src/mooer_ge150_mcp/`. Everything below was verified mechanically against
the capture; the golden frames live in `tests/test_capture_golden.py`.

## Device and transport

| | Value | Source |
|---|---|---|
| VID:PID | `0x34DB:0x000F` | device descriptor, frame 56 |
| HID interface | 5 | configuration descriptor, frame 58 |
| Endpoint OUT | `0x05`, interrupt, 64 bytes | frame 58 |
| Endpoint IN | `0x85`, interrupt, 64 bytes | frame 58 |

The pedal is a composite device — audio streaming, MIDI, and HID. Only the
HID interface carries the editor protocol; the MIDI endpoints (`0x04`/`0x84`)
saw no editor traffic.

This corrects the previous target of `0x0483:0x5703` on endpoints
`0x02`/`0x81`, which belongs to older STM32-based Mooer units.

## Frame format

```
+----------+----------+--------+---------+---------+----------+---------+
| HID size | Preamble |  Size  | Command | Payload | Checksum | Padding |
|  1 byte  | AA 55    | 2 (LE) | 1 byte  |   var   |  2 (BE)  | to 64 B |
+----------+----------+--------+---------+---------+----------+---------+
```

- **HID size** is always `4 + Size + 2`.
- **Size** counts the command byte plus payload.
- **Checksum** is CRC-16/CCITT (poly `0x1021`, init `0x0000`) with the
  result inverted, computed over **the size field *and* the body** — the
  preamble is excluded — and stored **big-endian**.

The checksum parameters were recovered by brute-forcing polynomial, init,
reflection, xor-out, covered range and byte order against the capture. Only
one combination matched, and it matches **146 of 146** protocol messages.

Two details differed from this repo's pre-capture assumption: the checksum
range starts at the size field rather than the command byte, and the two
bytes are big-endian rather than little-endian.

No message in the capture exceeded a single 64-byte report, so the
multi-report chunking in `build_chunked_frames()` remains **unverified**.

## Command IDs

Device replies generally reuse the request ID with bit 7 cleared —
`0x85 → 0x05`, `0x89 → 0x09`, `0xB4 → 0x34`. The preset-read reply is the
exception (see below).

### Effect module blocks — `0x82`–`0x89`

Each is a fixed 24-byte payload of **12 little-endian u16 words**:

| Word | Meaning |
|---|---|
| 0 | enable flag (0 / 1) |
| 1 | model index |
| 2–11 | parameter values, zero-padded |

MOOER Studio never sends single-parameter deltas — every knob movement
resends the module's entire block.

| ID | Module | Confidence |
|---|---|---|
| `0x81` | NS | inferred from chain position; never seen |
| `0x82` | FX | confirmed as a block; 4 params in use |
| `0x83` | DS/OD | confirmed; 3 params (level/tone/gain) |
| `0x84` | AMP | **confirmed**; 6 params, model 17 |
| `0x85` | CAB | **confirmed** |
| `0x86` | EQ | confirmed as a block; identity inferred (models 0–2) |
| `0x87` | MOD | inferred from chain position; never seen |
| `0x88` | DELAY | confirmed; 7 params |
| `0x89` | REVERB | confirmed |

AMP and CAB are the strongest identifications: editing the `0x84` block
makes the pedal push an updated `0x85` block back as a `0x05` reply
(frames 4059 → 4061), which is the familiar amp-selects-cabinet linkage.

### Preset and setting commands

| ID | Payload | Meaning |
|---|---|---|
| `0x96` | 1 byte slot | read preset; replies `0x2A` then `0x29` |
| `0x97` | slot byte + 16-byte ASCII | set preset name |
| `0xA4`/`0xA5`/`0xA7` | 1× u16 | settings, meaning not yet pinned down |
| `0xA6` | 2× u16 flags | paired boolean setting |
| `0xAC` | 9-byte struct | unknown |
| `0xB4` | u16 selector | status poll, acked by `0x34` |
| `0xD1` | 18 bytes | assignment block (three 4-byte groups) |

Preset names observed: slot `0xC5` "Crazy Diamond", `0xC6` "Watercolors",
`0xC3` "Room335".

## What the capture does *not* answer

- **Preset read payloads.** The `0x2A`/`0x29` replies to `0x96` were
  all-zero in this capture, so the preset data layout is unconfirmed.
  Note also that `0x96 0xC6` replied `0x29 0xC5` and `0x96 0xC3` replied
  `0x29 0xC2` — an off-by-one between request and reply that needs
  explaining before slot arithmetic can be trusted.
- **Preset writes.** No store operation appears; `STORE_PATCH = 0xA8`
  and the 512-byte preset structure are still guesses.
- **Identify, volume, and system settings.** `0x10`, `0xA1`, `0xA2` were
  never sent.
- **Multi-report messages.** Nothing large enough to fragment.

Code paths covering these are marked *unverified* in
`src/mooer_ge150_mcp/protocol/commands.py`.

## Reproducing

```sh
tshark -r log/various_tests.pcapng -Y "usb.device_address==11 && usb.data_len>0" -x
```

The device address is capture-specific; find it with:

```sh
tshark -r log/various_tests.pcapng -Y "usb.idVendor==0x34db" \
       -T fields -e usb.device_address
```
