"""Which tests actually execute which functions - and with what arguments.

This is what makes a mutation run on a real library finish. Running a whole suite once per
mutant is the naive approach and it is quadratic in the worst way: `toolz` has ~500 tests
and a few hundred mutable functions, so a full pass is hours. Only the tests that *reach* a
function can possibly kill its mutants, and for most functions that is two or three tests.

The map is built in a single pass. A tiny pytest plugin is written into a scratch directory
(never into the target), put on PYTHONPATH, and the suite runs once under it while
`sys.settrace` records which functions each test enters.

The same pass records **the arguments each function was actually called with**, when they
are plain literals. Those are the inputs the differential stage uses to prove a survivor
wrong: values the covering tests really passed, not text scraped out of the test source.
Scraping the source handed a function the *decorator's* arguments -
`('x,exp', 'x,exp', [(-1, 0), (0, 0), ...])` - and reported the resulting difference as a
proven gap on a suite that was fine.

`coverage.py` would do the tracing and is the obvious choice. It is not used because it
would be the only runtime dependency in a tool whose output is a claim about somebody
else's tests, and a short `settrace` hook is a smaller thing to trust than a package.

A function nothing covers is **reported, not skipped**. "No test reaches this" is a finding
in its own right, and the cheapest one available.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from suite_auditor.workspace import Timeout, child_env, run

# Runs inside the TARGET's interpreter, which may be older than this package's own
# minimum - so plain Python 3.8 syntax, and no import of suite_auditor.
PLUGIN = r'''"""Injected by suite-auditor from a scratch directory. Records which functions each
test enters, and the literal arguments they were entered with."""
import json
import math
import os
import sys

import pytest

_OUT = os.environ["SA_COVERAGE_OUT"] + "." + str(os.getpid())
_ROOT_REAL = os.path.realpath(os.environ["SA_ROOT"])
_ROOT = os.path.normcase(_ROOT_REAL) + os.sep
_ORIG = os.environ.get("SA_ORIGINAL", "")
_ORIG = (os.path.normcase(os.path.realpath(_ORIG)) + os.sep) if _ORIG else ""
_TEST_DIRS = {"tests", "test", "testing"}
_ENV_DIRS = set(filter(None, os.environ.get("SA_ENV_DIRS", "").split(os.pathsep)))
_SKIP_PARTS = {"site-packages", "dist-packages", ".venv", "venv", ".tox", ".nox", "__pycache__"}
_MAX_CALLS = 16
_MAX_TRIES = 400
_MAX_REPR = 400
_VARARGS = 0x04 | 0x08
_GENLIKE = 0x20 | 0x80 | 0x200

_seen = {}
_calls = {}
_tries = {}
_stray = set()
_failed = set()
_rel_cache = {}
_gen_frames = set()
_current = [None]


def _is_test(rel):
    parts = rel.split("/")
    name = parts[-1]
    if name == "conftest.py" or (name.startswith("test_") and name.endswith(".py")):
        return True
    if name.endswith("_test.py"):
        return True
    return any(p in _TEST_DIRS for p in parts[:-1])


def _is_env(rel):
    # A virtual environment inside the project holds pytest itself and every dependency;
    # none of it is the project's code.
    parts = rel.split("/")
    if parts[0] in _ENV_DIRS:
        return True
    return any(p in _SKIP_PARTS for p in parts[:-1])


def _classify(path):
    hit = _rel_cache.get(path)
    if hit is not None:
        return hit
    # realpath on both sides: a temp directory can be spelled with an 8.3 short name on
    # Windows, or through a symlink on macOS, and a spelling mismatch traces nothing.
    result = ("", "")
    if path.startswith("<"):
        # "<frozen os>", "<string>": not a file, and realpath would resolve it against
        # the working directory - which is the repo root.
        _rel_cache[path] = result
        return result
    real = os.path.realpath(path)
    norm = os.path.normcase(real)
    if norm.startswith(_ROOT):
        rel = norm[len(_ROOT):].replace(os.sep, "/")
        if not _is_test(rel) and not _is_env(rel):
            # Keep the file's real spelling, not the normcased one.
            result = ("in", real[len(_ROOT):].replace(os.sep, "/"))
    elif _ORIG and norm.startswith(_ORIG):
        rel = norm[len(_ORIG):].replace(os.sep, "/")
        if not _is_test(rel) and not _is_env(rel):
            result = ("stray", rel)
    _rel_cache[path] = result
    return result


def takes_a_receiver(code) -> bool:
    """Is this code object's first parameter a receiver rather than an argument?

    A leading `self` or `cls` is the receiver only in a method, and `co_qualname` carries
    the dot that says so. On Python 3.10, where `co_qualname` does not exist, the old
    assumption stands - it is wrong only for a module-level function that names its first
    parameter `self` or `cls`, which is what this exists for, and there is nothing else
    in a code object to tell them apart.

    `toolz/functoolz.py::_restore_curry(cls, func, args, kwargs, userdict, is_decorated)`
    is such a function. Dropping its `cls` recorded every observed call one argument
    short; `argument_sets` then saw an observed arity that disagreed with the signature,
    fell back to the observed sets alone, and all 21 of them raised `missing 1 required
    positional argument`. The audit called that "no valid input could be built" - a
    limitation of the tool's reach, for what was a miscounted call.
    """
    qualname = getattr(code, "co_qualname", None)
    if qualname is None:  # pragma: no cover - Python 3.10 only
        return True
    # Not just "contains a dot": a function nested inside another function has one too.
    # `test_x.<locals>.plain` would then be read as a method and lose its first argument,
    # which is how the test for this found the flaw. What distinguishes them is the
    # segment immediately before the name - `<locals>` means nested in a function, a
    # class name means a method, and nothing means module level.
    #
    #   _restore_curry            -> no parent        -> an argument
    #   curry._should_curry       -> parent `curry`   -> a receiver
    #   test_x.<locals>.plain     -> parent <locals>  -> an argument
    #   f.<locals>.C.m            -> parent `C`       -> a receiver
    parts = qualname.split(".")
    if len(parts) < 2:
        return False
    return parts[-2] != "<locals>"


def _simple(v, depth=0):
    t = type(v)
    if t is float:
        return math.isfinite(v)
    if t in (int, str, bool, bytes, type(None)):
        return True
    if depth >= 3:
        return False
    if t in (list, tuple, set, frozenset):
        return len(v) <= 32 and all(_simple(x, depth + 1) for x in v)
    if t is dict:
        return len(v) <= 32 and all(
            _simple(k, depth + 1) and _simple(x, depth + 1) for k, x in v.items()
        )
    return False


def _capture(key, frame, code):
    tries = _tries.get(key, 0)
    if tries >= _MAX_TRIES:
        return
    _tries[key] = tries + 1
    bucket = _calls.setdefault(key, [])
    if len(bucket) >= _MAX_CALLS or code.co_flags & _VARARGS:
        return
    if code.co_flags & _GENLIKE:
        # A generator's frame fires "call" again on every resume, with arguments that
        # may have been reassigned by then. Only its first entry is a real call.
        if id(frame) in _gen_frames:
            return
        _gen_frames.add(id(frame))
    loc = frame.f_locals
    names = code.co_varnames[: code.co_argcount + code.co_kwonlyargcount]
    npos = code.co_argcount
    pos, kw = [], {}
    receiver = takes_a_receiver(code)
    for i, name in enumerate(names):
        if i == 0 and receiver and name in ("self", "cls"):
            continue
        if name not in loc:
            return
        v = loc[name]
        if not _simple(v):
            return
        r = repr(v)
        if len(r) > _MAX_REPR:
            return
        if i < npos:
            pos.append(r)
        else:
            kw[name] = r
    entry = {"pos": pos, "kw": kw}
    if entry not in bucket:
        bucket.append(entry)


def _tracer(frame, event, arg):
    if event != "call":
        return None
    test = _current[0]
    if test is None:
        return None
    code = frame.f_code
    where, rel = _classify(code.co_filename)
    if not where:
        return None
    if where == "stray":
        _stray.add(rel)
        return None
    name = getattr(code, "co_qualname", code.co_name)
    if "<" in name:
        return None
    key = rel + "::" + name
    _seen.setdefault(key, set()).add(test)
    try:
        _capture(key, frame, code)
    except Exception:
        pass
    return None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    _current[0] = item.nodeid
    previous = sys.gettrace()
    sys.settrace(_tracer)
    try:
        yield
    finally:
        sys.settrace(previous)
        _current[0] = None
        _gen_frames.clear()


def pytest_runtest_logreport(report):
    if report.failed:
        _failed.add(report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    with open(_OUT, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "seen": {k: sorted(v) for k, v in _seen.items()},
                "calls": _calls,
                "failed": sorted(_failed),
                "stray": sorted(_stray),
            },
            fh,
        )
'''


@dataclass(frozen=True)
class SuiteHealth:
    """How the target's OWN suite behaved while it was being traced.

    This has to travel with the coverage map, because the map is only as
    complete as the run that produced it. A test that errors executes nothing,
    so every function it would have reached reads as unreached - and
    "unreached" is the headline this tool prints.

    The failure is silent and points the wrong way: a broken target environment
    makes a healthy suite look full of gaps, and the report says "no test
    reaches these" with total confidence. Published results for `toolz` said 14
    functions were unreached; re-run in a working environment it is 5. Nothing
    about toolz or this tool changed in between - only how many of toolz's
    tests managed to run.
    """

    ran: bool
    exit_code: int
    passed: int = 0
    failed: int = 0
    errors: int = 0
    collected_nothing: bool = False
    output_tail: str = ""

    @property
    def clean(self) -> bool:
        """Whether an unreached list from this run can be believed."""
        return self.ran and not self.collected_nothing and self.failed == 0 and self.errors == 0

    def caveat(self) -> str:
        """One line for the report, empty when there is nothing to say."""
        if not self.ran:
            return "the target's test suite did not run at all"
        if self.collected_nothing:
            return "the target's test suite collected no tests"
        parts = []
        if self.errors:
            parts.append(f"{self.errors} test(s) errored")
        if self.failed:
            parts.append(f"{self.failed} test(s) failed")
        return " and ".join(parts)


_COUNTS = re.compile(r"(\d+) (passed|failed|error|errors|skipped)")


def _read_health(stdout: str, stderr: str, returncode: int) -> SuiteHealth:
    blob = stdout + stderr
    counts = {"passed": 0, "failed": 0, "errors": 0}
    for n, word in _COUNTS.findall(blob):
        key = "errors" if word.startswith("error") else word
        if key in counts:
            counts[key] = max(counts[key], int(n))
    tail = "\n".join(blob.strip().splitlines()[-12:])
    # pytest exits 5 when it collected nothing at all, which is a different
    # problem from a suite that ran and failed.
    return SuiteHealth(
        ran=True,
        exit_code=returncode,
        passed=counts["passed"],
        failed=counts["failed"],
        errors=counts["errors"],
        collected_nothing=returncode == 5 or ("no tests ran" in blob and not counts["passed"]),
        output_tail=tail,
    )


@dataclass
class Trace:
    """Everything one traced suite run established."""

    cov: dict[str, list[str]]
    """`path::function` -> ids of the PASSING tests that execute it."""
    health: SuiteHealth
    calls: dict[str, list[dict]] = field(default_factory=dict)
    """`path::function` -> the literal arguments it was really called with."""
    failed: list[str] = field(default_factory=list)
    failing_only: dict[str, list[str]] = field(default_factory=dict)
    """`path::function` -> the FAILING tests that reach it, for functions no passing test
    reaches. Not "unreached": the suite tries, and the test is broken."""
    stray: list[str] = field(default_factory=list)
    """Files of the ORIGINAL repo the suite executed while running in the scratch copy -
    the sign of an install that bypasses the copy, which would hide every mutant."""

    def __iter__(self):
        # `cov, health = build_map(...)` - the shape callers had before calls were traced.
        return iter((self.cov, self.health))


def _env_dirs(*roots: Path | None) -> list[str]:
    """Top-level directories of these roots that are virtual environments."""
    out: list[str] = []
    for root in roots:
        if root is None or not root.is_dir():
            continue
        for d in root.iterdir():
            if d.is_dir() and (d / "pyvenv.cfg").is_file() and d.name not in out:
                out.append(d.name)
    return out


def build_map(
    repo: Path,
    test_target: str = "",
    timeout: float = 1800.0,
    python: str = "",
    original: Path | None = None,
    extra_paths: list[str] | None = None,
) -> Trace:
    """Trace the suite once. Never raises for a target that will not run.

    The SuiteHealth is returned alongside and is not optional. The result of the
    pytest subprocess used to be discarded entirely, so a target whose tests could
    not even be imported produced an empty map, and the report presented every
    function in the package as reached by no test.

    Tests that FAILED are dropped from the map. A covering test that already fails on
    the unmutated code "kills" every mutant it is run against, and the kill rate would
    count a broken test as a vigilant one.
    """
    import sys

    # Absolute, always. The subprocess runs with cwd=repo, so a relative path would be
    # re-resolved against the repo and the output looked for in the wrong place.
    repo = repo.resolve()
    scratch = Path(tempfile.mkdtemp(prefix="suite-auditor-trace-"))
    try:
        (scratch / "_sa_plugin.py").write_text(PLUGIN, encoding="utf-8", newline="\n")
        out_base = scratch / "coverage.json"
        env = child_env(
            [str(scratch), *(extra_paths or [])],
            SA_COVERAGE_OUT=str(out_base),
            SA_ROOT=str(repo),
            SA_ORIGINAL=str(original.resolve()) if original else "",
            SA_ENV_DIRS=os.pathsep.join(_env_dirs(repo, original)),
        )
        cmd = [
            python or sys.executable,
            "-B",
            "-m",
            "pytest",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
            "-p",
            "_sa_plugin",
            "--tb=short",
            f"--basetemp={scratch / 'basetemp'}",
        ]
        if test_target:
            cmd.append(test_target)
        try:
            code, out = run(cmd, repo, env, timeout)
        except Timeout:
            return Trace({}, SuiteHealth(ran=False, exit_code=-1, collected_nothing=True))
        except OSError as exc:
            return Trace({}, SuiteHealth(ran=False, exit_code=-1, output_tail=str(exc)))
        health = _read_health(out, "", code)

        seen: dict[str, set[str]] = {}
        calls: dict[str, list[dict]] = {}
        failed: set[str] = set()
        stray: set[str] = set()
        # One file per process: under pytest-xdist every worker traces its own share.
        for f in scratch.glob("coverage.json.*"):
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for k, v in raw.get("seen", {}).items():
                seen.setdefault(k, set()).update(v)
            for k, v in raw.get("calls", {}).items():
                bucket = calls.setdefault(k, [])
                bucket.extend(e for e in v if e not in bucket)
            failed.update(raw.get("failed", []))
            stray.update(raw.get("stray", []))
        cov = {k: sorted(v - failed) for k, v in seen.items()}
        cov = {k: v for k, v in cov.items() if v}
        failing_only = {k: sorted(v) for k, v in seen.items() if k not in cov}
        return Trace(
            cov,
            health,
            calls=calls,
            failed=sorted(failed),
            stray=sorted(stray),
            failing_only=failing_only,
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def tests_for(cov: dict[str, list[str]], path: str, name: str) -> list[str]:
    """Tests covering one function, including the methods of a class it belongs to.

    The trace keys functions by `co_qualname` (`Curry.__eq__`) on Python 3.11+, and by
    the bare `co_name` on older interpreters, so a lookup for `Curry.__eq__` falls back
    to `__eq__`. Getting this wrong makes every method look uncovered, and a whole class
    of function would then be silently dropped.
    """
    direct = cov.get(f"{path}::{name}")
    if direct:
        return direct
    bare = name.rpartition(".")[2]
    return cov.get(f"{path}::{bare}", [])


def calls_for(calls: dict[str, list[dict]], path: str, name: str) -> list[dict]:
    """The recorded argument sets for one function, found the same way as its tests."""
    direct = calls.get(f"{path}::{name}")
    if direct:
        return direct
    return calls.get(f"{path}::{name.rpartition('.')[2]}", [])
