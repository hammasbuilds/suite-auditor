"""How much of a Python mutant set is trivially equivalent or duplicate?

Papadakis, Jia, Harman and Le Traon (ICSE 2015) report the figure for C and Java: over
7% of mutants discarded as equivalent and over 21% as duplicates on large C programs,
with a detection rate against hand-analysed ground truth of about 30% for C and about
54% for Java, which they attribute to mutation characteristics being language-dependent.

Python is not in that series, and the usual reason given - CPython has no optimising
compiler, so nothing collapses - is a hypothesis rather than a measurement. This script
measures it, over every function this tool would mutate in a corpus of real Python
packages, with the tool's own operators at its own cap.

    .venv-research/Scripts/python research/tce_rate.py --repo D:/github/blast-radius ...

Writes research/results/tce_rate.json. The planted-positive check that says the
instrument works at all lives in tests/test_tce.py and runs on every suite run; a rate
of zero reported by a detector nobody has shown to detect anything is not a result.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from suite_auditor import tce  # noqa: E402
from suite_auditor.mutate import find_targets, mutants  # noqa: E402

RESULTS = ROOT / "research" / "results"


def measure(repo: Path, cap: int) -> dict:
    """Every target in one repository, mutated and partitioned."""
    totals = Counter()
    by_operator: dict[str, Counter] = {}
    functions = 0
    try:
        targets = find_targets(repo)
    except (OSError, ValueError) as exc:  # a repository that cannot be walked
        return {"repo": repo.name, "error": f"{type(exc).__name__}: {exc}"}

    for target in targets:
        try:
            source = textwrap.dedent(target.source)
        except (AttributeError, TypeError):
            continue
        planned = mutants(source, cap=cap)
        if not planned:
            continue
        functions += 1
        part = tce.partition(source, planned)
        summary = tce.summarise(part)
        for key in ("planned", "kept", "equivalent", "duplicate", "undecidable"):
            totals[key] += summary[key]
        for _, operator in part["equivalent"]:
            by_operator.setdefault(operator, Counter())["equivalent"] += 1
        for _, operator, _ in part["duplicate"]:
            by_operator.setdefault(operator, Counter())["duplicate"] += 1
        for _, operator in planned:
            by_operator.setdefault(operator, Counter())["planned"] += 1

    planned = totals["planned"]
    return {
        "repo": repo.name,
        "functions_with_mutants": functions,
        **dict(totals),
        "equivalent_share": round(totals["equivalent"] / planned, 6) if planned else None,
        "duplicate_share": round(totals["duplicate"] / planned, 6) if planned else None,
        "by_operator": {k: dict(v) for k, v in sorted(by_operator.items())},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", action="append", required=True, type=Path)
    parser.add_argument("--cap", type=int, default=8, help="mutants per function")
    parser.add_argument("--out", type=Path, default=RESULTS / "tce_rate.json")
    args = parser.parse_args()

    rows = []
    for repo in args.repo:
        row = measure(repo, args.cap)
        rows.append(row)
        if "error" in row:
            print(f"{row['repo']:22s} {row['error']}")
            continue
        print(
            f"{row['repo']:22s} {row['planned']:6d} planned  "
            f"{row['equivalent']:4d} equivalent  {row['duplicate']:4d} duplicate  "
            f"{row['undecidable']:4d} undecidable"
        )

    measured = [r for r in rows if "error" not in r]
    planned = sum(r["planned"] for r in measured)
    equivalent = sum(r["equivalent"] for r in measured)
    duplicate = sum(r["duplicate"] for r in measured)
    payload = {
        "method": (
            "Trivial Compiler Equivalence (Papadakis et al., ICSE 2015), applied to "
            "CPython bytecode. A mutant whose canonical compiled form equals the "
            "original's is equivalent; two mutants sharing a form are duplicates. The "
            "canonical form is the instruction stream with operands resolved to the "
            "values they name, so a shuffled constant table does not hide a match."
        ),
        "interpreter": f"CPython {sys.version_info.major}.{sys.version_info.minor}",
        "mutants_per_function_cap": args.cap,
        "operators": sorted(
            {op for r in measured for op in r["by_operator"]},
        ),
        "totals": {
            "planned": planned,
            "equivalent": equivalent,
            "duplicate": duplicate,
            "undecidable": sum(r["undecidable"] for r in measured),
            "equivalent_share": round(equivalent / planned, 6) if planned else None,
            "duplicate_share": round(duplicate / planned, 6) if planned else None,
        },
        "repositories": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(
        f"\n{planned:,} mutants planned over {len(measured)} repositories: "
        f"{equivalent} equivalent, {duplicate} duplicate"
    )
    print(f"written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
