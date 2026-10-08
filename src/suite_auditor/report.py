"""The report. Proven gaps first, strongest witness first within them.

A mutation score on its own is close to useless to a maintainer: "your kill rate is 72%"
prompts the reasonable question "which 28%, and does it matter?". So the score is reported
and the *gaps* lead, each with the input that proves it.

Three things are kept apart that a single number would merge:

- **proven gaps** - the suite passed and there is an input on which the mutant differs
- **unproven survivors** - the suite passed and no separating input was found, which has
  several causes and only one of them is about the suite: see `unproven_by_reason`. Possibly
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
        lines.append("  with __init__.py), in single modules directly under src/, and")
        lines.append("  (when there is no src/ at all) in .py files at the repo root.")
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
        "(not counted as gaps; by reason below)",
        *_unproven_lines(audit),
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

    # An install of the project shadowing the scratch copy means the suite imported the
    # real files, so mutants in them were never seen and every one of them is scored as
    # killed. The audit warns about this while it runs and nothing carried it afterwards:
    # not this report, not --json. A high kill rate with invisible mutants behind it is
    # precisely the clean bill of health this tool exists to refuse.
    if audit.stray:
        lines.append(
            f"  ! SHADOWED: the suite executed {len(audit.stray)} file(s) from the real "
            "checkout rather than the scratch copy, so mutants in them were invisible to "
            "the tests and counted as killed. Uninstall the project, or install it "
            "editable, and audit again."
        )
        for path in audit.stray[:5]:
            lines.append(f"      {path}")
        if len(audit.stray) > 5:
            lines.append(f"      ... and {len(audit.stray) - 5} more")

    # The kill rate is a rate over the functions a test reaches, so on its own it
    # says nothing about how much of the package that is. Printed together, and
    # never apart: a suite covering a tenth of the code and killing everything in
    # it scores 100% above and 10% here, and the first number is the quotable one.
    if audit.stopped_early:
        scored = (
            f"{len(audit.results)} of {audit.planned_mutants} planned mutants, in "
            if audit.planned_mutants
            else ""
        )
        lines.append(
            f"  ! PARTIAL: the time budget stopped this after {scored}"
            f"{audit.audited_functions} of {audit.covered_total} covered functions. Every "
            "rate below is over what was scored, not over the package."
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


# Why a survivor was not proven. The bucket is mostly the prover's reach rather than a
# property of the suite, and reporting one total let "possibly equivalent mutants" stand
# for all of it. Keyed off each result's own `detail`, so nothing new is computed.
UNPROVEN_REASONS = (
    ("method", "a method, which cannot be called without its instance"),
    ("no_input", "no valid input could be built - every argument set raised on both sides"),
    ("both_raised", "disagreed only where both versions raised, so it is not proof"),
    ("old_uncallable", "the unmutated function could not be called either"),
    ("equivalent", "ran on real inputs and agreed - possibly an equivalent mutant"),
    ("other", "not classified"),
)


def unproven_reason(detail: str) -> str:
    """Which of UNPROVEN_REASONS this survivor's detail describes."""
    text = (detail or "").lower()
    if "a method cannot be called" in text:
        return "method"
    if "old_uncallable" in text:
        return "old_uncallable"
    if "argument sets raised on both sides" in text:
        return "no_input"
    if "only where both versions" in text or "only because the function" in text:
        return "both_raised"
    if "all agree" in text:
        return "equivalent"
    return "other"


def unproven_breakdown(audit) -> dict:
    """Counts per reason, in UNPROVEN_REASONS order, omitting the empty ones."""
    tally: dict[str, int] = {}
    for result in audit.results:
        if str(getattr(result.verdict, "value", result.verdict)) != "unproven":
            continue
        key = unproven_reason(getattr(result, "detail", "") or "")
        tally[key] = tally.get(key, 0) + 1
    return {key: tally[key] for key, _ in UNPROVEN_REASONS if key in tally}


def _unproven_lines(audit) -> list[str]:
    """The unproven count broken out by reason, indented under it.

    Most of the bucket is this tool's reach rather than a property of the suite - on toolz
    5 of 31 ran on a real input and agreed, while 24 are methods or were never validly
    called - so one total let "possibly equivalent mutants" stand for all of it.
    """
    breakdown = unproven_breakdown(audit)
    if not breakdown:
        return []
    labels = dict(UNPROVEN_REASONS)
    out = [f"      {n:>4}  {labels[key]}" for key, n in breakdown.items()]
    agreed, total = breakdown.get("equivalent", 0), sum(breakdown.values())
    if total and agreed != total:
        out.append(f"      only {agreed} of {total} ran on a real input and agreed; the rest is")
        out.append("      this tool's reach, not evidence about the suite")
    return out


def _health_json(health: object) -> dict | None:
    """The baseline suite run, including the two members `asdict` would drop.

    `clean` and `caveat()` are a property and a method, so `dataclasses.asdict` omits both
    silently - and they are the two that say whether anything else here can be believed. A
    suite that collected no tests has passed=0 and failed=0, which reads as a healthy run
    with a small surface.
    """
    if health is None:
        return None
    return {
        "ran": bool(getattr(health, "ran", False)),
        "exit_code": getattr(health, "exit_code", None),
        "passed": getattr(health, "passed", 0),
        "failed": getattr(health, "failed", 0),
        "errors": getattr(health, "errors", 0),
        "collected_nothing": bool(getattr(health, "collected_nothing", False)),
        "clean": bool(getattr(health, "clean", False)),
        "caveat": health.caveat() if hasattr(health, "caveat") else "",
    }


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
                # Every reason not to read the rates above at face value. The text report
                # printed all of these and this export carried none of them, so the one
                # consumer that cannot ask a follow-up question - a CI gate reading
                # --json - got a kill rate with nothing qualifying it. `no_targets` alone
                # means the rates are not about this suite at all.
                "no_targets": audit.no_targets,
                "scored": len(audit.scored),
                "stopped_early": audit.stopped_early,
                "planned_mutants": audit.planned_mutants,
                "audited_functions": audit.audited_functions,
                "stray": audit.stray,
                # Why each survivor was not proven. Without it a consumer sees one
                # `unproven` count and cannot tell "the suite might not catch this" from
                # "this tool never managed to call the function".
                "unproven_by_reason": unproven_breakdown(audit),
                "suite_health": _health_json(audit.health),
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
        "input are listed separately, with the reason for each. Some are equivalent mutants",
        "no test could catch; most are functions this tool could not validly call, which is",
        "a fact about the tool and not about the suite. Counting any of them as gaps would",
        "inflate the number in a way nobody can check.",
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
            "They are not counted as gaps. Only one of the reasons below is about the test",
            "suite; the rest is how far this tool could reach.",
            "",
            "| why it was not proven | mutants |",
            "|---|---:|",
            *[
                f"| {dict(UNPROVEN_REASONS)[key]} | {n} |"
                for key, n in unproven_breakdown(audit).items()
            ],
        ]
    path.write_text("\n".join(out) + "\n", encoding="utf-8", newline="")
