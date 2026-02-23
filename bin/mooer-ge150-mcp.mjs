#!/usr/bin/env node

/**
 * Thin Node.js wrapper that launches the mooer-ge150-mcp Python MCP server.
 *
 * Resolution order:
 *   1. uvx  – ephemeral run from PyPI (recommended, no global install)
 *   2. pipx – similar to uvx but older tooling
 *   3. python -m mooer_ge150_mcp – direct invocation if already installed
 */

import { spawn, execFileSync } from "node:child_process";

const PYPI_PACKAGE = "mooer-ge150-mcp";
const MODULE_NAME = "mooer_ge150_mcp";

function which(cmd) {
  try {
    execFileSync("which", [cmd], { stdio: "pipe" });
    return true;
  } catch {
    return false;
  }
}

function findPython() {
  for (const candidate of ["python3", "python"]) {
    if (which(candidate)) return candidate;
  }
  return null;
}

function launch(command, args) {
  const child = spawn(command, args, {
    stdio: "inherit",
    env: { ...process.env },
  });

  child.on("error", (err) => {
    console.error(`Failed to start ${command}: ${err.message}`);
    process.exit(1);
  });

  child.on("exit", (code) => {
    process.exit(code ?? 1);
  });
}

// --- Resolution order ---

if (which("uvx")) {
  launch("uvx", [PYPI_PACKAGE]);
} else if (which("pipx")) {
  launch("pipx", ["run", PYPI_PACKAGE]);
} else {
  const python = findPython();
  if (python) {
    launch(python, ["-m", MODULE_NAME]);
  } else {
    console.error(
      "Error: Could not find uvx, pipx, or python3 on your PATH.\n" +
        "Install uv (https://docs.astral.sh/uv/) or Python 3.11+ to use this package."
    );
    process.exit(1);
  }
}
