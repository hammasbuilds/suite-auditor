"""Command line entry point for suite-auditor."""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from pathlib import Path

from suite_auditor import __version__

DESCRIPTION = """\
Find what a pytest suite would not notice, with the input that proves it.

  coverage   one suite run: which functions does no test reach?
  audit      mutate each covered function, run only its covering tests, and try
             to prove every surviving mutant wrong on inputs the tests really use

Your files are never modified: `audit` works on a scratch copy of the repository.
"""

EPILOG = """\
examples:
  suite-auditor coverage .
  suite-auditor audit . --out audit-out
  suite-auditor audit path/to/repo --python path/to/repo/.venv/bin/python -j 4

exit status:
  0  finished (and, with --fail-on-gap, no proven gap)
  1  --fail-on-gap and at least one gap was proven
  2  could not run: bad path, no pytest, or nothing could be traced or scored
"""

EXIT_OK, EXIT_GAPS, EXIT_ERROR = 0, 1, 2


def _positive_int(text: str) -> int:
    n = int(text)
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {n}")
    return n


def _jobs(text: str) -> int:
    if text == "auto":
        return max(1, min(4, (os.cpu_count() or 2) // 2))
    return _positive_int(text)


def _err(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr, flush=True)
    return EXIT_ERROR


def _prepare(args: argparse.Namespace) -> tuple[Path, str, str] | int:
    """Validate the repo and test paths and find a working interpreter."""
    from suite_auditor.workspace import check_pytest, resolve_python

    repo = Path(args.repo).expanduser()
    if not repo.exists():
        return _err(f"no such directory: {repo}")
    if not repo.is_dir():
        return _err(f"not a directory: {repo}")
    repo = repo.resolve()

    test = args.test
    if test:
        tp = Path(test).expanduser()
        # Relative to the repo first (`--test tests`), then to where the user is standing
        # (`--test ../proj/tests`, as a shell would complete it).
        candidates = [tp] if tp.is_absolute() else [repo / tp, Path.cwd() / tp]
        full = next((c for c in candidates if c.exists()), candidates[0])
        if not full.exists():
            return _err(f"--test {test}: no such file or directory under {repo}")
        try:
            test = full.resolve().relative_to(repo).as_posix()
        except ValueError:
            return _err(f"--test {test}: must be inside {repo}")

    try:
        interp = resolve_python(repo, args.python)
    except SystemExit as exc:
        print(exc, file=sys.stderr, flush=True)
        return EXIT_ERROR
    problem = check_pytest(interp.path)
    if problem:
        return _err(problem)
    if not args.quiet:
        print(f"python: {interp.path}  ({interp.why})", flush=True)
    return repo, test, interp.path


def cmd_coverage(args: argparse.Namespace) -> int:
    from suite_auditor.coverage import build_map, tests_for
    from suite_auditor.mutate import find_targets
    from suite_auditor.workspace import package_roots

    prepared = _prepare(args)
    if isinstance(prepared, int):
        return prepared
    repo, test, python = prepared

    targets = find_targets(repo)
    if not targets:
        return _err(
            f"no functions found in {repo}. Shipped code is looked for in packages "
            "(a directory with __init__.py) and in single modules directly under src/."
        )
    roots = package_roots(repo, [t.path for t in targets])
    trace = build_map(repo, test, python=python, extra_paths=roots)
    cov, health = trace.cov, trace.health
    if not cov:
        if not health.ran:
            print("the target's test suite did not run (or did not finish).", flush=True)
        elif health.passed:
            print(
                f"{health.passed} test(s) passed, but none of them executed any function in "
                f"{repo}.\n  Is the package installed non-editable, so the tests import a "
                "copy from site-packages? Reinstall it with `pip install -e .`.",
                flush=True,
            )
        else:
            print("no passing test executed any function in this repository.", flush=True)
        if health.caveat():
            print(f"  {health.caveat()}", flush=True)
        if health.output_tail and not health.clean:
            print("  last lines of the suite's output:", flush=True)
            for ln in health.output_tail.splitlines():
                print(f"    {ln}", flush=True)
        return EXIT_ERROR

    covered, broken, uncovered = [], [], []
    for t in targets:
        if tests_for(cov, t.path, t.name):
            covered.append(t)
        elif tests_for(trace.failing_only, t.path, t.name):
            broken.append(t)
        else:
            uncovered.append(t)

    print(f"{repo.name}: {len(targets)} functions")
    print(f"  reached by a passing test    : {len(covered)}")
    if broken:
        print(f"  reached only by FAILING tests: {len(broken)}")
    print(f"  reached by no test           : {len(uncovered)}")

    # A test that errors during collection executes nothing at all, so what it would have
    # reached reads as unreached - an upper bound, not a measurement.
    if not health.clean:
        print()
        print(f"  ! {health.caveat()} while tracing.")
        print("    A test that errors before reaching the code counts nothing, so the")
        print("    unreached number is an UPPER BOUND. Fix the target's suite, then re-run.")
    elif health.passed:
        print(f"  (traced across {health.passed} passing tests)")

    if broken:
        print("\n  only failing tests reach these (fix the tests first):")
        for t in broken[: args.show]:
            print(f"    {t.key}")
        if len(broken) > args.show:
            print(f"    ... and {len(broken) - args.show} more (--show N)")
    if uncovered:
        print("\n  no test reaches these:")
        for t in uncovered[: args.show]:
            print(f"    {t.key}")
        if len(uncovered) > args.show:
            print(f"    ... and {len(uncovered) - args.show} more (--show N)")
    return EXIT_OK


def cmd_audit(args: argparse.Namespace) -> int:
    from suite_auditor.audit import audit as run_audit
    from suite_auditor.report import summary, write_json, write_markdown

    prepared = _prepare(args)
    if isinstance(prepared, int):
        return prepared
    repo, test, python = prepared

    try:
        result = run_audit(
            repo,
            test_target=test,
            limit=args.limit,
            per_function=args.per_function,
            timeout=args.timeout,
            progress=not args.quiet,
            max_seconds=args.max_seconds,
            python=python,
            jobs=args.jobs,
        )
    except KeyboardInterrupt:
        print(
            "\ninterrupted. Nothing in your repository was modified - the audit ran on a "
            "scratch copy, which has been removed.",
            file=sys.stderr,
            flush=True,
        )
        return 130

    print(flush=True)
    print(summary(result, repo.name), flush=True)

    if args.out:
        out = Path(args.out).resolve()
        out.mkdir(parents=True, exist_ok=True)
        write_json(result, out / "audit.json")
        write_markdown(result, out / "AUDIT.md", repo.name)
        print(f"\n  wrote {out / 'AUDIT.md'} and {out / 'audit.json'}", flush=True)

    # Nothing scored is a failure to audit, not a clean result - CI must not read it as
    # a pass.
    if result.no_targets or not result.scored:
        return EXIT_ERROR
    return EXIT_GAPS if (args.fail_on_gap and result.gaps) else EXIT_OK


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("repo", help="path to the project (the directory pytest runs from)")
    p.add_argument("--test", default="", help="restrict to a test directory or file")
    p.add_argument(
        "--python",
        default="",
        help="interpreter (or venv directory) that runs the target's tests. Default: the "
        "project's .venv/venv/env if it has one, else an activated venv, else this one",
    )
    p.add_argument("-q", "--quiet", action="store_true", help="no progress output")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="suite-auditor",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="{coverage,audit}")

    c = sub.add_parser(
        "coverage",
        help="which functions no test reaches; one suite run",
        description="Run the suite once and list every function no passing test executes.",
    )
    _common(c)
    c.add_argument("--show", type=_positive_int, default=25, help="how many to list (25)")
    c.set_defaults(fn=cmd_coverage)

    a = sub.add_parser(
        "audit",
        help="mutate, run the covering tests, prove the survivors",
        description="Mutation-test the suite on a scratch copy and prove each survivor.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _common(a)
    a.add_argument("--limit", type=_positive_int, help="stop after N functions")
    a.add_argument("--per-function", type=_positive_int, default=6, help="mutants per function (6)")
    a.add_argument(
        "-j",
        "--jobs",
        type=_jobs,
        default="auto",
        help="functions audited in parallel, each worker in its own scratch copy with its "
        "own pytest basetemp. 'auto' (the default) is half the CPUs, at most 4. Use -j 1 "
        "if the target's tests share state outside the project (a fixed port, a file in "
        "the home directory)",
    )
    a.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="upper bound in seconds on one test run (300). Each mutant also gets at most "
        "10x its clean run, min 30s; a mutant that exceeds it counts as caught",
    )
    a.add_argument(
        "--max-seconds",
        type=float,
        default=0.0,
        help="stop after this many seconds and report the partial sample (0 = no budget)",
    )
    a.add_argument("--out", help="directory for AUDIT.md and audit.json")
    a.add_argument(
        "--fail-on-gap", action="store_true", help="exit 1 if any gap is proven (for CI)"
    )
    a.set_defaults(fn=cmd_audit)
    return ap


def main(argv: list[str] | None = None) -> int:
    # Line-buffered, so progress shows as it happens when output is piped or logged.
    for stream in (sys.stdout, sys.stderr):
        # And never crash on a character the console cannot show: witnesses are reprs
        # of the target's own data, which can be anything.
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(line_buffering=True, errors="backslashreplace")  # type: ignore[union-attr]
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
