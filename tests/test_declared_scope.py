"""Mutation targets come from what the project says it ships.

`find_targets` decided "shipped package code" by looking for an `__init__.py`, which is
true of every package in the tree - including vendored copies of other projects.
Auditing this repository reported 329 functions with 90 reached, because `targets/` holds
copies of other projects used as audit fixtures: 222 of those functions were other
people's code, and `covered_fraction` and `caught_share` - the two headline figures -
were diluted by them. With the declared scope it reports 107 functions, 92 reached.

Measured before changing the published census: of the 110 audited projects in the
1,000-project census, exactly one moves (charset-normalizer, 139 -> 136 functions) and 85
declare nothing at all, so the old rule still governs those and the rest of the census
stands.

A scope that cannot be determined must not narrow to nothing. `declared_packages` returns
None rather than an empty list whenever the declaration is absent or unreadable, because
a package reported as having no functions reads as a package with no gaps.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from suite_auditor.mutate import declared_packages, find_targets, in_scope  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def write(repo: Path, relative: str, text: str = "") -> Path:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def package(repo: Path, where: str) -> None:
    write(repo, f"{where}/__init__.py")
    write(repo, f"{where}/thing.py", "def f():\n    return 1\n")


class TestDeclaredPackages:
    def test_hatch(self, tmp_path: Path) -> None:
        package(tmp_path, "src/mine")
        write(tmp_path, "pyproject.toml",
              '[tool.hatch.build.targets.wheel]\npackages = ["src/mine"]\n')
        assert declared_packages(tmp_path) == [tmp_path / "src/mine"]

    def test_hatch_only_include(self, tmp_path: Path) -> None:
        package(tmp_path, "src/mine")
        write(tmp_path, "pyproject.toml",
              '[tool.hatch.build.targets.wheel]\nonly-include = ["src/mine"]\n')
        assert declared_packages(tmp_path) == [tmp_path / "src/mine"]

    def test_setuptools_find_where(self, tmp_path: Path) -> None:
        package(tmp_path, "src/mine")
        write(tmp_path, "pyproject.toml",
              '[tool.setuptools.packages.find]\nwhere = ["src"]\n')
        assert declared_packages(tmp_path) == [tmp_path / "src"]

    def test_setuptools_package_dir(self, tmp_path: Path) -> None:
        package(tmp_path, "lib/mine")
        write(tmp_path, "pyproject.toml",
              '[tool.setuptools]\npackages = ["mine"]\n\n'
              '[tool.setuptools.package-dir]\n"" = "lib"\n')
        assert declared_packages(tmp_path) == [tmp_path / "lib/mine", tmp_path / "lib"]

    def test_poetry(self, tmp_path: Path) -> None:
        package(tmp_path, "src/mine")
        write(tmp_path, "pyproject.toml",
              '[tool.poetry]\nname = "mine"\npackages = [{include = "mine", from = "src"}]\n')
        assert declared_packages(tmp_path) == [tmp_path / "src/mine"]

    def test_flit(self, tmp_path: Path) -> None:
        package(tmp_path, "mine")
        write(tmp_path, "pyproject.toml", '[tool.flit.module]\nname = "mine"\n')
        assert declared_packages(tmp_path) == [tmp_path / "mine"]

    def test_no_declaration_is_none_not_empty(self, tmp_path: Path) -> None:
        """The distinction the whole fallback rests on."""
        package(tmp_path, "src/mine")
        write(tmp_path, "pyproject.toml", '[project]\nname = "mine"\n')
        assert declared_packages(tmp_path) is None

    def test_no_pyproject_at_all_is_none(self, tmp_path: Path) -> None:
        package(tmp_path, "mine")
        assert declared_packages(tmp_path) is None

    def test_unreadable_pyproject_is_none(self, tmp_path: Path) -> None:
        """Malformed metadata must not silently empty the scope."""
        package(tmp_path, "src/mine")
        write(tmp_path, "pyproject.toml", "this is not toml [[[\n")
        assert declared_packages(tmp_path) is None

    def test_a_declaration_naming_something_absent_is_ignored(self, tmp_path: Path) -> None:
        """It describes a build step, not a source tree."""
        package(tmp_path, "src/mine")
        write(tmp_path, "pyproject.toml",
              '[tool.hatch.build.targets.wheel]\npackages = ["src/mine", "src/generated"]\n')
        assert declared_packages(tmp_path) == [tmp_path / "src/mine"]

    def test_a_declaration_pointing_outside_the_repo_is_ignored(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "elsewhere"
        outside.mkdir(exist_ok=True)
        repo = tmp_path / "repo"
        package(repo, "src/mine")
        write(repo, "pyproject.toml",
              '[tool.hatch.build.targets.wheel]\npackages = ["src/mine", "../elsewhere"]\n')
        assert declared_packages(repo) == [repo / "src/mine"]


class TestScopeApplied:
    def test_a_vendored_project_is_not_counted(self, tmp_path: Path) -> None:
        package(tmp_path, "src/mine")
        write(tmp_path, "pyproject.toml",
              '[tool.hatch.build.targets.wheel]\npackages = ["src/mine"]\n')
        # Somebody else's package, vendored as a fixture.
        package(tmp_path, "targets/theirs/src/theirs")
        write(tmp_path, "targets/theirs/src/theirs/more.py",
              "def g():\n    return 2\n\n\ndef h():\n    return 3\n")
        names = {t.name for t in find_targets(tmp_path)}
        assert names == {"f"}, f"vendored functions were counted: {names}"

    def test_without_a_declaration_everything_is_still_counted(self, tmp_path: Path) -> None:
        """The fallback, which is what 85 of the census's 110 projects rely on."""
        package(tmp_path, "src/mine")
        package(tmp_path, "extra/other")
        assert declared_packages(tmp_path) is None
        assert len(find_targets(tmp_path)) == 2

    def test_in_scope_accepts_everything_when_the_scope_is_unknown(self, tmp_path: Path) -> None:
        assert in_scope(tmp_path, tmp_path / "anything.py", None)

    def test_this_repository_sees_only_its_own_package(self) -> None:
        """The case that found it: `targets/` must not be audited as ours."""
        scope = declared_packages(REPO)
        assert scope == [REPO / "src" / "suite_auditor"]
        paths = {t.path.split("/")[0] for t in find_targets(REPO)}
        assert paths == {"src"}, f"targets were taken from {paths}"
