"""Audit many externally-chosen projects, instead of three picked by hand.

    python scripts/target_census.py --packages 60 [--out census.json]

docs/RESULTS.md reports three projects: toolz, tomli and repo-surgeon. Three
objections follow, and none is answered by auditing a fourth hand-picked project:

  * **n = 3.** Three projects cannot carry a claim about test suites in general.
  * **One of the three is ours.** repo-surgeon is our own package, and it is also the
    weakest column (60.4% kill rate, 42% of functions reached by no test). A reader
    cannot tell whether it is there because it is representative or because it made
    the point.
  * **They were chosen by hand**, with no record of whether the choice came before or
    after the numbers were known.

This script removes all three the same way the blast-radius census does:

  * The candidate list is **external** - the top packages by download count, from
    hugovk/top-pypi-packages. Position in it cannot depend on this measurement.
  * A candidate is included by a **stated, outcome-blind rule**: its sdist on PyPI must
    contain a test directory that pytest recognises, and its clean suite must pass.
    Both are checked before a single mutant is generated, so the audit's result cannot
    influence whether the project is in the sample.
  * Every exclusion is **recorded with its reason and counted**. A rate is quoted over
    the projects that produced an audit, and the number that did not is printed beside
    it - the same discipline the tool itself applies to unproven survivors.

Each project gets a wall-clock budget. A project that exceeds it is reported as
`budget exceeded`, which is an outcome, not a silent drop: mutation testing reruns a
suite once per mutant, and a slow suite would otherwise quietly select the sample
towards fast ones.
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

TOP = ("https://raw.githubusercontent.com/hugovk/top-pypi-packages/main/"
       "top-pypi-packages.min.json")
# Matches a test FILE inside a test directory. Matching the directory entry
# instead was a bug: most sdists list only files, so every project whose
# tarball omits directory entries looked like it shipped no tests at all -
# which would have shrunk and biased the very sample this script exists to
# make unbiased.
# A gate must cost far less than what it guards.
CLEAN_RUN_SECONDS = 300
CLEAN_RUN_MEMORY_MB = 4096

TEST_FILE = re.compile(r"^[^/]+/(?:src/)?(tests?)/(?:.*/)?test_[^/]*\.py$")


# A thousand candidates means a thousand virtualenvs. The system temp directory is
# on C: here, which filled, and `uv venv` then failed for 160 of 295 candidates -
# an infrastructure failure that looked like a property of those projects.
SCRATCH = Path(os.environ.get("CENSUS_SCRATCH", r"D:\_census_scratch"))


def _remove_tree(path: Path, tries: int = 5) -> None:
    """Delete a tree, waiting out the handles Windows holds briefly after a process exits."""
    for attempt in range(tries):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == tries - 1:
                return  # leave it rather than fail the run; the next start sweeps it
            time.sleep(0.4 * (attempt + 1))


def sweep_scratch() -> int:
    """Remove scratch trees left by an earlier run. Returns how many went."""
    if not SCRATCH.exists():
        return 0
    gone = 0
    for child in SCRATCH.iterdir():
        if child.is_dir():
            _remove_tree(child)
            gone += not child.exists()
    return gone


class IndexUnreachable(RuntimeError):
    """The package index could not be read. Says nothing about the package."""


def fetch_json(url: str, tries: int = 4) -> dict:
    """Read JSON from the index, retrying a transient failure.

    Without retries a single slow response discarded a candidate, and under load from
    several concurrent corpus jobs that discarded most of them: one run wrote off 950
    of 1,000 packages as publishing no sdist when nearly all of them do.
    """
    last: Exception | None = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return json.loads(response.read().decode())
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last = exc
            if attempt < tries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise IndexUnreachable(f"{type(last).__name__}: {last}")


def top_packages(count: int) -> list[str]:
    data = fetch_json(TOP)
    rows = data["rows"] if isinstance(data, dict) else data
    return [r["project"] for r in rows[:count]]


def newest_sdist(package: str) -> tuple[str, str] | None:
    """The newest final release's sdist url, or None if the project publishes none."""
    # Deliberately not caught here. A failure to reach the index is not a fact about
    # the package, and the caller records it as its own outcome.
    data = fetch_json(f"https://pypi.org/pypi/{package}/json")
    version = data["info"]["version"]
    for f in data["releases"].get(version, []):
        if f["packagetype"] == "sdist" and f["filename"].endswith(".tar.gz"):
            return version, f["url"]
    return None


def unpack(url: str, into: Path) -> Path | None:
    try:
        with urllib.request.urlopen(url, timeout=180) as response:
            blob = response.read()
        with tarfile.open(fileobj=io.BytesIO(blob)) as tf:
            if not any(TEST_FILE.match(n) for n in tf.getnames()):
                return None
            tf.extractall(into, filter="data")
    except Exception:  # noqa: BLE001
        return None
    roots = [p for p in into.iterdir() if p.is_dir()]
    return roots[0] if len(roots) == 1 else None


def test_dir(root: Path) -> Path | None:
    """The shallowest directory named test/tests that really holds test_*.py files."""
    for parent in (root, root / "src"):
        for name in ("tests", "test"):
            candidate = parent / name
            if candidate.is_dir() and any(candidate.rglob("test_*.py")):
                return candidate
    return None



TEST_GROUP = re.compile(r"test|dev|check|^ci$", re.I)
NOT_TEST_GROUP = re.compile(r"doc|bench|emscripten|lint|release|min|publish|typing", re.I)


def declared_groups(root: Path) -> list[str]:
    """Group names a project really declares, in a sensible install order.

    Read from pyproject.toml rather than guessed, because `uv pip install --group`
    returns 0 for a group that does not exist, so a return code cannot tell the
    difference between installed and absent.
    """
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return []
    try:
        import tomllib
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    names = list(data.get("dependency-groups", {}))
    wanted = [n for n in names if TEST_GROUP.search(n) and not NOT_TEST_GROUP.search(n)]
    # `test` before `dev` so the lighter set lands first, then anything it missed.
    return sorted(wanted, key=lambda n: (0 if n.lower().startswith("test") else 1, n))


def install_test_deps(root: Path, py: Path, pip) -> list[str]:
    """Install every test-ish group and extra. Returns what was actually applied."""
    applied = []
    for group in declared_groups(root):
        done = subprocess.run(["uv", "pip", "install", "-q", "--python", str(py),
                               "--group", group], cwd=root,
                              capture_output=True, text=True, timeout=900)
        if done.returncode == 0 and "was not found" not in (done.stderr or ""):
            applied.append(f"group:{group}")
    if not applied:
        for extra in ("test", "tests", "testing", "dev"):
            if pip(f"{root}[{extra}]").returncode == 0:
                applied.append(f"extra:{extra}")
                break
    for req in ("test-requirements.txt", "requirements-test.txt", "dev-requirements.txt",
                "requirements-dev.txt", "requirements/test.txt", "requirements/dev.txt"):
        path = root / req
        if path.exists() and pip("-r", str(path)).returncode == 0:
            applied.append(req)
            break
    return applied


def prepare(root: Path, package: str, version: str, tests: Path) -> tuple[Path | None, str]:
    """A venv with the project installed, but only if its clean suite really runs.

    Without this the audit "succeeded" on every project and measured nothing: no
    dependencies were installed, so pytest collected nothing, and the report came back
    covered_total 0, planned_mutants 0, kill_rate null. Four projects in a row looked
    audited. A census of empty audits is worse than no census, because it has a
    denominator.

    So the clean run is a gate, not a formality: the suite must exit 0 AND report a
    non-zero collected count before a single mutant is generated.
    """
    venv = root.parent / "venv"
    _remove_tree(venv)
    made = subprocess.run(["uv", "venv", str(venv), "-q"], capture_output=True, text=True)
    if made.returncode:
        # Recording only "could not create a venv" made 160 exclusions in one run
        # unexplainable after the fact - the same blind spot this tool exists to
        # find in other people's checks.
        why = (made.stderr or made.stdout or "").strip().splitlines()
        return None, f"could not create a venv: {why[-1][:110] if why else 'no output'}"
    py = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    # Install from the unpacked sdist, not from PyPI, so the project's own declared
    # test extras resolve. Installing only the published package left every suite
    # without its pytest plugins, and the clean run failed for all of them.
    def pip(*spec: str) -> subprocess.CompletedProcess:
        return subprocess.run(["uv", "pip", "install", "-q", "--python", str(py), *spec],
                              capture_output=True, text=True, timeout=900)

    base = pip(str(root), "pytest")
    if base.returncode:
        base = pip(f"{package}=={version}", "pytest")
    if base.returncode:
        last = (base.stderr or "").strip().splitlines()
        return None, f"install failed: {last[-1][:110] if last else '?'}"

    extras_used = install_test_deps(root, py, pip)
    # Budgeted like the audit, and it cannot raise: an unguarded TimeoutExpired here
    # killed a whole thousand-candidate run at its first slow project. The clean run
    # is a gate, so it gets a shorter time cap than the audit it guards.
    outcome, detail, text = run_with_budgets(
        [str(py), "-m", "pytest", str(tests), "-q", "-x", "--no-header",
         "-p", "no:cacheprovider"],
        seconds=CLEAN_RUN_SECONDS, memory_mb=CLEAN_RUN_MEMORY_MB, cwd=root)
    if outcome == "timeout":
        return None, f"clean suite did not finish inside {CLEAN_RUN_SECONDS}s"
    if outcome == "memory":
        return None, f"clean suite exceeded memory: {detail}"
    collected = 0
    for m in re.finditer(r"(\d+) (?:passed|collected)", text):
        collected = max(collected, int(m.group(1)))
    failed = max((int(m.group(1)) for m in re.finditer(r"(\d+) failed", text)), default=0)
    if collected == 0:
        return None, "clean suite collects no tests"
    # A few pre-existing failures are not a reason to drop a project: the tool
    # reports them ("N test(s) already fail on the unmodified code") and carries a
    # health caveat for the case. Only a mostly-broken suite is excluded, because
    # then "killed" stops meaning anything.
    if failed > max(5, collected * 0.2):
        return None, f"clean suite mostly broken: {failed} of {collected} failing"
    return py, (f"{collected} collected, {failed} already failing"
                + (f" (deps: {', '.join(extras_used)})" if extras_used else ""))


def _tree_rss_mb(root_pid: int) -> float:
    """Resident memory of a process and every descendant, in MB.

    Done with one CIM query rather than a library, because this package and its
    scripts carry no third-party dependencies.
    """
    query = (
        "Get-CimInstance Win32_Process | "
        "ForEach-Object { $_.ProcessId.ToString() + ',' + "
        "$_.ParentProcessId.ToString() + ',' + $_.WorkingSetSize.ToString() }"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", query],
                             capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return 0.0
    parent: dict[int, int] = {}
    size: dict[int, int] = {}
    for line in out.splitlines():
        bits = line.strip().split(",")
        if len(bits) == 3 and all(b.strip().isdigit() for b in bits):
            pid, ppid, rss = (int(b) for b in bits)
            parent[pid] = ppid
            size[pid] = rss
    total = 0
    for pid in size:
        walker, hops = pid, 0
        while walker in parent and hops < 40:
            if walker == root_pid:
                total += size[pid]
                break
            walker = parent[walker]
            hops += 1
    return total / 1048576


def run_with_budgets(cmd: list[str], seconds: int, memory_mb: int,
                     cwd: Path | None = None) -> tuple[str, str, str]:
    """Run a command under both a time and a memory budget.

    Returns (outcome, detail, output) where outcome is "ok", "timeout" or "memory".
    The output is returned in every case, including the two kills, because the
    partial output of a stopped run is still what says how far it got.
    """
    chunks: list[str] = []
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, cwd=cwd, encoding="utf-8", errors="replace")

    def drain() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            chunks.append(line)

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()

    deadline = time.time() + seconds
    peak = 0.0
    outcome, detail = "ok", ""
    while proc.poll() is None:
        if time.time() > deadline:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True)
            outcome, detail = "timeout", f"{seconds}s"
            break
        used = _tree_rss_mb(proc.pid)
        peak = max(peak, used)
        if used > memory_mb:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True)
            outcome, detail = "memory", f"{used:,.0f} MB over the {memory_mb:,} MB cap"
            break
        time.sleep(4)
    reader.join(timeout=10)
    if outcome == "ok":
        detail = f"peak {peak:,.0f} MB"
    return outcome, detail, "".join(chunks)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--packages", type=int, default=60)
    ap.add_argument("--budget", type=int, default=900,
                    help="seconds a single project's audit may take (900)")
    ap.add_argument("--per-function", type=int, default=5)
    ap.add_argument("--jobs", default="4")
    ap.add_argument("--memory-mb", type=int, default=4096,
                    help="memory a single project's audit may use, whole process tree "
                         "(4096). One suite reached 15.4 GB on a 31.6 GB machine "
                         "inside its time budget")
    ap.add_argument("--out", default="target-census.json")
    args = ap.parse_args()

    SCRATCH.mkdir(parents=True, exist_ok=True)
    swept = sweep_scratch()
    if swept:
        print(f"swept {swept} scratch tree(s) left by an earlier run")
    print(f"scratch: {SCRATCH}")
    names = top_packages(args.packages)
    print(f"{len(names)} packages from the external top-downloads list\n")
    out, results, started = Path(args.out), [], time.time()

    for i, name in enumerate(names, 1):
        try:
            row: dict = {"package": name}
            sd = newest_sdist(name)
            if sd is None:
                row["excluded"] = "no sdist published"
                results.append(row)
                print(f"  [{i}/{len(names)}] {name:24} excluded: no sdist")
                out.write_text(json.dumps(results, indent=1), encoding="utf-8")
                continue
            row["version"], url = sd
            with tempfile.TemporaryDirectory(prefix="sa-census-", dir=SCRATCH,
                                                ignore_cleanup_errors=True) as tmp:
                root = unpack(url, Path(tmp))
                if root is None:
                    row["excluded"] = "sdist ships no test directory"
                elif (tests := test_dir(root)) is None:
                    row["excluded"] = "no test_*.py under tests/"
                else:
                    py, why = prepare(root, name, row["version"], tests)
                    row["clean_run"] = why
                    if py is None:
                        row["excluded"] = why
                        results.append(row)
                        print(f"  [{i}/{len(names)}] {name:24} excluded: {why[:46]}", flush=True)
                        out.write_text(json.dumps(results, indent=1), encoding="utf-8")
                        continue
                    # --out is a DIRECTORY; it writes AUDIT.md and audit.json into it.
                    report_dir = Path(tmp) / "report"
                    report = report_dir / "audit.json"
                    cmd = [sys.executable, "-m", "suite_auditor", "audit", str(root),
                           "--test", str(tests.relative_to(root)),
                           "--python", str(py),
                           "--per-function", str(args.per_function),
                           "-j", str(args.jobs), "-q", "--out", str(report_dir)]
                    t0 = time.time()
                    try:
                        outcome, detail, _ = run_with_budgets(cmd, args.budget, args.memory_mb)
                        row["seconds"] = round(time.time() - t0, 1)
                        row["resources"] = detail
                        if outcome == "memory":
                            row["excluded"] = f"memory budget exceeded: {detail}"
                            results.append(row)
                            print(f"  [{i}/{len(names)}] {name:24} excluded: {row['excluded'][:46]}",
                                  flush=True)
                            out.write_text(json.dumps(results, indent=1), encoding="utf-8")
                            continue
                        if outcome == "timeout":
                            row["excluded"] = f"budget exceeded ({args.budget}s)"
                            results.append(row)
                            print(f"  [{i}/{len(names)}] {name:24} excluded: {row['excluded'][:46]}",
                                  flush=True)
                            out.write_text(json.dumps(results, indent=1), encoding="utf-8")
                            continue
                        if report.exists():
                            audit = json.loads(report.read_text(encoding="utf-8"))
                            # An audit that planned no mutants measured nothing. It is an
                            # exclusion with a reason, never a row in a rate.
                            if not audit.get("planned_mutants"):
                                row["excluded"] = "audit planned no mutants"
                                row["empty_audit"] = audit
                            else:
                                row["audit"] = audit
                        else:
                            row["excluded"] = "audit produced no report"
                    except subprocess.TimeoutExpired:  # handled by run_with_budgets
                        row["seconds"] = args.budget
                        row["excluded"] = f"budget exceeded ({args.budget}s)"
        except Exception as exc:  # noqa: BLE001
            # A census walks a thousand projects it has never seen, over a network,
            # running their code. Two runs died at their first awkward project - an
            # unguarded TimeoutExpired from a clean suite, an SSL TimeoutError while
            # fetching an sdist. Anything that escapes is that candidate's result.
            row["excluded"] = f"candidate failed: {type(exc).__name__}: {exc}"[:160]
        results.append(row)
        state = row.get("excluded", "audited")
        rate = (time.time() - started) / i
        print(f"  [{i}/{len(names)}] {name:24} {state[:46]:46} "
              f"{rate:.0f}s/pkg ~{rate * (len(names) - i) / 60:.0f}min left", flush=True)
        out.write_text(json.dumps(results, indent=1), encoding="utf-8")

    audited = [r for r in results if "audit" in r]
    print(f"\n{len(audited)} of {len(results)} projects audited; "
          f"{len(results) - len(audited)} excluded, each with its reason in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
