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
    quoted = number(cell(paper, label, 1))
    assert abs(quoted - computed) < 5e-4, (
        f"the manuscript's median for {label!r} is {quoted}, the ledger gives {computed}"
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
    assert abs(caught - published["distributions"]["caught_share"]["median"]) < 5e-4


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
