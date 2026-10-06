"""The README's numbers are what the code and the committed runs give.

- The "What it looks like" block is re-run: `suite-auditor audit examples/pricing` with the
  interpreter running these tests, and every line of the report from the summary to the
  uncovered list must match (progress lines and the wall time vary and are not compared).
- The two-audit table is checked against docs/audit-*.json, the files those runs wrote.

The table had drifted: it showed a Python 3.14 run of toolz (385 killed, 33 unproven)
while the classifiers stop at 3.13, where the same commit gives 387 and 31.

Skipped where the README and examples are not next to the tests (installed-wheel runs).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
PRICING = ROOT / "examples" / "pricing"

pytestmark = pytest.mark.skipif(
    not (README.exists() and PRICING.exists()),
    reason="README.md and examples/ are not shipped with the tests",
)


def _report(text: str) -> list[str]:
    """The report from the SUITE AUDIT banner up to, not including, the wall time."""
    lines = [line.rstrip() for line in text.splitlines()]
    start = next(i for i, line in enumerate(lines) if line.startswith("SUITE AUDIT - "))
    end = next(i for i, line in enumerate(lines) if line.strip().startswith("took "))
    return lines[start:end]


def test_the_readme_audit_block_is_what_the_command_prints():
    shown = README.read_text(encoding="utf-8").split("## What it looks like", 1)[1]
    shown = shown.split("```", 2)[1]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env.pop("VIRTUAL_ENV", None)
    done = subprocess.run(
        [sys.executable, "-m", "suite_auditor", "audit", str(PRICING), "--python",
         sys.executable, "-j", "auto"],
        capture_output=True, env=env, timeout=600, check=False,
    )  # fmt: skip
    printed = done.stdout.decode("utf-8", "replace")
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert _report(printed) == _report(shown)


def _row(label: str) -> str:
    text = README.read_text(encoding="utf-8")
    return next(line for line in text.splitlines() if line.startswith(f"| {label}"))


@pytest.mark.parametrize("column, name", [(0, "toolz"), (1, "repo-surgeon")])
def test_the_two_audit_table_is_the_committed_runs(column, name):
    run = json.loads((ROOT / "docs" / f"audit-{name}.json").read_text(encoding="utf-8"))
    counts = run["counts"]

    def cell(label: str) -> str:
        return _row(label).split("|")[2 + column].strip().strip("*")

    assert cell("mutants scored") == str(sum(counts.values()))
    assert cell("**kill rate**") == f"{100 * run['kill_rate']:.1f}%"
    assert cell("caught share") == f"{100 * run['caught_share']:.1f}%"
    assert cell("unproven survivors") == str(counts.get("unproven", 0))
    assert cell("proven gaps").startswith(str(run["proven_gaps"]))
    assert cell("**reached by no test**").startswith(str(len(run["uncovered"])))


def test_the_readme_unproven_breakdown_matches_the_committed_audits():
    """The README's unproven rows must equal what the audit JSONs actually contain.

    The bucket used to be published as one number labelled "possibly equivalent mutants",
    which is true of 5 of toolz's 31 and 12 of repo-surgeon's 54 - the rest is how far the
    prover could reach. Now that the breakdown is in the table, it is a set of numbers a
    reader can check, so it is checked here: hand-maintained tables drift.
    """
    import collections
    import json

    from suite_auditor.report import unproven_reason

    readme = README.read_text(encoding="utf-8")
    rows = {
        "ran on a real input and agreed": "equivalent",
        "a method, which cannot be called without its instance": "method",
        "no valid input could be built": "no_input",
        "disagreed only where both versions raised": "both_raised",
        "the unmutated function could not be called either": "old_uncallable",
    }
    audits = {}
    for name, path in (
        ("toolz", "docs/audit-toolz.json"),
        ("repo-surgeon", "docs/audit-repo-surgeon.json"),
    ):
        data = json.loads((README.parent / path).read_text(encoding="utf-8"))
        audits[name] = collections.Counter(
            unproven_reason(r.get("detail") or "")
            for r in data["results"]
            if r.get("verdict") == "unproven"
        )

    for label, key in rows.items():
        line = next((ln for ln in readme.splitlines() if label in ln and ln.startswith("|")), None)
        assert line is not None, f"the README has no row for {label!r}"
        cells = [c.strip() for c in line.strip("|").split("|")]
        claimed = [int(c) for c in cells[1:3]]
        actual = [audits["toolz"][key], audits["repo-surgeon"][key]]
        assert claimed == actual, f"{label}: README says {claimed}, audits say {actual}"

    # And the parts must still add up to the total the table publishes.
    for name, column in (("toolz", 1), ("repo-surgeon", 2)):
        total_line = next(
            ln for ln in readme.splitlines() if ln.startswith("| unproven survivors |")
        )
        total = int([c.strip() for c in total_line.strip("|").split("|")][column])
        assert total == sum(audits[name].values()), f"{name}: total {total} != parts"


def test_the_callable_pool_reaches_a_higher_order_function():
    """A parameter that wants a function has to be offered one.

    The pool is harvested from literals in the covering tests, and a callable passed in a
    test is an `ast.Name`, never a Constant - so `valfilter(predicate, d, factory=dict)`
    got 64 generated calls that all raised TypeError on both sides, and a planted gap in
    it could not be proven. Measured: 0 of 2 before, 2 of 2 after. See docs/SENSITIVITY.md.
    """
    import ast

    from suite_auditor.inputs import argument_sets

    source = (
        "def valfilter(predicate, d, factory=dict):\n"
        "    rv = factory()\n"
        "    for k, v in d.items():\n"
        "        if predicate(v):\n"
        "            rv[k] = v\n"
        "    return rv\n"
    )
    fn = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
    )
    sets = argument_sets(fn, {}, observed=[])
    assert sets, "no argument sets at all"
    firsts = {s.pos[0] for s in sets if getattr(s, "pos", None)}
    thirds = {s.pos[2] for s in sets if getattr(s, "pos", None) and len(s.pos) > 2}
    # Something callable must be on offer for BOTH - fixing `predicate` alone left the
    # third argument as a dict instance, so `factory()` raised and every call still failed.
    assert firsts & {"bool", "len", "str", "dict", "list"}, (
        f"no callable offered for `predicate`: {sorted(firsts)[:8]}"
    )
    assert thirds & {"dict", "list", "set", "tuple"}, (
        f"no factory offered for `factory`: {sorted(thirds)[:8]}"
    )


def test_the_sensitivity_study_is_published_with_its_data():
    """A rate with no committed output behind it is not checkable."""
    doc = ROOT / "docs" / "SENSITIVITY.md"
    assert doc.is_file(), "docs/SENSITIVITY.md is missing"
    text = doc.read_text(encoding="utf-8")
    data = json.loads(
        (ROOT / "docs" / "sensitivity-toolz.json").read_text(encoding="utf-8")
    )
    # The headline pair, which is the only part that measures the prover rather than the
    # plants, must match the committed run.
    assert data["proven_total"] == data["newly_surviving_total"], (
        "the committed run no longer shows every planted gap proven"
    )
    assert f"{data['proven_total']} of {data['newly_surviving_total']}" in text, (
        "SENSITIVITY.md and sensitivity-toolz.json disagree about the headline"
    )
    assert "0 of 2" in text, "the before-fix number is what gives the after-fix one meaning"
