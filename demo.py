"""Show what suite-auditor does, in one command, with nothing to set up.

    python demo.py

Audits `examples/pricing`, a small project bundled with this repository: one function
tested thoroughly, one tested at a single point either side of its boundary, one whose
test checks almost nothing, and one with no test at all. The audit should find nothing
wrong with the first and name the input that exposes each of the others.

Needs pytest importable by the interpreter running this script (`uv sync` or
`pip install -e . pytest` in a clone).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "examples" / "pricing"


def _run(*args: str) -> int:
    cmd = [sys.executable, "-m", "suite_auditor.cli", *args]
    print("$ suite-auditor " + " ".join(args), flush=True)
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(cmd, cwd=ROOT, env=env, check=False).returncode


def main() -> int:
    rel = TARGET.relative_to(ROOT).as_posix()
    code = _run("coverage", rel, "--python", sys.executable)
    if code != 0:
        return code
    print(flush=True)
    code = _run("audit", rel, "--python", sys.executable, "-j", "auto")
    print(flush=True)
    print("Point it at your own code with:", flush=True)
    for line in ["suite-auditor coverage <repo>", "suite-auditor audit <repo>"]:
        print("    " + line, flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
