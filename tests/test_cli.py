"""The command line as a user meets it: paths, interpreters, exit codes, output.

Every test here is a complaint somebody had: a traceback for a mistyped path, exit 0 when
nothing could be scored, pytest silently missing under a pipx install, --help opening with
a module docstring, no --version.
"""

from __future__ import annotations

import subprocess
import sys
import venv
from pathlib import Path

import pytest

from suite_auditor import __version__
from suite_auditor.cli import main
from suite_auditor.workspace import resolve_python
from test_user_view import SOLID_TESTS, _project, _tree


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_help_does_not_start_with_a_module_docstring(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "Command line entry point" not in out
    assert "coverage" in out and "audit" in out and "exit status" in out


@pytest.mark.parametrize("cmd", ["coverage", "audit"])
def test_a_missing_path_is_a_clean_error(cmd, tmp_path, capsys):
    code = main([cmd, str(tmp_path / "nope")])
    err = capsys.readouterr().err
    assert code == 2
    assert "no such directory" in err
    assert "Traceback" not in err


def test_a_missing_test_path_is_a_clean_error(tmp_path, capsys):
    repo = _project(tmp_path / "repo")
    assert main(["coverage", str(repo), "--test", "nope", "--python", sys.executable]) == 2
    assert "--test nope" in capsys.readouterr().err


@pytest.fixture(scope="module")
def bare_python(tmp_path_factory) -> str:
    """A real interpreter that cannot import pytest."""
    d = tmp_path_factory.mktemp("bare-venv")
    venv.create(d, with_pip=False)
    exe = d / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    probe = subprocess.run([str(exe), "-c", "import pytest"], capture_output=True, check=False)
    if probe.returncode == 0:
        pytest.skip("the base interpreter has pytest importable even in a bare venv")
    return str(exe)


@pytest.mark.parametrize("cmd", ["coverage", "audit"])
def test_no_pytest_in_the_target_interpreter_is_explained(cmd, tmp_path, capsys, bare_python):
    repo = _project(tmp_path / "repo")
    code = main([cmd, str(repo), "--python", bare_python])
    err = capsys.readouterr().err
    assert code == 2
    assert "pytest is not importable" in err
    assert "--python" in err


def test_nothing_scored_is_a_failure_not_a_pass(tmp_path, capsys):
    repo = tmp_path / "repo"
    (repo / "lib").mkdir(parents=True)
    (repo / "lib" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "lib" / "m.py").write_text("def f(x):\n    return x > 0\n", encoding="utf-8")
    code = main(["audit", str(repo), "--python", sys.executable, "--quiet"])
    assert code == 2
    assert "NOTHING COULD BE SCORED" in capsys.readouterr().out


def test_fail_on_gap_passes_a_healthy_suite(tmp_path):
    tests = SOLID_TESTS.replace(
        "assert double_all([]) == []", "assert double_all([1, 3]) == [2, 6]"
    )
    repo = _project(tmp_path / "repo", tests)
    args = ["audit", str(repo), "--python", sys.executable, "--quiet", "--fail-on-gap"]
    # clamp and grade: the two parametrized functions that drew the false gaps.
    assert main([*args, "--limit", "2", "-j", "2"]) == 0


def test_coverage_leaves_nothing_in_the_target(tmp_path):
    repo = _project(tmp_path / "repo")
    before = _tree(repo)
    assert main(["coverage", str(repo), "--python", sys.executable, "--quiet"]) == 0
    assert _tree(repo) == before, "coverage wrote into the repository"


def test_the_projects_own_venv_is_found(tmp_path):
    v = tmp_path / ".venv"
    venv.create(v, with_pip=False)
    found = resolve_python(tmp_path)
    assert Path(found.path).resolve().is_relative_to(v.resolve())
    assert ".venv" in found.why


def test_an_activated_venv_from_another_project_is_flagged(tmp_path, monkeypatch):
    other = tmp_path / "other" / ".venv"
    venv.create(other, with_pip=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("VIRTUAL_ENV", str(other))
    found = resolve_python(repo)
    assert Path(found.path).resolve().is_relative_to(other.resolve())
    assert "outside" in found.warning and "--python" in found.warning


def test_an_activated_venv_inside_the_project_is_not_flagged(tmp_path, monkeypatch):
    inner = tmp_path / "envs" / "dev"
    venv.create(inner, with_pip=False)
    monkeypatch.setenv("VIRTUAL_ENV", str(inner))
    assert resolve_python(tmp_path).warning == ""
