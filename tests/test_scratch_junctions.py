"""The scratch copy must not follow a Windows junction.

`shutil.copytree(symlinks=True)` recreates symlinks rather than following them, and that
was taken to cover every kind of link. It does not cover junctions: `os.path.islink()`
returns False for one, so copytree walks through it and copies the target.

Auditing a repository whose `data/` is a junction therefore copies whatever it points at.
One here points at 13.8 GB: `suite-auditor audit` printed "copying the repository to a
scratch directory" and then nothing for fifteen minutes, and `--max-seconds 600` did not
stop it, because the budget is only consulted inside the mutant loop and the loop had not
begun. After the fix the same copy takes 0.2 seconds and holds 3.2 MB.

Skipped rather than recreated: a junction recreated inside the copy points back at the
user's own files, and a mutant run that wrote through it would modify them - which this
tool prints a promise not to do.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from suite_auditor.workspace import Scratch, is_junction  # noqa: E402


def make_junction(link: Path, target: Path) -> bool:
    """A directory junction, or False where this platform cannot make one.

    `mklink /J` needs no administrator rights, unlike a symlink, which is why junctions
    are what these repositories use for their data directories.
    """
    if sys.platform != "win32":
        return False
    out = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True, text=True,
    )
    return out.returncode == 0 and link.exists()


@pytest.fixture
def repo_with_a_junction(tmp_path: Path) -> tuple[Path, Path]:
    """A tiny repository whose `data/` is a junction to a tree outside it."""
    outside = tmp_path / "outside"
    (outside / "deep").mkdir(parents=True)
    for i in range(5):
        (outside / "deep" / f"big{i}.bin").write_bytes(b"x" * 4096)

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "thing.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='thing'\n", encoding="utf-8")
    if not make_junction(repo / "data", outside):
        pytest.skip("this platform cannot create a directory junction")
    return repo, outside


def test_a_junction_is_recognised_as_one(repo_with_a_junction) -> None:
    """The detection itself, because os.path.islink() does not answer this question."""
    repo, _ = repo_with_a_junction
    import os

    assert is_junction(repo / "data")
    assert not os.path.islink(repo / "data"), (
        "if islink ever starts returning True for junctions this test should be revisited, "
        "but the copy must not depend on that"
    )
    assert not is_junction(repo / "src")
    assert not is_junction(repo / "pyproject.toml")
    assert not is_junction(repo / "does_not_exist")


def test_the_copy_does_not_contain_what_the_junction_points_at(repo_with_a_junction) -> None:
    repo, outside = repo_with_a_junction
    with Scratch(repo, copies=1) as scratch:
        copy = scratch.copies[0]
        assert (copy / "src" / "thing.py").is_file(), "the repository itself must be copied"
        assert not (copy / "data" / "deep").exists(), (
            "the junction's target was copied into the scratch tree"
        )
        files = [f for f in copy.rglob("*") if f.is_file()]
        assert not any(f.name.startswith("big") for f in files), (
            f"the copy holds {len(files)} files including the linked tree's"
        )


def test_the_skipped_junctions_are_reported(repo_with_a_junction) -> None:
    """The copy is not identical to the repository, so the caller must be able to say so.

    A suite that reads through the skipped path fails, and it should fail for a stated
    reason rather than a puzzling one.
    """
    repo, _ = repo_with_a_junction
    with Scratch(repo, copies=1) as scratch:
        assert [p.name for p in scratch.skipped_junctions] == ["data"]


def test_several_copies_all_omit_it(repo_with_a_junction) -> None:
    """The second and later copies are made from the first, not from the repository.

    They inherit the omission, and the reported list must not grow once per copy.
    """
    repo, _ = repo_with_a_junction
    with Scratch(repo, copies=3) as scratch:
        assert len(scratch.copies) == 3
        for copy in scratch.copies:
            assert not (copy / "data" / "deep").exists()
        assert [p.name for p in scratch.skipped_junctions] == ["data"]
