"""The hil-test skill's bundled script: compare and subset backups."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "skills" / "hil-test" / "backups.py"


@pytest.fixture(scope="module")
def backups():
    spec = importlib.util.spec_from_file_location("hil_backups", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _backup(path: Path, changes: dict[int, str] | None = None) -> Path:
    """A backup_all-shaped file; *changes* swaps in other record bytes."""
    presets = []
    for slot in range(200):
        bank, position = divmod(slot, 4)
        presets.append({
            "slot": slot,
            "address": f"{bank + 1}{'ABCD'[position]}",
            "name": f"Preset {slot + 1}",
            "record": (changes or {}).get(slot, f"{slot + 1:02x}" + "00" * 244),
        })
    path.write_text(json.dumps({"format": "mooer-ge150-backup", "version": 1,
                                "presets": presets}))
    return path


def test_identical_backups(backups, tmp_path, capsys):
    a = _backup(tmp_path / "a.json")
    b = _backup(tmp_path / "b.json")
    assert backups.main(["compare", str(a), str(b)]) == 0
    assert "identical" in capsys.readouterr().out


def test_lists_the_presets_that_differ(backups, tmp_path, capsys):
    a = _backup(tmp_path / "a.json")
    b = _backup(tmp_path / "b.json", {18: "ff" * 245})
    assert backups.main(["compare", str(a), str(b)]) == 1
    out = capsys.readouterr().out
    assert "5C" in out
    assert "1 preset(s) differ" in out


def test_subset_keeps_one_bank(backups, tmp_path):
    a = _backup(tmp_path / "a.json")
    out = tmp_path / "bank5.json"
    assert backups.main(["subset", str(a), str(out), "5"]) == 0
    presets = json.loads(out.read_text())["presets"]
    assert [p["address"] for p in presets] == ["5A", "5B", "5C", "5D"]
    assert json.loads(out.read_text())["format"] == "mooer-ge150-backup"


def test_subset_never_overwrites(backups, tmp_path):
    a = _backup(tmp_path / "a.json")
    out = tmp_path / "bank5.json"
    out.write_text("keep")
    with pytest.raises(SystemExit):
        backups.main(["subset", str(a), str(out), "5"])
    assert out.read_text() == "keep"


@pytest.mark.parametrize("bank", ["0", "51", "x"])
def test_subset_rejects_bad_banks(backups, tmp_path, bank):
    a = _backup(tmp_path / "a.json")
    with pytest.raises(SystemExit):
        backups.main(["subset", str(a), str(tmp_path / "o.json"), bank])


def test_rejects_files_that_are_not_backups(backups, tmp_path):
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"format": "something-else"}))
    with pytest.raises(SystemExit):
        backups.main(["compare", str(other), str(other)])


def test_bad_usage_exits_2(backups):
    assert backups.main(["frobnicate"]) == 2
