import os
import time
from pathlib import Path

import foldercleanup as fc


def make(tmp: Path):
    (tmp / "a.jpg").write_text("img")
    (tmp / "b.txt").write_text("same")
    (tmp / "sub").mkdir()
    (tmp / "sub" / "c.txt").write_text("same")
    (tmp / "empty").mkdir()
    (tmp / "noext").write_text("x")


def test_dry_run_changes_nothing(tmp_path):
    make(tmp_path)
    assert fc.main([str(tmp_path), "--sort", "--duplicates", "--empty-dirs"]) == 0
    assert (tmp_path / "a.jpg").exists() and (tmp_path / "empty").exists()


def test_apply_all(tmp_path):
    make(tmp_path)
    fc.main([str(tmp_path), "--sort", "--duplicates", "--empty-dirs", "--apply"])
    assert (tmp_path / "Images" / "a.jpg").exists()
    assert (tmp_path / "Other" / "noext").exists()
    assert not (tmp_path / "empty").exists()
    assert sum(1 for p in tmp_path.rglob("*.txt")) == 1  # duplicate removed


def test_collision_renamed(tmp_path):
    (tmp_path / "Images").mkdir()
    (tmp_path / "Images" / "a.jpg").write_text("old")
    (tmp_path / "a.jpg").write_text("new")
    fc.main([str(tmp_path), "--sort", "--apply"])
    assert (tmp_path / "Images" / "a (1).jpg").read_text() == "new"


def test_old(tmp_path):
    f = tmp_path / "old.txt"
    f.write_text("x")
    t = time.time() - 40 * 86400
    os.utime(f, (t, t))
    (tmp_path / "new.txt").write_text("y")
    fc.main([str(tmp_path), "--old", "30", "--apply"])
    assert (tmp_path / "_Old" / "old.txt").exists() and (tmp_path / "new.txt").exists()
