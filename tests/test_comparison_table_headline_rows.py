"""The headline rows of the three-audit comparison table, against the three audits.

`test_published_numbers.py` checks the table's *unproven reason* rows and the total they
sum to. The rows above them - mutants scored, kill rate, proven gaps - were published in
both README.md and docs/RESULTS.md with nothing checking them, which is how a 60.4% kill
rate came to sit in two documents unpinned. Every value turned out to be correct; the
point is that re-running an audit could change any of them silently.

Each row is recomputed from `docs/audit-*.json` rather than copied, so a changed audit
moves the expectation and the README is what fails.
"""

from __future__ import annotations

import collections
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
RESULTS = ROOT / "docs" / "RESULTS.md"

# The column order of the table, and the audit behind each column.
COLUMNS = (
    ("toolz", "docs/audit-toolz.json"),
    ("tomli", "docs/audit-tomli.json"),
    ("repo-surgeon", "docs/audit-repo-surgeon.json"),
)
# RESULTS.md prints the same table without the tomli column.
RESULTS_COLUMNS = ("toolz", "repo-surgeon")


def _audits() -> dict[str, dict]:
    out = {}
    for name, path in COLUMNS:
        full = ROOT / path
        if not full.is_file():
            pytest.skip(f"{path} is not shipped with the tests")
        out[name] = json.loads(full.read_text(encoding="utf-8"))
    return out


def _counts(audit: dict) -> dict[str, int]:
    """Scored, killed and proven, by the same rule the report uses.

    `skipped` is not scored - a mutant the harness never ran is not evidence about the
    suite - so the denominator is every other verdict.
    """
    verdicts = collections.Counter(r.get("verdict") for r in audit["results"])
    scored = sum(n for verdict, n in verdicts.items() if verdict != "skipped")
    return {
        "scored": scored,
        "killed": verdicts.get("killed", 0),
        "proven": verdicts.get("proven-gap", 0),
        "unproven": verdicts.get("unproven", 0),
    }


def _row(text: str, label: str) -> list[str]:
    line = next((ln for ln in text.splitlines() if ln.startswith("|") and label in ln), None)
    assert line is not None, f"no row labelled {label!r}"
    return [c.strip().strip("*").strip() for c in line.strip().strip("|").split("|")][1:]


@pytest.mark.parametrize("document", ["README", "RESULTS"])
def test_the_mutants_scored_row(document: str):
    text = (README if document == "README" else RESULTS).read_text(encoding="utf-8")
    names = (
        COLUMNS if document == "README" else [(n, p) for n, p in COLUMNS if n in RESULTS_COLUMNS]
    )
    audits = _audits()
    cells = _row(text, "mutants scored")
    for cell, (name, _) in zip(cells, names, strict=True):
        assert int(cell.replace(",", "")) == _counts(audits[name])["scored"], (name, cell)


@pytest.mark.parametrize("document", ["README", "RESULTS"])
def test_the_kill_rate_row(document: str):
    """The row that was unpinned: 92.1%, 97.4% and 60.4%."""
    text = (README if document == "README" else RESULTS).read_text(encoding="utf-8")
    names = (
        COLUMNS if document == "README" else [(n, p) for n, p in COLUMNS if n in RESULTS_COLUMNS]
    )
    audits = _audits()
    cells = _row(text, "kill rate")
    for cell, (name, _) in zip(cells, names, strict=True):
        counts = _counts(audits[name])
        expected = f"{counts['killed'] / counts['scored'] * 100:.1f}%"
        assert cell == expected, (document, name, cell, expected)


@pytest.mark.parametrize("document", ["README", "RESULTS"])
def test_the_proven_gaps_row(document: str):
    text = (README if document == "README" else RESULTS).read_text(encoding="utf-8")
    names = (
        COLUMNS if document == "README" else [(n, p) for n, p in COLUMNS if n in RESULTS_COLUMNS]
    )
    audits = _audits()
    cells = _row(text, "proven gaps")
    for cell, (name, _) in zip(cells, names, strict=True):
        # The repo-surgeon cell carries a parenthetical; the count is the leading number.
        assert int(cell.split()[0].strip("*")) == _counts(audits[name])["proven"], (name, cell)


def test_the_kill_rate_and_the_proven_gaps_disagree_across_the_three():
    """Otherwise the table compares nothing and pinning it is pointless.

    repo-surgeon is ours and is the worst of the three; that contrast is the table's whole
    reason for existing, so it is asserted rather than left to the reader.
    """
    audits = _audits()
    rates = {
        name: _counts(audits[name])["killed"] / _counts(audits[name])["scored"]
        for name, _ in COLUMNS
    }
    assert rates["repo-surgeon"] < rates["toolz"] < rates["tomli"], rates
    assert _counts(audits["repo-surgeon"])["proven"] > _counts(audits["toolz"])["proven"]


def test_the_rows_sum_the_way_the_table_claims():
    """killed + proven + unproven is the scored total, for each column."""
    audits = _audits()
    for name, _ in COLUMNS:
        c = _counts(audits[name])
        assert c["killed"] + c["proven"] + c["unproven"] == c["scored"], (name, c)
