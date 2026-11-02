"""Section 5's refusal table, against `research/results/unproven_reach.json`.

Section 5 used to concede that proven gaps are a lower bound "higher by an unknown amount".
The amount is not unknown, because every refusal carries its reason, and the section now
prints the breakdown. Two properties make that paragraph mean something and neither is
enforced by the arithmetic:

  * the counts have to **close**. 13,216 killed plus 219 proven plus 3,179 unproven is
    16,614 scored, and the per-reason counts have to sum to the unproven total. A gap in
    either direction means some survivors carry a detail the classifier does not recognise,
    and a table over an unknown subset is not a breakdown of anything.
  * the **last column** has to stay. Only `equivalent` is a fact about the mutant; every
    other reason is this prover's reach. A table that lost that distinction would let
    "possibly equivalent mutants" stand for 84.7% of refusals that are nothing of the kind,
    which is the reading the per-reason breakdown was added to prevent.

The script reads the committed census, so these run without network or compute.
"""

from __future__ import annotations

import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MEASURED = ROOT / "research" / "results" / "unproven_reach.json"
PAPER = ROOT / "paper" / "main.md"

# As section 5 publishes them.
PUBLISHED_PACKAGES = 112
PUBLISHED_SCORED = 16_614
PUBLISHED_KILLED = 13_216
PUBLISHED_PROVEN = 219
PUBLISHED_UNPROVEN = 3_179
PUBLISHED_REASONS = {
    # reason: (survivors, packages)
    "method": (1_993, 88),
    "equivalent": (487, 70),
    "no_input": (363, 50),
    "other": (164, 12),
    "both_raised": (110, 39),
    "old_uncallable": (62, 7),
}
PUBLISHED_METHOD_SHARE = 0.627
PUBLISHED_REACH_SHARE = 0.847


def _measured() -> dict:
    if not MEASURED.exists():
        pytest.skip("research/results/unproven_reach.json is not present")
    return json.loads(MEASURED.read_text(encoding="utf-8"))


def _repo_file(path: pathlib.Path) -> str:
    if not path.is_file():
        pytest.skip(f"{path.name} is not present - running against an installed wheel")
    return path.read_text(encoding="utf-8")


def _section() -> str:
    text = _repo_file(PAPER)
    start = text.index("**Proven gaps are a lower bound")
    return " ".join(text[start : text.index("\n## 5.1 ", start)].split())


# -------------------------------------------------------------------- the counts close


def test_the_verdicts_sum_to_the_mutants_scored():
    """Nothing is lost between scoring a mutant and giving it a verdict."""
    got = _measured()
    assert got["killed"] + got["proven_gaps"] + got["unproven_survivors"] == got["mutants_scored"]
    assert got["mutants_scored"] == PUBLISHED_SCORED
    assert got["packages_audited"] == PUBLISHED_PACKAGES


def test_every_unproven_survivor_carries_a_reason_the_classifier_recognises():
    got = _measured()
    assert got["unproven_unclassified"] == 0, (
        "some survivors carry a detail `report.unproven_reason` does not match, so the "
        "table is a breakdown of an unnamed subset"
    )
    assert got["unproven_classified"] == got["unproven_survivors"] == PUBLISHED_UNPROVEN
    assert sum(row["survivors"] for row in got["by_reason"].values()) == PUBLISHED_UNPROVEN


def test_the_published_rows_are_the_measured_ones():
    rows = _measured()["by_reason"]
    assert set(rows) == set(PUBLISHED_REASONS)
    for reason, (survivors, packages) in PUBLISHED_REASONS.items():
        assert rows[reason]["survivors"] == survivors, reason
        assert rows[reason]["packages"] == packages, reason


def test_no_reason_affects_more_packages_than_were_audited():
    got = _measured()
    for reason, row in got["by_reason"].items():
        assert 0 < row["packages"] <= got["packages_audited"], reason


# -------------------------------------------------- the distinction the table exists for


def test_only_the_equivalent_row_is_about_the_mutant():
    rows = _measured()["by_reason"]
    about_the_mutant = {r for r, row in rows.items() if row["about"] == "the mutant"}
    assert about_the_mutant == {"equivalent"}, (
        "a reason reclassified as being about the mutant would shrink the share of "
        "refusals that further work could move, which is the paragraph's conclusion"
    )


def test_most_refusals_are_the_prover_s_reach_rather_than_the_mutant():
    got = _measured()
    assert got["share_of_unproven_within_reach"] == pytest.approx(PUBLISHED_REACH_SHARE, abs=5e-4)
    # Derived from the rows rather than trusted as a field.
    reach = sum(
        row["survivors"] for row in got["by_reason"].values() if row["about"] != "the mutant"
    )
    assert reach == got["within_the_prover_s_reach"]
    assert reach / got["unproven_classified"] > 0.8


def test_methods_are_the_largest_single_cause_and_by_a_long_way():
    got = _measured()
    largest = got["largest_single_cause"]
    assert largest["reason"] == "method"
    assert largest["share_of_unproven"] == pytest.approx(PUBLISHED_METHOD_SHARE, abs=5e-4)
    # Larger than every other reason put together.
    others = got["unproven_classified"] - largest["survivors"]
    assert largest["survivors"] > others, (largest["survivors"], others)
    assert largest["about"] == "the prover's reach"


def test_the_tool_still_refuses_to_fake_a_receiver():
    """The refusal is correct, and the section says so. If it stopped, the count is stale."""
    audit = _repo_file(ROOT / "src" / "suite_auditor" / "audit.py")
    assert "a method cannot be called without its instance" in audit
    assert 'elif "." in target.name:' in audit


# ------------------------------------------------------------------------- the prose


def test_every_figure_in_the_paragraph_comes_from_the_artefact():
    got = _measured()
    section = _section()
    for value in (
        f"{got['mutants_scored']:,}",
        f"{got['killed']:,}",
        str(got["proven_gaps"]),
        f"{got['unproven_survivors']:,}",
        f"{got['share_of_unproven_within_reach'] * 100:.1f}%",
    ):
        assert value in section, value
    for reason, row in got["by_reason"].items():
        assert f"{row['survivors']:,}" in section, reason
        assert f"{row['share_of_unproven'] * 100:.1f}%" in section, reason


def test_the_paragraph_no_longer_concedes_an_unknown_amount():
    text = _repo_file(PAPER)
    assert "higher by an\nunknown amount" not in text
    assert "the amount is not unknown" in _section()


def test_the_paragraph_names_the_next_piece_of_work():
    """A sized bound whose dominant cause is unnamed is half a threat section."""
    section = _section()
    assert "instance construction" in section.lower()
    assert "__init__" in section
    assert "research/unproven_reach.py" in section
