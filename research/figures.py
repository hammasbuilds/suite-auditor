"""Figures for the thousand-package auditability census, one function each.

Same house rules as the sibling repositories: matplotlib only, Okabe-Ito colours, nothing
encoded by colour alone, PDF for the manuscript and PNG beside it, no chart junk, and
every caption states its n so a figure cannot reach a paper without its denominator.

The four here answer the four objections the README's three bare counts invite:

  1. **the funnel** - where 888 of 1,000 packages went, each stage labelled with what it
     removed. The headline "11.2% auditable" is a product of six filters and a reader
     should see all six.
  2. **the kill-rate distribution** - an ECDF, not a bar. The median is 0.85 and the
     minimum is 0.0; a single number hides that one audited package killed nothing.
  3. **coverage against kill rate** - the two are measured independently and a package can
     be high on one and low on the other. The scatter shows whether they travel together.
  4. **proven gaps** - the count per package, which is zero for the median package and 33
     for the worst. A mean of 1.96 describes no package in the sample.

Each function returns (pdf, png, caption).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIGURES = ROOT / "research" / "figures"

BLUE, ORANGE, GREEN, GREY, PURPLE = "#0072B2", "#E69F00", "#009E73", "#999999", "#CC79A7"
# Excluded stages in grey, the audited remainder in blue: one is data, the rest is loss.
EXCLUDED, KEPT = GREY, BLUE


def _save(fig, name: str) -> tuple[Path, Path]:
    FIGURES.mkdir(parents=True, exist_ok=True)
    pdf, png = FIGURES / f"{name}.pdf", FIGURES / f"{name}.png"
    fig.savefig(pdf, bbox_inches="tight")
    fig.savefig(png, bbox_inches="tight", dpi=200)
    plt.close(fig)
    return pdf, png


def _bare(ax) -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def _ecdf(values: list[float]) -> tuple[list[float], list[float]]:
    xs = sorted(values)
    n = len(xs)
    return xs, [(i + 1) / n for i in range(n)]


# --- 1. the funnel -------------------------------------------------------------------


def funnel_figure(funnel: dict) -> tuple[Path, Path, str]:
    """Where the 1,000 packages went, stage by stage.

    Drawn as a horizontal bar per stage showing how many packages were still in play when
    that filter ran and how many it removed, because the two conditional readings - "40.7%
    of the top 1,000 ship no tests" and "42.7% of those that published an sdist do" - are
    different sentences and the figure should let a reader take either.
    """
    stages = funnel["stages"]
    labels = list(stages)
    reached = [stages[k]["reached_this_stage"] for k in labels]
    lost = [stages[k]["excluded"] for k in labels]
    audited = funnel["audited"]

    labels = [*labels, "audited"]
    reached = [*reached, audited["k"]]
    lost = [*lost, 0]

    fig, ax = plt.subplots(figsize=(7.4, 4.4))
    y = range(len(labels))
    ax.barh(
        list(y),
        reached,
        height=0.64,
        color="#DDDDDD",
        edgecolor="white",
        linewidth=0.6,
        label="still in play",
    )
    ax.barh(
        list(y),
        lost,
        height=0.64,
        color=EXCLUDED,
        edgecolor="white",
        linewidth=0.6,
        hatch="//",
        label="removed here",
    )
    ax.barh(
        [len(labels) - 1],
        [audited["k"]],
        height=0.64,
        color=KEPT,
        edgecolor="white",
        linewidth=0.6,
        label="audited",
    )

    for i, (stage_reached, stage_lost) in enumerate(zip(reached, lost, strict=True)):
        text = f"{stage_lost:,} of {stage_reached:,}" if stage_lost else f"{stage_reached:,}"
        ax.text(stage_reached + 14, i, text, va="center", fontsize=8.5)

    ax.set_yticks(list(y))
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("packages")
    ax.set_xlim(0, funnel["total"] * 1.18)
    ax.legend(frameon=False, fontsize=8.5, loc="lower right")
    _bare(ax)
    pdf, png = _save(fig, "fig1_funnel")
    caption = (
        f"From PyPI's {funnel['total']:,} most-downloaded packages to the "
        f"{audited['k']} that could be audited, one row per filter. Each row gives what "
        f"the filter removed over what reached it. The audited share is "
        f"{audited['rate']:.1%} of all {audited['n']:,} "
        f"[{audited['lo']:.1%}, {audited['hi']:.1%}], Wilson 95%, and "
        f"{funnel['audited_of_those_shipping_a_usable_suite']['rate']:.1%} of the "
        f"{funnel['audited_of_those_shipping_a_usable_suite']['n']} that ship a suite "
        f"collecting at least one test."
    )
    return pdf, png, caption


# --- 2. the kill-rate distribution ---------------------------------------------------


def kill_rate_figure(distribution: dict, rates: list[float]) -> tuple[Path, Path, str]:
    """Kill rate over the audited packages as an ECDF, with the quartiles marked.

    An ECDF rather than a histogram because the question a reader has is "what fraction of
    packages are below x", and because a histogram's bin width would be a free parameter
    doing argumentative work at the left tail, where the interesting packages are.
    """
    xs, ys = _ecdf(rates)
    fig, ax = plt.subplots(figsize=(6.6, 4.0))
    ax.step(xs, ys, where="post", color=BLUE, linewidth=1.8)
    ax.plot(xs, ys, linestyle="none", marker="o", markersize=2.4, color=BLUE, alpha=0.55)

    for key, style, label in (("p25", ":", "p25"), ("median", "--", "median"), ("p75", ":", "p75")):
        value = distribution[key]
        ax.axvline(value, color=GREY, linestyle=style, linewidth=1.1)
        ax.text(
            value,
            1.02,
            f"{label}\n{value:.3f}",
            ha="center",
            va="bottom",
            fontsize=8,
            color="#333333",
        )

    ax.set_xlabel("mutation kill rate")
    ax.set_ylabel("fraction of audited packages at or below")
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(0, 1.0)
    _bare(ax)
    pdf, png = _save(fig, "fig2_kill_rate_ecdf")
    caption = (
        f"Empirical distribution of mutation kill rate over the {distribution['n']} "
        f"audited packages. Median {distribution['median']:.3f}, quartiles "
        f"{distribution['p25']:.3f} and {distribution['p75']:.3f}, range "
        f"{distribution['min']:.3f} to {distribution['max']:.3f}. The left tail is the "
        f"part a mean of {distribution['mean']:.3f} hides: at least one audited package "
        f"killed no mutant at all."
    )
    return pdf, png, caption


# --- 3. coverage against kill rate ---------------------------------------------------


def coverage_figure(pairs: list[tuple[float, float]], caught: dict) -> tuple[Path, Path, str]:
    """Covered fraction against kill rate, with iso-contours of their product.

    An earlier draft of this figure drew the diagonal and invited the reader to look for
    packages below it. That comparison is meaningless: `audit.py` mutates only
    `[t for t in targets if t.covering_tests]`, so kill rate is measured **over the
    covered functions alone** and is conditional on the x axis. The two numbers share no
    base and neither bounds the other.

    What is comparable is their product - the share of the package's functions that are
    both reached by a test and checked by one - so the contours are lines of constant
    caught share. Two packages on the same curve audit equally well however differently
    their coverage and kill rate are arranged, and the distance from the origin, not the
    distance from a diagonal, is the thing to read.
    """
    covered = [c for c, _ in pairs]
    killed = [k for _, k in pairs]
    fig, ax = plt.subplots(figsize=(6.0, 5.4))

    xs = [i / 400 for i in range(5, 401)]
    for level in (0.25, 0.5, 0.75, 0.9):
        ys = [level / x for x in xs]
        keep = [(x, y) for x, y in zip(xs, ys, strict=True) if y <= 1.0]
        if not keep:
            continue
        ax.plot(
            [x for x, _ in keep], [y for _, y in keep], color=GREY, linestyle=":", linewidth=1.0
        )
        x0, y0 = keep[0]
        ax.text(
            min(x0 + 0.01, 0.97),
            min(y0, 0.99),
            f"{level:.2f}",
            fontsize=7.5,
            color="#555555",
            va="top",
        )

    ax.scatter(
        covered,
        killed,
        s=26,
        color=BLUE,
        alpha=0.62,
        edgecolor="white",
        linewidth=0.5,
        label="one audited package",
    )
    ax.plot(
        [],
        [],
        color=GREY,
        linestyle=":",
        linewidth=1.0,
        label="constant caught share (coverage x kill rate)",
    )

    ax.set_xlabel("covered fraction (functions a passing test reaches)")
    ax.set_ylabel("kill rate (mutants noticed, over covered functions only)")
    ax.set_xlim(-0.03, 1.03)
    ax.set_ylim(-0.03, 1.03)
    ax.set_aspect("equal")
    ax.legend(frameon=False, fontsize=8, loc="lower left")
    _bare(ax)
    pdf, png = _save(fig, "fig3_coverage_vs_kill_rate")
    caption = (
        f"Covered fraction against kill rate, one point per audited package "
        f"(n={len(pairs)}), with dotted iso-contours of their product. Kill rate is "
        f"measured over covered functions only, so it is conditional on the x axis and "
        f"the two axes are not comparable to each other; the product is. Median caught "
        f"share is {caught['median']:.3f} (quartiles {caught['p25']:.3f} and "
        f"{caught['p75']:.3f}), against a median kill rate of 0.850 - the gap between "
        f"those two medians is what reporting kill rate alone conceals."
    )
    return pdf, png, caption


# --- 4. proven gaps ------------------------------------------------------------------


def gaps_figure(distribution: dict, share: dict, counts: list[float]) -> tuple[Path, Path, str]:
    """Proven gaps per package, as a count histogram with the zero bar kept visible.

    The mean is 1.96 and the median is 0. That gap is the whole point of the figure: most
    audited packages have no proven gap and a few have many, so a mean describes nobody.
    """
    top = int(max(counts)) if counts else 0
    edges = list(range(0, min(top, 12) + 2))
    clipped = [min(c, 11) for c in counts]

    fig, ax = plt.subplots(figsize=(6.8, 4.0))
    ax.hist(
        clipped,
        bins=edges,
        align="left",
        rwidth=0.82,
        color=ORANGE,
        edgecolor="white",
        linewidth=0.7,
    )
    ax.axvline(distribution["mean"], color=GREY, linestyle="--", linewidth=1.2)
    ax.text(
        distribution["mean"],
        ax.get_ylim()[1] * 0.92,
        f" mean {distribution['mean']:.2f}",
        fontsize=8.5,
        color="#333333",
    )
    ax.set_xticks(edges[:-1])
    ax.set_xticklabels([str(e) for e in edges[:-2]] + ["11+"], fontsize=8.5)
    ax.set_xlabel("proven gaps found in the package")
    ax.set_ylabel("packages")
    _bare(ax)
    pdf, png = _save(fig, "fig4_proven_gaps")
    caption = (
        f"Proven gaps per audited package (n={distribution['n']}), counts of 11 and above "
        f"collected in the last bar. The median package has "
        f"{distribution['median']:.0f}; the maximum is {distribution['max']:.0f}. "
        f"{share['k']} of {share['n']} packages have at least one "
        f"({share['rate']:.1%} [{share['lo']:.1%}, {share['hi']:.1%}], Wilson 95%), so "
        f"the mean of {distribution['mean']:.2f} describes no package in the sample."
    )
    return pdf, png, caption
