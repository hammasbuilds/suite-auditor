"""Regenerate the census's statistical treatment and figures from the committed ledger.

    .venv-research/Scripts/python research/run_all.py

Reads `docs/census-rows.json.gz` only - in the repository - so every number and every
figure rebuilds without re-running the thousand-package audit.

Nothing here ships: `research/` is in neither the wheel nor the sdist's include list.
"""

from __future__ import annotations

import json
from pathlib import Path

import figures
import metrics

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "research" / "results"


def write(name: str, payload) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{name}.json"
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return path


def main() -> int:
    rows = metrics.load()
    done = metrics.audited(rows)
    print(f"{len(rows):,} rows, {len(done):,} audited, "
          f"{len(rows) - len(done):,} excluded\n")

    funnel = metrics.funnel(rows)
    distributions = metrics.audit_distributions(rows)
    bias = metrics.selection_bias(rows)
    write("auditability", {"funnel": funnel, "distributions": distributions,
                           "selection_bias": bias})

    print("funnel:")
    for label, stage in funnel["stages"].items():
        print(f"  {label:34}{stage['excluded']:>5} of {stage['reached_this_stage']:>5}  "
              f"({stage['of_those_that_reached_this_stage']['rate']:.1%} of those that "
              f"reached it)")
    a = funnel["audited"]
    print(f"  {'audited':34}{a['k']:>5} of {a['n']:>5}  "
          f"({a['rate']:.1%} [{a['lo']:.1%}, {a['hi']:.1%}])\n")

    # The rows the figures draw, taken from the same `audited` predicate the funnel used
    # rather than re-filtered here, so a figure cannot be drawn over a different sample
    # than the table above it.
    rates = [r["audit"]["kill_rate"] for r in done
             if r["audit"].get("kill_rate") is not None]
    pairs = [(r["audit"]["covered_fraction"], r["audit"]["kill_rate"]) for r in done
             if r["audit"].get("covered_fraction") is not None
             and r["audit"].get("kill_rate") is not None]
    gaps = [float(r["audit"].get("proven_gaps") or 0) for r in done]

    assert len(rates) == distributions["kill_rate"]["n"], (
        f"{len(rates)} kill rates for the figure but "
        f"{distributions['kill_rate']['n']} in the table - the figure and the table "
        f"would describe different samples"
    )

    captions = {}
    for name, (pdf, png, caption) in {
        "fig1_funnel": figures.funnel_figure(funnel),
        "fig2_kill_rate_ecdf": figures.kill_rate_figure(
            distributions["kill_rate"], rates),
        "fig3_coverage_vs_kill_rate": figures.coverage_figure(
            pairs, distributions["caught_share"]),
        "fig4_proven_gaps": figures.gaps_figure(
            distributions["proven_gaps"],
            distributions["packages_with_a_proven_gap"], gaps),
    }.items():
        captions[name] = caption
        print(f"  {png.name}")
    write("captions", captions)

    print(f"\nwritten to {RESULTS} and {ROOT / 'research' / 'figures'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
