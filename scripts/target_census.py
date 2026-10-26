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
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

TOP = ("https://raw.githubusercontent.com/hugovk/top-pypi-packages/main/"
       "top-pypi-packages.min.json")
# Matches a test FILE inside a test directory. Matching the directory entry
# instead was a bug: most sdists list only files, so every project whose
# tarball omits directory entries looked like it shipped no tests at all -
# which would have shrunk and biased the very sample this script exists to
# make unbiased.
TEST_FILE = re.compile(r"^[^/]+/(?:src/)?(tests?)/(?:.*/)?test_[^/]*\.py$")


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.loads(response.read().decode())


def top_packages(count: int) -> list[str]:
    data = fetch_json(TOP)
    rows = data["rows"] if isinstance(data, dict) else data
    return [r["project"] for r in rows[:count]]


def newest_sdist(package: str) -> tuple[str, str] | None:
    """The newest final release's sdist url, or None if the project publishes none."""
    try:
        data = fetch_json(f"https://pypi.org/pypi/{package}/json")
    except Exception:  # noqa: BLE001 - unreadable is an outcome, recorded by the caller
        return None
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
    if subprocess.run(["uv", "venv", str(venv), "-q"], capture_output=True).returncode:
        return None, "could not create a venv"
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

    # Test dependencies are declared three different ways in the wild, and missing
    # them is why every clean run failed at first. All three are tried.
    extras_used = []
    for group in ("test", "tests", "testing", "dev"):
        # PEP 735 [dependency-groups] - what packaging, urllib3 and most modern
        # projects use. An extras-only install cannot see these at all.
        done = subprocess.run(["uv", "pip", "install", "-q", "--python", str(py),
                               "--group", group], cwd=root,
                              capture_output=True, text=True, timeout=900)
        if done.returncode == 0:
            extras_used.append(f"group:{group}")
            break
    if not extras_used:
        for extra in ("test", "tests", "testing", "dev"):
            if pip(f"{root}[{extra}]").returncode == 0:
                extras_used.append(f"extra:{extra}")
                break
    for req in ("test-requirements.txt", "requirements-test.txt", "dev-requirements.txt",
                "requirements-dev.txt", "requirements/test.txt", "requirements/dev.txt"):
        path = root / req
        if path.exists() and pip("-r", str(path)).returncode == 0:
            extras_used.append(req)
            break
    clean = subprocess.run([str(py), "-m", "pytest", str(tests), "-q", "-x", "--no-header",
                            "-p", "no:cacheprovider"],
                           cwd=root, capture_output=True, text=True, timeout=900)
    text = clean.stdout + clean.stderr
    collected = 0
    for m in re.finditer(r"(\d+) (?:passed|collected)", text):
        collected = max(collected, int(m.group(1)))
    if clean.returncode != 0:
        tail = [l for l in text.strip().splitlines() if l.strip()]
        return None, f"clean suite does not pass: {tail[-1][:110] if tail else '?'}"
    if collected == 0:
        return None, "clean suite passes but collects no tests"
    return py, (f"{collected} tests collected"
                + (f" (deps: {', '.join(extras_used)})" if extras_used else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--packages", type=int, default=60)
    ap.add_argument("--budget", type=int, default=900,
                    help="seconds a single project's audit may take (900)")
    ap.add_argument("--per-function", type=int, default=5)
    ap.add_argument("--jobs", default="4")
    ap.add_argument("--out", default="target-census.json")
    args = ap.parse_args()

    names = top_packages(args.packages)
    print(f"{len(names)} packages from the external top-downloads list\n")
    out, results, started = Path(args.out), [], time.time()

    for i, name in enumerate(names, 1):
        row: dict = {"package": name}
        sd = newest_sdist(name)
        if sd is None:
            row["excluded"] = "no sdist published"
            results.append(row)
            print(f"  [{i}/{len(names)}] {name:24} excluded: no sdist")
            out.write_text(json.dumps(results, indent=1), encoding="utf-8")
            continue
        row["version"], url = sd
        with tempfile.TemporaryDirectory(prefix="sa-census-") as tmp:
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
                    proc = subprocess.run(cmd, capture_output=True, text=True,
                                          timeout=args.budget)
                    row["seconds"] = round(time.time() - t0, 1)
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
                        tail = (proc.stderr or proc.stdout).strip().splitlines()
                        row["excluded"] = f"audit produced no report: {tail[-1][:120] if tail else '?'}"
                except subprocess.TimeoutExpired:
                    row["seconds"] = args.budget
                    row["excluded"] = f"budget exceeded ({args.budget}s)"
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
