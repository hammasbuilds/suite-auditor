"""What the audit says about the target's own suite before anything is mutated.

A suite that is already failing cannot be mutated against: a failing test "kills" every
mutant it meets, so the kill rate would describe the breakage rather than the suite. The
audit therefore reports the target suite's health, and that report has to be specific
enough to act on.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from suite_auditor.coverage import _read_health  # noqa: E402


class TestWhichTestsFailed:
    """A count of failures is not actionable; the ids are.

    The report said "2 test(s) failed" and nothing more. In the run that prompted this,
    those two failures were the tool's own doing: the scratch copy leaves out a
    junctioned data directory, so tests that read through it fail on the copy and pass in
    the repository. A reader needs to be able to tell a broken suite from a suite this
    tool broke, and a count cannot show the difference.
    """

    SUMMARY = (
        "=========================== short test summary info ===========================\n"
        "FAILED tests/test_a.py::test_one - FileNotFoundError: data/x.tsv\n"
        "FAILED tests/test_b.py::TestK::test_two - AssertionError\n"
        "ERROR tests/test_c.py::test_three\n"
        "2 failed, 233 passed in 4.2s\n"
    )

    def test_the_ids_come_from_pytests_own_summary(self):
        health = _read_health(self.SUMMARY, "", 1)
        assert health.failing == (
            "tests/test_a.py::test_one",
            "tests/test_b.py::TestK::test_two",
            "tests/test_c.py::test_three",
        )
        assert health.passed == 233
        assert health.failed == 2

    def test_the_caveat_names_them(self):
        caveat = _read_health(self.SUMMARY, "", 1).caveat()
        assert "tests/test_a.py::test_one" in caveat
        assert caveat.startswith("2 test(s) failed")

    def test_only_the_first_three_are_named(self):
        """A suite with 200 failures must not print 200 ids into one line."""
        lines = "".join(f"FAILED tests/t.py::test_{i}\n" for i in range(10))
        caveat = _read_health(f"{lines}10 failed in 1s", "", 1).caveat()
        assert "and 7 more" in caveat
        assert "test_9" not in caveat

    def test_a_clean_run_names_nothing(self):
        health = _read_health("235 passed in 4.2s", "", 0)
        assert health.failing == ()
        assert health.caveat() == ""
        assert health.clean

    def test_an_id_reported_twice_is_listed_once(self):
        """A test that both fails and errors appears in the summary twice."""
        blob = (
            "FAILED tests/t.py::test_one - AssertionError\n"
            "ERROR tests/t.py::test_one\n"
            "1 failed, 1 error in 1s\n"
        )
        assert _read_health(blob, "", 1).failing == ("tests/t.py::test_one",)
