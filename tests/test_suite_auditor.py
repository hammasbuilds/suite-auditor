"""Tests for the machinery that decides. No model, no network.

The failure that matters for this tool is **a gap reported that is not one**, or a number
that quietly counts equivalent mutants as evidence. A maintainer who closes one false report
does not read the second, so these mostly guard the line between "survived" and "proven".
"""

from __future__ import annotations

import ast
import textwrap

from suite_auditor.audit import patch_file
from suite_auditor.coverage import tests_for as covering_tests
from suite_auditor.differential import _looks_nondeterministic, compare
from suite_auditor.inputs import argument_sets, harvest_for, witness_strength
from suite_auditor.mutate import OPERATORS, find_targets, mutants
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
    assert kinds <= OPERATORS, f"unnamed operator: {sorted(kinds - OPERATORS)}"
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
    assert r["witness"]["args"] == "(2)"  # rendered as the call: f(2)


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


def test_a_function_that_disagrees_with_its_own_repeat_is_not_a_gap():
    # random.randint(1, 6) disagreeing with itself between two calls is not the mutant
    # doing anything - it is what this function always does. Repro from a real audit:
    # `dice()` reported 2, 3 and 3 "unarguable" proven gaps on three re-runs of
    # byte-identical code.
    old = "def dice(n=0):\n    import random\n    return random.randint(1, 6) + n\n"
    new = "def dice(n=0):\n    import random\n    return random.randint(1, 6) + n + 1\n"
    r = compare("", old, new, "dice", ["(0,)"] * 20)
    assert r["status"] != "differs"


def test_uuid_and_clock_calls_are_also_recognised_as_nondeterministic():
    assert _looks_nondeterministic("def f():\n    import uuid\n    return uuid.uuid4()\n")
    assert _looks_nondeterministic("def f():\n    import time\n    return time.time()\n")
    assert _looks_nondeterministic(
        "def f():\n    from datetime import datetime\n    return datetime.now()\n"
    )
    assert not _looks_nondeterministic("def f(x):\n    return x * 2\n")
    # A local variable that merely happens to share a name with one of the flagged
    # calls (not called as one) must not trip this - it costs sensitivity for nothing.
    assert not _looks_nondeterministic("def f(time):\n    return time + 1\n")


def test_a_genuinely_deterministic_disagreement_is_still_a_gap():
    # The fix must not make the tool blind to real gaps - only to noise from a
    # function whose own source calls something nondeterministic.
    old = "def f(n):\n    return n > 2\n"
    new = "def f(n):\n    return n >= 2\n"
    r = compare("", old, new, "f", ["(2,)", "(5,)"])
    assert r["status"] == "differs"


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
    baseline = sets[0].pos
    for a in sets[1:]:
        differing = sum(x != y for x, y in zip(a.pos, baseline, strict=True))
        assert differing <= 1, f"{a.pos} differs from {baseline} in {differing} places"


def test_self_is_not_an_argument():
    fn = ast.parse("def m(self, x):\n    pass\n").body[0]
    assert len(argument_sets(fn, {}, cap=3)[0].pos) == 1


def test_a_function_with_no_parameters_gives_an_empty_call():
    sets = argument_sets(ast.parse("def f():\n    pass\n").body[0], {})
    assert [a.display() for a in sets] == ["()"]


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

    f.write_bytes(before)
    assert f.read_text(encoding="utf-8") == src + "\n"


# --- the target's own suite has to be healthy for any of this to mean anything ---
#
# build_map runs the TARGET repository's tests and records what they executed.
# The result of that subprocess used to be thrown away, so a target whose tests
# could not be imported produced an empty map and the report announced that no
# test reaches any function in the package. Total failure and total absence of
# coverage are indistinguishable from the map alone, and they call for opposite
# responses.


class TestSuiteHealth:
    def test_a_clean_run_is_clean(self):
        from suite_auditor.coverage import _read_health

        h = _read_health("192 passed in 1.0s", "", 0)
        assert h.clean
        assert h.passed == 192
        assert h.caveat() == ""

    def test_a_failing_test_makes_the_unreached_count_untrustworthy(self):
        """A failed test executes nothing after the point it failed, so every
        function it would have reached is counted as unreached."""
        from suite_auditor.coverage import _read_health

        h = _read_health("1 failed, 191 passed, 1 skipped in 1.03s", "", 1)
        assert not h.clean
        assert h.passed == 191 and h.failed == 1
        assert "failed" in h.caveat()

    def test_an_erroring_test_is_reported_too(self):
        from suite_auditor.coverage import _read_health

        h = _read_health("2 errors in 0.4s", "", 1)
        assert not h.clean
        assert h.errors == 2
        assert "errored" in h.caveat()

    def test_collecting_nothing_is_its_own_condition(self):
        """pytest exits 5 when it found no tests. That is a different problem
        from a suite that ran and failed, and it needs a different message."""
        from suite_auditor.coverage import _read_health

        h = _read_health("no tests ran in 0.01s", "", 5)
        assert not h.clean
        assert h.collected_nothing
        assert "collected no tests" in h.caveat()

    def test_a_suite_that_never_ran_says_so(self):
        from suite_auditor.coverage import SuiteHealth

        h = SuiteHealth(ran=False, exit_code=-1)
        assert not h.clean
        assert "did not run at all" in h.caveat()


class TestBuildMapReportsHealth:
    def test_a_repo_with_no_tests_returns_an_empty_map_and_says_why(self, tmp_path):
        """The case that was silently wrong.

        Nothing to collect, so nothing is traced. Without the health record the
        caller sees an empty map and cannot tell that apart from a suite that
        ran perfectly and covered nothing.
        """
        from suite_auditor.coverage import build_map

        (tmp_path / "mod.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
        cov, health = build_map(tmp_path, timeout=120)
        assert cov == {}
        assert not health.clean
        assert health.caveat()

    def test_a_repo_whose_tests_pass_traces_them_and_reports_clean(self, tmp_path):
        from suite_auditor.coverage import build_map

        (tmp_path / "mod.py").write_text("def f(x):\n    return x + 1\n", encoding="utf-8")
        (tmp_path / "test_mod.py").write_text(
            "from mod import f\n\n\ndef test_f():\n    assert f(1) == 2\n", encoding="utf-8"
        )
        cov, health = build_map(tmp_path, timeout=120)
        assert health.ran
        assert health.passed >= 1
        assert health.clean
        assert any(k.endswith("::f") for k in cov), cov

    def test_a_repo_whose_tests_fail_is_traced_but_not_clean(self, tmp_path):
        """Coverage is still collected - a failing test runs code before it
        fails - but the run is flagged, because what it did NOT reach is now
        a property of the failure rather than of the suite's design."""
        from suite_auditor.coverage import build_map

        (tmp_path / "mod.py").write_text(
            "def f(x):\n    return x + 1\n\n\ndef g(x):\n    return x * 2\n", encoding="utf-8"
        )
        (tmp_path / "test_mod.py").write_text(
            "from mod import f\n\n\ndef test_f():\n    assert f(1) == 99\n", encoding="utf-8"
        )
        cov, health = build_map(tmp_path, timeout=120)
        assert health.ran
        assert health.failed >= 1
        assert not health.clean


def test_a_high_kill_rate_over_a_narrow_base_cannot_hide():
    """The stated weakness, made visible.

    `kill_rate` is a rate over the functions a test reaches, because only those
    are ever mutated. A suite covering one function out of ten and killing every
    mutant in it reports 100% - the same number a suite covering all ten and
    killing everything reports. `caught_share` multiplies by the covered fraction,
    so the only way to raise it is to test more.
    """
    from suite_auditor.types import Audit, Result, Verdict

    narrow = Audit(
        results=[Result("pkg.a", "x + 1", "arithmetic", Verdict.KILLED)],
        uncovered=[f"pkg.f{i}" for i in range(9)],
        covered_total=1,
    )
    wide = Audit(
        results=[Result(f"pkg.f{i}", "x + 1", "arithmetic", Verdict.KILLED) for i in range(10)],
        uncovered=[],
        covered_total=10,
    )

    assert narrow.kill_rate == 1.0
    assert wide.kill_rate == 1.0, "kill rate alone cannot tell these apart"

    assert narrow.covered_fraction == 0.1
    assert wide.covered_fraction == 1.0
    assert narrow.caught_share == 0.1
    assert wide.caught_share == 1.0


def test_caught_share_is_none_when_there_is_nothing_to_divide_by():
    from suite_auditor.types import Audit

    empty = Audit()
    assert empty.kill_rate is None
    assert empty.covered_fraction is None
    assert empty.caught_share is None


def test_a_single_module_library_is_found(tmp_path):
    """A distribution whose whole library is `src/thing.py`, with no __init__.py.

    The "must be inside a package" rule skipped these entirely, so the audit found
    0 functions, scored 0 mutants and reported a kill rate of None - which in a
    table of results is indistinguishable from a library with nothing worth
    mutating. docstring-drift is exactly this shape: 5 functions in src/drift.py,
    42 tests, and an audit that said nothing about either.
    """
    from suite_auditor.mutate import find_targets

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "thing.py").write_text(
        "def double(x):\n    return x * 2\n", encoding="utf-8"
    )
    # Not the library: excluded because its directory is neither a package nor src/.
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "demo.py").write_text("def ignore_me():\n    pass\n", encoding="utf-8")

    keys = [t.key for t in find_targets(tmp_path)]
    assert "src/thing.py::double" in keys
    assert not any("examples" in k for k in keys), "examples/ is not the library"


def test_a_flat_root_module_with_no_src_at_all_is_found(tmp_path):
    """A distribution whose whole library is `tool.py` at the true repo root - no
    `src/` directory anywhere. A common shape for a small script or a single-file
    library. `suite-auditor coverage .` on exactly this shape reported "no functions
    found" until this was recognised as a third shipped-code shape alongside a
    package and a src/-module distribution.
    """
    from suite_auditor.mutate import find_targets

    (tmp_path / "tool.py").write_text("def greet(name):\n    return name\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_tool.py").write_text(
        "def test_it():\n    assert True\n", encoding="utf-8"
    )

    keys = [t.key for t in find_targets(tmp_path)]
    assert "tool.py::greet" in keys


def test_a_root_level_script_beside_a_real_src_package_is_not_swept_in(tmp_path):
    """The flat-root rule is gated on 'no src/ at all' precisely so it does not treat
    every stray demo.py or setup.py next to a real package as shipped code.
    """
    from suite_auditor.mutate import find_targets

    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "pkg" / "m.py").write_text(
        "def real(n):\n    return n > 0\n", encoding="utf-8"
    )
    (tmp_path / "demo.py").write_text("def not_the_library():\n    pass\n", encoding="utf-8")

    keys = [t.key for t in find_targets(tmp_path)]
    assert "src/pkg/m.py::real" in keys
    assert not any("demo.py" in k for k in keys), "a root script beside src/ is not the library"


def test_an_audit_with_nothing_to_mutate_says_so(tmp_path):
    """Silence here reads as a clean bill of health, and it is the opposite."""
    from suite_auditor.report import summary
    from suite_auditor.types import Audit

    empty = Audit(no_targets=True)
    out = summary(empty, "somelib")

    assert "NO FUNCTIONS WERE MUTATED" in out
    assert "nothing below is a statement about" in out


def test_a_truncated_audit_says_it_is_partial():
    """A rate over half the functions is not a rate over the package. Stopping early is
    safe - every figure is a rate over what was scored - but only while the truncation
    is visible."""
    from suite_auditor.report import summary
    from suite_auditor.types import Audit, Result, Verdict

    partial = Audit(
        results=[Result("pkg.a", "x + 1", "binop", Verdict.KILLED)],
        uncovered=["pkg.z"],
        covered_total=20,
        audited_functions=1,
        stopped_early=True,
    )
    out = summary(partial, "somelib")

    assert "PARTIAL" in out
    assert "1 of 20 covered functions" in out


def test_a_complete_audit_does_not_claim_to_be_partial():
    from suite_auditor.report import summary
    from suite_auditor.types import Audit, Result, Verdict

    whole = Audit(
        results=[Result("pkg.a", "x + 1", "binop", Verdict.KILLED)],
        covered_total=1,
        audited_functions=1,
    )
    assert "PARTIAL" not in summary(whole, "somelib")


def test_a_partial_audit_counts_mutants_not_just_functions():
    """With a 1-second budget on examples/pricing, all three functions were started and 3
    of 17 mutants scored, and the report said "after 3 of 3 covered functions" - which
    reads as complete. The mutant count is what says how partial it is."""
    from suite_auditor.report import summary
    from suite_auditor.types import Audit, Result, Verdict

    partial = Audit(
        results=[Result("pkg.a", "x + 1", "binop", Verdict.KILLED)] * 3,
        covered_total=3,
        audited_functions=3,
        planned_mutants=17,
        stopped_early=True,
    )
    assert "after 3 of 17 planned mutants, in 3 of 3 covered functions" in summary(
        partial, "somelib"
    )


def test_the_nondeterminism_guard_matches_the_module_not_only_the_method():
    """`random.randint` was the one call it missed, and the only one it documented.

    `_NONDETERMINISTIC_CALLS` held `random` - which matches a bare `random()` - and not
    `randint`, so `random.randint(1, 6)` reached the runtime repeat check as its only
    guard. That check is probabilistic: two draws from a six-sided die agree one time in
    six, both sides' repeats agree one time in thirty-six, and the prover reported a
    proven gap on byte-identical behaviour **1.5% of the time** (measured over 200 runs).

    That made the test above this one flaky at 1.5%, which flake-detective found while
    being run over this repository and attributed to test ORDER - correctly, in the sense
    that one flip in one arm out of five runs is what an order dependence also looks like.
    """
    for source in (
        "def f():\n    import random\n    return random.randint(1, 6)\n",
        "def f(xs):\n    import random\n    return random.choice(xs)\n",
        "def f(xs):\n    import random\n    random.shuffle(xs)\n    return xs\n",
        "def f():\n    import random\n    return random.uniform(0, 1)\n",
        "def f():\n    import secrets\n    return secrets.token_hex(4)\n",
        "def f():\n    import time\n    return time.monotonic_ns()\n",
    ):
        assert _looks_nondeterministic(source), source

    # And a local variable that merely shares a name with one of those modules must not
    # trip it - the cost of being wrong that way is sensitivity, for nothing.
    assert not _looks_nondeterministic("def f(random):\n    return random + 1\n")
    assert not _looks_nondeterministic("def f(time):\n    return time * 2\n")
    assert not _looks_nondeterministic("def f(x):\n    return x * 2\n")


def test_a_low_entropy_random_function_never_reports_a_proven_gap():
    """The regression test for the 1.5%, run enough times to have seen it.

    40 comparisons of a six-sided die against itself-plus-one. Before the guard matched
    the module, this failed about one run in sixty-seven; the source-level rule makes it
    impossible rather than unlikely, so a single pass here is not what makes this test
    worth having - it is that the rule cannot be probabilistic any more.
    """
    old = "def dice(n=0):\n    import random\n    return random.randint(1, 6) + n\n"
    new = "def dice(n=0):\n    import random\n    return random.randint(1, 6) + n + 1\n"
    for _ in range(40):
        assert compare("", old, new, "dice", ["(0,)"] * 20)["status"] != "differs"


def test_a_leading_cls_on_a_module_function_is_not_a_receiver():
    """`_restore_curry(cls, func, args, kwargs, userdict, is_decorated)` is a function.

    toolz has a module-level function whose first parameter is named `cls`. Both the
    argument generator and the call recorder dropped it as a receiver, so every generated
    call and every observed call was one argument short. All 64 of them raised
    `missing 1 required positional argument`, and the audit reported "no valid input
    could be built" - which reads as a limitation of the tool's reach, for what was a
    miscounted call.

    A dot in the qualified name is what makes a function a method, in the AST (the
    target's name) and at runtime (`code.co_qualname`).
    """
    import ast

    from suite_auditor.inputs import _params

    source = "def _restore_curry(cls, func, args, kwargs, userdict, is_decorated):\n    pass\n"
    fn = ast.parse(source).body[0]
    as_function, _ = _params(fn, is_method=False)
    as_method, _ = _params(fn, is_method=True)
    assert as_function[0] == "cls" and len(as_function) == 6
    assert as_method[0] == "func" and len(as_method) == 5

    # And a real method still loses its receiver.
    method = ast.parse("def m(self, x):\n    pass\n").body[0]
    assert _params(method, is_method=True)[0] == ["x"]


def test_the_recorder_keeps_a_leading_cls_on_a_plain_function():
    """The other half: observed calls were recorded one argument short too.

    An observed arity that disagrees with the signature makes `argument_sets` fall back
    to the observed sets alone, so a miscount here poisons every call for that function -
    which is how 64 generated sets became 21 observed ones, all of them invalid.

    The recorder lives in `PLUGIN`, a source template written to a temporary directory
    and never imported, so the template is executed here and the function called out of
    it. Two earlier versions of this test were worse: one read the template's text through
    a hard-coded absolute path and asserted that certain characters were present, and one
    imported the function from the module, which has never had it.
    """
    import ast

    from suite_auditor.coverage import PLUGIN

    # Only this function out of the template. Executing the whole plugin raises KeyError
    # from `os.environ`: it reads the variables the audit sets for it, which is right for
    # a plugin and makes it unusable as a unit under test.
    tree = ast.parse(PLUGIN)
    node = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "takes_a_receiver"
    )
    namespace: dict = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "<plugin>", "exec"), namespace)
    takes_a_receiver = namespace["takes_a_receiver"]

    def plain(cls, func, args):  # a module-level shape: `cls` is an argument
        return cls, func, args

    class Holder:
        def method(self, x):
            return x

        @classmethod
        def maker(cls, x):
            return x

    assert takes_a_receiver(plain.__code__) is False
    assert takes_a_receiver(Holder.method.__code__) is True
    assert takes_a_receiver(Holder.maker.__code__) is True

    # And the case this test's own fixture exposed: `plain` is nested inside this test, so
    # its qualname is `test_...<locals>.plain` - a dot, and not a method. "Contains a dot"
    # was the first rule and it called that a method.
    assert "<locals>" in plain.__code__.co_qualname
    assert "." in plain.__code__.co_qualname
    # A method of a class defined inside a function is still a method.
    assert takes_a_receiver(Holder.method.__code__) is True
    assert "<locals>" in Holder.method.__code__.co_qualname


def test_a_parameter_named_args_is_offered_something_unpackable():
    """A function doing `f(*args, **kwargs)` needs a tuple and a dict, not literals.

    Same idea as the callable pool: a parameter whose NAME says what shape it takes gets
    that shape first, because a pool harvested from literals never produces one.
    """
    import ast

    from suite_auditor.inputs import argument_sets

    fn = ast.parse("def f(args, kwargs):\n    return len(args) + len(kwargs)\n").body[0]
    sets = argument_sets(fn, {}, is_method=False)
    first = {s.pos[0] for s in sets if s.pos}
    second = {s.pos[1] for s in sets if len(s.pos) > 1}
    assert any(v.startswith(("(", "[")) for v in first), first
    assert any(v.startswith("{") for v in second), second
