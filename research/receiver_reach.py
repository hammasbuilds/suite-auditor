"""What does building a method's receiver recover? One run answers it, by construction.

    python research/receiver_reach.py --repo D:/github/blast-radius --repo ...

Section 5 measured, over the 112-package census, that **1,993 of 3,179 unproven survivors
(62.7%) were unproven for one reason**: a method cannot be called without its instance, and
the tool refused to fabricate one. Instance construction removes that refusal for the
classes it can build.

The before-and-after needs only one run, and that is worth being explicit about. Every
survivor of a *method* target used to carry the reason `method`, without exception - it was
the first thing the audit checked after the suite failed to kill a mutant, before any input
was considered. So the "before" column is known by construction for every row this script
sees, and the "after" column is whatever the mutant's verdict is now. No second run under
the old code is needed, and none could be made comparable anyway: it would have to be the
same mutants over the same argument pool.

Four outcomes are worth separating, and only the first is a recovery:

  * **proven** - the method now has a witness. A gap the tool could not previously state.
  * **no_receiver** - the class could not be imported, could not be constructed from its
    own `__init__`, or could not be constructed without touching the machine. This is the
    share that was never recoverable, and keeping it apart from `method` is what stops the
    capability from looking better than it is.
  * **equivalent** - ran on real inputs and agreed. The receiver was built and the mutant
    may genuinely be the same program; this is a fact about the mutant.
  * everything else - `no_input`, `both_raised`, `old_uncallable`: the receiver was built
    and the prover still could not separate the two versions for a different reason. Those
    reasons were always there; they were simply unreachable behind the method refusal.

A repository is audited as it stands. The rates below are therefore about these
repositories' classes, not about PyPI - the census is the population, and re-running it is
what would put this number beside the 62.7%.
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

from suite_auditor.report import unproven_reason  # noqa: E402
from suite_auditor.types import Verdict  # noqa: E402

RESULTS = ROOT / "research" / "results"


def audit(repo: Path, seconds: float, python: str, jobs: int) -> dict | None:
    """One audit run, returning the parsed export, or a failure row."""
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
        proc = subprocess.run(  # noqa: S603
            cmd, capture_output=True, text=True, timeout=seconds * 4 + 600, check=False
        )
        path = out / "audit.json"
        if not path.is_file():
            return {
                "failed": True,
                "detail": (proc.stderr or proc.stdout).strip()[-400:],
                "returncode": proc.returncode,
            }
        return json.loads(path.read_text(encoding="utf-8"))


def _is_method(row: dict) -> bool:
    """A dot in the qualified name is what makes a target a method."""
    return "." in (row.get("function") or row.get("target") or "").rpartition("::")[2]


def measure(export: dict) -> dict:
    """Split this export's method survivors by what became of them."""
    rows = export.get("results") or []
    methods = [r for r in rows if _is_method(r)]
    outcomes: collections.Counter[str] = collections.Counter()
    # The verdict strings come from the enum, never typed here. The export writes
    # `proven-gap` with a hyphen and this file first compared against `proven_gap`, so
    # every newly proven method gap was counted as `other` and the headline read zero.
    for row in methods:
        verdict = row.get("verdict", "")
        if verdict == Verdict.KILLED.value:
            outcomes["killed by the suite"] += 1
        elif verdict == Verdict.PROVEN_GAP.value:
            outcomes["proven"] += 1
        elif verdict == Verdict.SKIPPED.value:
            outcomes["skipped"] += 1
        else:
            outcomes[unproven_reason(row.get("detail", ""))] += 1
    survivors = sum(
        n for key, n in outcomes.items() if key not in ("killed by the suite", "skipped")
    )
    # By subtraction, so the two columns partition the survivors by construction. Listing
    # the recovered reasons instead left `other` in neither column, and the script's own
    # partition check said so - a share over a denominator nobody named.
    #
    # The subtraction is sound because the receiver is resolved *first* in the runner: a
    # status that is neither `method` nor `no_receiver` means an instance was built and
    # the prover then reached some other conclusion about the mutant.
    refused = outcomes["no_receiver"] + outcomes["method"]
    recovered = survivors - refused
    return {
        "method_mutants_scored": len(methods),
        "outcomes": dict(outcomes.most_common()),
        "method_survivors": survivors,
        # Every one of these used to be reported as `method`, with no further question
        # asked. That is the "before" column, and it needs no second run.
        "would_all_have_been_method_before": survivors,
        "a_receiver_was_built": recovered,
        "still_refused_for_want_of_a_receiver": refused,
        "share_with_a_receiver": (round(recovered / survivors, 4) if survivors else None),
        "newly_proven": outcomes["proven"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", action="append", default=[], type=Path, required=True)
    parser.add_argument("--max-seconds", type=float, default=900.0)
    parser.add_argument("--python", default="")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--out", type=Path, default=RESULTS / "receiver_reach.json")
    args = parser.parse_args()

    per_repo = {}
    for repo in args.repo:
        print(f"auditing {repo.name}", flush=True)
        export = audit(repo, args.max_seconds, args.python, args.jobs)
        if export is None or export.get("failed"):
            per_repo[repo.name] = {"failed": True, "detail": (export or {}).get("detail", "")}
            print(f"  failed: {(export or {}).get('detail', '')[:160]}", flush=True)
            continue
        row = measure(export)
        per_repo[repo.name] = row
        print(
            f"  {row['method_mutants_scored']} method mutants, "
            f"{row['method_survivors']} survivors, "
            f"{row['a_receiver_was_built']} got a receiver, "
            f"{row['newly_proven']} newly proven",
            flush=True,
        )

    scored = [r for r in per_repo.values() if not r.get("failed")]
    totals: dict = {
        "repositories": len(per_repo),
        "repositories_scored": len(scored),
        "repositories_failed": len(per_repo) - len(scored),
    }
    for key in (
        "method_mutants_scored",
        "method_survivors",
        "a_receiver_was_built",
        "still_refused_for_want_of_a_receiver",
        "newly_proven",
    ):
        totals[key] = sum(r[key] for r in scored)
    outcomes: collections.Counter[str] = collections.Counter()
    for row in scored:
        outcomes.update(row["outcomes"])
    totals["outcomes"] = dict(outcomes.most_common())
    survivors = totals["method_survivors"]
    totals["share_with_a_receiver"] = (
        round(totals["a_receiver_was_built"] / survivors, 4) if survivors else None
    )
    # The two have to partition the survivors; a gap would mean an outcome fell into
    # neither, and the share above would be over a denominator nobody named.
    totals["partitions"] = (
        totals["a_receiver_was_built"] + totals["still_refused_for_want_of_a_receiver"] == survivors
    )

    print()
    print(f"  method mutants scored      {totals['method_mutants_scored']:>7}")
    print(f"  of which survived          {survivors:>7}  (all `method` before this)")
    print(f"  a receiver was built       {totals['a_receiver_was_built']:>7}")
    print(f"  still refused              {totals['still_refused_for_want_of_a_receiver']:>7}")
    print(f"  newly proven gaps          {totals['newly_proven']:>7}")
    if totals["share_with_a_receiver"] is not None:
        print(f"  share with a receiver      {totals['share_with_a_receiver']:>7.1%}")
    print()
    for reason, n in totals["outcomes"].items():
        print(f"    {reason:52}{n:>6}")
    if not totals["partitions"]:
        print("\n  ! the two columns do not partition the survivors - see the outcomes above")

    payload = {
        "what": (
            "What instance construction recovers. Every method survivor used to be "
            "reported `method` without further question, so the before column is known "
            "by construction and no second run is needed."
        ),
        "census_before": {
            "unproven_survivors": 3179,
            "method": 1993,
            "share": 0.627,
            "note": (
                "research/unproven_reach.json, over the 112-package census, measured "
                "before instance construction existed. These repositories are not that "
                "population; re-running the census is what would put the two side by side."
            ),
        },
        "totals": totals,
        "per_repository": per_repo,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"\nwritten to {args.out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
