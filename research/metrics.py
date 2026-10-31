"""Statistical treatment of the thousand-package auditability census.

The README's publishable nugget is a funnel: of the 1,000 most-downloaded packages on
PyPI, 407 ship no test directory, 299 ship tests that collect nothing, and 112 could be
audited. Three counts with no intervals, no distributional view of the 112, and no
statement of what the funnel does to generalisability - which is three reviewer comments
waiting to happen.

What is computed here, from `docs/census-rows.json.gz` alone:

  * **Wilson intervals** on every stage of the funnel. 407/1000 is a proportion of a
    sample and needs one; and the interval on 112/1000 is what says how precisely the
    auditable fraction is known.
  * **the funnel as conditional probabilities**, because "11% of packages are auditable"
    and "11% of packages that ship tests are auditable" are different claims and only one
    of them is true.
  * **the distribution of kill rate and covered fraction** over the 112, with quartiles.
    A median kill rate is the headline; the spread is what says whether 112 packages
    behave like one population.
  * **selection bias, stated numerically.** The 112 are not a random sample of PyPI: they
    are the packages that ship a suite, whose suite passes alone, and that fit a 300s
    budget. Each filter is a plausible correlate of test quality, so the audited sample is
    biased towards better-tested packages - which means the kill rates are an upper bound
    on the population, not an estimate of it. The size of each filter is reported so a
    reader can see how much selection happened.
"""

from __future__ import annotations

import gzip
import json
import math
import statistics
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "docs" / "census-rows.json.gz"

# The funnel, in the order a package falls out of it. Keys are substrings of the
# recorded exclusion reason; order matters because a package is excluded once.
STAGES = [
    ("no sdist published", "no sdist on PyPI"),
    ("sdist ships no test directory", "ships no tests"),
    ("clean suite collects no tests", "tests collect nothing"),
    ("clean suite did not finish", "suite did not finish in 120s"),
    ("budget exceeded", "audit budget exceeded (300s)"),
    ("audit planned no mutants", "no mutable targets found"),
]


def load(path: Path = LEDGER) -> list[dict]:
    with gzip.open(path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))["rows"]


def wilson(successes: int, n: int, z: float = 1.96) -> dict | None:
    """Wilson score interval - stays inside [0, 1] where the normal approximation does not."""
    if n == 0:
        return None
    p = successes / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return {"rate": round(p, 4), "lo": round(max(0.0, centre - half), 4),
            "hi": round(min(1.0, centre + half), 4), "n": n, "k": successes}


def audited(rows: list[dict]) -> list[dict]:
    return [r for r in rows if not r.get("excluded") and isinstance(r.get("audit"), dict)]


def funnel(rows: list[dict]) -> dict:
    """Each stage with its share of the whole and of what reached it.

    Both, because "40.7% of PyPI's top 1,000 ship no tests" and "40.7% of those that
    published an sdist ship no tests" are different sentences and a paper has to pick.
    """
    reasons = Counter()
    for row in rows:
        why = str(row.get("excluded") or "")
        for needle, label in STAGES:
            if why.startswith(needle) or needle in why:
                reasons[label] += 1
                break
        else:
            if why:
                reasons["other reason"] += 1

    total = len(rows)
    remaining = total
    out = {"total": total, "stages": {}}
    for _, label in [*STAGES, ("", "other reason")]:
        lost = reasons.get(label, 0)
        if not lost:
            continue
        out["stages"][label] = {
            "excluded": lost,
            "of_all": wilson(lost, total),
            "of_those_that_reached_this_stage": wilson(lost, remaining),
            "reached_this_stage": remaining,
        }
        remaining -= lost
    out["audited"] = wilson(len(audited(rows)), total)
    out["audited_of_those_shipping_a_usable_suite"] = wilson(
        len(audited(rows)),
        total - reasons.get("no sdist on PyPI", 0) - reasons.get("ships no tests", 0),
    )
    return out


def distribution(values: list[float]) -> dict | None:
    """Quartiles and spread, not a mean.

    A mean kill rate over 112 packages is a number no package has; the quartiles say
    whether they behave like one population.
    """
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    n = len(values)

    def q(p: float) -> float:
        return round(values[min(n - 1, int(p * n))], 4)

    return {
        "n": n, "min": round(values[0], 4), "p25": q(0.25), "median": q(0.5),
        "p75": q(0.75), "p90": q(0.90), "max": round(values[-1], 4),
        "mean": round(statistics.fmean(values), 4),
        "stdev": round(statistics.stdev(values), 4) if n > 1 else None,
    }


def audit_distributions(rows: list[dict]) -> dict:
    done = audited(rows)
    return {
        "kill_rate": distribution([r["audit"].get("kill_rate") for r in done]),
        "covered_fraction": distribution(
            [r["audit"].get("covered_fraction") for r in done]
        ),
        # Kill rate is measured only over functions a passing test reaches (`audit.py`
        # mutates `[t for t in targets if t.covering_tests]`), so it is conditional on
        # coverage and the two are not comparable to each other. Their product is the
        # unconditional quantity - the share of the package's functions that are both
        # reached and checked - and it is the number a reader should take away. The
        # median kill rate of 0.85 and the median caught share are different claims.
        "caught_share": distribution([r["audit"].get("caught_share") for r in done]),
        "proven_gaps": distribution(
            [float(r["audit"].get("proven_gaps") or 0) for r in done]
        ),
        "packages_with_a_proven_gap": wilson(
            sum(1 for r in done if r["audit"].get("proven_gaps")), len(done)
        ),
        "mutants_scored": distribution(
            [float(r["audit"].get("scored") or 0) for r in done]
        ),
    }


def selection_bias(rows: list[dict]) -> dict:
    """The filters between PyPI's top 1,000 and the 112, each a plausible correlate.

    Stated rather than adjusted for, because there is nothing to adjust with: the excluded
    packages have no kill rate by construction. The direction is knowable even so - every
    filter removes packages with weaker or slower test suites - so the audited kill rates
    are an upper bound on the population.
    """
    total = len(rows)
    done = len(audited(rows))
    return {
        "population": "the 1,000 most-downloaded packages on PyPI",
        "audited": done,
        "share_audited": wilson(done, total),
        "filters_in_order": [label for _, label in STAGES],
        "direction": (
            "Each filter removes packages that ship no suite, whose suite fails or "
            "collects nothing alone, or whose suite is too slow to audit in the budget. "
            "All of those plausibly correlate with weaker testing, so the 112 are biased "
            "towards better-tested packages and their kill rates are an upper bound on "
            "the population rather than an estimate of it."
        ),
        "what_would_remove_the_bias": (
            "Auditing a random sample of the excluded packages is impossible for the ones "
            "with no suite, and would need a much larger time budget for the ones that "
            "timed out. The 60 budget-exceeded and 25 did-not-finish packages are the only "
            "recoverable part, and recovering them would need roughly an order of "
            "magnitude more compute."
        ),
    }
