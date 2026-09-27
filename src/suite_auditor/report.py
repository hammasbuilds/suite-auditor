"""The report. Proven gaps first, strongest witness first within them.

A mutation score on its own is close to useless to a maintainer: "your kill rate is 72%"
prompts the reasonable question "which 28%, and does it matter?". So the score is reported
and the *gaps* lead, each with the input that proves it.

Three things are kept apart that a single number would merge:

- **proven gaps** - the suite passed and there is an input on which the mutant differs
- **unproven survivors** - the suite passed and no separating input was found. Possibly
  equivalent mutants; possibly a failure of the input generator. Either way not evidence.
- **uncovered functions** - no test reaches them at all. Not a mutation result, and the
  cheapest finding in the report.
"""

from __future__ import annotations

import json
from pathlib import Path

from suite_auditor.inputs import PROVENANCE_LABEL
from suite_auditor.types import Audit, Verdict

STRENGTH_LABEL = {
    0: "both versions return a value, and the values differ",
    1: "one version returns, the other raises",
    2: "both raise, differently (never counted as a gap)",
    3: "no witness",
}


def summary(audit: Audit, repo_name: str) -> str:
    counts = audit.counts()
    lines = [
        "=" * 78,
        f"SUITE AUDIT - {repo_name}",
        "=" * 78,
    ]

    scored = len(audit.scored)
    if audit.no_targets:
        lines.append("  NO FUNCTIONS WERE MUTATED - nothing below is a statement about")
        lines.append("  this suite. Shipped code is looked for in packages (a directory")
        lines.append("  with __init__.py) and in single modules directly under src/.")
        return "\n".join(lines)
    if not scored:
        lines.append("  NOTHING COULD BE SCORED - this is not a result about the suite.")
        reasons: dict[str, int] = {}
        for r in audit.results:
            reasons[r.detail or "unknown"] = reasons.get(r.detail or "unknown", 0) + 1
        for why, n in sorted(reasons.items(), key=lambda kv: -kv[1])[:5]:
            lines.append(f"    {n} mutant(s): {why}")
        if not audit.results and audit.covered_total == 0 and audit.uncovered:
            lines.append(
                f"    no passing test reaches any of the {len(audit.uncovered)} functions found"
            )
        health = audit.health
        if health is not None and health.caveat():
            lines.append(f"    {health.caveat()}")
            if health.output_tail:
                lines.append("    last lines of the suite's output:")
                lines += [f"      {ln}" for ln in health.output_tail.splitlines()]
        return "\n".join(lines)

    kr = audit.kill_rate
    lines += [
        f"  mutants scored     : {scored}",
        f"  killed by the suite: {counts.get('killed', 0)}  ({kr:.1%})" if kr is not None else "",
        f"  PROVEN GAPS        : {len(audit.gaps)}  "
        f"({len(audit.strong_gaps)} with an unarguable witness)",
        f"  unproven survivors : {counts.get('unproven', 0)}  "
        "(possibly equivalent mutants; not counted as gaps)",
        f"  uncovered functions: {len(audit.uncovered)}  (no test reaches them at all)",
    ]
    if audit.failing_only:
        lines.append(
            f"  reached only by failing tests: {len(audit.failing_only)}  "
            "(counted as not covered; fix those tests)"
        )
    if counts.get("skipped"):
        lines.append(
            f"  skipped            : {counts['skipped']}  (could not be scored; not in any rate)"
        )

    # The kill rate is a rate over the functions a test reaches, so on its own it
    # says nothing about how much of the package that is. Printed together, and
    # never apart: a suite covering a tenth of the code and killing everything in
    # it scores 100% above and 10% here, and the first number is the quotable one.
    if audit.stopped_early:
        lines.append(
            f"  ! PARTIAL: the time budget stopped this after {audit.audited_functions} of "
            f"{audit.covered_total} covered functions. Every rate below is over what was "
            "scored, not over the package."
        )

    cf, cs = audit.covered_fraction, audit.caught_share
    if cf is not None and cs is not None:
        lines += [
            f"  covered fraction   : {cf:.1%}  "
            f"({audit.covered_total} of "
            f"{audit.covered_total + len(audit.uncovered) + len(audit.failing_only)} functions)",
            f"  CAUGHT SHARE       : {cs:.1%}  "
            "(kill rate x covered fraction - the share of the whole package",
            "                       whose mutants this suite would notice, and the one",
            "                       figure that testing less cannot raise)",
        ]

    if audit.gaps:
        lines.append("\n  gaps, strongest evidence first:\n")
        for g in audit.gaps[:15]:
            w = g.witness or {}
            lines.append(f"  {g.target}   [{g.kind}]")
            lines.append(f"    why it counts : {STRENGTH_LABEL[g.strength]}")
            fn = g.target.rpartition("::")[2].rpartition(".")[2]
            lines.append(f"    call          : {fn}{w.get('args', '?')}")
            if g.provenance in PROVENANCE_LABEL:
                lines.append(f"    input         : {PROVENANCE_LABEL[g.provenance]}")
            lines.append(f"    before        : {w.get('old', '?')[:100]}")
            lines.append(f"    after         : {w.get('new', '?')[:100]}")
            lines.append("")
        if len(audit.gaps) > 15:
            lines.append(f"  ... and {len(audit.gaps) - 15} more in the JSON")

    if audit.uncovered:
        lines.append(f"\n  {len(audit.uncovered)} function(s) no test reaches:")
        for key in audit.uncovered[:10]:
            lines.append(f"    {key}")
        if len(audit.uncovered) > 10:
            lines.append(f"    ... and {len(audit.uncovered) - 10} more")

    if not audit.strong_gaps and audit.gaps:
        lines.append(
            "\n  No gap here rests on two differing return values. Every one is a mutant\n"
            "  that raises where the original returns, or the reverse - real, and weaker\n"
            "  evidence, because it depends on the reader judging whether the input was\n"
            "  one the function would ever see."
        )
    elif not audit.gaps:
        lines.append(
            "\n  No provable gap found. That is a statement about this run, not a clean\n"
            "  bill of health: unproven survivors and uncovered functions are both listed\n"
            "  above, and either may hide one."
        )

    lines.append(f"\n  took {audit.seconds:.0f}s")
    return "\n".join(ln for ln in lines if ln != "")


def write_json(audit: Audit, path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "counts": audit.counts(),
                "kill_rate": audit.kill_rate,
                "covered_total": audit.covered_total,
                "covered_fraction": audit.covered_fraction,
                "caught_share": audit.caught_share,
                "proven_gaps": len(audit.gaps),
                "strong_gaps": len(audit.strong_gaps),
                "uncovered": audit.uncovered,
                "failing_only": audit.failing_only,
                "seconds": round(audit.seconds, 1),
                "results": [r.as_row() for r in audit.results],
            },
            indent=2,
        ),
        encoding="utf-8",
        newline="",
    )


def write_markdown(audit: Audit, path: Path, repo_name: str) -> None:
    """A report a maintainer could act on: every gap with its reproduction."""
    kr = audit.kill_rate
    out = [
        f"# Test suite audit: `{repo_name}`",
        "",
        f"**{len(audit.gaps)} proven gaps** out of {len(audit.scored)} mutants scored"
        + (f", kill rate {kr:.1%}." if kr is not None else "."),
        "",
        (
            f"Kill rate is measured over the **{audit.covered_fraction:.1%}** of functions a "
            f"test reaches at all, so the share of the whole package whose mutants this suite "
            f"would notice is **{audit.caught_share:.1%}**."
            if audit.covered_fraction is not None and audit.caught_share is not None
            else ""
        ),
        "",
        "A gap is a mutant the suite did not catch **and** for which there is a concrete",
        "input showing it behaves differently from the original. Survivors without such an",
        "input are listed separately - some are equivalent mutants and no test could catch",
        "them, so counting them here would inflate the number in a way nobody can check.",
        "",
    ]
    if audit.gaps:
        out += [
            "## Gaps",
            "",
            "| function | operator | input | input source | before | after |",
            "|---|---|---|---|---|---|",
        ]
        for g in audit.gaps:
            w = g.witness or {}
            esc = lambda s: str(s).replace("|", "\\|")[:80]  # noqa: E731
            out.append(
                f"| `{g.target}` | `{g.kind}` | `{esc(w.get('args'))}` | {g.provenance or '-'} | "
                f"`{esc(w.get('old'))}` | `{esc(w.get('new'))}` |"
            )
    if audit.uncovered:
        out += ["", "## Reached by no test", ""]
        out += [f"- `{k}`" for k in audit.uncovered]
    if audit.failing_only:
        out += ["", "## Reached only by tests that fail", ""]
        out += [f"- `{k}`" for k in audit.failing_only]

    unproven = [r for r in audit.results if r.verdict is Verdict.UNPROVEN]
    if unproven:
        out += [
            "",
            "## Survivors with no separating input",
            "",
            f"{len(unproven)} mutants survived the suite and could not be shown to differ.",
            "Some of these are equivalent to the original. They are not counted as gaps.",
        ]
    path.write_text("\n".join(out) + "\n", encoding="utf-8", newline="")
