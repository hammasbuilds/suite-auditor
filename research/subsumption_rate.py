"""How many proven gaps are restatements of another proven gap?

`tce` cuts redundant mutants before they are ever scored, by compiling them: two that
compile to the same bytecode cannot be told apart by anything. `subsume` asks the semantic
version of the question about the *survivors* - two mutants separated by exactly the same
inputs are one defect reported twice - following Ammann, Delamaro & Offutt (ICST 2014).

This script runs the auditor over a set of repositories and reports how much of the gap
count that removes. It also reports the figure that bounds the answer before any kill set
is compared: **how many gaps are even eligible**. Kill sets are indices into one function's
argument pool, so only gaps in the same function can be compared at all, and a function
with a single proven gap can never have a restatement. If gaps do not cluster by function,
the ceiling on what subsumption can collapse is low whatever the kill sets look like, and
reporting the collapse without the ceiling would make a structural fact look like a
measurement about kill sets.

    .venv/Scripts/python research/subsumption_rate.py --repo D:/github/blast-radius ...

Writes research/results/subsumption_rate.json. The planted-positive checks that say the
instrument detects anything at all live in tests/test_subsume.py and run on every suite
run: a collapse rate of zero reported by a detector nobody has shown to detect anything is
not a result.
"""

from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from suite_auditor import subsume  # noqa: E402

RESULTS = ROOT / "research" / "results"


def audit(repo: Path, seconds: float, python: str, jobs: int) -> dict | None:
    """One audit run, returning the parsed export, or `None` if it produced none."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out"
        cmd = [
            sys.executable,
            "-m",
            "suite_auditor",
            "audit",
            str(repo),
            "--out",
            str(out),
            "--max-seconds",
            str(seconds),
            "-j",
            str(jobs),
        ]
        if python:
            cmd += ["--python", python]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds * 4 + 600)
        path = out / "audit.json"
        if not path.is_file():
            return {
                "failed": True,
                # The tail, not the head: the reason an audit produced nothing is at the
                # end of its output, and a truncated head reads as "no error".
                "detail": (proc.stderr or proc.stdout).strip()[-400:],
                "returncode": proc.returncode,
            }
        return json.loads(path.read_text(encoding="utf-8"))


def measure(export: dict) -> dict:
    """The eligibility ceiling and the collapse, from one audit export."""
    rows = export.get("results") or []
    gaps = [r for r in rows if r["verdict"] == "proven-gap"]
    placed = [r for r in gaps if r.get("signature")]
    per_function = collections.Counter(r["target"] for r in placed)
    # A gap alone in its function cannot be a restatement of anything, so it is outside
    # what subsumption could ever act on. This is the ceiling.
    eligible = [r for r in placed if per_function[r["target"]] >= 2]
    restated = [r for r in placed if r.get("subsumed_by")]

    # Recomputed from the exported kill sets rather than read off `subsumed_by`, so the
    # artefact and the engine have to agree. One of them being wrong is otherwise
    # invisible, because both would be wrong in the same direction.
    recomputed = 0
    for target, count in per_function.items():
        if count < 2:
            continue
        part = subsume.partition(
            {r["mutant"]: frozenset(r["signature"]) for r in placed if r["target"] == target}
        )
        recomputed += part.restatements

    return {
        "mutants_scored": len(rows),
        "proven_gaps": len(gaps),
        "placed_in_the_hierarchy": len(placed),
        "unplaceable": len(gaps) - len(placed),
        "functions_with_a_gap": len(per_function),
        "functions_with_two_or_more": sum(1 for c in per_function.values() if c >= 2),
        "gaps_eligible_for_collapse": len(eligible),
        "eligible_share": (round(len(eligible) / len(placed), 4) if placed else None),
        "restatements": len(restated),
        "restatements_recomputed_from_the_export": recomputed,
        "distinct": len(placed) - len(restated),
        "collapse_share": (round(len(restated) / len(placed), 4) if placed else None),
        "engine_counts": export.get("subsumption"),
        # The engine now computes the ceiling too, so that a consumer holding only the
        # report - the 112-package census, which keeps no per-mutant rows - has it. That
        # makes three of these figures quantities with two implementations, and the whole
        # argument of this file is that two implementations of one number drift. So they
        # are compared here, by name, and the disagreements are listed rather than left to
        # a reader noticing that two tables differ.
        "engine_disagrees_about": _disagreements(
            export.get("subsumption") or {},
            {
                "proven": len(gaps),
                "placed": len(placed),
                "unplaceable": len(gaps) - len(placed),
                "distinct": len(placed) - len(restated),
                "restatements": len(restated),
                "functions_with_a_gap": len(per_function),
                "functions_with_two_or_more": sum(1 for c in per_function.values() if c >= 2),
                "eligible": len(eligible),
            },
        ),
    }


def _disagreements(engine: dict, here: dict) -> dict:
    """Fields where the engine's count and this file's differ, with both values.

    Empty when they agree, which is the normal case and the one worth being able to assert.
    A field the engine does not carry is reported as absent rather than as a disagreement:
    an export written before the field existed is stale, not wrong.
    """
    out = {}
    for key, value in sorted(here.items()):
        if key not in engine:
            out[key] = {"engine": None, "recomputed": value, "why": "absent from the export"}
        elif engine[key] != value:
            out[key] = {"engine": engine[key], "recomputed": value}
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", action="append", default=[], type=Path, required=True)
    parser.add_argument("--max-seconds", type=float, default=900.0)
    parser.add_argument("--python", default="")
    parser.add_argument("-j", "--jobs", type=int, default=2)
    parser.add_argument("--out", type=Path, default=RESULTS / "subsumption_rate.json")
    args = parser.parse_args()

    per_repo = {}
    for repo in args.repo:
        name = repo.name
        print(f"[{name}] auditing...", flush=True)
        venv = repo / ".venv" / "Scripts" / "python.exe"
        export = audit(
            repo,
            args.max_seconds,
            args.python or (str(venv) if venv.is_file() else ""),
            args.jobs,
        )
        if export is None or export.get("failed"):
            per_repo[name] = {"failed": True, "detail": (export or {}).get("detail", "")}
            print(f"[{name}] produced no export: {per_repo[name]['detail'][:160]}", flush=True)
            continue
        per_repo[name] = measure(export)
        row = per_repo[name]
        print(
            f"[{name}] {row['proven_gaps']} gaps, "
            f"{row['gaps_eligible_for_collapse']} eligible, "
            f"{row['restatements']} restatements",
            flush=True,
        )

    scored = {k: v for k, v in per_repo.items() if not v.get("failed")}
    totals = {
        key: sum(v[key] for v in scored.values())
        for key in (
            "mutants_scored",
            "proven_gaps",
            "placed_in_the_hierarchy",
            "unplaceable",
            "functions_with_a_gap",
            "functions_with_two_or_more",
            "gaps_eligible_for_collapse",
            "restatements",
            "restatements_recomputed_from_the_export",
            "distinct",
        )
    }
    placed = totals["placed_in_the_hierarchy"]
    totals["eligible_share"] = (
        round(totals["gaps_eligible_for_collapse"] / placed, 4) if placed else None
    )
    totals["collapse_share"] = round(totals["restatements"] / placed, 4) if placed else None
    totals["repositories_scored"] = len(scored)
    totals["repositories_failed"] = len(per_repo) - len(scored)

    payload = {
        "what": (
            "Proven gaps over local repositories, split into distinct ones and "
            "restatements of another gap of the same function, after Ammann, Delamaro & "
            "Offutt (ICST 2014). Reports the eligibility ceiling as well as the collapse: "
            "kill sets are only comparable within one function."
        ),
        "totals": totals,
        "by_repository": per_repo,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print("\n" + json.dumps(totals, indent=1))
    print(f"\nwritten to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
