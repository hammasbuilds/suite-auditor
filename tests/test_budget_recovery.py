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
        "6 of the 7 recovered",
        "21 gaps in total",
    ):
        assert fragment in section, fragment


@pytest.mark.skipif(not PAPER.exists(), reason="paper/main.md is not shipped")
def test_the_quoted_durations_are_the_measured_ones(audited):
    """The four durations are printed in the prose, so they are read back from the file."""
    paper = PAPER.read_text(encoding="utf-8")
    inside = sorted(r["seconds"] for r in audited if r["seconds"] < 300)
    quoted = ", ".join(f"{s:.0f}" for s in inside[:-1]) + f" and {inside[-1]:.0f} seconds"
    assert quoted in paper, (quoted, inside)


def test_the_proven_gap_comparison_is_reported_as_a_difference(audited):
    """6 of 7 against 51 of 112 is not agreement, and a draft read it as agreement.

    85.7% against 45.5%, Fisher two-sided p = 0.054. The section quoted both counts
    beside a claim that the recovered packages "score like the included ones", which
    the kill rate supports weakly and this figure contradicts. Recomputed here so the
    numbers in that paragraph cannot drift back into agreeing.
    """
    import math

    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    with_gaps = sum(1 for r in audited if r["audit"]["proven_gaps"])
    recovered_share = with_gaps / len(audited)
    published_share = summary["projects_with_a_proven_gap"] / summary["audited"]
    assert recovered_share > published_share, (recovered_share, published_share)

    a, b = with_gaps, len(audited) - with_gaps
    c = summary["projects_with_a_proven_gap"]
    d = summary["audited"] - c
    total = a + b + c + d

    def table(w, x, y, z):
        return math.comb(w + x, w) * math.comb(y + z, y) / math.comb(total, w + y)

    observed = table(a, b, c, d)
    p = sum(
        q
        for x in range(max(0, (a + c) - (c + d)), min(a + b, a + c) + 1)
        if (q := table(x, a + b - x, a + c - x, (c + d) - (a + c - x))) <= observed + 1e-12
    )

    paper = (PAPER).read_text(encoding="utf-8")
    section = paper[paper.index("## 5.1 Re-measuring") :].split("\n# ")[0]
    assert f"{recovered_share * 100:.1f}% against {published_share * 100:.1f}%" in section
    assert f"*p* = {p:.3f}" in section, f"{p:.3f}"


def test_the_order_statistic_interval_is_the_one_quoted(audited):
    """Section 5.1 quotes [0.824, 0.877] at 87.5% coverage from seven points."""
    import math

    rates = sorted(r["audit"]["kill_rate"] for r in audited)
    assert len(rates) == 7, len(rates)
    low, high = rates[1], rates[5]
    coverage = sum(math.comb(7, k) for k in range(2, 6)) / 2**7
    paper = PAPER.read_text(encoding="utf-8")
    section = paper[paper.index("## 5.1 Re-measuring") :].split("\n# ")[0]
    assert f"[{low:.3f}, {high:.3f}]" in section, (low, high)
    assert f"{coverage * 100:.1f}% coverage" in section, coverage
    assert low <= json.loads(SUMMARY.read_text(encoding="utf-8"))["kill_rate_median"] <= high
