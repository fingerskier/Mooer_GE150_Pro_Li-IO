#!/usr/bin/env node

/**
 * Thin Node.js wrapper that launches the mooer-ge150-mcp Python MCP server.
 *
 * Resolution order:
 *   1. uvx --from <this bundle>  – runs the code shipped alongside this
 *      wrapper (the plugin bundle contains pyproject.toml + src/), with
 *      dependencies resolved by uv. Self-contained; no registry needed.
 *   2. mooer-ge150-mcp on PATH   – a uv-tool / pipx global install.
 *   3. uvx / pipx from PyPI      – once the package is published.
 *   4. python -m with PYTHONPATH – last resort, bundled source directly
 *      (requires mcp + hidapi importable in that python).
 */

import { spawn, execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const PYPI_PACKAGE = "mooer-ge150-mcp";
const MODULE_NAME = "mooer_ge150_mcp";

const bundleRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const hasBundledSource =
  existsSync(join(bundleRoot, "pyproject.toml")) &&
  existsSync(join(bundleRoot, "src", MODULE_NAME));

/** True if `cmd` exists and runs (probe with a terminating flag). */
function probe(cmd, args = ["--version"]) {
  try {
    execFileSync(cmd, args, { stdio: "pipe" });
    return true;
  } catch {
    return false;
  }
}

/** True if `cmd` resolves on PATH. Never runs it (a stdio server would
 * block a probe), so this is safe for the installed executable. */
function onPath(cmd) {
  const lookup = process.platform === "win32" ? "where" : "which";
  try {
    execFileSync(lookup, [cmd], { stdio: "pipe" });
    return true;
  } catch {
    return false;
  }
}

function launch(command, args, extraEnv = {}) {
  const child = spawn(command, args, {
    stdio: "inherit",
    env: { ...process.env, ...extraEnv },
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

if (hasBundledSource && probe("uvx")) {
  launch("uvx", ["--from", bundleRoot, PYPI_PACKAGE]);
} else if (onPath(PYPI_PACKAGE)) {
  launch(PYPI_PACKAGE, []);
} else if (probe("uvx")) {
  launch("uvx", [PYPI_PACKAGE]);
} else if (probe("pipx")) {
  launch("pipx", ["run", PYPI_PACKAGE]);
} else {
  const python = ["python3", "python"].find((p) => probe(p));
  if (python && hasBundledSource) {
    launch(python, ["-m", MODULE_NAME], {
      PYTHONPATH: join(bundleRoot, "src"),
    });
  } else if (python) {
    launch(python, ["-m", MODULE_NAME]);
  } else {
    console.error(
      "Error: could not find uv, a mooer-ge150-mcp install, or python on PATH.\n" +
        "Install uv (https://docs.astral.sh/uv/) to run this plugin."
    );
    process.exit(1);
  }
}
