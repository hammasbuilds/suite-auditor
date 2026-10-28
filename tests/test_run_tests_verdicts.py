"""`run_tests` turns a pytest run into a verdict, and the verdict decides the headline.

The last three functions `suite-auditor coverage .` reports as unreached are `run_tests`,
`_prove` and `_kill`. `run_tests` is the one whose answer is published: every mutant's
fate is this function's `True`, `False` or `None`, and the kill rate is a count of them.

Three of its rules are easy to get wrong in a way no exception reveals:

  * no test ids means `None` - unscorable - and never `True`. A mutant in a function the
    suite does not reach must not be counted as noticed.
  * pytest's exit code 5 is "no tests collected". Treating it as a failure would make
    every unreachable mutant look killed, which inflates the one number the tool exists
    to report.
  * the output has to show that something passed. A run that collected nothing, or died
    before running, exits non-zero for reasons that are not a mutant being noticed.

`run` is substituted, so these are about the rules rather than about pytest.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from suite_auditor import audit as audit_module  # noqa: E402
from suite_auditor.audit import run_tests  # noqa: E402

PASSED = "4 passed in 0.12s"
FAILED = "1 failed, 3 passed in 0.14s"


@pytest.fixture
def pytest_run(monkeypatch):
    """Substitute the subprocess; record the command, return a chosen result."""
    state: dict = {"result": (0, PASSED), "commands": []}

    def fake_run(cmd, repo, env, timeout):  # noqa: ANN001, ANN202
        state["commands"].append(list(cmd))
        outcome = state["result"]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(audit_module, "run", fake_run)
    return state


class TestVerdicts:
    def test_everything_passing_is_true(self, tmp_path, pytest_run) -> None:
        assert run_tests(tmp_path, ["tests/t.py::a"]) is True

    def test_a_failure_is_false(self, tmp_path, pytest_run) -> None:
        """False means the suite noticed the mutant, which is the good case."""
        pytest_run["result"] = (1, FAILED)
        assert run_tests(tmp_path, ["tests/t.py::a"]) is False

    def test_no_test_ids_is_unscorable_rather_than_a_pass(self, tmp_path, pytest_run) -> None:
        """A mutant in a function no test reaches must not count as noticed."""
        assert run_tests(tmp_path, []) is None
        assert pytest_run["commands"] == [], "pytest was run with no tests to run"

    def test_exit_code_five_is_unscorable(self, tmp_path, pytest_run) -> None:
        """5 is "no tests collected" - neither a pass nor a kill.

        Counting it as a failure would mark every unreachable mutant killed and inflate
        the kill rate, which is the single number this tool exists to report.
        """
        pytest_run["result"] = (5, "no tests ran in 0.01s")
        assert run_tests(tmp_path, ["tests/t.py::a"]) is None

    def test_output_with_no_passing_tests_is_unscorable(self, tmp_path, pytest_run) -> None:
        """A collection error exits non-zero without any mutant being noticed."""
        pytest_run["result"] = (2, "ERROR tests/t.py - ImportError: no module named x")
        assert run_tests(tmp_path, ["tests/t.py::a"]) is None

    def test_an_oserror_is_unscorable_rather_than_a_crash(self, tmp_path, pytest_run) -> None:
        """A missing interpreter must not end an audit of a thousand mutants."""
        pytest_run["result"] = OSError("no such interpreter")
        assert run_tests(tmp_path, ["tests/t.py::a"]) is None

    def test_a_timeout_is_raised_for_the_caller_to_judge(self, tmp_path, pytest_run) -> None:
        """For a mutant it usually means an infinite loop, which the caller counts as
        noticed - so this must travel up rather than becoming None here."""
        from suite_auditor.workspace import Timeout

        pytest_run["result"] = Timeout("took too long")
        with pytest.raises(Timeout):
            run_tests(tmp_path, ["tests/t.py::a"])


class TestTheCommand:
    def test_the_test_ids_are_passed_to_pytest(self, tmp_path, pytest_run) -> None:
        run_tests(tmp_path, ["tests/t.py::a", "tests/t.py::b"])
        cmd = pytest_run["commands"][0]
        assert cmd[-2:] == ["tests/t.py::a", "tests/t.py::b"]

    def test_it_stops_at_the_first_failure(self, tmp_path, pytest_run) -> None:
        """A mutant is killed by one failing test; the rest of the suite is wasted time."""
        run_tests(tmp_path, ["tests/t.py::a"])
        assert "-x" in pytest_run["commands"][0]

    def test_the_cache_is_off(self, tmp_path, pytest_run) -> None:
        """Mutants run in parallel copies, and a shared cache directory is a race."""
        run_tests(tmp_path, ["tests/t.py::a"])
        cmd = pytest_run["commands"][0]
        assert "no:cacheprovider" in cmd

    def test_a_basetemp_is_passed_when_given(self, tmp_path, pytest_run) -> None:
        run_tests(tmp_path, ["tests/t.py::a"], basetemp=tmp_path / "bt")
        assert any(a.startswith("--basetemp=") for a in pytest_run["commands"][0])

    def test_no_basetemp_argument_when_none(self, tmp_path, pytest_run) -> None:
        run_tests(tmp_path, ["tests/t.py::a"])
        assert not any(a.startswith("--basetemp=") for a in pytest_run["commands"][0])

    def test_the_given_interpreter_is_used(self, tmp_path, pytest_run) -> None:
        """The target's interpreter, not ours: its dependencies are what matter."""
        run_tests(tmp_path, ["tests/t.py::a"], python="D:/elsewhere/python.exe")
        assert pytest_run["commands"][0][0] == "D:/elsewhere/python.exe"

    def test_a_very_long_id_list_collapses_to_files(self, tmp_path, pytest_run) -> None:
        """The same budget `_test_args` applies, reaching pytest through this path."""
        ids = [f"tests/test_{i // 50}.py::test_{i}" for i in range(2000)]
        run_tests(tmp_path, ids)
        passed = pytest_run["commands"][0]
        assert not any("::" in a for a in passed)
