"""The xmltodict comparison's suite-auditor column must match its own audit file.

docs/COMPARISON.md states a table of both tools on `xmltodict` 1.0.4. cosmic-ray's column
cannot be re-derived here - it needs cosmic-ray installed and a 351-second run - but our
own column can, and docs/xmltodict-audit.json is the run it came from. The half that can
be checked is checked, and the half that cannot is named as such.

The claim worth guarding most is the narrow one: exactly one proven gap, on
`_process_namespace`, with the witness the document quotes. That sentence is the whole
comparison, and a document that drifted from its artefact would still read convincingly.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "COMPARISON.md"
AUDIT = ROOT / "docs" / "xmltodict-audit.json"

pytestmark = pytest.mark.skipif(
    not (DOC.exists() and AUDIT.exists()),
    reason="the comparison document or its audit artefact is absent",
)


def _audit() -> dict:
    return json.loads(AUDIT.read_text(encoding="utf-8"))


XMLTODICT_HEADING = "## The same pair on a real package: `xmltodict` 1.0.4"
NEXT_HEADING = "## The one gap, and why the other tool could not express it"


def _section() -> str:
    """The xmltodict section, and nothing above it.

    The document holds two comparison tables with the same row labels - the 27-line
    example first, `xmltodict` second - so "the first row called `killed`" is the wrong
    one. The first version of this file asserted against 102 and 17, the example's
    numbers, and failed. It is the third time in this repository's documents that "the
    first line that matches" turned out not to be "the row I mean".
    """
    text = DOC.read_text(encoding="utf-8")
    assert XMLTODICT_HEADING in text, "the xmltodict heading moved - the guards key on it"
    out = text.split(XMLTODICT_HEADING, 1)[1]
    assert NEXT_HEADING in out, "the gap section is no longer after the table"
    return out.split(NEXT_HEADING, 1)[0]


def _table_row(label: str) -> list[int]:
    """The integers in the xmltodict table's row for `label`."""
    for line in _section().splitlines():
        if line.startswith(f"| {label}") or line.startswith(f"| **{label}"):
            cells = line.strip("|").split("|")
            return [
                int(n.replace(",", ""))
                for cell in cells[1:]
                for n in re.findall(r"\b(\d[\d,]*)\b", cell)
            ]
    raise AssertionError(f"no row for {label!r} in the xmltodict table")


def test_the_mutant_count_is_the_audits() -> None:
    audit = _audit()
    assert audit["planned_mutants"] in _table_row("mutants generated")
    assert audit["scored"] == audit["planned_mutants"], (
        "the table reports one number for generated and scored, so the audit must have "
        "scored everything it planned"
    )


def test_the_kill_count_and_rate_are_the_audits() -> None:
    audit = _audit()
    killed = round(audit["kill_rate"] * audit["scored"])
    row = _table_row("killed")
    assert killed in row, f"the table's killed row is {row}, the audit says {killed}"
    assert round(audit["kill_rate"] * 1000) == 910, (
        f"the document says 91.0%, the audit says {audit['kill_rate'] * 100:.1f}%"
    )


def test_there_is_exactly_one_proven_gap_and_the_document_says_so() -> None:
    audit = _audit()
    gaps = [r for r in audit["results"] if r["verdict"] == "proven-gap"]
    assert len(gaps) == 1, f"{len(gaps)} proven gaps in the artefact"
    assert audit["proven_gaps"] == 1
    text = DOC.read_text(encoding="utf-8")
    gap = gaps[0]
    assert gap["target"] in text, f"the document does not name {gap['target']}"
    assert gap["kind"] in text, f"the document does not name the operator {gap['kind']}"
    for part in (gap["witness"]["args"], gap["witness"]["old"], gap["witness"]["new"]):
        assert part in text, f"the document does not quote the witness part {part!r}"


def test_the_unproven_survivors_are_counted_as_refused() -> None:
    """Seven survivors with a stated reason, not seven unexplained ones."""
    audit = _audit()
    unproven = [r for r in audit["results"] if r["verdict"] == "unproven"]
    assert len(unproven) in _table_row("…refused, with a reason")
    for r in unproven:
        assert r["detail"].strip(), "an unproven survivor with no reason recorded"


def test_no_function_went_unreached() -> None:
    audit = _audit()
    assert audit["uncovered"] == []
    assert audit["covered_fraction"] == 1.0
    assert "0 of 19" in DOC.read_text(encoding="utf-8")


def test_the_disagreeing_input_count_is_the_audits() -> None:
    """"6 of the 64 argument sets disagree" is a measurement, not a flourish."""
    audit = _audit()
    gap = next(r for r in audit["results"] if r["verdict"] == "proven-gap")
    match = re.search(r"(\d+) of (\d+) inputs disagree", gap["detail"])
    assert match, f"the gap's detail no longer states the input counts: {gap['detail']!r}"
    disagreeing, tried = match.groups()
    text = DOC.read_text(encoding="utf-8")
    assert f"{disagreeing} of the {tried} argument sets" in text, (
        f"the document does not state {disagreeing} of {tried}"
    )
