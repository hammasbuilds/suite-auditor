"""Section 3.3's numbers, against `research/results/subsumption_rate.json`.

The section's arithmetic has to close in three directions and two of its totals coincide by
accident, which is precisely the shape where a wrong number survives: a reader checking the
row would find 33 twice and 19 twice and assume one of each pair was computed from the
other. They are not, so each is pinned on its own and the identities that do hold are
asserted as identities.

The claim that carries the section is the ceiling. Only gaps sharing a function can be
compared at all, so the structural bound on what subsumption could remove is the share of
gaps sitting in a multi-gap function. Reporting the collapse without that bound would make a
fact about how gaps cluster look like a fact about kill sets.
"""

from __future__ import annotations

import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MEASURED = ROOT / "research" / "results" / "subsumption_rate.json"
PAPER = ROOT / "paper" / "main.md"

HEADING = "## 3.3 How many of these *gaps* are the same gap?"

# As section 3.3 publishes them.
PUBLISHED_MUTANTS = 1_047
PUBLISHED_GAPS = 52
PUBLISHED_FUNCTIONS = 30
PUBLISHED_MULTI_GAP_FUNCTIONS = 11
PUBLISHED_ELIGIBLE = 33
PUBLISHED_RESTATEMENTS = 19
PUBLISHED_DISTINCT = 33
PUBLISHED_COLLAPSE = 0.3654
PUBLISHED_ELIGIBLE_SHARE = 0.6346
PUBLISHED_BY_REPOSITORY = {
    # mutants, gaps, functions, with 2+, eligible, restatements, distinct
    "flake-detective": (293, 20, 13, 4, 11, 6, 14),
    "blast-radius": (138, 12, 7, 2, 7, 5, 7),
    "assay-drift": (291, 2, 2, 0, 0, 0, 2),
    "suite-auditor": (325, 18, 8, 5, 15, 8, 10),
}


def _measured():
    if not MEASURED.exists():
        pytest.skip("research/results/subsumption_rate.json is not present")
    return json.loads(MEASURED.read_text(encoding="utf-8"))


def _repo_file(path: pathlib.Path) -> str:
    """A file that lives in the repository rather than in the wheel."""
    if not path.is_file():
        pytest.skip(f"{path.name} is not present - running against an installed wheel")
    return path.read_text(encoding="utf-8")


def _section() -> str:
    text = _repo_file(PAPER)
    start = text.index(HEADING)
    return " ".join(text[start : text.index("\n# 4. ", start)].split())


# -------------------------------------------------------------------- the arithmetic


def test_the_totals_are_the_ones_the_section_publishes():
    totals = _measured()["totals"]
    assert totals["mutants_scored"] == PUBLISHED_MUTANTS
    assert totals["proven_gaps"] == PUBLISHED_GAPS
    assert totals["functions_with_a_gap"] == PUBLISHED_FUNCTIONS
    assert totals["functions_with_two_or_more"] == PUBLISHED_MULTI_GAP_FUNCTIONS
    assert totals["gaps_eligible_for_collapse"] == PUBLISHED_ELIGIBLE
    assert totals["restatements"] == PUBLISHED_RESTATEMENTS
    assert totals["distinct"] == PUBLISHED_DISTINCT
    assert totals["repositories_scored"] == 4
    assert totals["repositories_failed"] == 0


def test_every_gap_could_be_placed_in_the_hierarchy():
    """An unplaceable gap would make the denominator smaller than the gap count."""
    totals = _measured()["totals"]
    assert totals["unplaceable"] == 0
    assert totals["placed_in_the_hierarchy"] == totals["proven_gaps"]


def test_the_distinct_and_restated_counts_partition_the_gaps():
    totals = _measured()["totals"]
    assert totals["distinct"] + totals["restatements"] == totals["proven_gaps"]


def test_the_dominators_among_the_eligible_close_the_other_way():
    """33 distinct = 19 gaps alone in their function + 14 dominators among the eligible."""
    totals = _measured()["totals"]
    alone = totals["placed_in_the_hierarchy"] - totals["gaps_eligible_for_collapse"]
    dominators_among_eligible = totals["gaps_eligible_for_collapse"] - totals["restatements"]
    assert alone == 19
    assert dominators_among_eligible == 14
    assert alone + dominators_among_eligible == totals["distinct"]


def test_the_two_coincidences_are_coincidences():
    """Each looks like a derivation. Asserting them separately is the point."""
    totals = _measured()["totals"]
    alone = totals["placed_in_the_hierarchy"] - totals["gaps_eligible_for_collapse"]
    # eligible == distinct, and ineligible == restatements, both by accident.
    assert totals["gaps_eligible_for_collapse"] == totals["distinct"]
    assert alone == totals["restatements"]
    # Neither identity is forced: the per-repository rows break both.
    rows = _measured()["by_repository"]
    assert any(row["gaps_eligible_for_collapse"] != row["distinct"] for row in rows.values()), (
        "if no repository breaks it, the equality may not be a coincidence after all"
    )
    assert _section().count("by accident") >= 2


def test_the_shares_are_the_counts_rather_than_separately_typed_numbers():
    totals = _measured()["totals"]
    assert totals["collapse_share"] == pytest.approx(PUBLISHED_COLLAPSE, abs=5e-5)
    assert totals["eligible_share"] == pytest.approx(PUBLISHED_ELIGIBLE_SHARE, abs=5e-5)
    # The artefact stores these rounded to four places, so the identity is checked at
    # that precision. A tolerance of 1e-9 against the exact quotient fails on a correct
    # rounding, and a loose tolerance would pass on a share computed from the wrong
    # denominator - which is the thing being checked.
    placed = totals["placed_in_the_hierarchy"]
    assert totals["collapse_share"] == round(totals["restatements"] / placed, 4)
    assert totals["eligible_share"] == round(totals["gaps_eligible_for_collapse"] / placed, 4)


def test_the_engine_and_the_artefact_agree_on_the_restatement_count():
    """One quantity, two code paths; this is the thing that compares them."""
    totals = _measured()["totals"]
    assert totals["restatements_recomputed_from_the_export"] == totals["restatements"], totals
    for name, row in _measured()["by_repository"].items():
        assert row["restatements_recomputed_from_the_export"] == row["restatements"], name


def test_the_per_repository_rows_sum_to_the_totals():
    rows = _measured()["by_repository"]
    totals = _measured()["totals"]
    for key in (
        "mutants_scored",
        "proven_gaps",
        "functions_with_a_gap",
        "functions_with_two_or_more",
        "gaps_eligible_for_collapse",
        "restatements",
        "distinct",
    ):
        assert sum(row[key] for row in rows.values()) == totals[key], key


def test_every_published_repository_row_matches():
    rows = _measured()["by_repository"]
    assert set(rows) == set(PUBLISHED_BY_REPOSITORY)
    for name, published in PUBLISHED_BY_REPOSITORY.items():
        row = rows[name]
        got = (
            row["mutants_scored"],
            row["proven_gaps"],
            row["functions_with_a_gap"],
            row["functions_with_two_or_more"],
            row["gaps_eligible_for_collapse"],
            row["restatements"],
            row["distinct"],
        )
        assert got == published, name


def test_a_repository_with_no_multi_gap_function_collapses_nothing():
    """The ceiling, visible in one row: assay-drift's two gaps are in two functions."""
    row = _measured()["by_repository"]["assay-drift"]
    assert row["functions_with_two_or_more"] == 0
    assert row["gaps_eligible_for_collapse"] == 0
    assert row["restatements"] == 0
    assert row["distinct"] == row["proven_gaps"]
    assert row["collapse_share"] == 0.0


# ------------------------------------------------------------------------- the prose


def test_every_figure_in_the_section_comes_from_the_artefact():
    totals = _measured()["totals"]
    section = _section()
    for value in (
        f"{totals['mutants_scored']:,}",
        str(totals["proven_gaps"]),
        str(totals["functions_with_a_gap"]),
        str(totals["functions_with_two_or_more"]),
        str(totals["gaps_eligible_for_collapse"]),
        str(totals["restatements"]),
        str(totals["distinct"]),
        f"{totals['collapse_share'] * 100:.1f}%",
        f"{totals['eligible_share'] * 100:.1f}%",
    ):
        assert value in section, value


def test_the_section_states_the_bound_in_both_directions():
    """Relative to an input pool, so distinct is a lower bound and raw an upper one."""
    section = _section()
    assert "lower\nbound" in _repo_file(PAPER) or "**lower bound**" in section
    assert "upper" in section
    assert "undecidable" in section


def test_the_section_does_not_compare_the_two_filters_rates_directly():
    """5.38% is over mutants and 36.5% over proven gaps; conflating them is the error."""
    section = _section()
    assert "not comparable" in section
    assert "different populations at different stages" in section


def test_the_section_records_the_empty_kill_set_trap():
    section = _section()
    assert "empty set is a subset of every set" in section
    assert "unkillable" in section or "no input\ndistinguishes" in _repo_file(PAPER)


def test_the_new_references_are_cited_and_defined():
    text = _repo_file(PAPER)
    for key in ("@subsumption2014", "@subsumption-graphs"):
        assert text.count(f"[{key}]") >= 2, key
    assert "Establishing Theoretical Minimal Sets of Mutants" in text
    assert "Mutant Subsumption Graphs" in text
