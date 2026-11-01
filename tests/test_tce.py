"""Trivial Compiler Equivalence, checked against planted positives before being believed.

A detector whose headline result is "it found nothing" is worth nothing until something
it should find is put in front of it. The TCE rate on this package's own operator set may
well be zero - CPython has no optimising compiler, which is the usual reason given for
not trying - and a zero is only a finding if the instrument demonstrably works.

So: planted equivalences that TCE must catch, planted differences it must not call
equivalent, and the soundness direction stated as a property - anything called equivalent
must actually be the same program.
"""

from __future__ import annotations

import pytest

from suite_auditor.tce import (
    Unfingerprintable,
    fingerprint,
    partition,
    summarise,
)

# --- the fingerprint itself -----------------------------------------------------------


def test_the_same_program_written_differently_has_one_fingerprint():
    """Whitespace, comments and line numbers are not the program."""
    a = "def f(x):\n    return x + 1\n"
    b = "def f(x):\n\n    # a comment\n    return x + 1\n"
    c = "\n\n\ndef f(x):\n    return x  +  1\n"
    assert fingerprint(a) == fingerprint(b) == fingerprint(c)


def test_a_changed_operator_changes_the_fingerprint():
    assert fingerprint("def f(a, b):\n    return a < b\n") != fingerprint(
        "def f(a, b):\n    return a <= b\n"
    )


def test_a_changed_constant_changes_the_fingerprint():
    assert fingerprint("def f():\n    return 1\n") != fingerprint("def f():\n    return 2\n")


def test_constants_that_compare_equal_are_still_different_constants():
    """`1`, `1.0` and `True` are equal to each other and are three different programs.

    A canonicalisation that went through a set or a dict keyed on the constant's value
    would collapse them, and `f() is True` would then be called equivalent to
    `f() is 1`.
    """
    seen = {
        fingerprint("def f():\n    return 1\n"),
        fingerprint("def f():\n    return 1.0\n"),
        fingerprint("def f():\n    return True\n"),
    }
    assert len(seen) == 3


def test_a_renamed_function_is_not_the_same_program():
    """`__name__` is observable, so the name is semantics, not metadata."""
    assert fingerprint("def f():\n    return 1\n") != fingerprint("def g():\n    return 1\n")


def test_a_changed_docstring_is_not_the_same_program():
    """`__doc__` is observable; stripping docstrings would have been wrong."""
    assert fingerprint('def f():\n    "one"\n    return 1\n') != fingerprint(
        'def f():\n    "two"\n    return 1\n'
    )


def test_an_except_clause_is_not_free_to_move():
    """On 3.11+ the try range lives in co_exceptiontable, which must be compared."""
    guarded = (
        "def f(x):\n    try:\n        return int(x)\n    except ValueError:\n        return 0\n"
    )
    unguarded = "def f(x):\n    return int(x)\n"
    assert fingerprint(guarded) != fingerprint(unguarded)


def test_source_that_does_not_compile_raises_rather_than_returning_a_value():
    """A caller must not be able to read 'could not decide' as 'not equivalent'."""
    with pytest.raises(Unfingerprintable):
        fingerprint("def f(:\n")


def test_the_fingerprint_is_stable_within_a_run():
    source = "def f(x):\n    return [i * 2 for i in range(x)]\n"
    assert fingerprint(source) == fingerprint(source)


# --- planted equivalences the partition must catch ------------------------------------


def test_a_planted_equivalent_mutant_is_detected():
    """CPython folds constant arithmetic, so `60 * 60` and `3600` are one program.

    This is the textbook trivially-equivalent mutant and the reason the method exists:
    no analysis decided it, the compiler did.
    """
    original = "def f():\n    return 3600\n"
    planted = [("def f():\n    return 60 * 60\n", "const")]
    result = partition(original, planted)
    assert len(result["equivalent"]) == 1, result
    assert result["kept"] == []


def test_a_second_kind_of_planted_equivalence_is_detected():
    """Folding inside a larger expression, so the match is not the whole function body."""
    original = "def f(x):\n    return x * 10\n"
    planted = [("def f(x):\n    return x * (5 + 5)\n", "binop")]
    assert len(partition(original, planted)["equivalent"]) == 1


def test_a_planted_duplicate_pair_is_detected_and_one_is_kept():
    original = "def f():\n    return 7\n"
    planted = [
        ("def f():\n    return 12\n", "const"),
        ("def f():\n    return 6 + 6\n", "binop"),
        ("def f():\n    return 13\n", "const"),
    ]
    result = partition(original, planted)
    assert len(result["duplicate"]) == 1, result
    assert len(result["kept"]) == 2
    # the first of the pair survives, in the order the mutant generator produced
    assert result["kept"][0][0].endswith("return 12\n")
    assert result["duplicate"][0][2] == "const", "the duplicate names what it duplicates"


def test_a_genuinely_different_mutant_is_not_called_equivalent():
    original = "def f(a, b):\n    return a < b\n"
    planted = [("def f(a, b):\n    return a <= b\n", "compare")]
    result = partition(original, planted)
    assert result["equivalent"] == []
    assert len(result["kept"]) == 1


def test_everything_called_equivalent_really_is_the_same_program():
    """The soundness direction, as a property over a mixed batch.

    The filter removing a mutant that does behave differently would silently delete a
    real gap, which is the only error here that matters.
    """
    original = "def f(x):\n    return x + 100\n"
    batch = [
        ("def f(x):\n    return x + 10 * 10\n", "binop"),
        ("def f(x):\n    return x + 101\n", "const"),
        ("def f(x):\n    return x - 100\n", "binop"),
        ("def f(x):\n    return x + 50 + 50\n", "binop"),
    ]
    result = partition(original, batch)
    for source, _ in result["equivalent"]:
        assert fingerprint(source) == result["base"]
    # x + 50 + 50 is NOT folded: addition is left-associative and x is unknown, so the
    # compiler cannot combine the two constants. Only the single folded product is.
    assert len(result["equivalent"]) == 1, [s for s, _ in result["equivalent"]]


def test_an_undecidable_mutant_is_kept_rather_than_filtered():
    original = "def f():\n    return 1\n"
    batch = [("def f(:\n", "const"), ("def f():\n    return 2\n", "const")]
    result = partition(original, batch)
    assert len(result["undecidable"]) == 1
    assert len(result["kept"]) == 2, "an undecidable mutant must stay in the set"


def test_an_uncompilable_original_disables_the_filter_entirely():
    batch = [("def f():\n    return 2\n", "const")]
    result = partition("def f(:\n", batch)
    assert result["base"] is None
    assert result["kept"] == batch
    assert result["equivalent"] == [] and result["duplicate"] == []


# --- the summary a report would publish -----------------------------------------------


def test_the_summary_counts_add_up_to_what_was_planned():
    original = "def f():\n    return 3600\n"
    batch = [
        ("def f():\n    return 60 * 60\n", "const"),
        ("def f():\n    return 3601\n", "const"),
        ("def f():\n    return 3600 + 1\n", "binop"),
        ("def f():\n    return 0\n", "const"),
    ]
    s = summarise(partition(original, batch))
    assert s["planned"] == 4
    assert s["equivalent"] == 1
    assert s["duplicate"] == 1
    assert s["kept"] == 2
    assert s["equivalent_share"] == 0.25
    assert s["duplicate_share"] == 0.25


def test_the_summary_of_an_empty_set_has_no_rate_rather_than_a_zero():
    """0/0 is not 0%. A share over nothing must be absent, not a flattering number."""
    s = summarise(partition("def f():\n    return 1\n", []))
    assert s["planned"] == 0
    assert s["equivalent_share"] is None
    assert s["duplicate_share"] is None


# --- the filter is wired into the pipeline, not just importable -----------------------


def test_the_audit_type_carries_the_counts():
    """A filter applied silently changes the published rate with no way to notice."""
    from suite_auditor.types import Audit

    assert isinstance(Audit().tce, dict)


def test_the_json_report_emits_the_counts(tmp_path):
    """The counts have to reach a reader, not just the dataclass."""
    import json as _json

    from suite_auditor import report
    from suite_auditor.types import Audit

    audit = Audit()
    audit.tce = {"planned": 4, "kept": 3, "equivalent": 0, "duplicate": 1, "undecidable": 0}
    path = tmp_path / "audit.json"
    report.write_json(audit, path)
    body = _json.loads(path.read_text(encoding="utf-8"))
    assert body["tce"] == audit.tce, body.get("tce")


def test_an_audit_that_filtered_nothing_reports_no_tce_block(tmp_path):
    """An empty dict must not print as a block of zeroes that look measured."""
    import json as _json

    from suite_auditor import report
    from suite_auditor.types import Audit

    path = tmp_path / "audit.json"
    report.write_json(Audit(), path)
    assert _json.loads(path.read_text(encoding="utf-8"))["tce"] is None


def test_a_realistic_function_yields_the_duplicate_the_paper_names():
    """`if not items:` is the shape that produces negate_if == drop_not.

    Named in section 4 of the manuscript as the duplicate TCE finds in real code, so
    the claim is checked rather than remembered.
    """
    from suite_auditor.mutate import mutants

    source = "def pick(items):\n    if not items:\n        return []\n    return sorted(items)\n"
    result = partition(source, mutants(source, cap=8))
    assert len(result["duplicate"]) == 1, result["duplicate"]
    duplicate_source, operator, duplicates_what = result["duplicate"][0]
    assert "if not not items:" in duplicate_source
    assert {operator, duplicates_what} == {"negate_if", "drop_not"}


# --- the jump branch, which shipped dead -----------------------------------------------


def test_jump_opcodes_were_actually_found():
    """`_JUMPS` must not be empty, and must hold opcodes rather than names.

    The first version read `dis.hasjump`, which exists only from 3.13 - so on 3.10 to
    3.12 it raised AttributeError, and on 3.13+ it compared `item.opname`, a string,
    against a list of integer opcodes, which is always False. The jump-target
    normalisation this module documents never ran, in any version.
    """
    from suite_auditor.tce import _JUMPS

    assert _JUMPS, "no jump opcodes found; the normalisation below cannot fire"
    assert all(isinstance(op, int) for op in _JUMPS), sorted(_JUMPS)[:5]


def test_the_jump_branch_fires_on_a_function_with_a_jump():
    """Not just that the set is non-empty: that this code path is taken.

    A test that only checked `_JUMPS` would pass with the branch still unreachable.
    """
    import dis

    from suite_auditor.tce import _JUMPS

    source = "def f(x):\n    if x > 0:\n        return 1\n    return 2\n"
    code = compile(source, "<t>", "exec")
    inner = next(c for c in code.co_consts if hasattr(c, "co_code"))
    jumps = [i for i in dis.get_instructions(inner) if i.opcode in _JUMPS]
    assert jumps, "this function was supposed to contain a jump"


def test_a_jump_over_a_region_that_changed_length_still_matches():
    """What the normalisation buys: offsets move, instruction positions do not.

    Two spellings of the same program whose jump crosses a differently-encoded
    constant. With jump targets left as raw byte offsets these can differ; expressed as
    positions in the instruction list they do not.
    """
    a = "def f(x):\n    if x:\n        return 3600\n    return 0\n"
    b = "def f(x):\n    if x:\n        return 60 * 60\n    return 0\n"
    assert fingerprint(a) == fingerprint(b)
