"""Command line entry point.

    suite-auditor audit <repo> [--test tests/] [--limit 40]
    suite-auditor coverage <repo>

`coverage` is free and instant - one suite run, no mutation - and answers the cheapest
question in the report on its own: which functions does no test reach?
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from suite_auditor.audit import audit as run_audit
from suite_auditor.coverage import build_map, tests_for
from suite_auditor.mutate import find_targets
from suite_auditor.report import summary, write_json, write_markdown


def cmd_coverage(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    cov, health = build_map(repo, args.test)
    if not cov:
        print("no coverage could be traced - is the suite runnable from this directory?")
        if health.caveat():
            print(f"  {health.caveat()}")
        return 1

    targets = find_targets(repo)
    covered, uncovered = [], []
    for t in targets:
        (covered if tests_for(cov, t.path, t.name) else uncovered).append(t)

    print(f"{repo.name}: {len(targets)} functions")
    print(f"  reached by at least one test : {len(covered)}")
    print(f"  reached by none              : {len(uncovered)}")

    # An unreached list is only as good as the run that produced it. A test
    # that errors executes nothing, so everything it would have covered reads
    # as uncovered - and the failure points the wrong way, making a healthy
    # suite look full of gaps.
    if not health.clean:
        print()
        print(f"  ! {health.caveat()} while tracing.")
        print("    Functions those tests would have reached are counted as unreached,")
        print("    so the number above is an UPPER BOUND, not a measurement.")
        print("    Fix the target's suite first, then re-run.")
    elif health.passed:
        print(f"  (traced across {health.passed} passing tests)")

    if uncovered:
        print("\n  no test reaches these:")
        for t in uncovered[: args.show]:
            print(f"    {t.key}")
        if len(uncovered) > args.show:
            print(f"    ... and {len(uncovered) - args.show} more")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        print(f"not a directory: {repo}")
        return 1

    result = run_audit(
        repo,
        test_target=args.test,
        limit=args.limit,
        per_function=args.per_function,
        timeout=args.timeout,
        progress=not args.quiet,
    )

    print()
    print(summary(result, repo.name))

    if args.out:
        out = Path(args.out).resolve()
        out.mkdir(parents=True, exist_ok=True)
        write_json(result, out / "audit.json")
        write_markdown(result, out / "AUDIT.md", repo.name)
        print(f"\n  wrote {out / 'AUDIT.md'} and {out / 'audit.json'}")

    return 1 if (args.fail_on_gap and result.gaps) else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="suite-auditor", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("coverage", help="which functions no test reaches; one suite run")
    c.add_argument("repo")
    c.add_argument("--test", default="", help="restrict to a test directory or file")
    c.add_argument("--show", type=int, default=25)
    c.set_defaults(fn=cmd_coverage)

    a = sub.add_parser("audit", help="mutate, run the covering tests, prove the survivors")
    a.add_argument("repo")
    a.add_argument("--test", default="", help="restrict to a test directory or file")
    a.add_argument("--limit", type=int, help="stop after N functions")
    a.add_argument("--per-function", type=int, default=6, help="mutants per function")
    a.add_argument("--timeout", type=float, default=300.0)
    a.add_argument("--out", help="directory for AUDIT.md and audit.json")
    a.add_argument("--quiet", action="store_true")
    a.add_argument("--fail-on-gap", action="store_true", help="exit 1 if any gap is proven")
    a.set_defaults(fn=cmd_audit)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
