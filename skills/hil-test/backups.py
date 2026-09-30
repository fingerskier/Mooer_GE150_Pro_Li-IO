#!/usr/bin/env python3
"""Compare and subset backups written by the mooer-ge150 backup_all tool.

Standard library only, so it runs anywhere Python 3 does.

  backups.py compare BEFORE AFTER     list presets whose bytes differ
  backups.py subset BACKUP OUT BANK   write OUT holding only bank BANK
                                      (e.g. 5 -> 5A-5D), for restore_backup;
                                      refuses a bank with an empty preset

Exit status: 0 on success or identical backups, 1 if they differ, 2 on
bad usage or input.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

FORMAT = "mooer-ge150-backup"


def _load(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{path}: cannot read: {exc}")
    if data.get("format") != FORMAT:
        raise SystemExit(f"{path}: not a backup_all file")
    return data


def compare(before: str, after: str) -> int:
    old = {e["slot"]: e for e in _load(before)["presets"]}
    new = {e["slot"]: e for e in _load(after)["presets"]}
    differing = [
        slot for slot in sorted(set(old) | set(new))
        if old.get(slot, {}).get("record") != new.get(slot, {}).get("record")
    ]
    if not differing:
        print(f"identical: all {len(old)} presets match byte for byte")
        return 0
    for slot in differing:
        a, b = old.get(slot), new.get(slot)
        address = (a or b)["address"]
        print(f"{address}: {a['name'] if a else '(missing)'!r} -> "
              f"{b['name'] if b else '(missing)'!r}")
    print(f"{len(differing)} preset(s) differ")
    return 1


def subset(backup: str, out: str, bank: str) -> int:
    data = _load(backup)
    try:
        number = int(bank)
    except ValueError:
        raise SystemExit(f"bank must be a number 1-50, got {bank!r}")
    if not 1 <= number <= 50:
        raise SystemExit(f"bank must be 1-50, got {number}")
    wanted = {f"{number}{position}" for position in "ABCD"}
    presets = [e for e in data["presets"] if e["address"] in wanted]
    if len(presets) != 4:
        raise SystemExit(f"{backup}: bank {number} is incomplete")
    # restore_backup never writes an empty backup entry over a named
    # preset, so a slot that starts empty could not be emptied again after
    # the test names it: the restore would silently leave test data behind.
    empty = [e["address"] for e in presets if not e["name"].strip()]
    if empty:
        raise SystemExit(
            f"bank {number} has empty presets ({', '.join(empty)}), which "
            "restore_backup could not restore after the test; choose a "
            "bank whose four presets all have names"
        )
    target = Path(out)
    if target.exists():
        raise SystemExit(f"{out} already exists")
    target.write_text(json.dumps({**data, "presets": presets}, indent=1),
                      encoding="utf-8")
    names = ", ".join(f"{e['address']} {e['name']!r}" for e in presets)
    print(f"wrote {out}: {names}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "compare":
        return compare(argv[1], argv[2])
    if len(argv) == 4 and argv[0] == "subset":
        return subset(argv[1], argv[2], argv[3])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
