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
import gzip
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


def ledger(rows: list[dict]) -> list[dict]:
    """Every row, without the per-mutant detail that makes the census 19 MB.

    `audit.results` holds one entry per scored mutant including its mutated source. The
    summariser never reads it: it reads kill_rate, covered_fraction, proven_gaps and the
    exclusion reasons, all of which are aggregates sitting beside it. So the ledger is
    the census with that one key removed - small enough to commit, and enough to recompute
    every published figure.

    Why commit it at all, when docs/census-summary.json is already committed: a summary is
    six medians and a package list. It cannot say what any one project scored, and it
    cannot show a row changing. blast-radius kept the same arrangement until a
    re-measurement overwrote four measured rows and there was nothing to compare against
    or restore from.
    """
    out = []
    for row in rows:
        copy = dict(row)
        audit = row.get("audit")
        if isinstance(audit, dict):
            copy["audit"] = {k: v for k, v in audit.items() if k != "results"}
            # Only when there were results to drop. Computing it unconditionally made
            # this non-idempotent: a ledger rebuilt from a ledger has no `results`, so
            # len(None) -> 0 silently overwrote the count with zero. The same shape of
            # bug as len() on an already-reduced value in blast-radius.
            if "results" in audit:
                copy["audit"]["results_dropped"] = len(audit.get("results") or [])
        out.append(copy)
    return out


def read_census(path: Path) -> list[dict]:
    """The raw census, or the committed ledger - whichever the path points at.

    Nothing in `summarise` reads a mutant's source, so the ledger answers every question
    the summary asks and anyone with the repository can recompute the published figures
    without the 19 MB artefact that is not in it.
    """
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as fh:
            return json.loads(fh.read().decode("utf-8"))["rows"]
    return json.loads(path.read_text(encoding="utf-8"))


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
    ap.add_argument("--ledger", type=Path, default=ROOT / "docs" / "census-rows.json.gz",
                    help="per-project rows without the per-mutant detail, committed, so "
                         "every published figure can be recomputed from the repository "
                         "and a changed row shows up as a diff")
    args = ap.parse_args()
    if not args.census.exists():
        raise SystemExit(f"no census at {args.census} - run scripts/target_census.py first")
    # Resolved, not as typed: `--census docs/census-rows.json.gz` and the default
    # `<repo>/docs/census-rows.json.gz` are the same file and compare unequal as Paths.
    if args.census.resolve() == args.ledger.resolve():
        raise SystemExit(
            "--census and --ledger are the same file: pass --ledger to write it "
            "elsewhere, or point --census at the raw census"
        )
    rows = read_census(args.census)
    summary = summarise(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    lines = ledger(rows)
    # mtime=0: gzip stores a timestamp, and a ledger identical except for when it was
    # written is a diff every time the script runs.
    with gzip.GzipFile(args.ledger, "wb", mtime=0) as fh:
        fh.write(json.dumps({"rows": lines}, indent=1).encode("utf-8"))
    shown = dict(summary)
    shown.pop("audited_packages")
    print(json.dumps(shown, indent=1))
    print(f"\nwritten to {args.out.name}, and {len(lines)} row(s) to {args.ledger.name} "
          f"({args.ledger.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
