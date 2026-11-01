"""How many of the reported gaps are the same gap? Dynamic mutant subsumption.

`tce` removes mutants that are *syntactically* redundant: compile two of them and if the
bytecode is identical they cannot be told apart by anything, so one of them is noise. That
catches a real share of the pool and it stops there, because two mutants can compile to
different bytecode and still be distinguished by exactly the same inputs - a comparison
flipped one way and a constant nudged the other, both of which only show up on an empty
list. TCE keeps both. The report then shows two gaps where a reader would see one defect.

Subsumption is the semantic form of the same idea, from Ammann, Delamaro & Offutt,
*Establishing Theoretical Minimal Sets of Mutants* (ICST 2014), and the subsumption graphs
of Kurtz et al. (ICSTW 2014). Write K(m) for the set of inputs that distinguish mutant m
from the original. Mutant A **subsumes** B when K(A) is a subset of K(B): every input that
catches A also catches B, so B tells a reader nothing A did not. A **minimal set** has no
such pair left in it, and the mutants at the top of the hierarchy - those no other mutant
subsumes - are the **dominators**. That set is what a count of distinct gaps should be
taken over.

This tool is unusually well placed to compute it. Classic mutation testing has to define
K(m) over the project's test suite, which is coarse: a test is one bit. Here every mutant
of one function is compared against the original over the *same* pool of argument sets, so
K(m) is a set of concrete inputs and the relation is computed at input granularity rather
than test granularity.

**Two things this does not claim.**

Subsumption is relative to the input pool, exactly as dynamic subsumption in the source
paper is relative to a test set. Two mutants with identical kill sets here may be
distinguishable by an input the pool does not contain, so merging them can be wrong in
the direction of merging too much. Computing the relation over *all* inputs is undecidable,
which is why the paper offers the dynamic form at all. So the dominator count is a lower
bound on the number of distinct behaviours and the raw count is an upper bound, and both
belong in a report rather than either alone.

And a mutant that no input distinguishes - K(m) empty - is not a dominator of anything. It
is an unproven survivor, and it is excluded here rather than treated as subsuming
everything, which is what a naive subset test would do: the empty set is a subset of every
set, so an unkillable mutant would subsume the entire pool and collapse the count to one.
That is the whole trap in this algorithm and it is silent.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Partition:
    """One function's proven gaps, split into distinct ones and restatements."""

    dominators: tuple[str, ...]
    """One mutant per distinct behaviour, each subsumed by no other."""

    subsumed: dict[str, str] = field(default_factory=dict)
    """Mutant -> the dominator that already covers it, for the ones dropped."""

    classes: tuple[tuple[str, ...], ...] = ()
    """Mutants grouped by identical kill set; the semantic form of a TCE duplicate."""

    @property
    def distinct(self) -> int:
        return len(self.dominators)

    @property
    def restatements(self) -> int:
        return len(self.subsumed)


def subsumes(left: frozenset[int], right: frozenset[int]) -> bool:
    """Whether a mutant with kill set `left` subsumes one with kill set `right`.

    Every input that catches the first catches the second, and the first is catchable at
    all. The second condition is not decoration: without it an empty kill set subsumes
    everything, because the empty set is a subset of every set.
    """
    return bool(left) and left <= right


def partition(kill_sets: dict[str, frozenset[int]]) -> Partition:
    """Split proven gaps into dominators and the restatements they cover.

    `kill_sets` maps a mutant to the inputs that distinguish it from the original, over
    one shared pool. Mutants with an empty kill set are not proven gaps and are ignored.

    Mutants with identical kill sets subsume each other, so they form one class and one of
    them is kept - the first in the given order, so the result does not depend on set
    iteration order. A class is kept when no *other* class's kill set is a strict subset
    of its own.
    """
    killed = {m: s for m, s in kill_sets.items() if s}
    if not killed:
        return Partition(dominators=())

    # Group by kill set. `dict` keeps insertion order, so the representative is the
    # caller's first mutant for that behaviour rather than an arbitrary one.
    by_set: dict[frozenset[int], list[str]] = {}
    for mutant, signature in killed.items():
        by_set.setdefault(signature, []).append(mutant)

    signatures = list(by_set)
    dominators: list[str] = []
    subsumed: dict[str, str] = {}
    for signature in signatures:
        members = by_set[signature]
        # Strictly smaller kill set elsewhere means something harder to catch already
        # implies this one, so this class is a restatement of that one.
        covering = next(
            (other for other in signatures if other < signature),
            None,
        )
        if covering is None:
            dominators.append(members[0])
            for extra in members[1:]:
                subsumed[extra] = members[0]
        else:
            keeper = by_set[covering][0]
            for member in members:
                subsumed[member] = keeper

    # A class whose covering class was itself subsumed would point at a mutant that is not
    # in the kept set, which would read as "covered by" something the report never shows.
    # Walk each pointer up to a dominator.
    kept = set(dominators)
    for mutant in list(subsumed):
        seen = {mutant}
        target = subsumed[mutant]
        while target not in kept and target in subsumed and target not in seen:
            seen.add(target)
            target = subsumed[target]
        subsumed[mutant] = target

    return Partition(
        dominators=tuple(dominators),
        subsumed=subsumed,
        classes=tuple(tuple(members) for members in by_set.values()),
    )


def summarise(part: Partition) -> dict:
    """Counts for the report, with shares that are `None` rather than 0 when empty."""
    proven = part.distinct + part.restatements
    return {
        "proven": proven,
        "distinct": part.distinct,
        "restatements": part.restatements,
        "inflation": (round(proven / part.distinct, 4) if part.distinct else None),
        "restated_share": (round(part.restatements / proven, 4) if proven else None),
    }
