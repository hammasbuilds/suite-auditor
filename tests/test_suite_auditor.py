"""Tests for the machinery that decides. No model, no network.

The failure that matters for this tool is **a gap reported that is not one**, or a number
that quietly counts equivalent mutants as evidence. A maintainer who closes one false report
does not read the second, so these mostly guard the line between "survived" and "proven".
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

from suite_auditor.audit import patch_file
from suite_auditor.coverage import tests_for as covering_tests
from suite_auditor.differential import compare
from suite_auditor.inputs import argument_sets, harvest_for, witness_strength
from suite_auditor.mutate import find_targets, mutants
from suite_auditor.types import Audit, Result, Target, Verdict

# --- mutation ---------------------------------------------------------------------------


def test_mutants_are_distinct_and_exclude_the_original():
    src = "def f(n):\n    if n > 0:\n        return n + 1\n    return 0\n"
    ms = mutants(src)
    assert len(ms) >= 3
    texts = [m for m, _ in ms]
    assert len(set(texts)) == len(texts)
    assert ast.unparse(ast.parse(src)) not in texts


def test_each_mutant_names_its_operator():
    src = "def f(n):\n    if n > 0:\n        return n + 1\n    return 0\n"
    kinds = {k for _, k in mutants(src)}
    assert kinds <= {"compare", "binop", "boolop", "const", "negate_if"}
    assert kinds


def test_booleans_are_not_mutated_as_integers():
    # `True` is an int in Python. Turning it into 2 is not a mistake anyone makes, and
    # the resulting mutant teaches nothing.
    src = "def f():\n    return True\n"
    assert all("2" not in m for m, k in mutants(src) if k == "const")


def test_a_function_with_no_mutable_site_yields_nothing():
    assert mutants("def f():\n    return None\n") == []


def test_unparseable_source_yields_nothing():
    assert mutants("def f(:\n") == []


# --- targets ----------------------------------------------------------------------------


def test_methods_are_targets_named_class_dot_method(tmp_path):
    pkg = tmp_path / "lib"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "m.py").write_text(
        "class A:\n    def go(self, n):\n        return n > 0\n\ndef free(n):\n    return n > 0\n",
        encoding="utf-8",
    )
    names = {t.name for t in find_targets(tmp_path)}
    assert names == {"A.go", "free"}


def test_code_outside_the_package_is_not_a_target(tmp_path):
    # `examples/` is not the library. A gap reported there is noise in the report.
    pkg = tmp_path / "lib"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "m.py").write_text("def inside(n):\n    return n > 0\n", encoding="utf-8")
    ex = tmp_path / "examples"
    ex.mkdir()
    (ex / "demo.py").write_text("def outside(n):\n    return n > 0\n", encoding="utf-8")

    assert {t.name for t in find_targets(tmp_path)} == {"inside"}


def test_tests_are_not_targets(tmp_path):
    pkg = tmp_path / "lib"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "m.py").write_text("def real(n):\n    return n > 0\n", encoding="utf-8")
    (pkg / "test_m.py").write_text("def test_real():\n    assert True\n", encoding="utf-8")
    assert {t.name for t in find_targets(tmp_path)} == {"real"}


# --- the proof step ---------------------------------------------------------------------


def test_a_survivor_that_really_differs_gets_a_witness():
    old = "def f(n):\n    return n > 2\n"
    new = "def f(n):\n    return n >= 2\n"
    r = compare("", old, new, "f", ["(2,)", "(5,)"])
    assert r["status"] == "differs"
    assert r["witness"]["args"] == "(2,)"


def test_an_equivalent_mutant_is_not_a_gap():
    # No input separates these, so no gap may be claimed. Counting survivors without
    # this check is how a mutation score becomes an inflated bug count.
    old = "def f(n):\n    return n * 1\n"
    new = "def f(n):\n    return n\n"
    assert compare("", old, new, "f", ["(1,)", "(0,)", "(-3,)"])["status"] == "agree"


def test_a_drained_iterator_that_raised_is_not_a_clean_return():
    # A lazy iterator is drained before comparison, so a generator that yields nothing and
    # then raises renders as `ok: []...then TypeError: ...`. Reading only the `ok` prefix
    # promoted the single "unarguable" gap in a real audit to the top of the report when
    # both sides had in fact raised.
    both_raised_late = {
        "old": "ok: []...then TypeError: '<' not supported",
        "new": "ok: []...then TypeError: '<=' not supported",
    }
    assert witness_strength(both_raised_late) == 2

    one_drained_cleanly = {"old": "ok: [1, 2]", "new": "ok: []...then ValueError: x"}
    assert witness_strength(one_drained_cleanly) == 1


def test_witness_strength_ranks_two_values_above_an_exception():
    both_ok = {"old": "ok: [1, 2]", "new": "ok: [2, 1]"}
    one_ok = {"old": "ok: ()", "new": "raise: AttributeError: ..."}
    neither = {"old": "raise: TypeError: ...", "new": "raise: ValueError: ..."}
    assert witness_strength(both_ok) == 0
    assert witness_strength(one_ok) == 1
    assert witness_strength(neither) == 2
    assert witness_strength(None) == 3


def test_gaps_are_ordered_by_strength():
    a = Audit()
    a.results = [
        Result(
            "x::c", "", "const", Verdict.PROVEN_GAP, {"old": "raise: a", "new": "raise: b"}, "", 2
        ),
        Result("x::a", "", "compare", Verdict.PROVEN_GAP, {"old": "ok: 1", "new": "ok: 2"}, "", 0),
        Result("x::b", "", "binop", Verdict.PROVEN_GAP, {"old": "ok: 1", "new": "raise: e"}, "", 1),
    ]
    assert [g.target for g in a.gaps] == ["x::a", "x::b", "x::c"]
    assert [g.target for g in a.strong_gaps] == ["x::a"]


# --- accounting -------------------------------------------------------------------------


def test_skipped_mutants_are_not_evidence_either_way():
    # A mutant that could not be scored is not a kill and not a survivor. Including it in
    # the denominator would move the kill rate for a reason unrelated to the tests.
    a = Audit()
    a.results = [
        Result("x::f", "", "compare", Verdict.KILLED),
        Result("x::f", "", "const", Verdict.SKIPPED),
        Result("x::f", "", "binop", Verdict.PROVEN_GAP, {"old": "ok: 1", "new": "ok: 2"}, "", 0),
    ]
    assert len(a.scored) == 2
    assert a.kill_rate == 0.5


def test_kill_rate_of_nothing_is_none_not_zero():
    assert Audit().kill_rate is None


def test_unproven_survivors_are_not_counted_as_gaps():
    a = Audit()
    a.results = [
        Result("x::f", "", "compare", Verdict.UNPROVEN, detail="no separating input"),
        Result("x::f", "", "const", Verdict.KILLED),
    ]
    assert a.gaps == []
    assert a.counts()["unproven"] == 1


# --- coverage lookup ----------------------------------------------------------------------


def test_a_method_is_found_by_its_bare_name():
    # settrace reports a method's own co_name, not Class.method. Missing this makes every
    # method look uncovered, and silently drops a whole category of function.
    cov = {"lib/m.py::go": ["tests/test_m.py::test_go"]}
    assert covering_tests(cov, "lib/m.py", "A.go") == ["tests/test_m.py::test_go"]


def test_a_free_function_is_found_directly():
    cov = {"lib/m.py::free": ["t::a"]}
    assert covering_tests(cov, "lib/m.py", "free") == ["t::a"]


def test_an_uncovered_function_returns_nothing():
    assert covering_tests({}, "lib/m.py", "free") == []


# --- inputs -------------------------------------------------------------------------------


def test_values_come_from_the_covering_tests(tmp_path):
    # The whole point: a repo-wide pool handed a function expecting signature objects the
    # argument (0, 0), and the resulting "proof" was one a maintainer would close on sight.
    t = tmp_path / "tests"
    t.mkdir()
    (t / "test_a.py").write_text(
        "def test_x():\n    assert parse(text='SELECT 1', limit=99)\n", encoding="utf-8"
    )
    (t / "test_b.py").write_text(
        "def test_y():\n    assert parse(text='unrelated')\n", encoding="utf-8"
    )
    pool = harvest_for(tmp_path, ["tests/test_a.py::test_x"])
    assert "'SELECT 1'" in pool["text"]
    assert "99" in pool["limit"]
    # test_b was not among the covering tests, so its values are not in the pool.
    assert "'unrelated'" not in pool.get("text", [])


def test_argument_sets_vary_one_parameter_at_a_time():
    fn = ast.parse("def f(a, b):\n    pass\n").body[0]
    sets = argument_sets(fn, {}, cap=8)

    def parts(call: str) -> list[str]:
        # Parsed, not split on ", ": a value can contain a comma of its own, and the
        # naive split then reports two differences where there is one.
        return [ast.unparse(e) for e in ast.parse(call).body[0].value.elts]

    baseline = parts(sets[0])
    for call in sets[1:]:
        differing = sum(x != y for x, y in zip(parts(call), baseline, strict=True))
        assert differing <= 1, f"{call} differs from {sets[0]} in {differing} places"


def test_self_is_not_an_argument():
    fn = ast.parse("def m(self, x):\n    pass\n").body[0]
    assert argument_sets(fn, {}, cap=3)[0].count(",") == 1


def test_a_function_with_no_parameters_gives_an_empty_call():
    assert argument_sets(ast.parse("def f():\n    pass\n").body[0], {}) == ["()"]


# --- patching -----------------------------------------------------------------------------


def test_a_method_mutant_is_written_back_at_the_right_indentation(tmp_path):
    # The mutant arrives dedented from ast.unparse. Writing it straight into a class body
    # is a SyntaxError, and every mutant of every method would score as killed - for a
    # reason that has nothing to do with the tests.
    src = textwrap.dedent("""
        class A:
            def go(self, n):
                return n > 0
    """).strip()
    f = tmp_path / "m.py"
    f.write_text(src + "\n", encoding="utf-8")

    target = Target(
        path="m.py",
        name="A.go",
        source="    def go(self, n):\n        return n > 0",
        lineno=2,
        end_lineno=3,
    )
    before = patch_file(tmp_path, target, "def go(self, n):\n    return n >= 0")
    out = f.read_text(encoding="utf-8")
    assert ast.parse(out)  # still valid Python
    assert "n >= 0" in out

    Path(tmp_path / "m.py").write_text(before, encoding="utf-8", newline="")
    assert f.read_text(encoding="utf-8") == src + "\n"
