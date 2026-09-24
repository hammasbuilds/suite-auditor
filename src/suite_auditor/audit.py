"""Mutate, run only the tests that cover it, and prove every survivor.

Three stages, and the third is what separates this from a mutation-score tool:

1. **Kill or survive.** Patch one mutant in, run the tests that cover that function, put
   the file back. A failure means the suite noticed.
2. **Prove.** A survivor is suspicious, not damning. Some mutants are equivalent to the
   original and no test could ever distinguish them. So the original and the mutant are
   called side by side on generated inputs, and a survivor is only a **gap** once an input
   is found on which they disagree.
3. **Report the rest as unproven.** Never folded into the headline. A number that quietly
   counts equivalent mutants as gaps is inflated in a way nobody can check.

The file is restored in a `finally`, always. A crash that leaves somebody's library mutated
on disk is a far worse outcome than a missing verdict.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from suite_auditor.coverage import build_map, tests_for
from suite_auditor.differential import compare
from suite_auditor.inputs import argument_sets, harvest_for, witness_strength
from suite_auditor.mutate import find_targets, mutants
from suite_auditor.types import Audit, Result, Target, Verdict

_PASSED = re.compile(r"\b(\d+) (passed|failed|error)")


def run_tests(repo: Path, test_ids: list[str], timeout: float = 300.0) -> bool | None:
    """True if everything passed. None if the run could not be scored at all."""
    if not test_ids:
        return None
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
        "--tb=no",
        "-x",
        *test_ids,
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=repo, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    out = (proc.stdout or "") + (proc.stderr or "")
    # Exit code 5 is "no tests collected" - not a pass, and not a kill either.
    if proc.returncode == 5 or not _PASSED.search(out):
        return None
    return proc.returncode == 0


def patch_file(repo: Path, target: Target, mutant: str) -> str:
    """Write a mutant in. Returns the original contents for restoration."""
    path = repo / target.path
    before = path.read_text(encoding="utf-8")
    lines = before.splitlines()
    original = "\n".join(lines[target.lineno - 1 : target.end_lineno])
    indent = ""
    for line in original.splitlines():
        if line.strip():
            indent = line[: len(line) - len(line.lstrip())]
            break

    body = textwrap.dedent(mutant)
    if indent:
        body = "\n".join(indent + ln if ln.strip() else ln for ln in body.splitlines())
    lines[target.lineno - 1 : target.end_lineno] = body.splitlines()
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
    return before


def audit_target(
    repo: Path,
    target: Target,
    per_function: int = 6,
    timeout: float = 300.0,
) -> list[Result]:
    """Every mutant of one function, with a verdict each."""
    results: list[Result] = []
    argsets: list[str] = []
    # Values from the tests that cover THIS function, not from the whole repository. A
    # repo-wide pool hands a function expecting signature objects the argument `(0, 0)`,
    # and the resulting "proof" is one any maintainer would close on sight.
    pool = harvest_for(repo, target.covering_tests)
    try:
        tree = ast.parse(target.source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                argsets = argument_sets(node, pool)
                break
    except SyntaxError:
        return results

    for mutant, kind in mutants(target.source, cap=per_function):
        before = None
        try:
            before = patch_file(repo, target, mutant)
            passed = run_tests(repo, target.covering_tests, timeout)
        except OSError as exc:
            results.append(Result(target.key, mutant, kind, Verdict.SKIPPED, detail=str(exc)))
            continue
        finally:
            # Always, even on an exception. Leaving somebody's library mutated on disk is
            # far worse than a missing verdict.
            if before is not None:
                (repo / target.path).write_text(before, encoding="utf-8", newline="")

        if passed is None:
            results.append(
                Result(
                    target.key,
                    mutant,
                    kind,
                    Verdict.SKIPPED,
                    detail="the covering tests could not be scored",
                )
            )
            continue
        if not passed:
            results.append(Result(target.key, mutant, kind, Verdict.KILLED))
            continue

        # Survived. Now find out whether it is actually a different program.
        diff = compare(
            target.header,
            target.source,
            mutant,
            target.name.rpartition(".")[2],
            argsets,
            30.0,
            target.sys_path,
            target.package,
        )
        if diff["status"] == "differs":
            w = diff["witness"]
            results.append(
                Result(
                    target.key,
                    mutant,
                    kind,
                    Verdict.PROVEN_GAP,
                    witness=w,
                    detail=diff["detail"],
                    strength=witness_strength(w),
                )
            )
        else:
            results.append(
                Result(
                    target.key,
                    mutant,
                    kind,
                    Verdict.UNPROVEN,
                    detail=f"survived; {diff['status']}: {diff['detail']}",
                )
            )
    return results


def audit(
    repo: Path,
    test_target: str = "",
    limit: int | None = None,
    per_function: int = 6,
    timeout: float = 300.0,
    progress: bool = True,
) -> Audit:
    t0 = time.time()
    out = Audit()

    if progress:
        print("tracing which tests cover which functions (one suite run)...")
    cov, health = build_map(repo, test_target)
    if progress:
        print(f"  {len(cov)} functions traced")
        # The mutation audit depends on this map twice over: to decide which
        # functions are worth mutating, and to pick which tests to re-run per
        # mutant. A partial trace therefore understates coverage AND runs too
        # few tests against each mutant, which inflates the survivor count in
        # the same direction. Worth saying out loud before any number is shown.
        if not health.clean:
            print(f"  ! {health.caveat()} - survivors below will be overstated")

    targets = find_targets(repo)
    for t in targets:
        t.covering_tests = tests_for(cov, t.path, t.name)

    covered = [t for t in targets if t.covering_tests]
    out.uncovered = [t.key for t in targets if not t.covering_tests]
    if limit:
        covered = covered[:limit]

    if progress:
        print(f"  {len(covered)} functions have covering tests, {len(out.uncovered)} have none")
        print(f"\nmutating {len(covered)} function(s)...")

    for i, t in enumerate(covered, 1):
        results = audit_target(repo, t, per_function, timeout)
        out.results.extend(results)
        if progress and results:
            gaps = sum(r.verdict is Verdict.PROVEN_GAP for r in results)
            killed = sum(r.verdict is Verdict.KILLED for r in results)
            print(
                f"  [{i}/{len(covered)}] {t.key:48} "
                f"{killed} killed, {gaps} proven gap(s), "
                f"{len(t.covering_tests)} covering test(s)"
            )

    out.seconds = time.time() - t0
    return out
