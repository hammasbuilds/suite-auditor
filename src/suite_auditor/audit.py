"""Mutate a scratch copy, run only the tests that cover it, and prove every survivor.

Three stages, and the third is what separates this from a mutation-score tool:

1. **Kill or survive.** Patch one mutant in, run the tests that cover that function, put
   the file back. A failure means the suite noticed.
2. **Prove.** A survivor is suspicious, not damning. Some mutants are equivalent to the
   original and no test could ever distinguish them. So the original and the mutant are
   called side by side on the arguments the covering tests really used, and a survivor is
   only a **gap** once an input is found on which they disagree.
3. **Report the rest as unproven.** Never folded into the headline. A number that quietly
   counts equivalent mutants as gaps is inflated in a way nobody can check.

**Every mutant is written into a scratch copy of the repository, never into the user's
files.** Patching the real tree - even with a restore in a `finally` - meant a crash, a
Ctrl-C or a concurrent reader could see mutated code, and the restore rewrote CRLF files as
LF. A copy costs a few seconds and makes both impossible.
"""

from __future__ import annotations

import ast
import re
import shutil
import sys
import textwrap
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue

from suite_auditor.coverage import build_map, calls_for, tests_for
from suite_auditor.differential import compare
from suite_auditor.inputs import argument_sets, harvest_for, witness_strength
from suite_auditor.mutate import find_targets, mutants
from suite_auditor.types import Audit, Result, Target, Verdict
from suite_auditor.workspace import Scratch, Timeout, child_env, kill_all, package_roots, run

_PASSED = re.compile(r"\b(\d+) (passed|failed|error)")

# Windows caps a command line at 32,767 characters. A function covered by hundreds of
# parametrised tests blows through that; past this length the covering FILES are run.
_MAX_ARGV = 24_000


def _test_args(test_ids: list[str]) -> list[str]:
    if sum(len(t) + 3 for t in test_ids) <= _MAX_ARGV:
        return list(test_ids)
    return sorted({t.split("::")[0] for t in test_ids})


def run_tests(
    repo: Path,
    test_ids: list[str],
    timeout: float = 300.0,
    python: str = "",
    basetemp: Path | None = None,
    extra_paths: list[str] | None = None,
) -> bool | None:
    """True if everything passed, False if something failed. None if unscorable.

    Raises `Timeout` when the run does not finish in time - which, for a mutant, usually
    means it made a loop infinite, and the caller counts that as noticed.
    """
    if not test_ids:
        return None
    cmd = [
        python or sys.executable,
        "-B",
        "-m",
        "pytest",
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
        "--tb=no",
        "-x",
    ]
    if basetemp is not None:
        cmd.append(f"--basetemp={basetemp}")
    cmd += _test_args(test_ids)
    try:
        code, out = run(cmd, repo, child_env(extra_paths), timeout)
    except OSError:
        return None
    # Exit code 5 is "no tests collected" - not a pass, and not a kill either.
    if code == 5 or not _PASSED.search(out):
        return None
    return code == 0


def _newline_of(line: str, default: str) -> str:
    for nl in ("\r\n", "\n", "\r"):
        if line.endswith(nl):
            return nl
    return default


def patch_file(repo: Path, target: Target, mutant: str) -> bytes:
    """Write a mutant in, changing nothing else about the file. Returns the original bytes.

    Bytes in, bytes out: line endings, a BOM and a missing final newline are all kept.
    The previous version read with universal newlines and wrote with `newline=""`, so a
    CRLF file came back LF - same text, different file, every time the tool ran.
    """
    path = repo / target.path
    before = path.read_bytes()
    text = before.decode("utf-8")
    lines = text.splitlines(keepends=True)
    first = lines[target.lineno - 1] if target.lineno - 1 < len(lines) else ""
    dominant = "\r\n" if "\r\n" in text else "\n"
    nl = _newline_of(first, dominant)

    original = lines[target.lineno - 1 : target.end_lineno]
    indent = ""
    for line in original:
        if line.strip():
            stripped = line.lstrip("﻿")
            indent = stripped[: len(stripped) - len(stripped.lstrip())]
            break

    body = textwrap.dedent(mutant).splitlines()
    if indent:
        body = [indent + ln if ln.strip() else ln for ln in body]
    last = original[-1] if original else ""
    tail_nl = _newline_of(last, "") if last else nl
    new_lines = [ln + nl for ln in body]
    if new_lines:
        new_lines[-1] = body[-1] + tail_nl
    lines[target.lineno - 1 : target.end_lineno] = new_lines
    path.write_bytes("".join(lines).encode("utf-8"))
    return before


@dataclass
class _Progress:
    total: int
    enabled: bool
    done: int = 0
    t0: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)
    tty: bool = field(default_factory=lambda: sys.stdout.isatty())

    def eta(self) -> str:
        if not self.done:
            return "ETA --"
        rate = (time.time() - self.t0) / self.done
        left = rate * (self.total - self.done)
        return f"ETA {_fmt(left)}"

    def tick(self, label: str) -> None:
        with self.lock:
            self.done += 1
            if self.enabled and self.tty:
                line = f"  mutant {self.done}/{self.total}  {self.eta()}  {label}"
                sys.stdout.write("\r" + line[:110].ljust(110))
                sys.stdout.flush()

    def say(self, text: str) -> None:
        if not self.enabled:
            return
        with self.lock:
            if self.tty:
                sys.stdout.write("\r" + " " * 110 + "\r")
            print(text, flush=True)


def _fmt(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def _argsets_for(repo: Path, target: Target, observed: list[dict]):
    # Values from the tests that cover THIS function, not from the whole repository.
    pool = harvest_for(repo, target.covering_tests)
    try:
        tree = ast.parse(textwrap.dedent(target.source))
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            return argument_sets(node, pool, observed=observed)
    return None


def audit_target(
    repo: Path,
    target: Target,
    per_function: int = 6,
    timeout: float = 300.0,
    python: str = "",
    basetemp: Path | None = None,
    extra_paths: list[str] | None = None,
    observed: list[dict] | None = None,
    todo: list[tuple[str, str]] | None = None,
    progress: _Progress | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[Result]:
    """Every mutant of one function, with a verdict each. `repo` must be a scratch copy."""
    results: list[Result] = []
    argsets = _argsets_for(repo, target, observed or [])
    if argsets is None:
        return results
    if todo is None:
        todo = mutants(textwrap.dedent(target.source), cap=per_function)

    def tick() -> None:
        if progress:
            progress.tick(target.key)

    # The covering tests must pass on the UNMUTATED code, run exactly the way each
    # mutant's will be. A test that fails alone - order-dependent, or relying on state
    # another test leaves behind - would otherwise "kill" every mutant it meets, and the
    # kill rate would credit the suite for a broken test.
    t_base = time.time()
    try:
        baseline = run_tests(repo, target.covering_tests, timeout, python, basetemp, extra_paths)
    except Timeout:
        baseline = None
    if baseline is not True:
        why = (
            "the covering tests fail on the unmutated code when run on their own"
            if baseline is False
            else "the covering tests could not be run on the unmutated code"
        )
        for mutant, kind in todo:
            results.append(Result(target.key, mutant, kind, Verdict.SKIPPED, detail=why))
            tick()
        return results
    # A mutant gets ten times the clean run, and never less than 30 s: enough for noise,
    # short enough that a mutant which made a loop infinite does not eat the budget.
    per_mutant = min(timeout, max(30.0, 10 * (time.time() - t_base)))

    for mutant, kind in todo:
        if should_stop is not None and should_stop():
            break
        before = None
        timed_out = False
        try:
            before = patch_file(repo, target, mutant)
            passed = run_tests(
                repo, target.covering_tests, per_mutant, python, basetemp, extra_paths
            )
        except Timeout:
            passed, timed_out = False, True
        except (OSError, UnicodeDecodeError) as exc:
            results.append(Result(target.key, mutant, kind, Verdict.SKIPPED, detail=str(exc)))
            tick()
            continue
        finally:
            if before is not None:
                (repo / target.path).write_bytes(before)

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
        elif not passed:
            detail = (
                f"the covering tests did not finish within {per_mutant:.0f}s" if timed_out else ""
            )
            results.append(Result(target.key, mutant, kind, Verdict.KILLED, detail=detail))
        elif "." in target.name:
            # A method needs its instance, and calling it with `self` missing or faked
            # proves nothing either way - so no proof is attempted, and the survivor
            # stays unproven rather than becoming a gap on a nonsense call.
            results.append(
                Result(
                    target.key,
                    mutant,
                    kind,
                    Verdict.UNPROVEN,
                    detail="survived; a method cannot be called without its instance, "
                    "so no separating input was sought",
                )
            )
        else:
            results.append(_prove(target, mutant, kind, argsets, python))
        tick()
    return results


def _prove(target: Target, mutant: str, kind: str, argsets, python: str) -> Result:
    """The survivor's trial: is it a different program on an input that counts?"""
    diff = compare(
        target.header,
        textwrap.dedent(target.source),
        textwrap.dedent(mutant),
        target.name.rpartition(".")[2],
        argsets,
        60.0,
        target.sys_path,
        target.package,
        python,
    )
    if diff["status"] == "differs":
        w = diff["witness"]
        return Result(
            target.key,
            mutant,
            kind,
            Verdict.PROVEN_GAP,
            witness=w,
            detail=diff["detail"],
            strength=witness_strength(w),
            provenance=diff.get("provenance", ""),
        )
    return Result(
        target.key,
        mutant,
        kind,
        Verdict.UNPROVEN,
        detail=f"survived; {diff['status']}: {diff['detail']}",
    )


def audit(
    repo: Path,
    test_target: str = "",
    limit: int | None = None,
    per_function: int = 6,
    timeout: float = 300.0,
    progress: bool = True,
    max_seconds: float = 0.0,
    python: str = "",
    jobs: int = 1,
) -> Audit:
    t0 = time.time()
    repo = repo.resolve()
    out = Audit()

    def say(text: str) -> None:
        if progress:
            print(text, flush=True)

    say("copying the repository to a scratch directory (your files are never modified)...")
    with Scratch(repo, copies=1) as scratch:
        work = scratch.copies[0]
        roots = package_roots(work, [t.path for t in find_targets(work)])

        say("tracing which tests cover which functions (one suite run)...")
        trace = build_map(work, test_target, python=python, original=repo, extra_paths=roots)
        out.health = trace.health
        say(f"  {len(trace.cov)} functions traced, {trace.health.passed} tests passed")
        if trace.stray:
            out.stray = trace.stray
            say(
                f"  ! the suite executed {len(trace.stray)} file(s) from {repo} itself rather "
                "than from the scratch copy - an install of this project is shadowing it. "
                "Mutants in those files would be invisible to the tests."
            )
        # The mutation audit depends on this map twice over: to decide which functions
        # are worth mutating, and to pick which tests to re-run per mutant.
        if trace.failed:
            say(
                f"  ! {len(trace.failed)} test(s) already fail on the unmodified code; they "
                "are left out, because a failing test would 'kill' every mutant it meets"
            )
        elif not trace.health.clean:
            say(f"  ! {trace.health.caveat()}")

        targets = find_targets(work)
        if not targets:
            # Loud, because the alternative is silence that reads as a clean bill of
            # health. An audit with no targets reports 0 mutants and a kill rate of None.
            out.no_targets = True
            say(
                "  ! no functions to mutate. Shipped code is looked for in packages "
                "(a directory with __init__.py) and in single modules directly under "
                "src/. If the library is somewhere else, nothing below is about it."
            )
            out.seconds = time.time() - t0
            return out

        for t in targets:
            t.covering_tests = tests_for(trace.cov, t.path, t.name)

        covered = [t for t in targets if t.covering_tests]
        for t in targets:
            if t.covering_tests:
                continue
            if tests_for(trace.failing_only, t.path, t.name):
                out.failing_only.append(t.key)
            else:
                out.uncovered.append(t.key)
        # Before --limit, so the denominator describes the package rather than the run.
        out.covered_total = len(covered)
        if limit:
            covered = covered[:limit]
        if not covered:
            out.seconds = time.time() - t0
            say("  ! no function is reached by a passing test, so there is nothing to mutate")
            return out

        # Dedented: a method's source starts indented, which does not parse on its own,
        # and every method used to yield zero mutants without a word.
        plan = [(t, mutants(textwrap.dedent(t.source), cap=per_function)) for t in covered]
        # A function with no mutable site (a bare `return self.x`) has nothing to score,
        # and listing it as "0/0 killed" is noise.
        barren = sum(1 for _, m in plan if not m)
        plan = [(t, m) for t, m in plan if m]
        total = sum(len(m) for _, m in plan)
        jobs = max(1, min(jobs, len(plan) or 1))
        say(f"  {len(covered)} functions have covering tests, {len(out.uncovered)} have none")
        if out.failing_only:
            say(f"  {len(out.failing_only)} function(s) are reached only by failing tests")
        if barren:
            say(f"  {barren} covered function(s) have nothing to mutate")
        if not plan:
            out.seconds = time.time() - t0
            return out
        say(
            f"\nmutating {len(covered)} function(s): {total} mutants"
            + (f", {jobs} in parallel" if jobs > 1 else "")
            + "..."
        )

        # One scratch copy per worker: a mutant is a file on disk, so two workers
        # cannot share a tree.
        for i in range(1, jobs):
            dest = scratch.root / f"w{i}" / work.name
            shutil.copytree(work, dest, symlinks=True)
            scratch.copies.append(dest)

        prog = _Progress(total=total, enabled=progress)
        stop = threading.Event()

        def should_stop() -> bool:
            # A wall-clock budget, because `timeout` only bounds ONE mutant's test run
            # and `--limit` only bounds the function count. Stopping early is safe:
            # every figure is a rate over the mutants actually scored.
            if max_seconds and time.time() - t0 > max_seconds:
                out.stopped_early = True
                stop.set()
            return stop.is_set()

        free: Queue[int] = Queue()
        for i in range(jobs):
            free.put(i)
        per_fn: dict[int, list[Result]] = {}
        finished = [0]

        def work_on(index: int, t: Target, todo: list[tuple[str, str]]) -> None:
            if should_stop():
                return
            slot = free.get()
            try:
                if should_stop():
                    return
                copy = scratch.copies[slot]
                # Same relative layout in every copy; re-root the target and its paths.
                local = _rerooted(t, work, copy)
                results = audit_target(
                    copy,
                    local,
                    per_function,
                    timeout,
                    python,
                    scratch.basetemp(slot),
                    [r.replace(str(work), str(copy), 1) for r in roots],
                    calls_for(trace.calls, t.path, t.name),
                    todo,
                    prog,
                    should_stop,
                )
            finally:
                free.put(slot)
            per_fn[index] = results
            with prog.lock:
                finished[0] += 1
                n = finished[0]
            gaps = sum(r.verdict is Verdict.PROVEN_GAP for r in results)
            killed = sum(r.verdict is Verdict.KILLED for r in results)
            skipped = sum(r.verdict is Verdict.SKIPPED for r in results)
            extra = f", {skipped} skipped" if skipped else ""
            prog.say(
                f"  [{n}/{len(plan)}] {t.key:44} {killed}/{len(results)} killed, "
                f"{gaps} gap(s){extra}   [{prog.done}/{total} mutants, {prog.eta()}]"
            )

        pool = ThreadPoolExecutor(max_workers=jobs)
        try:
            futures = [pool.submit(work_on, i, t, m) for i, (t, m) in enumerate(plan)]
            pending = set(futures)
            # Polled, not a blocking wait: a blocking wait is not interruptible by
            # Ctrl-C on every platform.
            while pending:
                _, pending = wait(pending, timeout=0.5, return_when=FIRST_EXCEPTION)
                for f in futures:
                    if f.done() and f.exception() is not None:
                        raise f.exception()
        except BaseException:
            stop.set()
            kill_all()
            pool.shutdown(wait=True, cancel_futures=True)
            raise
        pool.shutdown(wait=True)

        for i in sorted(per_fn):
            out.results.extend(per_fn[i])
        out.audited_functions = len(per_fn)
        if out.stopped_early:
            say(
                f"  ! stopped after {time.time() - t0:.0f}s of a {max_seconds:.0f}s budget, "
                f"having audited {len(per_fn)} of {len(plan)} functions. "
                "Rates below are over what was scored."
            )

    out.seconds = time.time() - t0
    return out


def _rerooted(t: Target, old_root: Path, new_root: Path) -> Target:
    if old_root == new_root:
        return t
    sys_path = t.sys_path
    if sys_path.startswith(str(old_root)):
        sys_path = str(new_root) + sys_path[len(str(old_root)) :]
    return Target(
        path=t.path,
        name=t.name,
        source=t.source,
        header=t.header,
        sys_path=sys_path,
        package=t.package,
        lineno=t.lineno,
        end_lineno=t.end_lineno,
        covering_tests=t.covering_tests,
    )
