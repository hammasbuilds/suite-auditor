"""What stops a survivor from being proven? The refusals, counted over the census.

    python research/unproven_reach.py [--census docs/census-rows.json.gz]

Section 5 of the manuscript says that proven gaps are a lower bound, that the tool refuses
what it cannot witness, and that the true share of packages with a real gap is therefore
"higher by an unknown amount". The amount is not unknown - every refusal is recorded with
its reason, and the census carries 112 packages' worth of them. This counts them.

That matters more than a tidier sentence. A lower bound whose gap is dominated by one cause
is a different object from one spread evenly over six: the first names the next piece of
work, and the second says the tool is near the limit of the approach. It is the first.

The six reasons, as `report.UNPROVEN_REASONS` defines them, in the order that file lists:

  * **method** - a method, which cannot be called without its instance. The tool refuses to
    fake `self`, because a call with a fabricated receiver proves nothing either way.
  * **no_input** - every generated argument set raised on both sides.
  * **both_raised** - the two versions disagreed only where both raised, which is not proof.
  * **old_uncallable** - the unmutated function could not be called either.
  * **equivalent** - ran on real inputs and agreed, so possibly an equivalent mutant. This
    is the only reason that is a fact about the *mutant* rather than about the prover's
    reach, and the only one more compute cannot move.
  * **other** - unclassified.

The breakdown is read from each row's own `unproven_by_reason`, which the audit computes
from each result's `detail`, so nothing here re-derives a classification. The script exists
to aggregate and to separate the reasons that are the prover's reach from the one that is
not - a total that mixed them would let "possibly equivalent mutants" stand for all of it,
which is exactly what the per-reason breakdown was added to stop.
"""

from __future__ import annotations

import argparse
import collections
import gzip
import json
import pathlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

RESULTS = ROOT / "research" / "results"

# The one reason that is a property of the mutant rather than of the prover. Everything
# else is reach, and reach is what more work can change.
ABOUT_THE_MUTANT = ("equivalent",)


def _rows(path: Path) -> list[dict]:
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as handle:
            payload = json.loads(handle.read().decode("utf-8"))
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["rows"] if isinstance(payload, dict) else payload


def measure(rows: list[dict]) -> dict:
    from census_summary import scored_nothing  # noqa: PLC0415

    # The summariser's own predicate, imported rather than restated: a row recording an
    # audit that scored none of its mutants is an exclusion, and counting its refusals
    # would put a package that measured nothing into a rate about what measuring finds.
    audited = [
        r
        for r in rows
        if not r.get("excluded") and r.get("audit") and not scored_nothing(r["audit"])
    ]
    reasons: collections.Counter[str] = collections.Counter()
    packages_with: collections.Counter[str] = collections.Counter()
    scored = unproven = killed = proven = 0
    for row in audited:
        audit = row["audit"]
        counts = audit.get("counts") or {}
        scored += audit.get("scored") or 0
        killed += counts.get("killed") or 0
        unproven += counts.get("unproven") or 0
        proven += audit.get("proven_gaps") or 0
        for reason, n in (audit.get("unproven_by_reason") or {}).items():
            reasons[reason] += n
            packages_with[reason] += 1

    total = sum(reasons.values())
    reach = total - sum(reasons.get(r, 0) for r in ABOUT_THE_MUTANT)
    out = {
        "packages_audited": len(audited),
        "mutants_scored": scored,
        "killed": killed,
        "proven_gaps": proven,
        "unproven_survivors": unproven,
        # The two counts come from different fields of the same report - `counts.unproven`
        # and the sum of `unproven_by_reason` - so they are compared rather than assumed
        # equal. A gap between them would mean some survivors carry a detail the
        # classifier does not recognise, which is the "other" bucket's job to catch.
        "unproven_classified": total,
        "unproven_unclassified": unproven - total,
        "by_reason": {
            reason: {
                "survivors": n,
                "share_of_unproven": round(n / total, 4) if total else None,
                "packages": packages_with[reason],
                "about": "the mutant" if reason in ABOUT_THE_MUTANT else "the prover's reach",
            }
            for reason, n in reasons.most_common()
        },
        "within_the_prover_s_reach": reach,
        "share_of_unproven_within_reach": round(reach / total, 4) if total else None,
    }
    if total:
        largest, count = reasons.most_common(1)[0]
        out["largest_single_cause"] = {
            "reason": largest,
            "survivors": count,
            "share_of_unproven": round(count / total, 4),
            "share_of_all_scored_mutants": round(count / scored, 4) if scored else None,
            "packages_affected": packages_with[largest],
            "about": "the mutant" if largest in ABOUT_THE_MUTANT else "the prover's reach",
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--census", type=pathlib.Path, default=ROOT / "docs" / "census-rows.json.gz"
    )
    parser.add_argument("--out", type=pathlib.Path, default=RESULTS / "unproven_reach.json")
    args = parser.parse_args()
    if not args.census.exists():
        raise SystemExit(f"no census at {args.census}")
    report = measure(_rows(args.census))

    print(f"{report['packages_audited']} packages, {report['mutants_scored']:,} mutants scored")
    print(f"  killed            {report['killed']:>7,}")
    print(f"  proven gaps       {report['proven_gaps']:>7,}")
    print(f"  unproven          {report['unproven_survivors']:>7,}")
    if report["unproven_unclassified"]:
        print(f"  ! unclassified    {report['unproven_unclassified']:>7,}")
    print()
    print(f"  {'reason':16}{'survivors':>11}{'share':>9}{'packages':>10}  about")
    for reason, row in report["by_reason"].items():
        print(
            f"  {reason:16}{row['survivors']:>11,}{row['share_of_unproven']:>9.1%}"
            f"{row['packages']:>10}  {row['about']}"
        )
    print()
    largest = report.get("largest_single_cause")
    if largest:
        print(
            f"  largest single cause: {largest['reason']} - {largest['survivors']:,} "
            f"survivors, {largest['share_of_unproven']:.1%} of all refusals, across "
            f"{largest['packages_affected']} packages"
        )
        print(f"  it is about {largest['about']}")
    print(
        f"  {report['share_of_unproven_within_reach']:.1%} of refusals are the prover's "
        "reach rather than a property of the mutant"
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"\nwritten to {args.out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
