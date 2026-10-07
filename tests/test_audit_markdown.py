"""AUDIT.md - the report a maintainer is meant to act on.

`--out` writes this file, and the first coverage measurement ever taken on this package
showed `write_markdown` had **no test at all**: 61 statements, the tool's main
human-readable deliverable, never executed by the suite. Everything asserted here is
something a reader of that file depends on being present.

The reproduction is the point of the whole report. A gap stated without the call that
demonstrates it is a claim, and this tool's position is that a claim needs proof - so a
gap section with no witness in it would be the report contradicting the tool.
"""

from __future__ import annotations

from suite_auditor.report import write_markdown
from suite_auditor.types import Audit, Result, Verdict


def _audit() -> Audit:
    return Audit(
        results=[
            Result(
                target="pricing/rules.py::discount",
                mutant="total >= 20 -> total >= 19",
                kind="comparison",
                verdict=Verdict.PROVEN_GAP,
                # The real keys the differential pass emits: args / old / new. Written
                # from invented names (`call`, `before`, `after`) this fixture produced
                # a Gaps table of `None | None | None` and the test still "passed" on
                # the rows above it.
                witness={
                    "args": "discount(19, 'SAVE10')",
                    "old": "ok: 0",
                    "new": "ok: 1.9",
                },
                detail="both versions return a value, and the values differ",
                strength=1,
                provenance="observed",
            ),
            Result(
                target="pricing/rules.py::shipping",
                mutant="weight * 2 -> weight * 3",
                kind="arithmetic",
                verdict=Verdict.UNPROVEN,
                detail="all 64 argument sets raised on both sides",
            ),
            Result(
                target="pricing/rules.py::with_tax",
                mutant="rate + 1 -> rate + 2",
                kind="arithmetic",
                verdict=Verdict.UNPROVEN,
                detail="all agree",
            ),
            Result(
                target="pricing/rules.py::total",
                mutant="a + b -> a - b",
                kind="arithmetic",
                verdict=Verdict.KILLED,
            ),
        ],
        uncovered=["pricing/rules.py::refund"],
        failing_only=["pricing/rules.py::audit_log"],
        covered_total=4,
        audited_functions=4,
        planned_mutants=4,
        seconds=12.0,
    )


def test_a_proven_gap_is_written_with_the_call_that_proves_it(tmp_path) -> None:
    path = tmp_path / "AUDIT.md"
    write_markdown(_audit(), path, "pricing")
    text = path.read_text(encoding="utf-8")
    assert "pricing/rules.py::discount" in text
    assert "discount(19, 'SAVE10')" in text, (
        "a gap stated without its reproduction is a claim, and this tool's whole "
        "position is that a claim needs proof"
    )
    assert "ok: 0" in text and "ok: 1.9" in text
    assert "observed" in text, "the reader has to know the input was one the tests use"


def test_the_unproven_survivors_are_broken_out_by_reason(tmp_path) -> None:
    """Not "2 unproven" but which 2, because only one reason is about the suite.

    `no_input` and `method` are this tool failing to reach the code; `equivalent` may
    mean the mutant genuinely cannot be distinguished. Reported as one number they read
    as two more defects in somebody's suite.
    """
    path = tmp_path / "AUDIT.md"
    write_markdown(_audit(), path, "pricing")
    text = path.read_text(encoding="utf-8")
    assert "Survivors with no separating input" in text
    assert "2 mutants survived" in text
    assert "| why it was not proven | mutants |" in text
    # One of each reason went in, so both rows have to come out.
    body = text.split("Survivors with no separating input", 1)[1]
    assert body.count("| 1 |") >= 2, body


def test_a_killed_mutant_is_not_listed_as_a_gap(tmp_path) -> None:
    path = tmp_path / "AUDIT.md"
    write_markdown(_audit(), path, "pricing")
    text = path.read_text(encoding="utf-8")
    assert "a + b -> a - b" not in text


def test_uncovered_and_failing_only_functions_are_both_named(tmp_path) -> None:
    """A kill rate over the covered functions means nothing without these two lists.

    A suite reaching three functions and killing every mutant in them reports the same
    rate as one reaching all of them, so the file has to say what was never reached -
    and, separately, what was reached only by a test that was already failing.
    """
    path = tmp_path / "AUDIT.md"
    write_markdown(_audit(), path, "pricing")
    text = path.read_text(encoding="utf-8")
    assert "Reached by no test" in text
    assert "pricing/rules.py::refund" in text
    assert "Reached only by tests that fail" in text
    assert "pricing/rules.py::audit_log" in text


def test_an_audit_with_nothing_in_it_still_writes_a_readable_file(tmp_path) -> None:
    """The empty case has to be a file that says nothing was found, not a crash.

    `no_targets` and "every mutant was killed" and "no gaps found" all look alike from
    the outside, and the report is where they have to be told apart.
    """
    path = tmp_path / "AUDIT.md"
    write_markdown(Audit(no_targets=True), path, "empty")
    text = path.read_text(encoding="utf-8")
    assert "empty" in text
    assert "0 proven gaps" in text
    assert "Survivors with no separating input" not in text


def test_the_gaps_table_never_renders_a_bare_none(tmp_path) -> None:
    """A renamed witness key would silently produce `None` cells, not an error.

    `write_markdown` reads `args`, `old` and `new` off the witness with `.get`, so any
    rename upstream turns every gap row into `| None | ... | None | None |` and the
    report still writes successfully. That is how this file's own first fixture passed
    while describing a table of nothing: the keys were invented and the assertions
    above the table did not look at it.
    """
    path = tmp_path / "AUDIT.md"
    write_markdown(_audit(), path, "pricing")
    rows = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("| `pricing/rules.py::")
    ]
    assert rows, "no gap row was written at all"
    for row in rows:
        assert "`None`" not in row, (
            f"a gap row has an empty cell, which means a witness key was renamed: {row}"
        )
