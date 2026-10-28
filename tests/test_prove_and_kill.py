"""`_prove` turns a survivor into a verdict, and `_kill` ends a run that will not stop.

The last two functions `suite-auditor coverage .` reports unreached.

`_prove` is the step the whole tool is named for: a mutant the suite did not notice is
either a proven gap, with an input showing the two programs disagree, or unproven and
possibly equivalent. The difference is what the README publishes - "50 of 110 had at
least one proven gap" - so the mapping from the differ's answer to the verdict is a
published claim. `compare` is substituted here; what is checked is that mapping, and that
the witness and its strength survive into the Result.

`_kill` ends a mutant whose test run has hung. On Windows it kills the process tree,
because pytest starts children of its own and killing only the parent leaves them holding
the scratch directory. Every branch is reachable with a stub process.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from suite_auditor import audit as audit_module  # noqa: E402
from suite_auditor import workspace as workspace_module  # noqa: E402
from suite_auditor.audit import _prove  # noqa: E402
from suite_auditor.mutate import Target  # noqa: E402
from suite_auditor.types import Verdict  # noqa: E402
from suite_auditor.workspace import _kill  # noqa: E402


def target() -> Target:
    return Target(
        path="src/pkg/mod.py",
        name="f",
        source="def f(x):\n    return x + 1\n",
        header="",
        sys_path="",
        package="pkg",
        lineno=1,
        end_lineno=2,
    )


@pytest.fixture
def differ(monkeypatch):
    """Substitute `compare`; choose what it reports and record how it was called."""
    state: dict = {"answer": {"status": "same", "detail": "agreed on 4 inputs"}, "calls": []}

    def fake_compare(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        state["calls"].append((args, kwargs))
        return state["answer"]

    monkeypatch.setattr(audit_module, "compare", fake_compare)
    return state


class TestProve:
    def test_a_difference_is_a_proven_gap(self, differ) -> None:
        differ["answer"] = {
            "status": "differs",
            "witness": {"args": "(1,)", "old": "2", "new": "3"},
            "detail": "f(1) was 2, now 3",
            "provenance": "a call your tests really made",
        }
        result = _prove(target(), "return x + 2", "binop", [], "")
        assert result.verdict is Verdict.PROVEN_GAP
        assert result.witness == {"args": "(1,)", "old": "2", "new": "3"}
        assert result.provenance == "a call your tests really made"
        assert result.detail == "f(1) was 2, now 3"

    def test_agreement_is_unproven_not_killed(self, differ) -> None:
        """"Possibly equivalent" is a different claim from "the suite noticed"."""
        result = _prove(target(), "return x + 1", "binop", [], "")
        assert result.verdict is Verdict.UNPROVEN
        assert result.witness is None

    def test_the_unproven_detail_says_why_nothing_was_proven(self, differ) -> None:
        """A bare "unproven" sends a reader to look for a bug that may not exist."""
        differ["answer"] = {"status": "no-inputs", "detail": "every argument set raised"}
        result = _prove(target(), "return x + 2", "binop", [], "")
        assert "no-inputs" in result.detail
        assert "every argument set raised" in result.detail

    def test_a_witness_gets_a_strength(self, differ) -> None:
        """Strength orders the report, so the strongest evidence is read first."""
        differ["answer"] = {
            "status": "differs",
            "witness": {"args": "(1,)", "old": "ok: 2", "new": "ok: 3"},
            "detail": "d",
        }
        result = _prove(target(), "return x + 2", "binop", [], "")
        assert result.strength is not None

    def test_the_kind_and_target_travel_through(self, differ) -> None:
        result = _prove(target(), "return x - 1", "binop", [], "")
        assert result.kind == "binop"
        assert result.target == target().key
        assert result.mutant == "return x - 1"

    def test_the_differ_is_given_the_modules_real_path(self, differ) -> None:
        """So an extracted function that reads `__file__` behaves as it would in place.

        A cache directory or a data file beside the module is found relative to that
        path, and a function proved against the wrong one can differ for a reason that
        has nothing to do with the mutant.
        """
        t = Target(
            path="src/pkg/mod.py", name="f", source="def f():\n    return 1\n",
            header="", sys_path=r"D:\repo\src", package="pkg", lineno=1, end_lineno=2,
        )
        _prove(t, "return 2", "const", [], "")
        args = differ["calls"][0][0]
        assert str(Path(r"D:\repo\src") / "src/pkg/mod.py") in args

    def test_a_target_without_a_sys_path_falls_back_to_its_relative_path(self, differ) -> None:
        _prove(target(), "return 2", "const", [], "")
        assert "src/pkg/mod.py" in differ["calls"][0][0]


class _Proc:
    """Just enough of Popen for `_kill`."""

    def __init__(self, alive: bool = True, pid: int = 4242) -> None:
        self.pid = pid
        self._alive = alive
        self.killed = False

    def poll(self):  # noqa: ANN201
        return None if self._alive else 0

    def kill(self) -> None:
        self.killed = True


class TestKill:
    def test_a_process_that_already_exited_is_left_alone(self, monkeypatch) -> None:
        def refuse(*a, **k):  # noqa: ANN002, ANN003, ANN202
            raise AssertionError("taskkill was run on a process that had already exited")

        monkeypatch.setattr(workspace_module.subprocess, "run", refuse)
        proc = _Proc(alive=False)
        _kill(proc)
        assert not proc.killed

    @pytest.mark.skipif(sys.platform != "win32", reason="the tree kill is Windows-only")
    def test_on_windows_the_whole_tree_is_killed_by_pid(self, monkeypatch) -> None:
        """pytest starts children of its own, and they hold the scratch directory open.

        By PID and with /T - never by image name, which would reach other people's
        processes on a machine running more than one of these.
        """
        seen: list[list[str]] = []

        def record(cmd, **kwargs):  # noqa: ANN001, ANN003, ANN202
            seen.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(workspace_module.subprocess, "run", record)
        _kill(_Proc(pid=4242))
        assert seen, "nothing was run to kill the tree"
        cmd = seen[0]
        assert cmd[0] == "taskkill"
        assert "/T" in cmd and "/PID" in cmd
        assert cmd[cmd.index("/PID") + 1] == "4242"
        assert "/IM" not in cmd, "killing by image name would reach other processes"

    def test_kill_is_called_as_well(self, monkeypatch) -> None:
        """taskkill can fail - a race, a permission - and the process must still go."""
        monkeypatch.setattr(
            workspace_module.subprocess,
            "run",
            lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, "", "not found"),
        )
        proc = _Proc()
        _kill(proc)
        assert proc.killed

    def test_an_oserror_from_the_tree_kill_is_swallowed(self, monkeypatch) -> None:
        """This runs while tearing down a timed-out mutant; it must not raise."""
        def boom(*a, **k):  # noqa: ANN002, ANN003, ANN202
            raise OSError("no taskkill here")

        monkeypatch.setattr(workspace_module.subprocess, "run", boom)
        proc = _Proc()
        _kill(proc)
        assert proc.killed
