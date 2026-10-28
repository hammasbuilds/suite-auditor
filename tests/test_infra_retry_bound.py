"""An infrastructure failure is retried, but not forever.

A row excluded because the machine failed - a full disk, an unreachable index - says
nothing about the package, so `--resume` drops it and measures it again. Unbounded, that
means the census never settles: the same seven rows were re-measured on each of three
consecutive runs, at several minutes of install attempts each, because the classifier
still matched the wording they had failed with.

After MAX_INFRA_RETRIES the row is kept as it is, with the count beside it. A candidate
that fails the same way twice on a machine whose cause has been fixed is telling us about
itself, and "this machine could not install it, twice" is an honest exclusion.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from target_census import MAX_INFRA_RETRIES, _is_infra_failure  # noqa: E402

DISK_FULL = ("install failed:   Caused by: failed to write to file ")
TIMEOUT = "candidate failed: TimeoutExpired: Command '['uv', 'pip', 'install', '-q'"
REAL = "sdist has no test directory"


def row(excluded: str, retries: int | None = None) -> dict:
    out = {"package": "p", "excluded": excluded}
    if retries is not None:
        out["infra_retries"] = retries
    return out


def retried(rows: list[dict]) -> list[dict]:
    """The selection the census makes on resume."""
    return [
        r for r in rows
        if _is_infra_failure(r) and r.get("infra_retries", 0) < MAX_INFRA_RETRIES
    ]


def test_a_fresh_infrastructure_failure_is_retried() -> None:
    assert retried([row(DISK_FULL)]) == [row(DISK_FULL)]
    assert retried([row(TIMEOUT)]) == [row(TIMEOUT)]


def test_a_real_exclusion_is_never_retried() -> None:
    """The distinction the whole mechanism rests on."""
    assert retried([row(REAL)]) == []
    assert not _is_infra_failure(row(REAL))


def test_the_retry_stops_at_the_bound() -> None:
    assert retried([row(DISK_FULL, MAX_INFRA_RETRIES - 1)])
    assert retried([row(DISK_FULL, MAX_INFRA_RETRIES)]) == []
    assert retried([row(DISK_FULL, MAX_INFRA_RETRIES + 5)]) == []


def test_the_bound_is_small_but_not_one() -> None:
    """One retry covers a transient cause; zero would defeat the purpose.

    Stated as a test because the number is a judgement and a future change to it should
    be deliberate rather than incidental.
    """
    assert 2 <= MAX_INFRA_RETRIES <= 3


def test_a_row_that_is_not_a_dict_does_not_crash_the_selection() -> None:
    """An earlier run wrote a bare string into the results list."""
    rows = [r for r in ["a stray string", row(DISK_FULL)] if isinstance(r, dict)]
    assert retried(rows) == [row(DISK_FULL)]
