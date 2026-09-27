"""Regression tests for problems a user hit, each one found by running the tool as a user.

- the audit rewrote a clean CRLF file as LF, and wrote mutants onto the real files;
- a correct, fully tested suite was reported with "proven gaps", because inputs were
  scraped from the text of `@pytest.mark.parametrize` and because "both raise,
  differently" on an injected `''` was counted as proof;
- a missing pytest, a bad path and "nothing scored" were a traceback or an exit 0;
- `latest.py` was skipped as if it were a test.
"""

from __future__ import annotations

import ast
import hashlib
import sys
import textwrap
from pathlib import Path

import pytest

from suite_auditor.audit import audit, audit_target, patch_file
from suite_auditor.coverage import PLUGIN, build_map
from suite_auditor.differential import compare
from suite_auditor.inputs import (
    GENERATED,
    OBSERVED,
    RECOMBINED,
    ArgSet,
    _literals_from,
    argument_sets,
    eligible,
)
from suite_auditor.mutate import find_targets, mutants
from suite_auditor.types import Target, Verdict
from suite_auditor.workspace import is_test_path

CRLF_MODULE = (
    "def clamp(x, lo=0, hi=10):\r\n"
    "    if x < lo:\r\n"
    "        return lo\r\n"
    "    if x > hi:\r\n"
    "        return hi\r\n"
    "    return x\r\n"
    "\r\n"
    "\r\n"
    "def grade(pct):\r\n"
    "    if pct >= 90:\r\n"
    '        return "A"\r\n'
    "    if pct > 50:\r\n"
    '        return "pass"\r\n'
    '    return "fail"\r\n'
    "\r\n"
    "\r\n"
    "def double_all(xs):\r\n"
    "    return [x * 2 for x in xs]\r\n"
)

SOLID_TESTS = textwrap.dedent(
    """
    import pytest
    from calc.core import clamp, grade, double_all


    @pytest.mark.parametrize("x,exp", [(-1, 0), (0, 0), (1, 1), (10, 10), (11, 10)])
    def test_clamp(x, exp):
        assert clamp(x) == exp


    @pytest.mark.parametrize(
        "pct,exp", [(100, "A"), (90, "A"), (89, "pass"), (51, "pass"), (50, "fail")]
    )
    def test_grade(pct, exp):
        assert grade(pct) == exp


    def test_double_all():
        # Called only with an EMPTY list - so a mutant of the multiplication survives
        # and the proof has to come from somewhere other than this call.
        assert double_all([]) == []
    """
)


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def _project(root: Path, tests: str = SOLID_TESTS) -> Path:
    (root / "calc").mkdir(parents=True)
    (root / "calc" / "__init__.py").write_bytes(b"")
    (root / "calc" / "core.py").write_bytes(CRLF_MODULE.encode())
    (root / "tests").mkdir()
    (root / "tests" / "test_core.py").write_text(tests, encoding="utf-8")
    return root


def _tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): _md5(p) for p in sorted(root.rglob("*")) if p.is_file()}


# --- 1. the user's files ------------------------------------------------------------------


def test_patching_a_crlf_file_keeps_crlf_and_restores_byte_for_byte(tmp_path):
    f = tmp_path / "m.py"
    f.write_bytes(CRLF_MODULE.encode())
    target = find_targets_single(tmp_path, "m.py", "clamp")
    before = patch_file(tmp_path, target, "def clamp(x, lo=0, hi=10):\n    return x\n")
    mutated = f.read_bytes()
    assert b"\r\n" in mutated
    assert b"\n" not in mutated.replace(b"\r\n", b""), "a bare LF crept into a CRLF file"
    assert ast.parse(mutated.decode())
    f.write_bytes(before)
    assert f.read_bytes() == CRLF_MODULE.encode()


def test_patching_keeps_a_missing_final_newline(tmp_path):
    f = tmp_path / "m.py"
    f.write_bytes(b"def f(n):\n    return n > 0")
    t = Target(path="m.py", name="f", source="def f(n):\n    return n > 0", lineno=1, end_lineno=2)
    patch_file(tmp_path, t, "def f(n):\n    return n >= 0")
    assert f.read_bytes() == b"def f(n):\n    return n >= 0"


def find_targets_single(repo: Path, rel: str, name: str) -> Target:
    # find_targets only looks inside packages; build the Target by hand for a loose file.
    src = (repo / rel).read_bytes().decode()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            lines = src.splitlines()
            return Target(
                path=rel,
                name=name,
                source="\n".join(lines[node.lineno - 1 : node.end_lineno]),
                lineno=node.lineno,
                end_lineno=node.end_lineno,
            )
    raise AssertionError(name)


def test_the_file_is_restored_when_a_mutant_run_raises(tmp_path, monkeypatch):
    """The restore must survive anything, including an exception nobody anticipated."""
    _project(tmp_path)
    core = tmp_path / "calc" / "core.py"
    original = core.read_bytes()
    target = next(t for t in find_targets(tmp_path) if t.name == "clamp")
    target.covering_tests = ["tests/test_core.py::test_clamp"]

    import suite_auditor.audit as audit_mod

    calls = {"n": 0}

    def exploding(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return True  # the unmutated baseline
        raise RuntimeError("boom, mid-mutant")

    monkeypatch.setattr(audit_mod, "run_tests", exploding)
    with pytest.raises(RuntimeError):
        audit_target(tmp_path, target, per_function=2)
    assert core.read_bytes() == original


@pytest.fixture(scope="module")
def solid_run(tmp_path_factory):
    """One real audit of the CRLF project, shared: it is the slowest thing in the suite.
    Run with three workers, so the parallel path is what gets checked."""
    repo = _project(tmp_path_factory.mktemp("solid") / "repo")
    before = _tree(repo)
    result = audit(repo, progress=False, per_function=6, python=sys.executable, jobs=3)
    return repo, before, result


def test_an_audit_never_writes_to_the_repository(solid_run):
    """CRLF preserved, no mutant ever on disk, no plugin or __pycache__ left behind -
    because nothing is written to the repository at all."""
    repo, before, result = solid_run
    assert result.scored, "the audit must have scored something for this to mean anything"
    assert _tree(repo) == before


def test_an_interrupted_audit_leaves_the_repository_untouched(tmp_path, monkeypatch):
    repo = _project(tmp_path / "repo")
    before = _tree(repo)

    import suite_auditor.audit as audit_mod

    real = audit_mod.run_tests
    calls = {"n": 0}

    def interrupted(*a, **k):
        calls["n"] += 1
        if calls["n"] > 1:  # after the baseline, i.e. with a mutant written in
            raise KeyboardInterrupt
        return real(*a, **k)

    monkeypatch.setattr(audit_mod, "run_tests", interrupted)
    with pytest.raises(KeyboardInterrupt):
        audit(repo, progress=False, per_function=3, python=sys.executable)
    assert _tree(repo) == before


# --- 2. no false gaps on a healthy suite ----------------------------------------------------


def test_a_solid_parametrized_suite_gets_no_false_gap(solid_run):
    _, _, result = solid_run
    by_fn = {}
    for r in result.results:
        by_fn.setdefault(r.target.rpartition("::")[2], []).append(r)
    assert by_fn["clamp"] and by_fn["grade"]
    for name in ("clamp", "grade"):
        gaps = [r for r in by_fn[name] if r.verdict is Verdict.PROVEN_GAP]
        assert gaps == [], f"false gap on a fully tested {name}: {gaps[0].witness}"
    # The weakly tested one IS a gap, proven on a value the test never passed, and the
    # original handles that value - so it is admissible.
    weak = [r for r in by_fn["double_all"] if r.verdict is Verdict.PROVEN_GAP]
    assert weak, [r.detail for r in by_fn["double_all"]]
    assert all(r.strength <= 1 for r in result.gaps)


def test_parametrize_is_a_table_not_a_call(tmp_path):
    f = tmp_path / "test_x.py"
    f.write_text(
        textwrap.dedent(
            """
            import pytest

            @pytest.mark.parametrize("x,exp", [(-1, 0), (0, 0), pytest.param(7, 7, id="s")])
            def test_f(x, exp):
                assert f(x) == exp
            """
        ),
        encoding="utf-8",
    )
    found = _literals_from(f)
    everything = [v for vs in found.values() for v in vs]
    assert "'x,exp'" not in everything
    assert not any("[(-1, 0)" in v for v in everything), everything
    assert found["x"] == ["-1", "0", "7"]
    assert found["exp"] == ["0", "7"]


def test_the_trace_records_the_arguments_tests_really_passed(tmp_path):
    repo = _project(tmp_path / "repo")
    trace = build_map(repo, python=sys.executable)
    calls = trace.calls["calc/core.py::clamp"]
    assert {"pos": ["-1", "0", "10"], "kw": {}} in calls
    assert all(c["pos"][0] != "'x,exp'" for c in calls)


def test_both_raising_differently_is_never_proof():
    assert not eligible(OBSERVED, "raise: TypeError: a", "raise: ValueError: b")
    assert not eligible(GENERATED, "raise: TypeError: a", "raise: TypeError: b")


def test_a_generated_input_counts_only_when_the_original_accepts_it():
    assert eligible(GENERATED, "ok: 1", "raise: IndexError: x")
    assert eligible(GENERATED, "ok: 1", "ok: 2")
    assert not eligible(GENERATED, "raise: TypeError: '<' not supported", "ok: None")
    assert not eligible(RECOMBINED, "raise: ValueError: lo > hi", "ok: 3")
    # A call the tests really made may be one that is meant to raise.
    assert eligible(OBSERVED, "raise: ValueError: negative", "ok: -1")


def test_a_type_error_on_a_made_up_argument_is_not_proof():
    """toolz `get_exclude_keywords(0, 0)`: the original returns early on the first 0 and
    never touches the second; the mutant skips the early return and asks the int for
    `.parameters`. The input was never a signature, so nothing is proven."""
    old = "def f(n, sig):\n    if n == 0:\n        return ()\n    return tuple(sig.parameters)\n"
    new = old.replace("n == 0", "n == 1")
    r = compare("", old, new, "f", [ArgSet(("0", "0"), (), GENERATED)])
    assert r["status"] != "differs", r
    assert not eligible(GENERATED, "ok: ()", "raise: AttributeError: 'int' object has no")
    assert not eligible(GENERATED, "ok: ()", "ok: []...then TypeError: bad")
    # An IndexError on an input the original handles is a real difference.
    assert eligible(GENERATED, "ok: None", "raise: IndexError: list index out of range")
    # And a call the tests really made is proof whatever the mutant raises.
    assert eligible(OBSERVED, "ok: ()", "raise: AttributeError: x")


def test_values_equal_under_eq_are_not_a_difference():
    """`clamp(False)` returns False; the equivalent mutant `x <= lo` returns 0. Different
    reprs, equal values - no `assert ==` could catch that, so it is not a gap."""
    old = "def clamp(x, lo=0, hi=10):\n    if x < lo:\n        return lo\n    return x\n"
    new = old.replace("x < lo", "x <= lo")
    r = compare("", old, new, "clamp", [ArgSet(("False", "0", "10"), (), GENERATED)])
    assert r["status"] == "agree", r


def test_an_injected_empty_string_on_which_both_raise_is_not_a_gap():
    old = "def f(pct):\n    return pct > 50\n"
    new = "def f(pct):\n    return pct > 51\n"
    sets = [ArgSet(("70",), (), OBSERVED), ArgSet(('""',), (), GENERATED)]
    r = compare("", old, new, "f", sets)
    assert r["status"] != "differs", r


def test_each_side_gets_its_own_copy_of_a_mutable_argument():
    """A function that mutates its argument would otherwise hand the second version an
    input the first already changed - a difference manufactured by the harness."""
    src = "def f(xs):\n    xs.append(1)\n    return len(xs)\n"
    r = compare("", src, src.replace("return len(xs)", "return len(xs) + 0"), "f", ["([],)"])
    assert r["status"] == "agree", r


def test_real_calls_come_first_and_keep_their_provenance():
    fn = ast.parse("def f(a, b):\n    pass\n").body[0]
    observed = [{"pos": ["1", "2"], "kw": {}}, {"pos": ["3", "4"], "kw": {}}]
    sets = argument_sets(fn, {}, observed=observed)
    assert [s.provenance for s in sets[:2]] == [OBSERVED, OBSERVED]
    assert sets[0].pos == ("1", "2")
    assert RECOMBINED in {s.provenance for s in sets}


# --- 7. what counts as a test file -----------------------------------------------------------


@pytest.mark.parametrize(
    "rel,is_test",
    [
        ("pkg/latest.py", False),
        ("pkg/contest.py", False),
        ("pkg/attestation.py", False),
        ("pkg/testing_utils.py", False),
        ("pkg/test_m.py", True),
        ("pkg/m_test.py", True),
        ("pkg/conftest.py", True),
        ("tests/helpers.py", True),
        ("pkg/tests/util.py", True),
    ],
)
def test_test_files_are_recognised_by_convention(rel, is_test):
    assert is_test_path(rel) is is_test


def test_the_plugin_uses_the_same_rule():
    """The plugin runs in the target's interpreter without importing this package, so it
    carries its own copy of the rule. This keeps the two from drifting apart."""
    tree = ast.parse(PLUGIN)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_is_test")
    ns: dict = {"_TEST_DIRS": {"tests", "test", "testing"}}
    exec(compile(ast.Module([fn], []), "<plugin>", "exec"), ns)
    for rel in ["a/latest.py", "a/contest.py", "a/test_x.py", "a/x_test.py", "tests/y.py"]:
        assert ns["_is_test"](rel) is is_test_path(rel), rel


def test_a_module_named_like_a_test_is_still_audited(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for name in ("latest", "contest", "attestation"):
        (pkg / f"{name}.py").write_text(f"def {name}(x):\n    return x > 0\n", encoding="utf-8")
    assert {t.name for t in find_targets(tmp_path)} == {"latest", "contest", "attestation"}


# --- things found along the way --------------------------------------------------------------


def test_methods_yield_mutants():
    """A method's source is indented, which does not parse on its own, so every method
    used to yield zero mutants - silently."""
    src = "    def go(self, n):\n        return n > 0\n"
    assert mutants(textwrap.dedent(src))


def test_a_test_that_already_fails_covers_nothing(tmp_path):
    """A failing test would 'kill' every mutant it is run against."""
    (tmp_path / "mod.py").write_text(
        "def f(x):\n    return x + 1\n\n\ndef g(x):\n    return x * 2\n", encoding="utf-8"
    )
    (tmp_path / "test_mod.py").write_text(
        "from mod import f, g\n\n\ndef test_f():\n    assert f(1) == 2\n\n\n"
        "def test_g():\n    assert g(1) == 99\n",
        encoding="utf-8",
    )
    trace = build_map(tmp_path, python=sys.executable)
    assert "mod.py::f" in trace.cov
    assert "mod.py::g" not in trace.cov
    assert trace.failed == ["test_mod.py::test_g"]
    # ...but it is not "unreached" either: a test tries, and is broken.
    assert trace.failing_only["mod.py::g"] == ["test_mod.py::test_g"]


def test_a_venv_inside_the_project_is_not_the_project(tmp_path):
    """Found by installing the wheel and auditing a project with its own .venv: pytest's
    files live under the repo, so the audit reported them as the original repo "shadowing"
    the scratch copy, and `coverage` would have traced them as project code."""
    from suite_auditor.workspace import Scratch

    repo = _project(tmp_path / "repo")
    site = repo / ".venv" / "Lib" / "site-packages"
    site.mkdir(parents=True)
    (repo / ".venv" / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    (site / "fakedep.py").write_text("def helper(x):\n    return x\n", encoding="utf-8")
    (repo / "tests" / "test_dep.py").write_text(
        f"import sys\nsys.path.insert(0, {str(site)!r})\nimport fakedep\n\n\n"
        "def test_dep():\n    assert fakedep.helper(3) == 3\n",
        encoding="utf-8",
    )
    direct = build_map(repo, python=sys.executable)
    assert not any(".venv" in k for k in direct.cov), list(direct.cov)
    with Scratch(repo) as scratch:
        copied = build_map(scratch.copies[0], python=sys.executable, original=repo)
    assert copied.stray == [], copied.stray


def test_the_trace_ignores_frozen_modules(tmp_path):
    """`<frozen os>` resolves against the working directory - the repo root - and was
    reported as a function of the project."""
    repo = _project(tmp_path / "repo")
    trace = build_map(repo, python=sys.executable)
    assert all(not k.startswith("<") for k in trace.cov), list(trace.cov)
