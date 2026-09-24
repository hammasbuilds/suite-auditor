"""Which tests actually execute which functions.

This is what makes a mutation run on a real library finish. Running a whole suite once per
mutant is the naive approach and it is quadratic in the worst way: `toolz` has ~500 tests
and a few hundred mutable functions, so a full pass is hours. Only the tests that *reach* a
function can possibly kill its mutants, and for most functions that is two or three tests.

The map is built in a single pass. A tiny pytest plugin is written into the target repo, the
suite runs once under it, and `sys.settrace` records which functions each test enters. The
plugin is removed afterwards.

`coverage.py` would do this and is the obvious choice. It is not used because it would be
the only runtime dependency in a tool whose output is a claim about somebody else's tests,
and forty lines of `settrace` is a smaller thing to trust than a package. The trade is real
and it is the wrong call for a library; for a one-shot auditor it is the right one.

A function nothing covers is **reported, not skipped**. "No test reaches this" is a finding
in its own right, and the cheapest one available.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PLUGIN = '''"""Injected by suite-auditor, and removed again.

Records which functions each test enters.
"""
import json
import os
import sys

import pytest

_OUT = os.environ["SA_COVERAGE_OUT"]
_ROOT = os.path.abspath(os.environ["SA_ROOT"])
_seen = {}
_current = [None]


def _tracer(frame, event, arg):
    # Only "call" events are wanted, and returning None declines to trace the frame's
    # lines - which keeps the overhead to one dict write per function entry rather than
    # one per executed line.
    if event != "call":
        return None
    code = frame.f_code
    path = code.co_filename
    if not path.startswith(_ROOT):
        return None
    rel = os.path.relpath(path, _ROOT).replace(os.sep, "/")
    if "test" in os.path.basename(rel) or "tests/" in rel:
        return None
    test = _current[0]
    if test is None:
        return None
    _seen.setdefault(rel + "::" + code.co_name, set()).add(test)
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


def pytest_sessionfinish(session, exitstatus):
    with open(_OUT, "w", encoding="utf-8") as fh:
        json.dump({k: sorted(v) for k, v in _seen.items()}, fh)
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
    # pytest exits 5 when it collected nothing at all, which is a different
    # problem from a suite that ran and failed.
    return SuiteHealth(
        ran=True,
        exit_code=returncode,
        passed=counts["passed"],
        failed=counts["failed"],
        errors=counts["errors"],
        collected_nothing=returncode == 5 or ("no tests ran" in blob and not counts["passed"]),
    )


def build_map(
    repo: Path, test_target: str = "", timeout: float = 1800.0
) -> tuple[dict[str, list[str]], SuiteHealth]:
    """`path::function` -> the test ids that execute it, and how the run went.

    Returns an empty map rather than raising if the trace could not be collected; the
    caller then falls back to running the whole suite, which is slow but correct.

    The SuiteHealth is returned alongside and is not optional. The result of the
    pytest subprocess used to be discarded entirely - `check=False`, return value
    unused - so a target whose tests could not even be imported produced an empty
    map, and the report presented every function in the package as reached by no
    test. Total failure and total absence of coverage are indistinguishable from
    the map alone, and they call for opposite responses.
    """
    # Absolute, always. The subprocess runs with cwd=repo, so a relative path here is
    # re-resolved against the repo and the plugin writes to `repo/repo/_sa_coverage.json`.
    # Nothing raises: the file is simply not where it is looked for, the map comes back
    # empty, and every function in the project reads as uncovered.
    repo = repo.resolve()
    plugin = repo / "_sa_plugin.py"
    out_file = repo / "_sa_coverage.json"
    plugin.write_text(PLUGIN, encoding="utf-8", newline="")

    env = {
        **dict(__import__("os").environ),
        "SA_COVERAGE_OUT": str(out_file),
        "SA_ROOT": str(repo),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
        "-p",
        "_sa_plugin",
        "--tb=no",
    ]
    if test_target:
        cmd.append(test_target)

    try:
        proc = subprocess.run(
            cmd,
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
        health = _read_health(proc.stdout, proc.stderr, proc.returncode)
        if out_file.is_file():
            raw = json.loads(out_file.read_text(encoding="utf-8"))
            # The plugin traces itself; drop it and any lambda, neither of which is a
            # mutation target.
            return {
                k: v
                for k, v in raw.items()
                if not k.startswith("_sa_plugin") and not k.endswith("::<lambda>")
            }, health
        return {}, health
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as exc:
        return {}, SuiteHealth(
            ran=False, exit_code=-1, collected_nothing=isinstance(exc, subprocess.TimeoutExpired)
        )
    finally:
        plugin.unlink(missing_ok=True)
        out_file.unlink(missing_ok=True)


def tests_for(cov: dict[str, list[str]], path: str, name: str) -> list[str]:
    """Tests covering one function, including the methods of a class it belongs to.

    `settrace` reports a method's own `co_name`, not `Class.method`, so a lookup for
    `Curry.__eq__` has to also try `__eq__`. Getting this wrong makes every method look
    uncovered, and a whole class of function would then be silently dropped.
    """
    direct = cov.get(f"{path}::{name}")
    if direct:
        return direct
    bare = name.rpartition(".")[2]
    return cov.get(f"{path}::{bare}", [])
