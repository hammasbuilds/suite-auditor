"""The helpers its own coverage pass reports as reached by no test.

`suite-auditor coverage .` lists fifteen, and they are not all presentational. Three
decide what the audit actually does:

  * `_test_args` falls back from test ids to whole files when the command line would be
    too long. Running a whole file instead of one test changes which tests get a say in
    whether a mutant lives, so the threshold is a correctness boundary, not a convenience.
  * `_rerooted` rewrites a target's `sys_path` for the scratch copy. A wrong prefix
    replacement breaks the import for every mutant of that file, and the audit then
    reports unscorable mutants rather than a broken path.
  * `_fmt` and `_Progress.eta` are what a user reads for twenty minutes while deciding
    whether to wait. The docstring records that an average over a fast start promised two
    minutes and took ten, so the rate calculation already had to be fixed once.

The rest - `as_row`, `by_function`, `_health_json`, `write_json`, `calls_for`,
`basetemp` - are the report's own shape, which the README and the tests read.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from suite_auditor.audit import _MAX_ARGV, _fmt, _Progress, _rerooted, _test_args  # noqa: E402
from suite_auditor.coverage import SuiteHealth, calls_for  # noqa: E402
from suite_auditor.mutate import Target  # noqa: E402
from suite_auditor.report import _health_json, write_json  # noqa: E402
from suite_auditor.types import Audit, Result, Verdict  # noqa: E402
from suite_auditor.workspace import Scratch  # noqa: E402


class TestTestArgs:
    def test_a_short_list_is_passed_through_unchanged(self) -> None:
        ids = ["tests/test_a.py::test_one", "tests/test_b.py::test_two"]
        assert _test_args(ids) == ids

    def test_a_long_list_collapses_to_the_files(self) -> None:
        """Windows caps a command line, and 8,000 test ids do not fit in one."""
        ids = [f"tests/test_{i // 50}.py::test_{i}" for i in range(2000)]
        out = _test_args(ids)
        assert all("::" not in arg for arg in out)
        assert set(out) == {f"tests/test_{i}.py" for i in range(40)}

    def test_the_collapsed_list_is_sorted_and_unique(self) -> None:
        """Deterministic, because the command goes into a report a reader may re-run."""
        ids = [f"tests/test_{i % 3}.py::test_{i}" for i in range(4000)]
        out = _test_args(ids)
        assert out == sorted(set(out))

    def test_the_boundary_is_the_budget_not_a_count(self) -> None:
        """Long ids hit the limit sooner than short ones, which a count would miss."""
        long_id = "tests/" + "a" * 200 + ".py::test_one"
        many = [long_id.replace("test_one", f"test_{i}") for i in range(200)]
        assert sum(len(t) + 3 for t in many) > _MAX_ARGV
        assert all("::" not in arg for arg in _test_args(many))


class TestFmt:
    @pytest.mark.parametrize(
        ("seconds", "text"),
        [
            (0, "0s"),
            (1, "1s"),
            (59, "59s"),
            (60, "1m00s"),
            (61, "1m01s"),
            (3599, "59m59s"),
            (3600, "1h00m"),
            (7265, "2h01m"),
        ],
    )
    def test_each_band(self, seconds: float, text: str) -> None:
        assert _fmt(seconds) == text

    def test_a_negative_duration_does_not_print_a_minus(self) -> None:
        """Clock skew and a finished-early estimate both produce one."""
        assert _fmt(-5) == "0s"

    def test_the_seconds_are_zero_padded_so_the_line_does_not_jitter(self) -> None:
        assert _fmt(65) == "1m05s"


class TestProgress:
    def test_no_eta_before_anything_finishes(self) -> None:
        assert _Progress(total=10, enabled=False).eta() == "ETA --"

    def test_an_eta_appears_once_something_has(self) -> None:
        p = _Progress(total=10, enabled=False)
        p.tick("first")
        assert p.eta().startswith("ETA ")
        assert p.eta() != "ETA --"

    def test_ticking_counts_and_does_not_require_a_terminal(self) -> None:
        p = _Progress(total=3, enabled=True)
        p.tick("a")
        p.tick("b")
        assert p.done == 2

    def test_say_is_silent_when_disabled(self, capsys) -> None:
        p = _Progress(total=1, enabled=False)
        p.say("something")
        assert capsys.readouterr().out == ""

    def test_say_prints_when_enabled(self, capsys) -> None:
        p = _Progress(total=1, enabled=True)
        p.say("something")
        assert "something" in capsys.readouterr().out

    def test_the_eta_uses_the_recent_rate(self, monkeypatch) -> None:
        """The defect the docstring records: an average over a fast start under-promised.

        Twenty quick mutants then two slow ones must not still predict the quick rate.
        """
        clock = [1000.0]
        monkeypatch.setattr("suite_auditor.audit.time.time", lambda: clock[0])
        p = _Progress(total=100, enabled=False, t0=1000.0)
        for _ in range(20):
            clock[0] += 1
            p.tick("quick")
        quick = p.eta()
        for _ in range(4):
            clock[0] += 60
            p.tick("slow")
        slow = p.eta()
        assert quick != slow
        assert _fmt_seconds(slow) > _fmt_seconds(quick), (
            f"the estimate did not rise when the work slowed: {quick} then {slow}"
        )


def _fmt_seconds(eta: str) -> int:
    """Turn 'ETA 1h02m' back into seconds, for comparing two estimates."""
    text = eta.removeprefix("ETA ").strip()
    if text.endswith("s") and "m" not in text and "h" not in text:
        return int(text[:-1])
    if "h" in text:
        hours, rest = text.split("h")
        return int(hours) * 3600 + int(rest.rstrip("m")) * 60
    minutes, rest = text.split("m")
    return int(minutes) * 60 + int(rest.rstrip("s") or 0)


def target(path: str = "src/pkg/mod.py", sys_path: str = "") -> Target:
    return Target(
        path=path,
        name="f",
        source="def f():\n    return 1\n",
        header="",
        sys_path=sys_path,
        package="pkg",
        lineno=1,
        end_lineno=2,
    )


class TestRerooted:
    def test_the_same_root_returns_the_target_unchanged(self) -> None:
        t = target(sys_path=r"D:\repo\src")
        assert _rerooted(t, Path(r"D:\repo"), Path(r"D:\repo")) is t

    def test_a_path_under_the_old_root_is_moved(self) -> None:
        t = target(sys_path=str(Path(r"D:\repo") / "src"))
        out = _rerooted(t, Path(r"D:\repo"), Path(r"D:\scratch\w0\repo"))
        assert out.sys_path == str(Path(r"D:\scratch\w0\repo") / "src")

    def test_a_path_outside_the_old_root_is_left_alone(self) -> None:
        """A target whose sys_path points elsewhere must not be rewritten by prefix luck."""
        t = target(sys_path=r"C:\elsewhere\lib")
        out = _rerooted(t, Path(r"D:\repo"), Path(r"D:\scratch"))
        assert out.sys_path == r"C:\elsewhere\lib"

    def test_everything_else_survives_the_move(self) -> None:
        t = target(sys_path=str(Path(r"D:\repo") / "src"))
        out = _rerooted(t, Path(r"D:\repo"), Path(r"D:\scratch"))
        for field in ("path", "name", "source", "header", "package", "lineno", "end_lineno"):
            assert getattr(out, field) == getattr(t, field)


def result(target_name: str = "pkg/mod.py::f", verdict: Verdict = Verdict.KILLED) -> Result:
    return Result(
        target=target_name,
        kind="compare",
        verdict=verdict,
        mutant="return 2",
        detail="x" * 500,
    )


class TestResultRow:
    def test_the_verdict_is_a_string_not_an_enum(self) -> None:
        """It goes straight into JSON, where an enum is not serialisable."""
        row = result().as_row()
        assert row["verdict"] == Verdict.KILLED.value
        assert isinstance(row["verdict"], str)
        json.dumps(row)

    def test_a_long_detail_is_truncated(self) -> None:
        """A mutant's detail can be a whole traceback, and audit.json holds thousands."""
        assert len(result().as_row()["detail"]) == 300

    def test_the_row_carries_what_the_report_reads(self) -> None:
        row = result().as_row()
        assert set(row) == {
            "target", "kind", "verdict", "mutant", "witness", "strength", "provenance",
            "detail",
        }


class TestAuditByFunction:
    def test_results_are_grouped_by_target(self) -> None:
        audit = Audit(results=[result("a::f"), result("b::g"), result("a::f")])
        grouped = audit.by_function()
        assert sorted(grouped) == ["a::f", "b::g"]
        assert len(grouped["a::f"]) == 2

    def test_the_order_within_a_group_is_the_order_measured(self) -> None:
        first, second = result("a::f"), result("a::f", Verdict.PROVEN_GAP)
        grouped = Audit(results=[first, second]).by_function()
        assert grouped["a::f"] == [first, second]

    def test_no_results_is_an_empty_mapping_not_an_error(self) -> None:
        assert Audit().by_function() == {}


class TestHealthJson:
    def test_a_clean_run(self) -> None:
        out = _health_json(SuiteHealth(ran=True, exit_code=0, passed=10))
        assert out["passed"] == 10
        assert out["caveat"] == ""
        assert out["failing"] == []
        assert out["deselected"] == 0

    def test_a_failing_run_names_the_tests(self) -> None:
        health = SuiteHealth(
            ran=True, exit_code=1, passed=8, failed=2,
            failing=("tests/t.py::a", "tests/t.py::b"),
            deselected=14,
        )
        out = _health_json(health)
        assert out["failing"] == ["tests/t.py::a", "tests/t.py::b"]
        assert out["deselected"] == 14
        assert "2 test(s) failed" in out["caveat"]

    def test_it_is_json_serialisable(self) -> None:
        json.dumps(_health_json(SuiteHealth(ran=False, exit_code=-1)))


class TestWriteJson:
    def test_it_writes_a_readable_report(self, tmp_path: Path) -> None:
        audit = Audit(results=[result()])
        path = tmp_path / "audit.json"
        write_json(audit, path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["results"][0]["target"] == "pkg/mod.py::f"
        assert data["kill_rate"] is not None

    def test_the_caller_owns_the_directory(self, tmp_path: Path) -> None:
        """Stated rather than assumed: this does NOT create one.

        `cmd_audit` mkdirs `--out` before calling, so the CLI is safe; a library caller
        that does not gets a FileNotFoundError rather than a silent miss. This test
        records which of the two it is, because the first version of it assumed the
        opposite and failed.
        """
        with pytest.raises(FileNotFoundError):
            write_json(Audit(), tmp_path / "a" / "b" / "audit.json")

    def test_the_cli_creates_the_directory_it_is_given(self, tmp_path: Path) -> None:
        """The other half of the pair, so the guarantee lives somewhere."""
        source = (ROOT / "src" / "suite_auditor" / "cli.py").read_text(encoding="utf-8")
        assert "out.mkdir(parents=True, exist_ok=True)" in source, (
            "the CLI no longer creates --out, so write_json must"
        )


class TestCallsFor:
    def test_an_unknown_target_has_no_calls(self) -> None:
        assert calls_for({}, "pkg/mod.py", "f") == []

    def test_an_exact_key_wins(self) -> None:
        calls = {"pkg/mod.py::C.f": [{"args": "(1,)"}], "pkg/mod.py::f": [{"args": "(2,)"}]}
        assert calls_for(calls, "pkg/mod.py", "C.f") == [{"args": "(1,)"}]

    def test_a_method_falls_back_to_its_bare_name(self) -> None:
        """The tracer may have recorded `f` where the target is `C.f`."""
        calls = {"pkg/mod.py::f": [{"args": "(2,)"}]}
        assert calls_for(calls, "pkg/mod.py", "C.f") == [{"args": "(2,)"}]


class TestScratchBasetemp:
    def test_each_copy_gets_its_own_basetemp(self, tmp_path: Path) -> None:
        """Two mutants running at once must not share pytest's temp directory."""
        (tmp_path / "src" / "pkg").mkdir(parents=True)
        (tmp_path / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        with Scratch(tmp_path, copies=2) as scratch:
            assert scratch.basetemp(0) != scratch.basetemp(1)
            assert scratch.basetemp(0).parent == scratch.root
