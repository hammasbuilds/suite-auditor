"""`python -m suite_auditor` has to behave exactly like the console script.

Coverage showed `__main__.py` at 0%: three statements, a documented way to run the tool,
never executed by the suite. It is the entry point that survives when the console script
is not on PATH - a fresh `pip install --target`, a CI step that calls the interpreter
directly - so it breaking is invisible until somebody is already stuck.
"""

from __future__ import annotations

import subprocess
import sys


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "suite_auditor", *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_the_module_form_reports_the_same_version() -> None:
    done = _run("--version")
    assert done.returncode == 0, done.stderr
    assert "suite-auditor" in (done.stdout + done.stderr)


def test_both_subcommands_are_reachable_through_the_module_form() -> None:
    for sub in ("coverage", "audit"):
        done = _run(sub, "--help")
        assert done.returncode == 0, f"{sub}: {done.stderr}"
        assert f"suite-auditor {sub}" in done.stdout, done.stdout


def test_no_arguments_is_an_error_with_usage_rather_than_a_traceback() -> None:
    """Running it bare must say what to do, not print a stack trace."""
    done = _run()
    assert done.returncode != 0
    output = done.stdout + done.stderr
    assert "usage:" in output
    assert "Traceback" not in output
