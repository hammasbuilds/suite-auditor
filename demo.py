"""Show what suite-auditor does, in one command, with nothing to set up.

    python demo.py

Audits `toolz`, a real and well-maintained library vendored under targets/,
and reports the functions its own test suite never reaches - not as a
percentage, which averages the gaps away, but as a list of names you can go
and look at.

toolz is a deliberate choice: a library with a serious test suite, so the
handful of gaps it does have are the interesting kind rather than evidence
of neglect.

The target is real third-party code, not a fixture built to flatter the tool.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    print("suite-auditor: which functions does toolz's own test suite never reach?", flush=True)
    print(flush=True)
    result = subprocess.run(
        [sys.executable, "-m", 'suite_auditor.cli', "coverage", "targets/toolz"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"},
        check=False,
    )
    if result.returncode != 0:
        return result.returncode
    print(flush=True)
    print("Point it at your own code with:", flush=True)
    for line in ['suite-auditor coverage <repo>', 'suite-auditor audit <repo>']:
        print("    " + line, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
