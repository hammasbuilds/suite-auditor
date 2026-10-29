"""The committed ledger reproduces every published census figure.

The raw census is 19 MB and gitignored, so until this existed the repository held a
reduction - six medians and a package list - and nothing in it could say what any one
project scored or show a row changing. blast-radius kept the same arrangement until a
re-measurement overwrote four measured rows with install timeouts and there was no copy
of what they had said.

So the test that matters is not "the ledger exists": it is that the summary in the
repository is what the ledger produces. If those two ever disagree, one of them is stale
and the published figures are not recomputable from the repository.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SUMMARY = ROOT / "docs" / "census-summary.json"
LEDGER = ROOT / "docs" / "census-rows.json.gz"
RAW = ROOT / "docs" / "target-census.json"

pytestmark = pytest.mark.skipif(
    not (SUMMARY.exists() and LEDGER.exists()),
    reason="docs/ is not shipped with the tests",
)

sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    with gzip.open(LEDGER, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))["rows"]


@pytest.fixture(scope="module")
def summary() -> dict:
    return json.loads(SUMMARY.read_text(encoding="utf-8"))


def test_the_committed_summary_is_what_the_committed_ledger_produces(rows, summary):
    """The whole point. Everything else here is a detail of it."""
    from census_summary import summarise

    assert summarise(rows) == summary


def test_the_ledger_holds_one_row_per_project_considered(rows, summary):
    assert len(rows) == summary["considered"]
    packages = [r["package"] for r in rows if "package" in r]
    assert len(set(packages)) == len(packages), "a package appears twice in the ledger"


def test_the_ledger_carries_no_mutated_source(rows):
    """What it drops is the 19 MB: one entry per scored mutant, with its source.

    A ledger that quietly kept them would be the artefact it replaces.
    """
    for row in rows:
        audit = row.get("audit")
        if isinstance(audit, dict):
            assert "results" not in audit, f"{row.get('package')} still carries results"


def test_the_dropped_mutant_count_survives_a_rebuild(rows):
    """Rebuilding the ledger from the ledger must not change it.

    `results_dropped` was computed as `len(audit.get("results") or [])` on every pass, so
    a ledger built from a ledger - which has no `results` - silently set every count to
    zero. The same shape of bug as calling len() on an already-reduced value.
    """
    from census_summary import ledger

    again = ledger(rows)
    assert again == rows
    dropped = sum((r.get("audit") or {}).get("results_dropped", 0) for r in rows)
    assert dropped > 0, "no row records how many scored mutants were dropped"


def test_the_ledger_is_small_enough_to_commit(rows):
    """It is in git, so its size is a property worth holding to.

    The figure is generous on purpose: this fails if the per-mutant detail creeps back,
    not if a hundred more projects are measured.
    """
    assert LEDGER.stat().st_size < 1_000_000, (
        f"the ledger is {LEDGER.stat().st_size / 1024:.0f} KB; something per-mutant has "
        "come back into it"
    )


def test_the_figures_the_readme_quotes_come_out_of_the_ledger(rows):
    """Recomputed from the rows, not read from the summary beside them."""
    from census_summary import scored_nothing, summarise

    recomputed = summarise(rows)
    audited = [
        r for r in rows
        if not r.get("excluded") and not scored_nothing(r.get("audit") or {})
    ]
    assert recomputed["audited"] == len(audited)
    gaps = [(r.get("audit") or {}).get("proven_gaps", 0) for r in audited]
    assert recomputed["proven_gaps_total"] == sum(gaps)
    assert recomputed["projects_with_a_proven_gap"] == sum(1 for g in gaps if g)


@pytest.mark.skipif(not RAW.exists(), reason="the 19 MB census is not in this checkout")
def test_the_ledger_agrees_with_the_raw_census_when_it_is_present():
    """Only where the raw artefact happens to be on the machine that ran the sweep.

    Skipped elsewhere by design - the raw census is gitignored, and a test that needed it
    would pass here and skip everywhere else, which is how a check stops being one.
    """
    from census_summary import ledger, read_census, summarise

    raw = read_census(RAW)
    with gzip.open(LEDGER, "rb") as fh:
        committed = json.loads(fh.read().decode("utf-8"))["rows"]
    assert ledger(raw) == committed
    assert summarise(raw) == summarise(committed)
