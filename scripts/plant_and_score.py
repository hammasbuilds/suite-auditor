"""Measure the prover's sensitivity by planting gaps whose existence is known.

    python scripts/plant_and_score.py [--target examples/pricing] [--json out.json]

**Why this exists.** The headline evidence for this tool was "toolz: 418 mutants, 92.6%
kill rate, 0 proven gaps". That reads as a clean bill of health for toolz's suite, and it
cannot be: a prover that proved nothing anywhere would also score 0. Nothing here measured
how often the prover proves a gap that is really there, so "0" had no scale behind it.

**How a gap is planted.** A gap, in this tool's terms, is a mutant the suite fails to kill
*and* for which a concrete call separates the mutant from the original. So one is planted
by weakening the SUITE, never the source: take a test assertion that currently kills a
mutant and loosen it. The mutant then survives, it is still behaviourally different from
the original, and the prover ought to prove it. Each plant is one edit to one test file,
written out below so the reader can see exactly what hole was made.

**What is measured.** Audit the pristine target, then audit each planted variant. The
mutants that survive in the variant and did not survive pristine are the hole the plant
made. Of those, the share the prover PROVES is its sensitivity:

    sensitivity = proven gaps among newly-surviving mutants / newly-surviving mutants

A survivor the prover cannot separate is not a failure of the suite, and counting it as
one is the mistake this tool exists to avoid - so the denominator is survivors, not
mutants, and the number says what share of real holes this tool can demonstrate rather
than merely suspect.

**What it does not measure.** Whether the plants resemble the gaps that matter in real
code. They are deliberately ordinary - a loosened equality, a deleted boundary check, a
tautological assertion - but they are chosen by the same person who wrote the prover, and
no planted-gap study escapes that. It establishes a floor on sensitivity, not a
distribution.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from suite_auditor.audit import audit  # noqa: E402
from suite_auditor.report import unproven_reason  # noqa: E402

# Each plant: a name, the test file to edit, the text to find, and what to replace it
# with. One edit each, so the hole it makes is unambiguous.
PLANTS: tuple[tuple[str, str, str, str], ...] = (
    (
        "discount-equality-loosened",
        "tests/test_rules.py",
        "    assert discount(total, code) == expected",
        "    assert discount(total, code) >= 0",
    ),
    (
        "discount-boundary-cases-removed",
        "tests/test_rules.py",
        '        (19, "SAVE10", 0),\n        (20, "SAVE10", 10),',
        '        (50, "SAVE10", 10),',
    ),
    (
        "shipping-second-point-removed",
        "tests/test_rules.py",
        "    assert shipping(1) == 5\n    assert shipping(5) == 11",
        "    assert shipping(1) == 5",
    ),
    (
        "shipping-made-tautological",
        "tests/test_rules.py",
        "    assert shipping(1) == 5\n    assert shipping(5) == 11",
        "    assert shipping(1) is not None\n    assert shipping(5) is not None",
    ),
    (
        "with-tax-assertion-dropped",
        "tests/test_rules.py",
        "    assert with_tax(10) > 10",
        "    with_tax(10)",
    ),
)


def survivors(report) -> dict[str, str]:
    """Mutant -> verdict, for every mutant that was not killed.

    An unproven survivor records WHICH reason it was unproven for, not just
    "unproven". The bare verdict is what this script used to keep, and it made a run
    on more-itertools read "0 of 1 proven" with nothing to act on - while the whole
    reason the toolz measurement moved from 0 of 2 to 2 of 2 was reading the reason
    ("all 64 argument sets raised on both sides") and fixing the argument pool it
    pointed at. A sensitivity number with no reason beside it says the prover failed
    without saying what to do about it.
    """
    out: dict[str, str] = {}
    for result in report.results:
        verdict = str(getattr(result.verdict, "value", result.verdict))
        if verdict == "killed":
            continue
        if verdict != "proven-gap":
            verdict = f"{verdict}:{unproven_reason(getattr(result, 'detail', ''))}"
        key = f"{result.target}|{result.kind}|{getattr(result, 'mutant', '')}"
        out[key] = verdict
    return out


def run_audit(
    target: Path,
    python: str,
    per_function: int,
    test_target: str = "",
    limit: int | None = None,
) -> object:
    """One audit. `test_target` and `limit` keep a real target's run bounded: auditing all
    of toolz is 418 mutants, and this needs one audit per plant plus a pristine one."""
    return audit(
        target,
        test_target=test_target,
        limit=limit,
        per_function=per_function,
        progress=False,
        python=python,
        timeout=300,
    )


def interpreter(target: Path) -> str:
    """The interpreter the target's tests run with.

    A real target needs its OWN environment - toolz installed, pytest present - so
    --python is the way to point at it. The repo's venv is only the right answer for the
    bundled example.
    """
    """The interpreter the target's own tests run with, as the CLI chooses it."""
    for candidate in (
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "bin" / "python",
    ):
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", type=Path, default=ROOT / "examples" / "pricing")
    ap.add_argument("--per-function", type=int, default=8)
    ap.add_argument("--json", type=Path)
    ap.add_argument(
        "--python",
        default="",
        help="the interpreter the TARGET's tests run with. A real target needs its "
        "own environment with itself and pytest installed; the default only suits "
        "the bundled example",
    )
    ap.add_argument(
        "--test-target",
        default="",
        help="pass through to the audit: which tests to run, e.g. one test file. Keeps a "
        "real target bounded, since this runs one audit per plant plus a pristine one",
    )
    ap.add_argument(
        "--limit", type=int, help="pass through to the audit: cap the functions mutated"
    )
    ap.add_argument(
        "--plants",
        type=Path,
        help="a JSON list of [name, file, find, replace] to use instead of the bundled "
        "ones, so the same measurement can be run against somebody else's suite",
    )
    args = ap.parse_args()

    plants = PLANTS
    if args.plants:
        plants = tuple(tuple(row) for row in json.loads(args.plants.read_text("utf-8")))
        print(f"plants: {len(plants)} from {args.plants}")

    if not args.target.is_dir():
        print(f"missing target: {args.target}", file=sys.stderr)
        return 1

    python = args.python or interpreter(args.target)
    started = time.time()
    print(f"target: {args.target}\npython: {python}\n")

    with tempfile.TemporaryDirectory(prefix="plant-pristine-") as tmp:
        pristine_dir = Path(tmp) / "pricing"
        shutil.copytree(args.target, pristine_dir)
        pristine = run_audit(
            pristine_dir, python, args.per_function, args.test_target, args.limit
        )
    base_survivors = survivors(pristine)
    print(
        f"pristine: {len(pristine.results)} mutants, {len(base_survivors)} survived, "
        f"{len(pristine.gaps)} proven"
    )

    rows = []
    for name, rel, find, replace in plants:
        with tempfile.TemporaryDirectory(prefix="plant-") as tmp:
            work = Path(tmp) / "pricing"
            shutil.copytree(args.target, work)
            path = work / rel
            text = path.read_text(encoding="utf-8")
            found = text.count(find)
            if found == 0:
                print(f"  SKIP {name}: the text to weaken is not in {rel}")
                continue
            if found > 1:
                # `replace(..., 1)` would take the first one, which may be in a
                # different test than the plant is named for. In cachetools'
                # test_keys.py the line `self.assertNotEqual(key(1, 2, 3), key(3, 2, 1))`
                # appears in test_hashkey and again in test_typedkey, so a plant aimed
                # at one would silently weaken the other and the row would be
                # attributed to the wrong function. A measurement that can land
                # somewhere other than where it says is worse than no measurement.
                print(
                    f"  SKIP {name}: the text to weaken appears {found} times in {rel};"
                    " a plant has to name one place, so include more context"
                )
                continue
            path.write_text(text.replace(find, replace, 1), encoding="utf-8")
            report = run_audit(
                work, python, args.per_function, args.test_target, args.limit
            )

        now = survivors(report)
        # The hole this plant made: mutants that survive now and did not before.
        new = {k: v for k, v in now.items() if k not in base_survivors}
        proven = [k for k, v in new.items() if v == "proven-gap"]
        unproven = [k for k, v in new.items() if v != "proven-gap"]
        rows.append(
            {
                "plant": name,
                "file": rel,
                "newly_surviving": len(new),
                "proven": len(proven),
                "unproven": len(unproven),
                "unproven_verdicts": sorted({new[k] for k in unproven}),
                # WHICH mutant, not just how many. A row reading "0 of 2 proven,
                # no_input" says the argument pool is at fault without saying for which
                # function, and the pool is per-parameter - so the next step is always
                # "which one", and finding out meant re-running a 25-minute audit.
                "unproven_mutants": sorted(
                    f"{k.split('|')[0]} [{k.split('|')[1]}] {new[k]}" for k in unproven
                ),
                "proven_mutants": sorted(k.split("|")[0] for k in proven),
            }
        )
        share = f"{len(proven)}/{len(new)}" if new else "no new survivors"
        # The reason an unproven survivor was not proven is the actionable half. Without
        # it a run reads "0 of 1" and there is nothing to do; with it, "no_input" points
        # straight at the argument pool, which is what moved toolz from 0 of 2 to 2 of 2.
        why = sorted({new[k].partition(":")[2] for k in unproven} - {""})
        print(
            f"  {name:<34} {share:>18} proven"
            + (f"   [{', '.join(why)}]" if why else "")
        )

    planted = sum(r["newly_surviving"] for r in rows)
    proved = sum(r["proven"] for r in rows)
    reasons: dict[str, int] = {}
    for row in rows:
        for verdict in row.get("unproven_verdicts", []):
            reason = str(verdict).partition(":")[2] or "unknown"
            reasons[reason] = reasons.get(reason, 0) + 1
    print()
    if reasons:
        print(
            "why the unproven ones were unproven: "
            + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items()))
        )
        print(
            "  no_input      the argument pool could build no call that runs on both"
            " sides\n"
            "  both_raised   they differ only where both versions raise\n"
            "  equivalent    every argument set agrees - the mutant may be a true"
            " equivalent\n"
            "  method        a method could not be called without building its class\n"
            "Only no_input and method are this tool's to fix. equivalent may mean the"
            " plant\ndid not actually change behaviour, which is a fact about the plant.\n"
        )
    if not planted:
        print("No plant created a surviving mutant - the plants no longer bite.")
        return 1
    print(
        f"SENSITIVITY: {proved} of {planted} newly-surviving mutants were proven "
        f"= {proved / planted:.1%}"
    )
    print(
        "A survivor the prover cannot separate is not evidence against the suite, so the\n"
        "denominator is survivors rather than mutants. This is a floor on sensitivity: the\n"
        "plants are ordinary but they were chosen by the same person who wrote the prover."
    )
    if proved == planted:
        # A rate of exactly 1.000 says the plants did not discriminate, which is a
        # fact about this target rather than a result about the prover. On a 27-line
        # module with four functions there is nowhere for a hard case to hide.
        print(
            f"\n! {proved}/{planted} is exactly 100%, which means no plant here was"
            " hard enough to separate\n  a good prover from a lucky one. Run it"
            " against a real target - --target path/to/clone -\n  before quoting"
            " this as sensitivity."
        )

    payload = {
        # Not relative_to(ROOT): a real target lives outside this repository, which is
        # the whole point of running it against one.
        "target": str(args.target),
        "per_function": args.per_function,
        "seconds": round(time.time() - started, 1),
        "pristine": {
            "mutants": len(pristine.results),
            "survivors": len(base_survivors),
            "proven_gaps": len(pristine.gaps),
        },
        "plants": rows,
        "newly_surviving_total": planted,
        "proven_total": proved,
        "sensitivity": round(proved / planted, 4),
    }
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nwritten to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
