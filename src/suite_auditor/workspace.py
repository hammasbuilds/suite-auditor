"""Where the target's tests run, with which interpreter, and on whose files.

Three rules, each of which was a user-visible bug first:

- **The user's files are never written.** An audit copies the repository into a scratch
  directory and mutates the copy. Patching mutants into the real tree meant a crash, a
  Ctrl-C or an editor reading mid-audit could see `if pct > 51:` in somebody's library,
  and the restore step rewrote CRLF files as LF even when it worked.
- **The target's own interpreter runs the target's tests.** The tool used to run pytest
  with whatever interpreter it was itself installed in, so a `pipx`/`uv tool` install -
  which has no pytest and none of the target's dependencies - could not work at all.
- **Test files are recognised by pytest's conventions, not by a substring.** Any module
  with "test" in its name (`latest.py`, `contest.py`, `attestation.py`) used to be
  silently skipped as if it were a test.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

# Directories that are never part of what a test suite needs, and are often huge.
JUNK_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    ".tox",
    ".nox",
    ".mypy_cache",
    ".pytest_cache",
    ".eggs",
    ".ruff_cache",
    ".hypothesis",
    "node_modules",
    "site-packages",
}

TEST_DIRS = {"tests", "test", "testing"}


def is_test_path(rel: str) -> bool:
    """Whether a repo-relative path is test code, by pytest's own conventions.

    `test_*.py`, `*_test.py`, `conftest.py`, or anything under a `tests/`, `test/` or
    `testing/` directory. Deliberately NOT "contains the substring test": that skipped
    `latest.py`, `contest.py` and `attestation.py` as though they were tests.

    The same rule is inlined in the coverage plugin, which has to run with no import of
    this package; a test keeps the two in step.
    """
    parts = rel.replace("\\", "/").split("/")
    name = parts[-1]
    if name == "conftest.py" or (name.startswith("test_") and name.endswith(".py")):
        return True
    if name.endswith("_test.py"):
        return True
    return any(p in TEST_DIRS for p in parts[:-1])


def _is_venv(path: Path) -> bool:
    return (path / "pyvenv.cfg").is_file()


def _interpreter_in(venv: Path) -> Path | None:
    for rel in ("Scripts/python.exe", "bin/python", "bin/python3"):
        cand = venv / rel
        if cand.is_file():
            return cand
    return None


@dataclass(frozen=True)
class Interpreter:
    path: str
    why: str
    warning: str = ""


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def resolve_python(repo: Path, given: str = "") -> Interpreter:
    """Pick the interpreter that runs the target's suite.

    `--python` wins. Otherwise a virtual environment inside the project (`.venv`, `venv`,
    `env` - what uv, poetry-in-project and `python -m venv` create), then an activated
    one, then this interpreter. The first two are what make an isolated install of this
    tool usable: the tool has no pytest of its own, the project does.
    """
    if given:
        p = Path(given).expanduser()
        if p.is_dir():
            found = _interpreter_in(p)
            if found is None:
                raise SystemExit(f"error: --python {given}: no interpreter found in that directory")
            p = found
        if not p.exists():
            resolved = shutil.which(given)
            if not resolved:
                raise SystemExit(f"error: --python {given}: no such interpreter")
            p = Path(resolved)
        # abspath, not resolve: a venv's python is often a symlink to the base
        # interpreter, and following it would silently leave the venv.
        return Interpreter(os.path.abspath(p), "from --python")
    for name in (".venv", "venv", "env"):
        venv = repo / name
        if _is_venv(venv):
            found = _interpreter_in(venv)
            if found:
                return Interpreter(str(found), f"the project's own {name}/")
    active = os.environ.get("VIRTUAL_ENV")
    if active:
        found = _interpreter_in(Path(active))
        if found:
            warning = ""
            if not _inside(Path(active), repo):
                # The documented order picks it up, but a shell left activated in another
                # project is the common case, and its missing dependencies would surface as
                # baffling import errors in the baseline run rather than as this sentence.
                warning = (
                    f"warning: the activated virtual environment {active} is outside {repo}.\n"
                    "  If it belongs to another project, its packages are not this project's\n"
                    "  dependencies; pass --python path/to/this/project's/venv instead."
                )
            return Interpreter(str(found), "the activated virtual environment", warning)
    return Interpreter(sys.executable, "the interpreter suite-auditor is installed in")


def check_pytest(python: str) -> str | None:
    """None if `python` can import pytest, otherwise a message saying what to do."""
    try:
        proc = subprocess.run(
            [python, "-c", "import pytest, sys; print(pytest.__version__)"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"could not start {python}: {exc}"
    if proc.returncode == 0:
        return None
    return (
        f"pytest is not importable by {python}.\n"
        "  suite-auditor runs the target's test suite with the target's interpreter, so\n"
        "  pytest (and the project's own dependencies) must be installed there.\n"
        "  Either install pytest into that environment, or point at the right one:\n"
        "      suite-auditor ... --python path/to/project/.venv/bin/python"
    )


def package_roots(repo: Path, rel_paths: list[str]) -> list[str]:
    """The sys.path entries that make the repo's own packages importable, most specific first.

    Put ahead of everything else on PYTHONPATH, so the suite imports the code in THIS
    directory - the scratch copy during an audit - and not a non-editable install of the
    same package in site-packages, which would make every mutant invisible.
    """
    roots: list[str] = []
    for rel in rel_paths:
        current = (repo / rel).parent
        while (current / "__init__.py").is_file() and current != repo:
            current = current.parent
        s = str(current)
        if s not in roots:
            roots.append(s)
    return roots


def child_env(extra_paths: list[str] | None = None, **env: str) -> dict[str, str]:
    """Environment for a target subprocess: no bytecode, our paths first."""
    out = dict(os.environ)
    out["PYTHONDONTWRITEBYTECODE"] = "1"
    out.setdefault("PYTHONIOENCODING", "utf-8")
    paths = list(extra_paths or [])
    if out.get("PYTHONPATH"):
        paths.append(out["PYTHONPATH"])
    if paths:
        out["PYTHONPATH"] = os.pathsep.join(paths)
    out.update(env)
    return out


# --- subprocesses this tool starts, so an interrupt can stop exactly those -------------

_LIVE: set[subprocess.Popen] = set()
_LIVE_LOCK = threading.Lock()


class Timeout(Exception):
    pass


def run(cmd: list[str], cwd: Path | str, env: dict[str, str], timeout: float) -> tuple[int, str]:
    """Run a child to completion; (returncode, stdout+stderr). Raises Timeout."""
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        **kwargs,
    )
    with _LIVE_LOCK:
        _LIVE.add(proc)
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out or ""
    except subprocess.TimeoutExpired:
        _kill(proc)
        proc.communicate()
        raise Timeout from None
    finally:
        with _LIVE_LOCK:
            _LIVE.discard(proc)


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            # The whole tree: pytest may have started children of its own.
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    with contextlib.suppress(OSError):
        proc.kill()


def kill_all() -> None:
    """Stop every child this process started. Only those - nothing else is touched."""
    with _LIVE_LOCK:
        procs = list(_LIVE)
    for p in procs:
        _kill(p)


# --- the scratch copy --------------------------------------------------------------------


def _ignore_for(root: Path):
    root = root.resolve()

    def ignore(directory: str, names: list[str]) -> set[str]:
        d = Path(directory)
        at_top = d.resolve() == root

        def junk(n: str) -> bool:
            if n in JUNK_DIRS or n.endswith(".egg-info"):
                return True
            if at_top and n in ("build", "dist"):
                return True
            return (d / n).is_dir() and _is_venv(d / n)

        return {n for n in names if junk(n)}

    return ignore


class Scratch:
    """Copies of a repository that mutants may be written into. Removed on exit."""

    def __init__(self, repo: Path, copies: int = 1) -> None:
        self.repo = repo.resolve()
        self.root = Path(tempfile.mkdtemp(prefix="suite-auditor-")).resolve()
        self.copies: list[Path] = []
        try:
            first = self.root / "w0" / self.repo.name
            shutil.copytree(self.repo, first, symlinks=True, ignore=_ignore_for(self.repo))
            self.copies.append(first)
            for i in range(1, copies):
                dest = self.root / f"w{i}" / self.repo.name
                shutil.copytree(first, dest, symlinks=True)
                self.copies.append(dest)
        except BaseException:
            self.close()
            raise

    def basetemp(self, i: int) -> Path:
        return self.root / f"t{i}"

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> Scratch:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
