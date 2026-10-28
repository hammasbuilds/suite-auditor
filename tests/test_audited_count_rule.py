"""The census's own closing count must use the same rule as the published summary.

`scored_nothing()` was written to stop a project that planned mutants and scored none
from being averaged over, and it was called from the writer and from the summariser. The
closing line of the census was not changed with them: it tested `"audit" in row`, which
is true of a row holding an audit that planned 524 mutants and scored zero. A completed
1,000-project run therefore printed "117 of 1000 projects audited" while
docs/census-summary.json published medians over 110 - the same two numbers, from the same
file, in the same run.

Resumed rows are the third reader. The census is restarted for every harness fix, so a
row keeps the classification that was current the day it was measured; the rule has to be
re-applied to what is already on disk, not only to rows written after the fix.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from target_census import audited_rows, scored_nothing  # noqa: E402


def _audit(planned: int, scored: int, detail: str = "the covering tests fail") -> dict:
    return {
        "planned_mutants": planned,
        "scored": scored,
        "results": [{"detail": detail} for _ in range(planned - scored)],
    }


def test_a_row_that_scored_nothing_is_not_counted_as_audited() -> None:
    rows = [{"package": "arrow", "audit": _audit(504, 0)}]
    assert audited_rows(rows) == [], (
        "a project that planned 504 mutants and scored none measured nothing; counting it "
        "as audited is what put 117 beside medians taken over 110"
    )


def test_a_row_that_scored_something_is_counted() -> None:
    rows = [{"package": "attrs", "audit": _audit(120, 118)}]
    assert len(audited_rows(rows)) == 1


def test_the_count_and_the_predicate_cannot_disagree() -> None:
    """Whatever the predicate excludes, the count excludes - for every shape of row."""
    rows = [
        {"package": "a", "audit": _audit(10, 10)},
        {"package": "b", "audit": _audit(10, 0)},
        {"package": "c", "audit": _audit(0, 0)},
        {"package": "d", "excluded": "no sdist"},
        {"package": "e", "excluded": "planned and scored none", "empty_audit": _audit(15, 0)},
    ]
    counted = {r["package"] for r in audited_rows(rows)}
    predicate_keeps = {
        r["package"] for r in rows if "audit" in r and not scored_nothing(r["audit"])
    }
    assert counted == predicate_keeps == {"a"}


def test_excluded_rows_without_an_audit_are_ignored_not_crashed_on() -> None:
    """Most of the thousand rows have no audit key at all, and some are not dicts.

    An earlier run wrote a bare string into the results list when a candidate failed
    before its row was built, and a count that indexes every row would die on it after
    hours of measurement.
    """
    rows = [{"excluded": "sdist has no test directory"}, "a stray string", {}]
    assert audited_rows(rows) == []
