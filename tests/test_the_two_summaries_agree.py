"""The repository publishes the census twice. The two publications must say the same thing.

`docs/census-summary.json` feeds the README. `research/results/auditability.json` feeds
`paper/main.md`. Both are derived from `docs/census-rows.json.gz` by different code -
`scripts/census_summary.py` and `research/metrics.py` - and nothing compared them, so for
one revision the README said 63 packages exceeded the audit budget and the paper said 67,
and the README said the median kill rate was 0.848 while the paper said 0.850.

Neither number was stale. `metrics.py` classified exclusion reasons by substring, and
"memory budget exceeded: 4,718 MB over the 4,096 MB cap" contains "budget exceeded", so
four memory-cap exclusions and three from a longer budget were counted as time-budget
exclusions. And its `median` was a nearest-rank quantile, which over an even sample
returns the upper of the two middle values.

Both were found by comparing the two files by hand. This file does it on every run.
"""

from __future__ import annotations

import gzip
import json
import statistics
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "docs" / "census-rows.json.gz"
SUMMARY = ROOT / "docs" / "census-summary.json"
RESEARCH = ROOT / "research" / "results" / "auditability.json"

pytestmark = pytest.mark.skipif(
    not (LEDGER.exists() and SUMMARY.exists() and RESEARCH.exists()),
    reason="the research tree is not shipped with the tests",
)

sys.path.insert(0, str(ROOT / "research"))


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    with gzip.open(LEDGER, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))["rows"]


@pytest.fixture(scope="module")
def summary() -> dict:
    return json.loads(SUMMARY.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def research() -> dict:
    return json.loads(RESEARCH.read_text(encoding="utf-8"))


def test_both_summaries_count_the_same_audited_packages(summary, research):
    assert summary["audited"] == research["funnel"]["audited"]["k"]
    assert summary["considered"] == research["funnel"]["total"]


def test_both_summaries_report_the_same_medians(summary, research):
    """The defect this file was written for: 0.8481 in one file, 0.8500 in the other."""
    pairs = [
        ("kill_rate_median", "kill_rate"),
        ("covered_fraction_median", "covered_fraction"),
    ]
    for summary_key, research_key in pairs:
        mine = summary[summary_key]
        theirs = research["distributions"][research_key]["median"]
        assert abs(mine - theirs) < 5e-4, (
            f"docs/census-summary.json reports {summary_key}={mine} and "
            f"research/results/auditability.json reports {research_key} median={theirs}. "
            f"Same 112 rows, same quantity, two values."
        )


def test_the_medians_are_the_ledger_s_medians(rows, summary):
    """Both files agreeing is not enough if they agree on the wrong number."""
    audited = [r for r in rows if not r.get("excluded") and isinstance(r.get("audit"), dict)]
    assert len(audited) == summary["audited"], (len(audited), summary["audited"])
    for key, field in (
        ("kill_rate_median", "kill_rate"),
        ("covered_fraction_median", "covered_fraction"),
    ):
        values = [r["audit"][field] for r in audited if r["audit"].get(field) is not None]
        assert abs(summary[key] - round(statistics.median(values), 4)) < 5e-4, (
            key,
            summary[key],
            statistics.median(values),
        )


def test_the_time_budget_stage_counts_only_time_budget_exclusions(rows, research):
    """A substring match put three other criteria inside one funnel row.

    The funnel is a classification: each package falls out at exactly one stage, so a
    stage that absorbs a neighbouring criterion overstates itself by exactly what it
    takes from the other. Counted straight off the recorded reasons here, with no
    substring matching at all.
    """
    reasons = [str(r.get("excluded") or "") for r in rows]
    exact_300 = sum(1 for why in reasons if why.startswith("budget exceeded (300s)"))
    memory = sum(1 for why in reasons if why.startswith("memory budget exceeded"))
    other_budget = sum(
        1
        for why in reasons
        if why.startswith("budget exceeded") and not why.startswith("budget exceeded (300s)")
    )
    assert exact_300 > 0 and memory > 0 and other_budget > 0, (
        exact_300,
        memory,
        other_budget,
    )

    stages = research["funnel"]["stages"]
    time_row = next(k for k in stages if k.startswith("audit budget exceeded (300s)"))
    assert stages[time_row]["excluded"] == exact_300, (
        f"the funnel's 300-second row counts {stages[time_row]['excluded']} where the "
        f"ledger records {exact_300} packages whose reason begins "
        f"'budget exceeded (300s)'. The extra {memory} memory-cap and {other_budget} "
        f"longer-budget exclusions belong to their own stages."
    )

    memory_row = next((k for k in stages if "memory cap" in k), None)
    assert memory_row is not None, (
        f"{memory} packages were excluded by the memory cap and the funnel has no stage "
        f"for them, so they are being counted somewhere they do not belong"
    )
    assert stages[memory_row]["excluded"] == memory


def test_every_exclusion_falls_out_at_exactly_one_stage(rows, research):
    """The funnel's own arithmetic: stages plus audited must be the whole sample."""
    stages = research["funnel"]["stages"]
    total = sum(s["excluded"] for s in stages.values()) + research["funnel"]["audited"]["k"]
    assert total == research["funnel"]["total"] == len(rows), (
        total,
        research["funnel"]["total"],
        len(rows),
    )


def test_the_summary_and_the_funnel_agree_on_each_exclusion_reason(summary, research):
    """Same counts, grouped differently, and the groupings have to reconcile.

    `census-summary.json` keys exclusions by reason prefix; the funnel groups them into
    stages plus one "other reason" bucket. Every reason the summary records must be
    accounted for exactly once on the funnel's side.
    """
    stages = research["funnel"]["stages"]
    staged = sum(s["excluded"] for s in stages.values())
    assert staged == summary["excluded"], (staged, summary["excluded"])
    assert sum(summary["exclusions"].values()) == summary["excluded"]
