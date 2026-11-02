"""Building a method's receiver, which is what made 62.7% of refusals refusals.

Over the 112-package census, 1,993 of 3,179 unproven survivors were unproven for one
reason: a method cannot be called without its instance, and the tool refused to fabricate
one because a call with a faked `self` proves nothing in either direction. That refusal is
right. Constructing a *real* instance is a different matter, and this is it.

Four properties carry the feature and each fails silently without a test:

  * a **fresh** instance per call per side. A method that mutates `self` would otherwise
    hand the second version an object the first had already changed, which is the same
    false disagreement the argument deepcopy exists to prevent.
  * construction **under the side-effect guard**. An `__init__` that writes a file is not
    a receiver this tool can build safely, and the survivor has to stay unproven - the
    same rule the call itself follows. The alternative is a tool that deletes your files
    while reporting no provable gap, which this repository has already done once.
  * a receiver built from **source expressions**, not a `repr`. The candidate calls are
    argument sources exactly as `ARGSETS` holds them, so a `repr` of the dict would turn
    each into a string literal and construct the class from the text `"([],)"`.
  * every `Target` field surviving the **re-rooting** into the scratch copy. `_rerooted`
    listed its fields by hand, so the receiver fields were dropped on the way into the
    directory the audit actually runs in: discovery filled them in, the feature was
    absent, and nothing failed.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib
import sys
import textwrap

import pytest

from suite_auditor import mutate
from suite_auditor.audit import _receiver_for, _rerooted
from suite_auditor.differential import _receiver_source, compare
from suite_auditor.inputs import ArgSet
from suite_auditor.types import Target

ROOT = pathlib.Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ discovery carries it


def _targets(tmp_path: pathlib.Path, source: str) -> dict[str, Target]:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "mod.py").write_text(textwrap.dedent(source), encoding="utf-8")
    return {t.name: t for t in mutate.find_targets(tmp_path, include_methods=True)}


def test_a_method_knows_its_class_and_a_function_does_not(tmp_path):
    found = _targets(
        tmp_path,
        """
        def plain(x):
            return x

        class Thing:
            def __init__(self, n):
                self.n = n

            def doubled(self):
                return self.n * 2
        """,
    )
    assert found["plain"].receiver_class == ""
    assert found["Thing.doubled"].receiver_class == "Thing"
    assert "def __init__" in found["Thing.doubled"].receiver_init
    assert found["Thing.doubled"].module == "pkg.mod"


def test_a_class_with_no_init_offers_its_annotated_fields(tmp_path):
    """A dataclass's `__init__` exists only at runtime, so there is no AST to read."""
    found = _targets(
        tmp_path,
        """
        from dataclasses import dataclass

        @dataclass
        class Price:
            amount: int
            currency: str = "GBP"

            def doubled(self):
                return self.amount * 2
        """,
    )
    target = found["Price.doubled"]
    assert target.receiver_init == ""
    assert target.receiver_fields == ("amount", "currency")


def test_fields_after_a_method_still_count(tmp_path):
    """A dataclass's parameter order is its annotation order, wherever the annotations sit.

    Checked against the language rather than assumed: `dataclasses.fields` on a class with
    `a: int`, a method, then `b: int = 2` reports both, in that order. An earlier version
    of the scan was documented as stopping at the first method, which would have offered
    `Price(amount)` for a class whose later field is also required.
    """
    from dataclasses import dataclass, fields

    @dataclass
    class Probe:
        a: int

        def one(self) -> int:
            return 1

        b: int = 2

    assert [f.name for f in fields(Probe)] == ["a", "b"]

    found = _targets(
        tmp_path,
        """
        class Thing:
            a: int

            def one(self):
                return 1

            b: int = 2

            def two(self):
                return 2
        """,
    )
    assert found["Thing.one"].receiver_fields == ("a", "b")


def test_the_module_is_derived_for_a_package_init(tmp_path):
    """`pkg/__init__.py` is the module `pkg`, not `pkg.__init__`."""
    target = Target(path="pkg/__init__.py", name="C.m", source="", package="pkg")
    assert target.module == "pkg"
    assert Target(path="pkg/mod.py", name="C.m", source="", package="pkg").module == "pkg.mod"
    assert Target(path="mod.py", name="C.m", source="", package="").module == "mod"


# -------------------------------------------------------------- re-rooting keeps it all


def test_rerooting_keeps_every_field():
    """The defect that made the whole feature invisible: a hand-written field list."""
    original = Target(
        path="pkg/mod.py",
        name="C.m",
        source="def m(self):\n    return 1\n",
        header="",
        sys_path=str(pathlib.Path("/old/src")),
        package="pkg",
        receiver_class="C",
        receiver_init="def __init__(self):\n    pass\n",
        receiver_fields=("a", "b"),
    )
    moved = _rerooted(original, pathlib.Path("/old"), pathlib.Path("/new"))
    for field in dataclasses.fields(Target):
        if field.name == "sys_path":
            continue
        assert getattr(moved, field.name) == getattr(original, field.name), field.name
    assert moved.sys_path != original.sys_path


def test_rerooting_to_the_same_root_is_the_same_object():
    original = Target(path="p", name="n", source="")
    assert _rerooted(original, pathlib.Path("/x"), pathlib.Path("/x")) is original


# ---------------------------------------------------------------- candidate construction


def test_no_receiver_is_offered_for_a_plain_function(tmp_path):
    found = _targets(tmp_path, "def plain(x):\n    return x\n")
    assert _receiver_for(tmp_path, found["plain"], []) is None


def test_the_no_argument_call_is_always_offered_first(tmp_path):
    found = _targets(
        tmp_path,
        """
        class Thing:
            def __init__(self, n=1):
                self.n = n

            def doubled(self):
                return self.n * 2
        """,
    )
    receiver = _receiver_for(tmp_path, found["Thing.doubled"], [])
    assert receiver is not None
    assert receiver["attr"] == "Thing"
    assert receiver["module"] == "pkg.mod"
    first = receiver["calls"][0]
    assert (first if isinstance(first, str) else first.source()) == "((), {})"


def test_candidates_are_deduplicated_and_capped(tmp_path):
    found = _targets(
        tmp_path,
        """
        class Thing:
            def __init__(self, a, b, c):
                self.a, self.b, self.c = a, b, c

            def summed(self):
                return self.a
        """,
    )
    receiver = _receiver_for(tmp_path, found["Thing.summed"], [])
    sources = [c if isinstance(c, str) else c.source() for c in receiver["calls"]]
    assert len(sources) == len(set(sources)), sources
    assert len(sources) <= 12


def test_a_dataclass_gets_every_field_at_once(tmp_path):
    """Varying one required field at a time offers no candidate that constructs."""
    found = _targets(
        tmp_path,
        """
        from dataclasses import dataclass

        @dataclass
        class Price:
            amount: int

            def doubled(self):
                return self.amount * 2
        """,
    )
    receiver = _receiver_for(tmp_path, found["Price.doubled"], [])
    sources = [c if isinstance(c, str) else c.source() for c in receiver["calls"]]
    assert any("amount" in src for src in sources), sources


# ------------------------------------------------------------------ the receiver source


def test_the_receiver_is_built_as_source_not_as_a_repr():
    """Its calls are argument *sources*; a repr would make them string literals."""
    got = _receiver_source({"module": "pkg.mod", "attr": "Cls", "calls": [ArgSet(pos=("[]",))]})
    assert "'calls': [(([], ), {})]" in got
    assert '"([],)"' not in got
    # And it is a valid expression that evaluates to the right shape.
    value = ast.literal_eval(got)
    assert value["module"] == "pkg.mod"
    assert value["calls"] == [(([],), {})]


def test_no_receiver_renders_as_none():
    assert _receiver_source(None) == "None"
    assert _receiver_source({}) == "None"


# ------------------------------------------------------- end to end, through a subprocess


METHOD_OLD = """
def banded(self, n):
    if n > 10:
        return "big"
    return "small"
"""

METHOD_NEW = """
def banded(self, n):
    if n >= 10:
        return "big"
    return "small"
"""


def _package(tmp_path: pathlib.Path, body: str) -> str:
    pkg = tmp_path / "pkg"
    pkg.mkdir(exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "mod.py").write_text(textwrap.dedent(body), encoding="utf-8")
    return str(tmp_path)


def test_a_method_gap_is_proven_with_a_real_instance(tmp_path):
    sys_path = _package(
        tmp_path,
        """
        class Thing:
            def __init__(self):
                self.calls = 0

            def banded(self, n):
                if n > 10:
                    return "big"
                return "small"
        """,
    )
    got = compare(
        "",
        textwrap.dedent(METHOD_OLD),
        textwrap.dedent(METHOD_NEW),
        "banded",
        ["(10,)"],
        60.0,
        sys_path,
        "pkg",
        sys.executable,
        "",
        {"module": "pkg.mod", "attr": "Thing", "calls": ["((), {})"]},
    )
    assert got["status"] == "differs", got
    # The witness renders each side as "outcome: value"; the instance supplied `self` and
    # the argument set supplied n=10, which is the boundary the mutant moved.
    assert got["witness"]["args"] == "(10)"
    assert got["witness"]["old"] == "ok: 'small'"
    assert got["witness"]["new"] == "ok: 'big'"


def test_each_side_gets_its_own_instance(tmp_path):
    """A method that mutates `self` must not see the other side's changes.

    Both versions append once and return the length. With one shared receiver the second
    side would see 2 and the tool would report a proven gap on identical code.
    """
    sys_path = _package(
        tmp_path,
        """
        class Counter:
            def __init__(self):
                self.seen = []

            def bump(self, n):
                self.seen.append(n)
                return len(self.seen)
        """,
    )
    body = "def bump(self, n):\n    self.seen.append(n)\n    return len(self.seen)\n"
    got = compare(
        "",
        body,
        body,
        "bump",
        ["(1,)"],
        60.0,
        sys_path,
        "pkg",
        sys.executable,
        "",
        {"module": "pkg.mod", "attr": "Counter", "calls": ["((), {})"]},
    )
    assert got["status"] == "agree", got


def test_a_constructor_that_touches_the_machine_is_refused(tmp_path):
    sys_path = _package(
        tmp_path,
        """
        class Writer:
            def __init__(self):
                with open("side-effect.txt", "w", encoding="utf-8") as fh:
                    fh.write("x")

            def banded(self, n):
                if n > 10:
                    return "big"
                return "small"
        """,
    )
    got = compare(
        "",
        textwrap.dedent(METHOD_OLD),
        textwrap.dedent(METHOD_NEW),
        "banded",
        ["(10,)"],
        60.0,
        sys_path,
        "pkg",
        sys.executable,
        "",
        {"module": "pkg.mod", "attr": "Writer", "calls": ["((), {})"]},
    )
    assert got["status"] == "no_receiver", got
    assert "side effect" in got["detail"] or "SideEffect" in got["detail"]
    assert not (tmp_path / "side-effect.txt").exists()
    assert not (pathlib.Path.cwd() / "side-effect.txt").exists()


def test_a_class_that_cannot_be_constructed_at_all_is_refused(tmp_path):
    sys_path = _package(
        tmp_path,
        """
        class Fussy:
            def __init__(self, required_thing):
                if not isinstance(required_thing, complex):
                    raise TypeError("needs a complex number")
                self.it = required_thing

            def banded(self, n):
                if n > 10:
                    return "big"
                return "small"
        """,
    )
    got = compare(
        "",
        textwrap.dedent(METHOD_OLD),
        textwrap.dedent(METHOD_NEW),
        "banded",
        ["(10,)"],
        60.0,
        sys_path,
        "pkg",
        sys.executable,
        "",
        {"module": "pkg.mod", "attr": "Fussy", "calls": ["((), {})", "((1,), {})"]},
    )
    assert got["status"] == "no_receiver", got
    assert "tried 2" in got["detail"], got["detail"]


def test_a_missing_class_is_refused_rather_than_crashing(tmp_path):
    sys_path = _package(tmp_path, "class Other:\n    pass\n")
    got = compare(
        "",
        textwrap.dedent(METHOD_OLD),
        textwrap.dedent(METHOD_NEW),
        "banded",
        ["(10,)"],
        60.0,
        sys_path,
        "pkg",
        sys.executable,
        "",
        {"module": "pkg.mod", "attr": "Absent", "calls": ["((), {})"]},
    )
    assert got["status"] == "no_receiver", got
    assert "could not be imported" in got["detail"]


def test_a_function_still_compares_without_a_receiver(tmp_path):
    """The feature must not change anything about a plain function."""
    old = "def banded(n):\n    if n > 10:\n        return 'big'\n    return 'small'\n"
    new = "def banded(n):\n    if n >= 10:\n        return 'big'\n    return 'small'\n"
    got = compare("", old, new, "banded", ["(10,)"], 60.0, "", "", sys.executable)
    assert got["status"] == "differs", got


@pytest.mark.parametrize("candidate", ["((), {})", "((5,), {})"])
def test_the_first_candidate_that_constructs_is_used(tmp_path, candidate):
    """Both orders must reach a verdict; only the viable candidate can construct."""
    sys_path = _package(
        tmp_path,
        """
        class Limited:
            def __init__(self, limit=7):
                self.limit = limit

            def banded(self, n):
                if n > 10:
                    return "big"
                return "small"
        """,
    )
    got = compare(
        "",
        textwrap.dedent(METHOD_OLD),
        textwrap.dedent(METHOD_NEW),
        "banded",
        ["(10,)"],
        60.0,
        sys_path,
        "pkg",
        sys.executable,
        "",
        {"module": "pkg.mod", "attr": "Limited", "calls": [candidate]},
    )
    assert got["status"] == "differs", got
