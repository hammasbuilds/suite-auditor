"""The manuscript's statistics are the ones the ledger produces.

`research/` recomputes the funnel, the distributions and the figures from
`docs/census-rows.json.gz`. The manuscript then quotes them. Nothing re-runs the audit, so
if the ledger changes, every number in `paper/main.md` is stale and only a test can say
so.

The guards here read the manuscript **by table row and column**, never by presence. An
earlier guard in a sibling repository asserted `f"{value:.4f}" in document`, which passes
when the number appears anywhere at all - including in a different row, a caption, or a
sentence about something else. Two planted wrong values went unnoticed that way. A row is
found by its label and the value is taken from a named column position.

The claim this file exists for is the conditional one: kill rate is measured over covered
functions only, so it is not comparable to covered fraction, and the manuscript must quote
the product beside it. A draft of the figure drew a diagonal between the two axes before
`audit.py` was checked.
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "docs" / "census-rows.json.gz"
PAPER = ROOT / "paper" / "main.md"
RESULTS = ROOT / "research" / "results" / "auditability.json"
FIGURES = ROOT / "research" / "figures"

pytestmark = pytest.mark.skipif(
    not (LEDGER.exists() and PAPER.exists() and RESULTS.exists()),
    reason="the research tree is not shipped with the tests",
)

sys.path.insert(0, str(ROOT / "research"))


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    with gzip.open(LEDGER, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))["rows"]


@pytest.fixture(scope="module")
def published() -> dict:
    return json.loads(RESULTS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def paper() -> str:
    return PAPER.read_text(encoding="utf-8")


def cell(document: str, label: str, column: int) -> str:
    """The `column`-th cell of the markdown table row whose first cell contains `label`.

    By position, so a value that drifts into the wrong column fails rather than passing
    because it is still somewhere on the page.
    """
    for line in document.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and label in cells[0]:
            assert column < len(cells), (
                f"row {cells[0]!r} has {len(cells)} columns, asked for {column}"
            )
            return cells[column]
    raise AssertionError(f"no table row whose first cell contains {label!r}")


def number(text: str) -> float:
    match = re.search(r"-?\d+(?:\.\d+)?", text.replace("**", "").replace(",", ""))
    assert match, f"no number in cell {text!r}"
    return float(match.group())


# --- the research tree agrees with the ledger ----------------------------------------


def test_published_results_recompute_from_the_ledger(rows, published):
    import metrics

    assert metrics.funnel(rows) == published["funnel"]
    assert metrics.audit_distributions(rows) == published["distributions"]


def test_caught_share_is_the_product_of_the_other_two(rows):
    """Not a definition test - a check that the ledger's field is that product.

    If `caught_share` were ever recorded as something else, the manuscript's central
    correction would be quoting a number that does not mean what it says.
    """
    import metrics

    checked = 0
    for row in metrics.audited(rows):
        audit = row["audit"]
        k, c, s = (audit.get("kill_rate"), audit.get("covered_fraction"), audit.get("caught_share"))
        if None in (k, c, s):
            continue
        assert abs(k * c - s) < 1e-9, f"{row.get('name')}: {k} x {c} != {s}"
        checked += 1
    assert checked >= 100, f"only {checked} rows carried all three fields"


# --- the manuscript quotes what the research tree computed ---------------------------


def at_the_quoted_precision(text: str, computed: float) -> str:
    """Round the computed value to as many decimals as the manuscript printed.

    An absolute tolerance of 5e-4 lands exactly on the boundary whenever the manuscript
    quotes three decimals of a four-decimal artefact - 0.709 against 0.7085 - and then
    passes or fails on the last bit of a float subtraction. The precision the manuscript
    chose is the precision to compare at.
    """
    decimals = len(text.split(".")[1]) if "." in text else 0
    return f"{computed:.{decimals}f}"


@pytest.mark.parametrize(
    ("label", "key"),
    [
        ("kill rate", "kill_rate"),
        ("covered fraction", "covered_fraction"),
        ("caught share", "caught_share"),
        ("proven gaps", "proven_gaps"),
    ],
)
def test_median_column_matches_the_computed_median(paper, published, label, key):
    computed = published["distributions"][key]["median"]
    shown = cell(paper, label, 1).strip().strip("*")
    assert at_the_quoted_precision(shown, computed) == shown, (
        f"the manuscript's median for {label!r} is {shown}, the ledger gives {computed}"
    )


def test_caught_share_row_is_present_and_below_kill_rate(paper, published):
    """The correction only lands if both rows are in the table, with caught share lower.

    A manuscript that dropped the caught-share row would read as though kill rate were
    the unconditional number again, which is the error this whole section fixes.
    """
    kill = number(cell(paper, "kill rate", 1))
    caught = number(cell(paper, "caught share", 1))
    assert caught < kill, (
        f"caught share {caught} is not below kill rate {kill}; since caught share is "
        f"kill rate times a covered fraction of at most 1, this cannot happen unless "
        f"the table rows have been swapped"
    )
    shown = cell(paper, "caught share", 1).strip().strip("*")
    computed = published["distributions"]["caught_share"]["median"]
    decimals = len(shown.split(".")[1]) if "." in shown else 0
    assert abs(caught - round(computed, decimals)) < 1e-9, (shown, computed)


def test_the_conditional_claim_is_stated_not_merely_implied(paper):
    body = paper.lower()
    assert "conditional on" in body
    assert "covered functions only" in body or "over covered functions" in body


def test_the_two_named_perfect_kill_rate_packages_are_real(rows, paper):
    """The manuscript names `pyasn1-modules` and `jaraco-context`; the ledger must agree.

    An earlier draft of this sentence said "four such packages" from memory. There are
    two. Naming them makes the claim checkable, and this is the check.
    """
    import metrics

    found = {}
    for row in metrics.audited(rows):
        audit = row["audit"]
        c, k = audit.get("covered_fraction"), audit.get("kill_rate")
        if c is not None and k is not None and c < 0.2 and k >= 0.999:
            found[str(row.get("name") or row.get("package"))] = round(c, 3)

    assert set(found) == {"pyasn1-modules", "jaraco-context"}, (
        f"the ledger's low-coverage perfect-kill-rate packages are {sorted(found)}, "
        f"but the manuscript names pyasn1-modules and jaraco-context"
    )
    for name, covered in found.items():
        assert name in paper, f"{name} is in the ledger but not in the manuscript"
        assert f"{covered:.3f}" in paper, (
            f"{name}'s covered fraction {covered:.3f} is not quoted in the manuscript"
        )


# --- the figures exist and are referenced --------------------------------------------


@pytest.mark.parametrize(
    "name", ["fig1_funnel", "fig2_kill_rate_ecdf", "fig3_coverage_vs_kill_rate", "fig4_proven_gaps"]
)
def test_each_figure_is_drawn_and_cited(paper, name):
    for suffix in (".png", ".pdf"):
        assert (FIGURES / f"{name}{suffix}").is_file(), f"{name}{suffix} was not drawn"
    assert f"{name}.png" in paper, f"{name} is drawn but the manuscript never shows it"


def test_every_caption_states_its_denominator(published):
    """A figure cannot reach a paper without its n - the house rule across all four."""
    captions = json.loads(
        (ROOT / "research" / "results" / "captions.json").read_text(encoding="utf-8")
    )
    assert len(captions) == 4
    for name, caption in captions.items():
        assert re.search(r"n=\d|\b\d{1,3}(?:,\d{3})*\b", caption), (
            f"{name}'s caption states no denominator: {caption!r}"
        )


# --- the funnel table, row by row -----------------------------------------------------


def test_every_funnel_stage_in_the_paper_matches_the_artefact(paper, published):
    """The gap that let a wrong count stand.

    `test_median_column_matches_the_computed_median` checks the distribution table, and
    nothing checked the funnel. The manuscript published 67 packages against a stage the
    artefact now counts as 60, and the suite was green: the stale number was in a table
    no test read. Each row is matched by its label and read by column position.
    """
    labels = {
        "no sdist on PyPI": "no sdist on PyPI",
        "ships no tests": "ships no tests",
        "tests collect nothing": "tests collect nothing",
        "suite did not finish in 120": "suite did not finish in 120s",
        "memory cap exceeded": "memory cap exceeded (4,096 MB)",
        "audit budget exceeded (300": "audit budget exceeded (300s)",
        "audit budget exceeded (longer": "audit budget exceeded (a longer budget)",
        "no mutable targets": "no mutable targets found",
        "other": "other reason",
    }
    stages = published["funnel"]["stages"]
    assert set(labels.values()) == set(stages), (
        f"the funnel has stages {sorted(stages)} and this test knows "
        f"{sorted(labels.values())}; a new stage needs a row in the manuscript"
    )
    for row_label, stage in labels.items():
        quoted = number(cell(paper, row_label, 1))
        assert quoted == stages[stage]["excluded"], (
            f"the manuscript's {row_label!r} row says {quoted:.0f}, the artefact's "
            f"{stage!r} stage counts {stages[stage]['excluded']}"
        )

    audited = number(cell(paper, "audited", 1))
    assert audited == published["funnel"]["audited"]["k"]


def test_the_funnel_rows_in_the_paper_sum_to_the_sample(paper, published):
    """A classification published as a table has to add up on the page, not just in JSON."""
    stages = published["funnel"]["stages"]
    total = published["funnel"]["total"]
    rows = [
        "no sdist on PyPI",
        "ships no tests",
        "tests collect nothing",
        "suite did not finish in 120",
        "memory cap exceeded",
        "audit budget exceeded (300",
        "audit budget exceeded (longer",
        "no mutable targets",
        "other",
        "audited",
    ]
    assert sum(number(cell(paper, r, 1)) for r in rows) == total, (
        f"the manuscript's funnel rows do not sum to {total}"
    )
    staged = sum(s["excluded"] for s in stages.values())
    assert staged + published["funnel"]["audited"]["k"] == total


def test_the_conditional_share_column_is_the_artefact_s(paper, published):
    """Column 3 is 'N of M'. Both halves are checked, because M is the stage's input.

    A funnel row whose count is right and whose denominator is stale reads as a smaller
    attrition than it was, and the denominators chain: each one is the previous stage's
    output.
    """
    stages = published["funnel"]["stages"]
    labels = {
        "ships no tests": "ships no tests",
        "tests collect nothing": "tests collect nothing",
        "suite did not finish in 120": "suite did not finish in 120s",
        "memory cap exceeded": "memory cap exceeded (4,096 MB)",
        "audit budget exceeded (300": "audit budget exceeded (300s)",
        "audit budget exceeded (longer": "audit budget exceeded (a longer budget)",
        "no mutable targets": "no mutable targets found",
        "other": "other reason",
    }
    for row_label, stage in labels.items():
        text = cell(paper, row_label, 3)
        share, _, denominator = text.partition(" of ")
        body = stages[stage]["of_those_that_reached_this_stage"]
        assert at_the_quoted_precision(share.strip(), body["rate"]) == share.strip(), (
            f"{row_label!r}: the manuscript says {share.strip()}, the artefact {body['rate']}"
        )
        assert int(denominator.replace(",", "").strip()) == stages[stage]["reached_this_stage"], (
            f"{row_label!r}: the manuscript's denominator is {denominator.strip()}, the "
            f"artefact's stage input is {stages[stage]['reached_this_stage']}"
        )
