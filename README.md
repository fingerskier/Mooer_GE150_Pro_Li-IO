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

## Features

* Connect to the pedal via USB and read/write system settings
* Manage all 200 preset slots (read, write, copy, swap, rename)
* Real-time effect parameter control
* Backup and restore presets (.mbf files)
* Import/export individual presets (.mo files)
* Upload impulse responses to user IR slots

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
