"""What a gap in a test suite is, and what it takes to call one proven.

A mutant that a suite fails to kill is *suspicious*, not damning. Some mutants are
semantically equivalent to the original - `x * 1` versus `x`, a comparison inside a branch
nothing can reach - and no test could ever distinguish them. Counting those as gaps inflates
the number and the inflation is invisible, because nobody checks.

So a survivor is only a **gap** once a separating input is found: a concrete argument set on
which the original and the mutant return different things. That input is the proof, and it
is what the report leads with. Everything else is `unproven`, reported separately and never
folded into the headline.

This is `mbpp-false-accepts` applied to real libraries. There the oracle was three asserts
per problem; here it is a suite somebody maintains, which is a far higher bar and makes any
gap that survives it more interesting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Verdict(StrEnum):
    KILLED = "killed"
    """The suite noticed. Working as intended."""

    PROVEN_GAP = "proven-gap"
    """The suite passed, and there is an input proving the mutant behaves differently."""

    UNPROVEN = "unproven"
    """The suite passed, and no separating input was found. May be equivalent."""

    SKIPPED = "skipped"
    """Could not be scored - the mutant did not import, or nothing covered it."""


@dataclass
class Target:
    """One function the auditor can mutate, and the tests that reach it."""

    path: str
    name: str
    source: str
    header: str = ""
    sys_path: str = ""
    package: str = ""
    lineno: int = 0
    end_lineno: int = 0
    covering_tests: list[str] = field(default_factory=list)
    """Which tests execute this function.

    Running an entire suite once per mutant is how a mutation run becomes an overnight
    job on a real library. Only the tests that actually reach the function can kill its
    mutants, so only those are run - and if none do, the function is uncovered, which is
    itself worth reporting and costs nothing to find out.
    """

    @property
    def key(self) -> str:
        return f"{self.path}::{self.name}"


@dataclass
class Result:
    """One mutant, and what became of it."""

    target: str
    mutant: str
    kind: str
    verdict: Verdict
    witness: dict | None = None
    detail: str = ""
    strength: int = 3
    """How convincing the witness is; lower is better. See inputs.witness_strength.

    A gap whose proof is "both sides raised, differently, on an argument neither was
    designed for" is real and weak. Ranking by this is what keeps the strong ones at the
    top of a report instead of buried.
    """

    def as_row(self) -> dict:
        return {
            "target": self.target,
            "kind": self.kind,
            "verdict": self.verdict.value,
            "witness": self.witness,
            "strength": self.strength,
            "detail": self.detail[:300],
        }


@dataclass
class Audit:
    results: list[Result] = field(default_factory=list)
    uncovered: list[str] = field(default_factory=list)
    covered_total: int = 0
    """Functions with at least one covering test, before `--limit` truncates them.

    Recorded because `kill_rate` is a rate over the functions that were mutated,
    and only functions with a covering test are ever mutated. Without this the
    headline number has no visible denominator: a suite covering three functions
    and killing every mutant in them reports the same 100% as a suite covering all
    of them. `repo-surgeon` posts a high kill rate over a small covered fraction,
    which is the case this exists to make visible.
    """

    seconds: float = 0.0

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.results:
            out[r.verdict.value] = out.get(r.verdict.value, 0) + 1
        return out

    @property
    def scored(self) -> list[Result]:
        """Mutants that got a real verdict. Skipped ones are not evidence either way."""
        return [r for r in self.results if r.verdict is not Verdict.SKIPPED]

    @property
    def gaps(self) -> list[Result]:
        """Proven gaps, strongest witness first."""
        return sorted(
            (r for r in self.results if r.verdict is Verdict.PROVEN_GAP),
            key=lambda r: (r.strength, r.target),
        )

    @property
    def strong_gaps(self) -> list[Result]:
        """Gaps where both versions returned a value and the values differ."""
        return [r for r in self.gaps if r.strength == 0]

    @property
    def kill_rate(self) -> float | None:
        s = self.scored
        if not s:
            return None
        return sum(r.verdict is Verdict.KILLED for r in s) / len(s)

    @property
    def covered_fraction(self) -> float | None:
        """Share of the package's functions that any test reaches at all."""
        total = self.covered_total + len(self.uncovered)
        if not total:
            return None
        return self.covered_total / total

    @property
    def caught_share(self) -> float | None:
        """Kill rate times covered fraction: the share of the WHOLE package whose
        mutants this suite would notice.

        The honest headline, because it cannot be raised by testing less. A suite
        that covers a tenth of the code and kills everything in it scores 0.1 here
        and 1.0 on `kill_rate`, and the second number is the one that gets quoted.
        """
        rate, fraction = self.kill_rate, self.covered_fraction
        if rate is None or fraction is None:
            return None
        return rate * fraction

    def by_function(self) -> dict[str, list[Result]]:
        out: dict[str, list[Result]] = {}
        for r in self.results:
            out.setdefault(r.target, []).append(r)
        return out
