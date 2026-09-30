# Mooer GE150 Pro Li – MCP Server

MCP server for programmatic control of the Mooer GE150 Pro Li guitar effects pedal over USB.

## Quick Start

You need [Node.js](https://nodejs.org) and [`uv`](https://docs.astral.sh/uv/)
on your `PATH`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

The launcher in `bin/` uses `uv` to run the bundled Python server with its
dependencies. Nothing is installed globally.

### Claude Code (plugin)

```
/plugin marketplace add fingerskier/claude-plugins
/plugin install mooer-ge150@fingerskier-plugins
```

### Claude Desktop and other MCP clients

Clone this repository and point the client at the launcher:

```json
{
  "mcpServers": {
    "mooer-ge150": {
      "command": "node",
      "args": ["/path/to/Mooer_GE150_Pro_Li-IO/bin/mooer-ge150-mcp.mjs"]
    }
  }
}
```

### Linux: USB permissions

Without a udev rule the pedal can only be opened as root:

```bash
sudo cp udev/70-mooer-ge150.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
```

### Working on this repo

Opening the repo in Claude Code starts the server from your checkout, via
the root `.mcp.json`. Source edits take effect the next time it starts.

To run the server by hand:

```bash
node bin/mooer-ge150-mcp.mjs
```

> The package is not on npm or PyPI yet, so `npx mooer-ge150-mcp` and
> `pip install mooer-ge150-mcp` do not work. See [Publishing](#publishing).

## Tools

Presets are addressed as the pedal shows them, such as `"5A"`, or by
slot number 0–199. The connection opens on first use.

| Group | Tools |
|---|---|
| Read | `get_device_info`, `list_presets`, `get_preset`, `get_ctrl_config`, `list_user_models` |
| Live edits, not stored until saved | `select_preset`, `set_effect_param`, `toggle_effect` |
| Stored, no reboot | `save_preset`, `set_preset`, `copy_preset`, `swap_presets`, `import_preset`, `set_ctrl_config` |
| Stored, **reboots the pedal** | `put_preset`, `restore_backup`, and `byte_exact=true` on copy/swap |
| Files | `backup_all`, `export_preset` |
| Global | `set_system_settings`, `set_global_eq`, `set_expression_target` |
| User models | `upload_cab`, `upload_amp` (wire blobs, not `.gir`/`.wav`/`.gnr` files) |
| Connection | `disconnect` |

Every tool is annotated as read-only or not, and as destructive or not,
so an MCP client can auto-allow reads and ask before writes. Backups
are JSON files holding each preset byte for byte.

## Skills (Claude Code)

The plugin also ships skills, which Claude Code loads only when they are
needed:

| Skill | What it does |
|---|---|
| `/mooer-ge150:guide` | The operating guide: addresses, live vs stored, what reboots the pedal, backups, and what is known about each module's parameters. Claude loads it on its own when you talk about the pedal. |
| `/mooer-ge150:tone [preset] [goal]` | Build or refine a sound by ear: live edits you listen to, then a save. |
| `/mooer-ge150:organize [goal]` | Group, move and rename presets across banks, from a plan you approve. |
| `/mooer-ge150:hil-test <bank>` | The hardware test in `TESTING.md`, run on one scratch bank that is restored afterwards. It only runs when you invoke it. |

Skills that change presets back up first, to
`~/.claude/plugins/data/<plugin>/backups/`.

## Publishing

### To npm (enables `npx mooer-ge150-mcp`)

```bash
npm publish
```

### To PyPI (enables `uvx mooer-ge150-mcp` / `pip install`)

```bash
pip install build twine
python -m build
twine upload dist/*
```

## Documentation

- [`GLOSSARY.md`](GLOSSARY.md) — terminology, taken from the owner's manual.
  Read this first; the pedal's vocabulary is precise and easy to get wrong.
- [`log/CAPTURE_ANALYSIS.md`](log/CAPTURE_ANALYSIS.md) — the USB protocol as
  derived from real captures, with confirmed-vs-inferred grading.
- [`SPEC.md`](SPEC.md) — the original design spec, partly superseded by the above.
