# Mooer GE150 Pro Li – MCP Server

MCP server for programmatic control of the Mooer GE150 Pro Li guitar effects pedal over USB.

## Quick Start

### Run via npx (no install required)

```bash
npx mooer-ge150-mcp
```

This requires **one** of the following on your `PATH`:

| Tool | Install |
|------|---------|
| `uvx` (recommended) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| `pipx` | `python3 -m pip install --user pipx` |
| `python3` with the package already installed | `pip install mooer-ge150-mcp` |

### Claude Desktop configuration

Add this to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mooer-ge150": {
      "command": "npx",
      "args": ["-y", "mooer-ge150-mcp"]
    }
  }
}
```

### Install from PyPI

```bash
pip install mooer-ge150-mcp
mooer-ge150-mcp
```

### Run from source

```bash
pip install -e .
mooer-ge150-mcp
```

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

