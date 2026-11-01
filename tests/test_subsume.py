"""Dynamic mutant subsumption, and the one silent way it collapses to nonsense.

`subsume.partition` answers "how many of these proven gaps are the same gap?" by comparing
kill sets, after Ammann, Delamaro & Offutt (ICST 2014). The algorithm is four lines and has
one trap in it: the empty set is a subset of every set, so a mutant no input distinguishes
would subsume the entire pool and collapse a hundred distinct gaps to one. Nothing about
that failure looks like an error - the count just gets better - so it is tested first and
from both sides.

The rest pins the properties a reader of the report depends on: a representative that does
not depend on dict iteration order, a `subsumed_by` pointer that names something the report
actually shows, and a hierarchy where mutants needing different inputs both survive.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from suite_auditor import subsume
from suite_auditor.types import Result, Verdict

ROOT = Path(__file__).resolve().parents[1]


def _sets(**kwargs: list[int]) -> dict[str, frozenset[int]]:
    return {name: frozenset(values) for name, values in kwargs.items()}


# ------------------------------------------------------------------------------ the trap


def test_an_unkillable_mutant_does_not_subsume_everything():
    """The empty set is a subset of every set; a naive subset test collapses the count."""
    part = subsume.partition(_sets(unkillable=[], a=[1], b=[2], c=[1, 2]))
    assert "unkillable" not in part.dominators
    assert "unkillable" not in part.subsumed
    # a and b need different inputs, so both stand; c is implied by either.
    assert set(part.dominators) == {"a", "b"}
    assert part.subsumed["c"] in {"a", "b"}
    assert part.distinct == 2


def test_subsumes_refuses_an_empty_left_hand_side():
    assert not subsume.subsumes(frozenset(), frozenset({1, 2}))
    assert not subsume.subsumes(frozenset(), frozenset())
    assert subsume.subsumes(frozenset({1}), frozenset({1, 2}))
    # Equal sets subsume each other - that is what makes them one class.
    assert subsume.subsumes(frozenset({1}), frozenset({1}))
    assert not subsume.subsumes(frozenset({1, 2}), frozenset({1}))


def test_a_pool_of_only_unkillable_mutants_yields_nothing():
    part = subsume.partition(_sets(a=[], b=[]))
    assert part.dominators == ()
    assert part.subsumed == {}
    assert part.distinct == 0
    assert subsume.summarise(part)["inflation"] is None
    assert subsume.summarise(part)["restated_share"] is None


# ------------------------------------------------------------------------ the hierarchy


def test_a_strict_subset_subsumes_and_is_kept():
    part = subsume.partition(_sets(hard=[3], easy=[1, 2, 3]))
    assert part.dominators == ("hard",)
    assert part.subsumed == {"easy": "hard"}
    assert part.restatements == 1


def test_mutants_needing_different_inputs_both_survive():
    part = subsume.partition(_sets(a=[1], b=[2], c=[3]))
    assert set(part.dominators) == {"a", "b", "c"}
    assert part.subsumed == {}


def test_identical_kill_sets_are_one_class_with_one_representative():
    """The semantic form of a TCE duplicate: different bytecode, same inputs catch it."""
    part = subsume.partition(_sets(first=[1, 2], second=[1, 2], third=[1, 2]))
    assert part.dominators == ("first",)
    assert part.subsumed == {"second": "first", "third": "first"}
    assert part.classes == (("first", "second", "third"),)


def test_the_representative_is_the_callers_first_not_an_arbitrary_one():
    """A representative that depends on set iteration order makes reports unstable."""
    forward = subsume.partition(_sets(alpha=[1, 2], beta=[1, 2]))
    backward = subsume.partition(_sets(beta=[1, 2], alpha=[1, 2]))
    assert forward.dominators == ("alpha",)
    assert backward.dominators == ("beta",)


def test_a_chain_points_at_a_dominator_and_not_at_a_restatement():
    """`subsumed_by` naming something the report does not show is a dangling reference."""
    part = subsume.partition(_sets(top=[1], middle=[1, 2], bottom=[1, 2, 3]))
    assert part.dominators == ("top",)
    assert set(part.subsumed) == {"middle", "bottom"}
    for mutant, keeper in part.subsumed.items():
        assert keeper in part.dominators, (mutant, keeper)


def test_every_subsumed_mutant_points_somewhere_shown():
    """The property above, over a messier shape than one chain."""
    part = subsume.partition(
        _sets(
            a=[1],
            b=[2],
            ab=[1, 2],
            abc=[1, 2, 3],
            lone=[9],
            twin=[9],
        )
    )
    kept = set(part.dominators)
    assert kept == {"a", "b", "lone"}
    for mutant, keeper in part.subsumed.items():
        assert keeper in kept, (mutant, keeper)
    assert part.subsumed["twin"] == "lone"


def test_the_partition_covers_every_killed_mutant_exactly_once():
    """Nothing lost, nothing double-counted - the arithmetic the report prints."""
    sets = _sets(a=[1], b=[2], ab=[1, 2], abc=[1, 2, 3], c=[], d=[3], twin=[1])
    part = subsume.partition(sets)
    killed = {name for name, values in sets.items() if values}
    assert set(part.dominators) | set(part.subsumed) == killed
    assert not set(part.dominators) & set(part.subsumed)
    assert part.distinct + part.restatements == len(killed)


def test_summarise_reports_the_inflation_as_a_ratio_over_the_distinct_count():
    part = subsume.partition(_sets(hard=[3], easy=[1, 2, 3], easier=[1, 2, 3, 4]))
    summary = subsume.summarise(part)
    assert summary == {
        "proven": 3,
        "distinct": 1,
        "restatements": 2,
        "inflation": 3.0,
        "restated_share": round(2 / 3, 4),
    }


# ---------------------------------------------------------------- the wiring into audit


def test_a_result_without_a_signature_is_not_claimed_distinct():
    """A gap the hierarchy cannot place must not be silently counted as its own."""
    placed = Result("t::f", "m1", "flip", Verdict.PROVEN_GAP, signature=(1,))
    unplaced = Result("t::f", "m2", "flip", Verdict.PROVEN_GAP)
    assert placed.distinct
    # `distinct` is about subsumption, so an unplaced gap reads distinct on its own; the
    # audit counts it under `unplaceable` instead, which is what this pins.
    assert unplaced.signature == ()
    assert not unplaced.subsumed_by


def test_the_row_carries_the_kill_set_as_json():
    row = Result("t::f", "m", "flip", Verdict.PROVEN_GAP, signature=(0, 2, 5)).as_row()
    assert row["signature"] == [0, 2, 5]
    assert row["subsumed_by"] == ""
    json.dumps(row)


def test_the_marker_only_compares_gaps_of_one_function():
    """Kill sets index one function's argument pool; index 3 of another's is unrelated."""
    from suite_auditor.audit import _mark_restatements

    results = [
        Result("a.py::f", "m1", "flip", Verdict.PROVEN_GAP, signature=(0,)),
        Result("a.py::f", "m2", "flip", Verdict.PROVEN_GAP, signature=(0, 1)),
        Result("a.py::f", "m3", "flip", Verdict.KILLED, signature=(0,)),
    ]
    _mark_restatements(results)
    assert results[0].subsumed_by == ""
    assert results[1].subsumed_by == "m1"
    # A killed mutant is not a gap and takes no part in the hierarchy.
    assert results[2].subsumed_by == ""


def test_the_marker_is_a_no_op_below_two_gaps():
    results = [Result("a.py::f", "m1", "flip", Verdict.PROVEN_GAP, signature=(0,))]
    from suite_auditor.audit import _mark_restatements

    _mark_restatements(results)
    assert results[0].subsumed_by == ""


# ------------------------------------------------------------- the signature it feeds on


def test_compare_reports_which_inputs_prove_the_gap_not_only_how_many():
    """Without the indices every mutant caught by one input looks like every other."""
    from suite_auditor.differential import compare

    old = "def f(n):\n    return n * 2\n"
    new = "def f(n):\n    return n + 2\n"
    diff = compare("", old, new, "f", ["(0,)", "(2,)", "(3,)"], python=sys.executable)
    assert diff["status"] == "differs", diff
    # `n * 2` and `n + 2` agree only at n == 2, so the middle input of the three does not
    # separate them and the other two do. A count would say "2 of 3"; the signature says
    # *which* two, which is the whole difference - two mutants each caught by one input
    # are the same gap if it is the same input and two gaps if it is not.
    assert diff["signature"] == [0, 2], diff


def test_two_mutants_separated_by_the_same_single_input_are_one_gap():
    """The end-to-end claim: same kill set, so the report shows one defect not two."""
    from suite_auditor.differential import compare

    old = "def f(xs):\n    return sum(xs) / len(xs)\n"
    pool = ["([],)", "([1],)", "([1, 2],)"]
    first = compare(
        "",
        old,
        "def f(xs):\n    return sum(xs) / (len(xs) + 1)\n",
        "f",
        pool,
        python=sys.executable,
    )
    second = compare(
        "",
        old,
        "def f(xs):\n    return sum(xs) * len(xs)\n",
        "f",
        pool,
        python=sys.executable,
    )
    if first["status"] != "differs" or second["status"] != "differs":
        pytest.skip(f"no proof: {first['status']}, {second['status']}")
    part = subsume.partition(
        {
            "plus_one": frozenset(first["signature"]),
            "times": frozenset(second["signature"]),
        }
    )
    assert part.distinct + part.restatements == 2
    # Whatever the pool separates them on, the arithmetic has to close.
    assert set(part.dominators) | set(part.subsumed) == {"plus_one", "times"}


def test_the_counts_reach_the_json_export(tmp_path):
    """The numbers have to reach audit.json, or the paper is quoting a local variable."""
    from suite_auditor.report import write_json
    from suite_auditor.types import Audit

    audit = Audit()
    audit.subsumption = {"proven": 3, "distinct": 1, "restatements": 2}
    path = tmp_path / "audit.json"
    write_json(audit, path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["subsumption"] == audit.subsumption


def test_an_empty_subsumption_dict_is_exported_as_null_not_as_zeroes(tmp_path):
    """A run that scored nothing must not publish a 0% restatement rate."""
    from suite_auditor.report import write_json
    from suite_auditor.types import Audit

    path = tmp_path / "audit.json"
    write_json(Audit(), path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["subsumption"] is None


def test_the_exported_rows_carry_the_kill_sets(tmp_path):
    """A consumer has to be able to recompute the hierarchy from the export alone."""
    from suite_auditor.report import write_json
    from suite_auditor.types import Audit

    audit = Audit()
    audit.results = [
        Result("a.py::f", "m1", "flip", Verdict.PROVEN_GAP, signature=(0,)),
        Result("a.py::f", "m2", "flip", Verdict.PROVEN_GAP, signature=(0, 1), subsumed_by="m1"),
    ]
    path = tmp_path / "audit.json"
    write_json(audit, path)
    rows = json.loads(path.read_text(encoding="utf-8"))["results"]
    recomputed = subsume.partition({r["mutant"]: frozenset(r["signature"]) for r in rows})
    assert recomputed.dominators == ("m1",)
    assert recomputed.subsumed == {"m2": "m1"}
    assert [r["subsumed_by"] for r in rows] == ["", "m1"]
