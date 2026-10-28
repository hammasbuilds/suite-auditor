"""Reduce docs/target-census.json to the small file the README's numbers are read from.

The census itself is 19 MB - it carries the full source of every mutant it scored - so it
is not committed. Without this step a thousand-project run finishes and publishes nothing:
no figure in the README came from it and no test read it, so the whole measurement existed
only as an uncommittable file on one laptop.

What it publishes, and why each number has a denominator beside it:

  considered      how many packages the census walked
  audited         how many produced a rate
  excluded        how many did not, broken down by reason
  kill_rate       median over the audited projects - and `kill_rate_n`, because a median
                  over 110 projects presented beside a headline of 117 is the fault this
                  file exists to prevent

    python scripts/census_summary.py [--census docs/target-census.json]
"""

from __future__ import annotations

import argparse
import collections
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from target_census import scored_nothing  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _bucket(reason: str) -> str:
    """The reason, with the varying part cut off so like rows group together.

    `budget exceeded (300s)` and `memory budget exceeded: 5,060 MB over ...` are one
    reason each however many numbers they carry.
    """
    for prefix in (
        "sdist ships no test directory",
        "clean suite collects no tests",
        "clean suite did not finish",
        "no sdist published",
        "audit planned no mutants",
        "audit produced no report",
        "audit scored none of its",
        "budget exceeded",
        "memory budget exceeded",
        "install failed",
        "candidate failed",
    ):
        if reason.startswith(prefix):
            return prefix
    return reason.split(":")[0][:60]


def summarise(rows: list[dict]) -> dict:
    audited, excluded = [], []
    for row in rows:
        if row.get("excluded"):
            excluded.append(row["excluded"])
            continue
        audit = row.get("audit") or {}
        # Re-applied here, not trusted from the file: rows written before this rule
        # existed are still on disk, recorded as audited while scoring nothing.
        why = scored_nothing(audit)
        if why:
            excluded.append(why)
        else:
            audited.append(row)

    def median_of(key: str, source: str = "audit") -> tuple[float | None, int]:
        values = [
            (r.get(source) or {})[key]
            for r in audited
            if (r.get(source) or {}).get(key) is not None
        ]
        return (round(statistics.median(values), 4) if values else None), len(values)

    kill_rate, kill_n = median_of("kill_rate")
    covered, covered_n = median_of("covered_fraction")
    gaps = [(r.get("audit") or {}).get("proven_gaps", 0) for r in audited]

    return {
        "considered": len(rows),
        "audited": len(audited),
        "excluded": len(excluded),
        "exclusions": dict(collections.Counter(_bucket(e) for e in excluded).most_common()),
        "kill_rate_median": kill_rate,
        "kill_rate_n": kill_n,
        "covered_fraction_median": covered,
        "covered_fraction_n": covered_n,
        "projects_with_a_proven_gap": sum(1 for g in gaps if g),
        "proven_gaps_total": sum(gaps),
        "audited_packages": sorted(r["package"] for r in audited),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--census", type=Path, default=ROOT / "docs" / "target-census.json")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "census-summary.json")
    args = ap.parse_args()
    if not args.census.exists():
        raise SystemExit(f"no census at {args.census} - run scripts/target_census.py first")
    rows = json.loads(args.census.read_text(encoding="utf-8"))
    summary = summarise(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    shown = dict(summary)
    shown.pop("audited_packages")
    print(json.dumps(shown, indent=1))
    print(f"\nwritten to {args.out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
