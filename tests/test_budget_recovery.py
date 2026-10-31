"""Section 5.1's numbers, recomputed from docs/budget-recovery.json.

The census excludes 63 of 1,000 packages for exceeding a budget, and section 5 called
that "the largest single recoverable exclusion" without recovering any of it. 12 of the
63 were re-audited at a 1,800-second budget. The result is in the manuscript, so it is
checked here against the file it came from rather than retyped.

Two of the findings are about the measurement rather than the packages, and both are
asserted: that "budget exceeded" covers several different criteria, and that four of the
seven recovered packages finish *inside* the 300-second budget that excluded them, which
makes a wall-clock budget an unstable exclusion criterion rather than a property of a
project.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RECOVERY = ROOT / "docs" / "budget-recovery.json"
SUMMARY = ROOT / "docs" / "census-summary.json"
PAPER = ROOT / "paper" / "main.md"

pytestmark = pytest.mark.skipif(
    not RECOVERY.exists(), reason="docs/budget-recovery.json is not shipped with the tests"
)


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    return json.loads(RECOVERY.read_text(encoding="utf-8"))["rows"]


@pytest.fixture(scope="module")
def audited(rows) -> list[dict]:
    return [r for r in rows if r.get("audit")]


def test_twelve_were_re_measured_and_seven_completed(rows, audited):
    assert len(rows) == 12, len(rows)
    assert len(audited) == 7, [r["package"] for r in audited]
    assert len(rows) - len(audited) == 5


def test_the_five_that_still_failed_failed_for_three_different_reasons(rows):
    """ "Budget exceeded" is not one criterion, which is the reason the funnel row split."""
    reasons = [r["excluded"] for r in rows if r.get("excluded")]
    assert len(reasons) == 5, reasons
    time_budget = [w for w in reasons if w.startswith("budget exceeded")]
    memory = [w for w in reasons if w.startswith("memory budget exceeded")]
    install = [w for w in reasons if w.startswith("candidate failed")]
    assert len(time_budget) == 1, time_budget
    assert len(memory) == 2, memory
    assert len(install) == 2, install
    assert len(time_budget) + len(memory) + len(install) == len(reasons)


def test_the_recovered_packages_score_like_the_included_ones(audited):
    """The direction the selection-bias paragraph predicts, measured instead of assumed."""
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    recovered = round(statistics.median(r["audit"]["kill_rate"] for r in audited), 4)
    published = summary["kill_rate_median"]
    assert abs(recovered - published) < 5e-4, (recovered, published)
    assert f"{recovered:.3f}" == "0.848", f"{recovered:.3f}"


def test_six_of_the_seven_carry_a_proven_gap(audited):
    with_gaps = [r for r in audited if r["audit"]["proven_gaps"]]
    assert len(with_gaps) == 6, [r["package"] for r in with_gaps]
    assert sum(r["audit"]["proven_gaps"] for r in audited) == 21


def test_four_of_the_seven_finish_inside_the_budget_that_excluded_them(audited):
    """The finding that makes the funnel's budget row unreproducible.

    Same code, same packages, a less loaded machine: four audits that had exceeded 300
    seconds complete in under 300, and a fifth in 302. A wall-clock budget measures the
    machine as much as the project, so the 60-package row is not a stable fact about
    those 60 packages.
    """
    payload = json.loads(RECOVERY.read_text(encoding="utf-8"))
    original = payload["original_audit_budget_seconds"]
    assert original == 300
    seconds = sorted(r["seconds"] for r in audited)
    inside = [s for s in seconds if s < original]
    assert len(inside) == 4, seconds
    assert max(inside) < original
    assert min(seconds) < original / 1.5, seconds
    # the fifth, within one percent of the budget it was excluded by
    fifth = seconds[4]
    assert original < fifth < original * 1.01, fifth


def test_everything_ran_under_the_raised_budget(audited):
    payload = json.loads(RECOVERY.read_text(encoding="utf-8"))
    for row in audited:
        assert row["seconds"] <= payload["audit_budget_seconds"], row["package"]


@pytest.mark.skipif(not PAPER.exists(), reason="paper/main.md is not shipped")
def test_the_manuscript_quotes_these_numbers():
    paper = PAPER.read_text(encoding="utf-8")
    section = paper[paper.index("## 5.1 Re-measuring the budget exclusions") :]
    section = section.split("\n# ")[0]
    for fragment in (
        "12 of the 63",
        "1,800-second",
        "**7 of the 12 completed.**",
        "171, 185, 194 and 203 seconds",
        "6 of the 7 carry at least one proven gap, 21 in total",
    ):
        assert fragment in section, fragment


@pytest.mark.skipif(not PAPER.exists(), reason="paper/main.md is not shipped")
def test_the_quoted_durations_are_the_measured_ones(audited):
    """The four durations are printed in the prose, so they are read back from the file."""
    paper = PAPER.read_text(encoding="utf-8")
    inside = sorted(r["seconds"] for r in audited if r["seconds"] < 300)
    quoted = ", ".join(f"{s:.0f}" for s in inside[:-1]) + f" and {inside[-1]:.0f} seconds"
    assert quoted in paper, (quoted, inside)
