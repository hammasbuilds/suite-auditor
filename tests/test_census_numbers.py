"""The README's census figures are read from docs/census-summary.json, and must add up.

Before this file the thousand-project census was a 19 MB artefact no test opened and no
document quoted: the measurement finished and published nothing, and nothing would have
noticed if the next run moved every number.

Three things are checked, because each has already been wrong once:

- the figures in the README are the ones in the summary;
- the table's rows sum to the number of packages considered, so an exclusion reason
  cannot be added to the census and left out of the table;
- every rate is reported over the same number of projects as the headline. A run reported
  "117 audited" and took its medians over 110, the seven difference being projects that
  planned mutants and scored none.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
SUMMARY = ROOT / "docs" / "census-summary.json"

pytestmark = pytest.mark.skipif(
    not (README.exists() and SUMMARY.exists()),
    reason="README.md and docs/census-summary.json are not shipped with the tests",
)


def _summary() -> dict:
    return json.loads(SUMMARY.read_text(encoding="utf-8"))


def _census_section() -> str:
    text = README.read_text(encoding="utf-8")
    start = text.index("**Across the ")
    return text[start : text.index("**Sensitivity.**", start)]


def test_the_audited_count_in_the_readme_is_the_measured_one() -> None:
    s = _summary()
    section = _census_section()
    row = re.search(r"\|\s*\*\*audited\*\*\s*\|\s*\*\*(\d+)\*\*\s*\|", section)
    assert row, "the census table has no bold audited row"
    assert int(row.group(1)) == s["audited"], (
        f"the README says {row.group(1)} audited, the summary says {s['audited']}"
    )
    assert f"{s['considered']:,}" in section, (
        f"the README does not state the denominator {s['considered']:,}"
    )


def test_the_table_accounts_for_every_package_considered() -> None:
    """Rows must sum to the denominator, or a reason has been dropped from the table.

    This is the check that an added exclusion reason cannot slip past: a new bucket in the
    census makes the sum fall short, and the test names the shortfall.
    """
    s = _summary()
    section = _census_section()
    table = [ln for ln in section.splitlines() if ln.startswith("|") and "---" not in ln]
    numbers = []
    for line in table[1:]:  # the header row carries the denominator, not a count
        # `\**` on both sides: the audited row is bold, and a pattern that did not
        # allow for that summed the exclusions alone - 890 of 1,000 - and called the
        # missing 110 unaccounted for. The row the table exists to show was the one
        # row the check could not see.
        found = re.findall(r"\*{0,2}(\d[\d,]*)\*{0,2}\s*\|\s*$", line)
        if found:
            numbers.append(int(found[0].replace(",", "")))
    assert numbers, "no counts found in the census table"
    assert sum(numbers) == s["considered"], (
        f"the table rows sum to {sum(numbers)}, but {s['considered']} packages were "
        f"considered - {s['considered'] - sum(numbers)} are unaccounted for"
    )


def test_every_published_rate_is_over_the_audited_count() -> None:
    """A median over fewer projects than the headline claims is the fault being guarded."""
    s = _summary()
    for key in ("kill_rate_n", "covered_fraction_n"):
        assert s[key] == s["audited"], (
            f"{key} is {s[key]} but {s['audited']} projects were audited - a rate and a "
            f"headline over different denominators"
        )


def test_the_median_rates_in_the_readme_are_the_measured_ones() -> None:
    s = _summary()
    section = _census_section()
    for label, key in (
        ("median kill rate", "kill_rate_median"),
        ("share of functions reached by some test", "covered_fraction_median"),
    ):
        expected = f"{s[key] * 100:.1f}%"
        assert expected in section, (
            f"the README's {label} is not {expected}, which is what the summary records"
        )


def test_the_proven_gap_counts_are_the_measured_ones() -> None:
    s = _summary()
    section = _census_section()
    assert f"{s['projects_with_a_proven_gap']} of {s['audited']}" in section, (
        f"the README does not say {s['projects_with_a_proven_gap']} of {s['audited']} "
        f"projects had a proven gap"
    )
    assert f"{s['proven_gaps_total']} gaps in total" in section, (
        f"the README does not state {s['proven_gaps_total']} proven gaps in total"
    )


def test_the_summary_is_the_reduction_of_a_census_that_exists() -> None:
    """The summary must be derivable, not hand-written.

    docs/target-census.json is gitignored (19 MB), so this runs only where it is present -
    but where it is, the committed summary must equal what the script produces from it.
    """
    census = ROOT / "docs" / "target-census.json"
    if not census.exists():
        pytest.skip("the raw census is not present (it is gitignored)")
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    import census_summary

    rows = json.loads(census.read_text(encoding="utf-8"))
    assert census_summary.summarise(rows) == _summary(), (
        "docs/census-summary.json is not what scripts/census_summary.py produces from "
        "docs/target-census.json - regenerate it"
    )
