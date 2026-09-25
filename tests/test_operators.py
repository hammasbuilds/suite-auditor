"""Every mutation operator, and what mistake it stands for.

An operator that never fires is dead weight that makes the kill rate look better:
the denominator only counts mutants that were generated, so a whole class of bug
nobody tests for simply does not appear. These pin that each one reaches code.

Recall is the only thing these raise. Precision is protected downstream - a
survivor still has to fail the differential proof before it is called a gap, so a
mutant no input can distinguish stays `unproven` and never reaches the headline.
"""

from __future__ import annotations

import pytest

from suite_auditor.mutate import mutants

# (label, source, the operator it must produce)
CASES = [
    ("a boundary comparison", "def f(a, b):\n    return a < b\n", "compare"),
    ("membership", "def f(x, seen):\n    return x in seen\n", "compare"),
    ("identity", "def f(x):\n    return x is None\n", "compare"),
    ("arithmetic", "def f(a, b):\n    return a + b\n", "binop"),
    ("a boolean join", "def f(a, b):\n    return a and b\n", "boolop"),
    ("an integer constant", "def f(a):\n    return a * 2\n", "const"),
    ("branch polarity", "def f(a):\n    if a:\n        return 1\n    return 0\n", "negate_if"),
    ("a lost return value", "def f(a):\n    return a\n", "drop_return"),
    ("a dropped negation", "def f(a):\n    return not a\n", "drop_not"),
    (
        # The handler body must not be a bare `raise`. Replacing it with `pass` then
        # produces byte-identical source to the drop_raise mutant, and the dedup in
        # `mutants()` drops the second - so the operator fires and disappears.
        "a swallowed exception",
        "def f(a):\n"
        "    try:\n"
        "        return int(a)\n"
        "    except ValueError:\n"
        "        return 0\n",
        "swallow_except",
    ),
    ("a deleted contract", "def f(a):\n    assert a > 0\n    return a\n", "drop_assert"),
    ("a deleted raise", "def f(a):\n    if a:\n        raise ValueError(a)\n", "drop_raise"),
    ("a slice lower bound", "def f(a):\n    return a[1:]\n", "slice_lower"),
    ("a slice upper bound", "def f(a, n):\n    return a[:n]\n", "slice_upper"),
]


@pytest.mark.parametrize(("label", "source", "operator"), CASES, ids=[c[0] for c in CASES])
def test_each_operator_reaches_its_shape(label, source, operator):
    produced = {kind for _text, kind in mutants(source, cap=12)}
    assert operator in produced, f"{label}: {operator} never fired, got {sorted(produced)}"


def test_no_mutant_equals_the_original():
    """A mutant identical to the original is a free kill or a free survival depending
    on which way the suite happens to fall, and either way it is not evidence."""
    source = "def f(a, b):\n    if not a:\n        return b[1:]\n    return a + b\n"
    base = "def f(a, b):\n    if not a:\n        return b[1:]\n    return a + b\n"
    import ast

    normalised = ast.unparse(ast.parse(base))
    for text, _kind in mutants(source, cap=20):
        assert text != normalised


def test_mutants_are_distinct():
    source = "def f(a, b, c):\n    return a < b < c\n"
    texts = [t for t, _ in mutants(source, cap=20)]
    assert len(texts) == len(set(texts))


def test_a_function_with_nothing_to_mutate_yields_nothing():
    """Not an error, and not a kill either. `pass` has no decision in it."""
    assert mutants("def f():\n    pass\n") == []


def test_a_return_of_none_is_not_mutated_to_none():
    """`return None` -> `return None` is the identical-mutant case, reached through the
    one operator most likely to produce it."""
    produced = {kind for _t, kind in mutants("def f():\n    return None\n", cap=8)}
    assert "drop_return" not in produced


def test_the_operator_set_matches_what_the_mutator_can_produce():
    """OPERATORS is a declaration and the visitors are the implementation. If they
    drift, a report grouping by operator silently drops a name nothing knows about."""
    from suite_auditor.mutate import OPERATORS

    produced = set()
    for _label, source, _expected in CASES:
        produced |= {kind for _t, kind in mutants(source, cap=12)}

    assert produced <= OPERATORS, f"produced but undeclared: {sorted(produced - OPERATORS)}"
    assert OPERATORS <= produced, (
        f"declared but never produced by any case above: {sorted(OPERATORS - produced)}"
    )


# --- how the sample is drawn ---------------------------------------------------------


def test_the_sample_is_not_the_first_n_in_walk_order():
    """A function with many of one shape used to spend its whole budget on that shape.

    Twelve integer constants and one slice: walk order hands back six `const` mutants
    and never mentions the slice, so the kill rate for that function says nothing about
    off-by-one errors - the single most common real bug in code that walks a sequence.
    """
    source = (
        "def f(a):\n"
        "    total = 1 + 2 + 3 + 4 + 5 + 6 + 7 + 8 + 9 + 10 + 11 + 12\n"
        "    return a[1:] + [total]\n"
    )
    kinds = {k for _t, k in mutants(source, cap=6)}
    assert "slice_lower" in kinds, f"the one slice was crowded out by constants: {kinds}"
    assert "const" in kinds


def test_every_operator_present_in_a_function_can_be_sampled():
    """Round-robin means no kind of mistake is invisible. An operator that is never
    sampled is worse than one that does not exist: the kill rate silently excludes it
    while appearing to cover the function."""
    source = (
        "def f(a, b, n):\n"
        "    assert n > 0\n"
        "    if not a:\n"
        "        raise ValueError(a)\n"
        "    try:\n"
        "        head = a[1:n] + b\n"
        "    except TypeError:\n"
        "        return 0\n"
        "    return head\n"
    )
    available = {k for _t, k in mutants(source, cap=10**6)}
    sampled = {k for _t, k in mutants(source, cap=len(available))}
    assert sampled == available, f"never sampled: {sorted(available - sampled)}"


def test_the_rarest_operator_survives_a_cap_that_falls_mid_round():
    """Within a round the rarest kind goes first, so a kind with one site is not
    crowded out by one with forty when the budget runs out part-way through."""
    source = (
        "def f(a):\n"
        "    x = 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1\n"
        "    return a[2:] if x else None\n"
    )
    kinds = [k for _t, k in mutants(source, cap=2)]
    assert "slice_lower" in kinds, f"the single slice lost to ten constants: {kinds}"


def test_a_cap_larger_than_the_supply_returns_everything():
    source = "def f(a):\n    return a + 1\n"
    everything = mutants(source, cap=10**6)
    assert mutants(source, cap=999) == everything


def test_the_sample_is_still_distinct_and_excludes_the_original():
    import ast

    source = "def f(a, b, n):\n    if a < b:\n        return a[1:n] + 1\n    return None\n"
    drawn = mutants(source, cap=8)
    texts = [t for t, _ in drawn]
    assert len(texts) == len(set(texts))
    assert ast.unparse(ast.parse(source)) not in texts
