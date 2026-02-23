# Mooer GE150 Pro Li — Communication Protocol & MCP Server Specification

## Table of Contents

1. [Overview](#overview)
2. [Device Identification](#device-identification)
3. [USB Transport Layer](#usb-transport-layer)
4. [Message Framing Protocol](#message-framing-protocol)
5. [Command Groups](#command-groups)
6. [Preset Data Structure](#preset-data-structure)
7. [Effect Module Formats](#effect-module-formats)
8. [File Formats](#file-formats)
9. [MIDI Interface](#midi-interface)
10. [MCP Server Design](#mcp-server-design)
11. [MCP Tools](#mcp-tools)
12. [MCP Resources](#mcp-resources)
13. [MCP Prompts](#mcp-prompts)
14. [Implementation Roadmap](#implementation-roadmap)
15. [References](#references)

---

## 1. Overview

The Mooer GE150 Pro Li is a battery-powered guitar multi-effects pedal featuring 200 presets, 151 effects, 55 amp models, 26 cabinet simulations, 10 IR slots, and 4 footswitches. It connects to a host computer via USB-C (audio + MIDI) and supports Bluetooth 5.0.

This specification documents:

- The binary USB protocol used between the pedal and host software (Mooer Studio / GE Editor)
- The structure of preset and settings data
- A proposed MCP (Model Context Protocol) server that exposes pedal control to AI agents

The protocol details are derived from the [MooerManager](https://github.com/ThijsWithaar/MooerManager) reverse-engineering project (targeting the GE-200, which shares the same protocol family) and community research on `.mo` / `.gnr` file formats.

---

## 2. Device Identification

| Property       | Value                              |
| -------------- | ---------------------------------- |
| USB Vendor ID  | `0x0483` (STMicroelectronics)      |
| USB Product ID | `0x5703`                           |
| Chipset        | STM32 microcontroller              |
| Connection     | USB-C (USB 2.0 Full Speed)         |
| USB Class      | Vendor-specific + Audio + MIDI     |

### Linux udev Rule

```
SUBSYSTEM=="usb", ATTR{idVendor}=="0483", ATTR{idProduct}=="5703", GROUP="plugdev", MODE="0666"
```

---

## 3. USB Transport Layer

The pedal exposes multiple USB interfaces. Communication for settings/patch management uses vendor-specific bulk and interrupt endpoints:

| Endpoint   | Direction | Transfer Type | Address |
| ---------- | --------- | ------------- | ------- |
| TX Bulk    | OUT       | Bulk          | `0x01`  |
| TX Intr    | OUT       | Interrupt     | `0x02`  |
| RX Intr    | IN        | Interrupt     | `0x81`  |

- **Bulk transfers** (endpoint `0x01`) are used for large data like preset uploads, IR/cab uploads, and firmware
- **Interrupt transfers** (endpoints `0x02` / `0x81`) are used for real-time parameter changes, status polling, and command/response exchanges
- USB hotplug detection is supported via libusb

### Connection Sequence

1. Open USB device by vendor/product ID (`0x0483:0x5703`)
2. Claim the vendor-specific interface
3. Send an **Identify** command (`0x10`) to confirm the device and retrieve model/firmware info
4. Begin issuing commands and listening for responses on the interrupt endpoints

---

## 4. Message Framing Protocol

All messages between host and pedal use a consistent binary frame:

```
+---------+---------+---------+------------------+----------+
| Preamble|  Size   | Command |     Payload      | Checksum |
| 2 bytes | 2 bytes | 1 byte  |  variable length |  2 bytes |
+---------+---------+---------+------------------+----------+
```

| Field      | Size    | Encoding       | Description                                       |
| ---------- | ------- | -------------- | ------------------------------------------------- |
| Preamble   | 2 bytes | `0xAA 0x55`    | Magic bytes marking start of frame                |
| Size       | 2 bytes | Little-endian  | Total payload size (command byte + payload data)   |
| Command    | 1 byte  | Unsigned       | Command group identifier (see section 5)          |
| Payload    | N bytes | Varies         | Command-specific data                             |
| Checksum   | 2 bytes | Little-endian  | Inverted CRC-16 (`~crc`) over command + payload   |

### CRC-16 Calculation

The checksum uses a CRC-16 algorithm with a 256-entry lookup table. The final value is bitwise-inverted (`~crc`). The CRC covers all bytes from the command byte through the end of the payload (does not include preamble or size fields).

```python
def crc16(data: bytes) -> int:
    """CRC-16 with Mooer's lookup table."""
    crc = 0x0000
    for byte in data:
        index = (crc ^ byte) & 0xFF
        crc = (crc >> 8) ^ CRC_TABLE[index]
    return ~crc & 0xFFFF
```

The full 256-entry CRC lookup table is defined in the MooerManager source (`MooerParser.cc`).

### Example: Building a Frame

```python
def build_frame(command: int, payload: bytes = b"") -> bytes:
    preamble = b"\xAA\x55"
    body = bytes([command]) + payload
    size = len(body).to_bytes(2, "little")
    checksum = crc16(body).to_bytes(2, "little")
    return preamble + size + body + checksum
```

---

## 5. Command Groups

Each command is identified by a single-byte group ID. The same IDs are used for both requests (host-to-device) and responses (device-to-host).

| ID     | Name              | Description                                  |
| ------ | ----------------- | -------------------------------------------- |
| `0x10` | Identify          | Device identification / handshake            |
| `0x82` | Menu              | Menu navigation state                        |
| `0x83` | Preset            | Preset data read/write                       |
| `0x84` | PedalAssign (?)   | Pedal assignment (tentative)                 |
| `0x85` | CabModels         | List available cabinet models                |
| `0x89` | FootSwitch        | Footswitch configuration                     |
| `0x90` | FX                | FX/Compressor module parameters              |
| `0x91` | DS_OD             | Distortion/Overdrive module parameters       |
| `0x93` | AMP               | Amp model parameters                         |
| `0x94` | CAB               | Cabinet simulation parameters                |
| `0x95` | NS_GATE           | Noise gate parameters                        |
| `0x96` | EQ                | Equalizer parameters                         |
| `0x97` | MOD               | Modulation effect parameters                 |
| `0x98` | DELAY             | Delay effect parameters                      |
| `0x99` | REVERB            | Reverb effect parameters                     |
| `0xA1` | System            | System-level settings (global config)        |
| `0xA2` | Volume            | Master volume control                        |
| `0xA3` | PedalAssignment   | Expression pedal assignment                  |
| `0xA4` | PatchAlternate    | Alternate patch selection                    |
| `0xA5` | PatchSetting      | Patch-level settings                         |
| `0xA6` | ActivePatch       | Get/set the currently active patch number    |
| `0xA8` | StorePatch        | Save current edits to a patch slot           |
| `0xA9` | ActivePatchSetting| Settings for the currently active patch      |
| `0xE1` | CabinetUpload     | Upload custom IR / cabinet file              |
| `0xE2` | AmpUpload         | Upload custom amp model                      |
| `0xE3` | AmpModels         | List available amp models                    |

### Command Sub-operations

Most command groups support sub-operation bytes within the payload to differentiate between read, write, and list operations. The exact sub-operation encoding varies by command group and requires further reverse-engineering for complete documentation.

---

## 6. Preset Data Structure

Each preset occupies **0x200 bytes (512 bytes)** in the device's internal format:

```
+------------------+--------+------+----+----+-----+-----+----+----+-----+-------+--------+
| Effect Order     | Size   | Name | FX | OD | AMP | CAB | NS | EQ | MOD | DELAY | REVERB |
| 10 bytes         | 2 bytes| 14 B |    |    |     |     |    |    |     |       |        |
+------------------+--------+------+----+----+-----+-----+----+----+-----+-------+--------+
```

| Field        | Offset | Size     | Description                                    |
| ------------ | ------ | -------- | ---------------------------------------------- |
| Effect Order | 0x00   | 10 bytes | Array defining signal chain order               |
| Size         | 0x0A   | 2 bytes  | Big-endian, total preset data size              |
| Name         | 0x0C   | 14 bytes | ASCII preset name, null-padded                  |
| FX           | 0x1A   | 13 bytes | FX/Compressor module (see section 7)            |
| OD           | 0x27   | 11 bytes | Distortion/Overdrive module                     |
| AMP          | 0x32   | 17 bytes | Amp model module                                |
| CAB          | 0x43   | 13 bytes | Cabinet simulation module                       |
| NS           | 0x50   | 11 bytes | Noise gate module                               |
| EQ           | 0x5B   | 23 bytes | Equalizer module                                |
| MOD          | 0x72   | 15 bytes | Modulation module                               |
| DELAY        | 0x81   | 17 bytes | Delay module                                    |
| REVERB       | 0x92   | 13 bytes | Reverb module                                   |

### .mo File Byte Offsets (Alternate Mapping)

For `.mo` preset files (0x800 bytes), the preset data begins at byte offset 0x200, yielding these absolute offsets:

| Field        | Absolute Offset | Size     |
| ------------ | --------------- | -------- |
| Preset Name  | 524 (0x20C)     | 16 bytes |
| FX/COMP      | 541 (0x21D)     | 8 bytes  |
| DS/OD        | 549 (0x225)     | 8 bytes  |
| AMP          | 557 (0x22D)     | 8 bytes  |
| CAB          | 565 (0x235)     | 8 bytes  |
| NS GATE      | 573 (0x23D)     | 8 bytes  |
| EQ           | 581 (0x245)     | 8 bytes  |
| MOD          | 589 (0x24D)     | 8 bytes  |
| DELAY        | 597 (0x255)     | 8 bytes  |
| REVERB       | 605 (0x25D)     | 6 bytes  |

> **Note**: The byte-level mappings may differ slightly between GE-200 and GE150 Pro Li. Validation against real device captures is required.

---

## 7. Effect Module Formats

Each effect module within a preset has a consistent structure: a 1-byte header followed by parameter bytes. All parameter values are unsigned 8-bit integers (0–255) unless noted otherwise.

### FX / Compressor (13 bytes)

| Byte | Parameter |
| ---- | --------- |
| 0    | Header    |
| 1    | Enabled   |
| 2    | Type      |
| 3    | Q         |
| 4    | Position  |
| 5    | Peak      |
| 6    | Level     |
| 7–12 | Reserved  |

### Distortion / Overdrive (11 bytes)

| Byte | Parameter |
| ---- | --------- |
| 0    | Header    |
| 1    | Enabled   |
| 2    | Type      |
| 3    | Volume    |
| 4    | Tone      |
| 5    | Gain      |
| 6–10 | Reserved  |

### AMP (17 bytes)

| Byte | Parameter |
| ---- | --------- |
| 0    | Header    |
| 1    | Enabled   |
| 2    | Type      |
| 3    | Gain      |
| 4    | Bass      |
| 5    | Mid       |
| 6    | Treble    |
| 7    | Presence  |
| 8    | Master    |
| 9–16 | Reserved  |

### CAB (13 bytes)

| Byte | Parameter |
| ---- | --------- |
| 0    | Header    |
| 1    | Enabled   |
| 2    | Type      |
| 3    | Mic       |
| 4    | Center    |
| 5    | Distance  |
| 6    | Tube      |
| 7–12 | Reserved  |

### Noise Gate (11 bytes)

| Byte | Parameter |
| ---- | --------- |
| 0    | Header    |
| 1    | Enabled   |
| 2    | Type      |
| 3    | Attack    |
| 4    | Release   |
| 5    | Threshold |
| 6–10 | Reserved  |

### EQ (23 bytes)

| Byte  | Parameter          |
| ----- | ------------------ |
| 0     | Header             |
| 1     | Enabled            |
| 2     | Type               |
| 3–8   | Band 1–6 levels    |
| 9–14  | Band 1-6 (unknown) |
| 15–22 | Reserved           |

### Modulation (15 bytes)

| Byte | Parameter |
| ---- | --------- |
| 0    | Header    |
| 1    | Enabled   |
| 2    | Type      |
| 3    | Rate      |
| 4    | Level     |
| 5    | Depth     |
| 6    | Param 4   |
| 7    | Param 5   |
| 8–14 | Reserved  |

### Delay (17 bytes)

| Byte | Parameter   |
| ---- | ----------- |
| 0    | Header      |
| 1    | Enabled     |
| 2    | Type        |
| 3    | Level       |
| 4    | Feedback    |
| 5–6  | Time (16-bit, see note) |
| 7    | Subdivision |
| 8    | Param 5     |
| 9    | Param 6     |
| 10–16| Reserved    |

> **Note**: Delay time is encoded as a 16-bit value. In `.mo` files, it appears at absolute offsets 862–863.

### Reverb (13 bytes)

| Byte | Parameter  |
| ---- | ---------- |
| 0    | Header     |
| 1    | Enabled    |
| 2    | Type       |
| 3    | Pre-delay  |
| 4    | Level      |
| 5    | Decay      |
| 6    | Tone       |
| 7–12 | Reserved  |

---

## 8. File Formats

### .MO Format (Single Preset) — 0x800 bytes (2048)

```
+----------------+------------------+------------------+
| Junk / Header  | Preset (padded)  |    Padding       |
| 0x200 bytes    | 0x200 bytes      |   0x400 bytes    |
+----------------+------------------+------------------+
```

- Bytes 0x000–0x1FF: Header / junk data
- Bytes 0x200–0x3FF: Preset data (512 bytes, structure per section 6)
- Bytes 0x400–0x7FF: Zero padding

### .GNR Format (IR / Cabinet)

```
+------------------+------------+-------------------+
| Magic ("mooerge")| Info Size  | Info + Data       |
| 8 bytes          | 4 bytes LE | Variable          |
+------------------+------------+-------------------+
```

- 8-byte ASCII header: `mooerge\0`
- 4-byte little-endian info section size
- Info section followed by raw IR/cabinet data

### .MBF Format (Full Backup)

```
+--------------+------------+---------+---------------------+
| Manufacturer | Model Name | Version | Preset Entries (x199)|
| 8 bytes      | 32 bytes   | varies  | 0x222 bytes each    |
+--------------+------------+---------+---------------------+
```

- 8-byte manufacturer string
- 32-byte model name
- Version fields
- 199 preset entries, each 0x222 bytes (546 bytes)

---

## 9. MIDI Interface

The GE150 Pro Li also presents a standard USB MIDI interface. This is separate from the vendor-specific USB protocol above, but can be used for real-time performance control.

### Supported MIDI Messages

| Message Type    | Usage                                      |
| --------------- | ------------------------------------------ |
| Program Change  | Switch active preset (0–199)               |
| Control Change  | Adjust parameters (volume, reverb, etc.)   |
| SysEx           | Arbitrary command passthrough               |

### MIDI Monitoring (Linux)

```bash
# List MIDI ports
aplaymidi -l

# Monitor incoming MIDI
aseqdump -p 129:0
```

### MIDI in Context of MCP

The MIDI interface is useful for:
- Real-time preset switching during performance
- Expression pedal mapping
- DAW integration and automation

The vendor USB protocol (section 4) is preferred for:
- Full preset read/write
- System settings management
- Bulk operations (backup/restore, IR uploads)

---

## 10. MCP Server Design

### Architecture

The MCP server sits between an AI agent (Claude) and the Mooer pedal, translating high-level intents into the vendor USB protocol.

```
+--------+       JSON-RPC        +------------+       USB        +-----------+
| Claude | <------ stdio ------> | MCP Server | <--- libusb ---> | GE150 Pro |
| (Host) |   tools / resources   |  (Python)  |   0483:5703      |    Li     |
+--------+                      +------------+                  +-----------+
```

### Technology Stack

| Component         | Choice                        | Rationale                           |
| ----------------- | ----------------------------- | ----------------------------------- |
| Language          | Python 3.11+                  | Rich USB/MIDI library ecosystem     |
| MCP SDK           | `mcp` (official Python SDK)   | First-class FastMCP support         |
| USB Communication | `pyusb` + `libusb1`           | Cross-platform USB access           |
| MIDI (optional)   | `python-rtmidi` or `mido`     | Standard MIDI for real-time control |
| Transport         | stdio (local) or SSE (remote) | stdio for Claude Desktop / CLI      |

### Project Structure

```
mooer-ge150-mcp/
  src/
    server.py              # MCP server entry point & tool definitions
    protocol/
      __init__.py
      framing.py            # Message frame builder & CRC-16
      commands.py            # Command group constants & builders
      parser.py              # Response parsing
    transport/
      __init__.py
      usb_connection.py      # pyusb device open/read/write
      midi_connection.py     # Optional MIDI transport
    models/
      __init__.py
      preset.py              # Preset data model (serialize/deserialize)
      effects.py             # Effect module data models
      system.py              # System settings model
    utils/
      crc.py                 # CRC-16 lookup table & function
  tests/
    test_framing.py
    test_preset.py
    test_commands.py
  pyproject.toml
  README.md
```

### Configuration

```json
{
  "mcpServers": {
    "mooer-ge150": {
      "command": "python",
      "args": ["-m", "mooer_ge150_mcp.server"],
      "env": {
        "MOOER_USB_VENDOR_ID": "0x0483",
        "MOOER_USB_PRODUCT_ID": "0x5703"
      }
    }
  }
}
```

---

## 11. MCP Tools

Tools are executable actions the AI agent can invoke. They are organized into functional groups.

### Connection Management

#### `connect`
Establish a USB connection to the pedal.
- **Input**: `{}` (no parameters — auto-discovers by vendor/product ID)
- **Output**: `{ connected: bool, model: string, firmware: string }`
- **Annotation**: `readOnlyHint: false, openWorldHint: false`

#### `disconnect`
Close the USB connection.
- **Input**: `{}`
- **Output**: `{ disconnected: bool }`

#### `get_device_info`
Retrieve device identification (model, firmware version, serial).
- **Input**: `{}`
- **Output**: `{ model: string, firmware: string, serial: string }`
- **Annotation**: `readOnlyHint: true`

### Preset Management

#### `list_presets`
List all 200 preset slots with names.
- **Input**: `{ range?: { start: int, end: int } }`
- **Output**: `{ presets: [{ slot: int, name: string, empty: bool }] }`
- **Annotation**: `readOnlyHint: true`

#### `get_preset`
Read the full preset data for a specific slot.
- **Input**: `{ slot: int }` (0–199)
- **Output**: `{ slot: int, name: string, effects: { fx: {...}, od: {...}, amp: {...}, cab: {...}, ns: {...}, eq: {...}, mod: {...}, delay: {...}, reverb: {...} }, effectOrder: int[] }`
- **Annotation**: `readOnlyHint: true`

#### `set_preset`
Write a complete preset to a slot. Sends the full 0x200-byte preset structure.
- **Input**: `{ slot: int, name?: string, effects?: { ... partial effect overrides ... } }`
- **Output**: `{ stored: bool, slot: int }`
- **Annotation**: `readOnlyHint: false, destructiveHint: true`

#### `select_preset`
Switch the pedal's active preset.
- **Input**: `{ slot: int }`
- **Output**: `{ active: int }`
- **Annotation**: `readOnlyHint: false`

#### `copy_preset`
Copy a preset from one slot to another.
- **Input**: `{ from_slot: int, to_slot: int }`
- **Output**: `{ copied: bool }`
- **Annotation**: `readOnlyHint: false, destructiveHint: true`

#### `swap_presets`
Swap two preset slots.
- **Input**: `{ slot_a: int, slot_b: int }`
- **Output**: `{ swapped: bool }`
- **Annotation**: `readOnlyHint: false`

### Effect Parameter Control

#### `set_effect_param`
Modify a single parameter on the currently active preset in real time.
- **Input**: `{ module: enum("fx"|"od"|"amp"|"cab"|"ns"|"eq"|"mod"|"delay"|"reverb"), param: string, value: int }`
- **Output**: `{ module: string, param: string, value: int }`
- **Annotation**: `readOnlyHint: false`

#### `toggle_effect`
Enable or disable an effect module.
- **Input**: `{ module: string, enabled: bool }`
- **Output**: `{ module: string, enabled: bool }`
- **Annotation**: `readOnlyHint: false`

#### `set_effect_order`
Change the signal chain order.
- **Input**: `{ order: string[] }` (e.g., `["fx", "od", "amp", "cab", "ns", "eq", "mod", "delay", "reverb"]`)
- **Output**: `{ order: string[] }`
- **Annotation**: `readOnlyHint: false`

### System Settings

#### `get_system_settings`
Read global system settings (global EQ, display brightness, auto-off, etc.).
- **Input**: `{}`
- **Output**: `{ settings: { ... } }`
- **Annotation**: `readOnlyHint: true`

#### `set_system_setting`
Modify a global system setting.
- **Input**: `{ setting: string, value: any }`
- **Output**: `{ setting: string, value: any }`
- **Annotation**: `readOnlyHint: false`

#### `get_volume`
Read master volume level.
- **Input**: `{}`
- **Output**: `{ volume: int }`
- **Annotation**: `readOnlyHint: true`

#### `set_volume`
Set master volume level.
- **Input**: `{ volume: int }` (0–100)
- **Output**: `{ volume: int }`
- **Annotation**: `readOnlyHint: false`

### Backup & Restore

#### `backup_all`
Download all presets as a `.mbf` backup file.
- **Input**: `{ output_path: string }`
- **Output**: `{ path: string, preset_count: int }`
- **Annotation**: `readOnlyHint: true`

#### `restore_backup`
Restore presets from a `.mbf` backup file.
- **Input**: `{ input_path: string, overwrite?: bool }`
- **Output**: `{ restored: bool, preset_count: int }`
- **Annotation**: `readOnlyHint: false, destructiveHint: true`

#### `export_preset`
Export a single preset to a `.mo` file.
- **Input**: `{ slot: int, output_path: string }`
- **Output**: `{ path: string }`
- **Annotation**: `readOnlyHint: true`

#### `import_preset`
Import a preset from a `.mo` file into a slot.
- **Input**: `{ input_path: string, slot: int }`
- **Output**: `{ imported: bool, slot: int, name: string }`
- **Annotation**: `readOnlyHint: false, destructiveHint: true`

### IR / Cabinet Management

#### `list_ir_slots`
List the 10 user IR slots and their contents.
- **Input**: `{}`
- **Output**: `{ slots: [{ slot: int, name: string, empty: bool }] }`
- **Annotation**: `readOnlyHint: true`

#### `upload_ir`
Upload a WAV/GNR impulse response to an IR slot.
- **Input**: `{ slot: int, file_path: string, name?: string }`
- **Output**: `{ uploaded: bool, slot: int }`
- **Annotation**: `readOnlyHint: false, destructiveHint: true`

---

## 12. MCP Resources

Resources provide read-only context that the AI agent can reference.

### Device State

| URI                              | Description                          |
| -------------------------------- | ------------------------------------ |
| `mooer://device/info`            | Device model, firmware, connection   |
| `mooer://device/status`          | Connection state, active preset, battery |

### Preset Data

| URI Template                      | Description                         |
| --------------------------------- | ----------------------------------- |
| `mooer://preset/{slot}`           | Full preset data for slot 0–199     |
| `mooer://preset/active`           | Currently active preset             |
| `mooer://presets/list`            | Summary list of all preset names    |

### Effect Catalogs

| URI                              | Description                          |
| -------------------------------- | ------------------------------------ |
| `mooer://catalog/amps`           | List of all 55 amp model names/IDs  |
| `mooer://catalog/cabs`           | List of all 26 cabinet sim names/IDs|
| `mooer://catalog/effects`        | List of all 151 effects by category |
| `mooer://catalog/ir-slots`       | User IR slot status                 |

### System

| URI                              | Description                          |
| -------------------------------- | ------------------------------------ |
| `mooer://system/settings`        | Global system settings               |
| `mooer://system/footswitch`      | Footswitch assignments               |
| `mooer://system/pedal-assign`    | Expression pedal assignments         |

---

## 13. MCP Prompts

Reusable prompt templates for common workflows.

### `create-tone`
Guide the AI to build a preset for a specific musical style or reference tone.

```
Create a preset for {genre/artist/song} style.
Consider:
- Amp model selection for the right gain structure
- Appropriate drive/overdrive settings
- EQ shaping for the style
- Modulation, delay, and reverb to taste
- Noise gate threshold based on gain level
```

### `optimize-preset`
Analyze an existing preset and suggest improvements.

```
Read preset {slot} and analyze its settings.
Suggest improvements for {goal: e.g., "less noise", "more clarity", "heavier tone"}.
```

### `batch-organize`
Help organize and rename presets across the 200 slots.

```
Read all presets. Group by style/genre.
Suggest a logical ordering and naming convention.
Optionally reorder presets on the device.
```

---

## 14. Implementation Roadmap

### Phase 1: Protocol Foundation
- [ ] Implement CRC-16 calculation with Mooer's lookup table
- [ ] Implement message frame builder/parser
- [ ] Implement USB connection via pyusb (open, claim, read, write)
- [ ] Send Identify command and parse response
- [ ] Verify communication with a real GE150 Pro Li device

### Phase 2: Preset Read/Write
- [ ] Implement preset data model (serialize/deserialize 0x200-byte structure)
- [ ] Read active preset from device
- [ ] Read preset from specific slot
- [ ] Write preset to slot (StorePatch command)
- [ ] Select active preset (ActivePatch command)
- [ ] Implement .mo file import/export

### Phase 3: Real-Time Parameter Control
- [ ] Send individual effect parameter changes (FX through REVERB commands)
- [ ] Toggle effect modules on/off
- [ ] Change effect order
- [ ] Volume control
- [ ] System settings read/write

### Phase 4: MCP Server
- [ ] Set up FastMCP server with stdio transport
- [ ] Expose connection tools (connect, disconnect, device_info)
- [ ] Expose preset management tools
- [ ] Expose real-time parameter tools
- [ ] Expose resources for device state and catalogs
- [ ] Add prompt templates

### Phase 5: Advanced Features
- [ ] Full backup/restore (.mbf format)
- [ ] IR/cabinet upload (.gnr + WAV conversion)
- [ ] Batch preset operations
- [ ] MIDI CC bridge for real-time performance integration
- [ ] Bluetooth connection support (if protocol is documented)

### Phase 6: Polish
- [ ] Error handling and USB reconnection logic
- [ ] Input validation for all parameter ranges
- [ ] Comprehensive test suite with mock USB device
- [ ] Documentation and usage examples
- [ ] Package for PyPI distribution

---

## 15. References

| Resource | URL |
| -------- | --- |
| MooerManager (protocol RE) | https://github.com/ThijsWithaar/MooerManager |
| Model Context Protocol | https://modelcontextprotocol.io |
| MCP Python SDK | https://github.com/modelcontextprotocol/python-sdk |
| pyusb | https://github.com/pyusb/pyusb |
| python-rtmidi | https://github.com/SpotlightKid/python-rtmidi |
| Mooer GE150 Pro Li Product Page | https://www.mooeraudio.com |
| GE200-to-GE150 Preset Converter | Community tools (various GitHub repos) |
| mcp-server-midi (reference) | https://github.com/sandst1/mcp-server-midi |
| elektron-mcp (reference) | https://github.com/zerubeus/elektron-mcp |

---

*This specification is a living document. Protocol details marked as tentative or approximate should be validated against real device captures. Contributions and corrections are welcome.*
