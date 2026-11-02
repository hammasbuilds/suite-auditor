"""The census-scale restatement rate, and the distinction that makes it honest.

Section 3.3 publishes a restatement rate over 52 proven gaps from four repositories, because
the 112-package census was measured before kill-set signatures existed and its rows carry no
`subsumption` block. `census_summary._subsumption` aggregates the block where it is present
so the rate can have the census's denominator.

One property carries the whole thing and nothing in the arithmetic enforces it: a package
whose gaps were **never compared** and a package whose gaps were compared and found
**distinct** contribute identically to a naive sum, and they mean opposite things. The first
is missing evidence; the second is evidence. Folding the first into the second is exactly how
a stale census lowers the published rate while looking like a larger sample, so it is
asserted here rather than left to the summariser's good behaviour.

The functions are exercised directly on constructed rows. A test that needed the real census
could not run before the census had been re-measured, which is the point in time at which
this guard is most needed.
"""

from __future__ import annotations

import gzip
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

census_summary = pytest.importorskip("census_summary")

PUBLISHED = ROOT / "docs" / "census-rows.json.gz"
SUMMARY = ROOT / "docs" / "census-summary.json"


def _row(package: str, subsumption: dict | None = None, **audit) -> dict:
    """One audited census row, with the fields the summariser requires to accept it."""
    block = {
        "counts": {"killed": 9, "unproven": 1},
        "kill_rate": 0.9,
        "covered_fraction": 0.8,
        "proven_gaps": 0,
        "scored": 10,
        "planned_mutants": 10,
        **audit,
    }
    if subsumption is not None:
        block["subsumption"] = subsumption
    return {"package": package, "version": "1.0", "audit": block}


def _block(**kw) -> dict:
    base = {
        "proven": 0,
        "placed": 0,
        "unplaceable": 0,
        "distinct": 0,
        "restatements": 0,
        "functions_with_a_gap": 0,
        "functions_with_two_or_more": 0,
        "eligible": 0,
    }
    base.update(kw)
    return base


# ------------------------------------------------------- the distinction that matters


def test_a_package_without_the_block_is_counted_not_assumed_distinct():
    rows = [
        _row("a", _block(proven=4, placed=4, distinct=3, restatements=1, eligible=4)),
        _row("b"),  # measured before signatures existed
    ]
    got = census_summary._subsumption(rows)
    assert got["packages_with_a_signature"] == 1
    assert got["packages_without_a_signature"] == 1
    # The rate is over the gaps that were actually compared.
    assert got["placed"] == 4
    assert got["restated_share"] == 0.25


def test_an_unmeasured_package_cannot_lower_the_rate():
    """The failure this file exists for: silence read as a distinct gap."""
    compared = [_row("a", _block(proven=4, placed=4, distinct=3, restatements=1, eligible=4))]
    with_silence = compared + [_row(f"q{i}") for i in range(20)]
    assert (
        census_summary._subsumption(with_silence)["restated_share"]
        == census_summary._subsumption(compared)["restated_share"]
    )


def test_the_eligibility_ceiling_is_reported_and_bounds_the_collapse():
    rows = [
        _row("a", _block(proven=6, placed=6, distinct=4, restatements=2, eligible=5)),
        _row("b", _block(proven=2, placed=2, distinct=2, restatements=0, eligible=0)),
    ]
    got = census_summary._subsumption(rows)
    assert got["eligible"] == 5
    assert got["placed"] == 8
    assert got["eligible_share"] == 0.625
    # No relation can collapse more gaps than were eligible to collapse.
    assert got["restatements"] <= got["eligible"]
    assert "ceiling" in got["note"]
    assert "5 of 8" in got["note"]


def test_a_row_carrying_the_block_without_the_ceiling_fields_is_separated():
    """An older block has `restatements` and no `eligible`; mixing them invents a share."""
    rows = [
        _row("new", _block(proven=4, placed=4, distinct=3, restatements=1, eligible=4)),
        _row("old", {"proven": 9, "placed": 9, "distinct": 2, "restatements": 7}),
    ]
    got = census_summary._subsumption(rows)
    assert got["packages_with_a_signature"] == 2
    assert got["packages_with_the_eligibility_fields"] == 1
    # The old row's seven restatements must not reach a rate whose denominator excludes it.
    assert got["placed"] == 4
    assert got["restatements"] == 1


def test_the_counts_partition_and_the_shares_follow_them():
    rows = [
        _row(
            "a", _block(proven=5, placed=4, unplaceable=1, distinct=3, restatements=1, eligible=3)
        ),
        _row("b", _block(proven=3, placed=3, distinct=1, restatements=2, eligible=3)),
    ]
    got = census_summary._subsumption(rows)
    assert got["distinct"] + got["restatements"] == got["placed"]
    assert got["placed"] + got["unplaceable"] == got["proven"]
    assert got["restated_share"] == pytest.approx(3 / 7, abs=5e-5)
    assert got["inflation"] == pytest.approx(7 / 4, abs=5e-5)


def test_a_census_where_nothing_was_compared_reports_nothing_rather_than_zero():
    """An empty dict keeps the key out of the summary; a zero rate would be a claim."""
    assert census_summary._subsumption([_row("a"), _row("b")]) == {}
    assert "subsumption" not in census_summary.summarise([_row("a")])


def test_a_package_with_no_gap_does_not_count_as_one_with_a_gap():
    rows = [
        _row("a", _block(proven=2, placed=2, distinct=2, eligible=0)),
        _row("b", _block()),
    ]
    got = census_summary._subsumption(rows)
    assert got["packages_with_a_proven_gap"] == 1
    assert got["packages_with_the_eligibility_fields"] == 2


# ------------------------------------------------- the published census, unchanged by this


def test_the_published_census_predates_signatures_and_is_left_alone():
    if not PUBLISHED.exists() or not SUMMARY.exists():
        pytest.skip("the census artefacts are not present - running against a wheel")
    with gzip.open(PUBLISHED) as handle:
        rows = json.loads(handle.read())["rows"]
    audited = [r for r in rows if not r.get("excluded")]
    assert audited, "the census has no audited rows"
    assert not any("subsumption" in (r.get("audit") or {}) for r in audited), (
        "this census now carries signatures, so section 3.3 should quote its denominator "
        "rather than the four-repository one"
    )
    # And the summariser's other figures are untouched by the addition.
    published = json.loads(SUMMARY.read_text(encoding="utf-8"))
    recomputed = census_summary.summarise(rows)
    assert "subsumption" not in recomputed
    assert {k: v for k, v in recomputed.items() if k != "audited_packages"} == {
        k: v for k, v in published.items() if k != "audited_packages"
    }
