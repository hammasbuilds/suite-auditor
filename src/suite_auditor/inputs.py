"""Argument values for proving a survivor wrong, taken from the tests that cover it.

The quality of a gap report is the quality of its witness, and a witness is only as good as
the input it rests on. Three generations of this module, each fixing the last:

1. Literals from the whole repository. A function expecting signature objects was handed
   `(0, 0)` and "proved" to differ. Real, and worthless.
2. Literals from the text of the covering test files. Better, but text is not a call:
   `@pytest.mark.parametrize("x,exp", [(-1, 0), (0, 0)])` was harvested as the argument
   `('x,exp', 'x,exp', [(-1, 0), (0, 0), ...])`, and a correct, fully tested suite was
   reported with two "proven gaps".
3. **The arguments the function was really called with**, recorded while the covering
   tests ran (see coverage.py). Those are by definition inputs the suite exercises, and
   a mutant that differs on one of them is a mutant the suite called and did not check.

Every argument set carries its provenance, and the proof step treats them differently:

- `observed`   - a call a covering test really made. Any disagreement where at least one
                 side returns a value counts.
- `recombined` - values the tests used, one swapped in from another call. Counts only
                 when the ORIGINAL handles the input cleanly: a combination nobody makes
                 can be outside the function's domain.
- `generated`  - literals from the test files and a few generic corner values, used when
                 the function's real arguments could not be recorded (objects, not
                 literals). Same rule as recombined. An injected `''` on which both
                 versions raise, differently, is a statement about the argument, not
                 about the suite, and is never a gap.
"""

from __future__ import annotations

import ast
import contextlib
from dataclasses import dataclass
from pathlib import Path

# Values that exercise the corners most mutation operators live at.
GENERIC = ["0", "1", "-1", "2", '""', '"a"', "[]", "[1, 2]", "None", "True", "False", "{}"]

# Parameters that take a function, by name, and what to offer them.
#
# The pool is harvested from LITERALS in the covering tests, and a callable passed in a
# test is an `ast.Name`, never a Constant - so a higher-order function got no valid call at
# all. Measured on toolz's `dicttoolz`: both planted gaps came back "all 64 argument sets
# raised on both sides", so the prover proved 0 of 2 gaps that were definitely there. toolz
# is a functional library, which is most of it.
#
# Builtins and a lambda only. The probe has to resolve these without importing anything
# from the target's test module, which is the one thing this design does not do - so
# `iseven` from the test file is not available even though it would be the ideal argument.
CALLABLE_NAMES = (
    "func",
    "fn",
    "f",
    "function",
    "predicate",
    "pred",
    "key",
    "keyfunc",
    "callback",
    "op",
    "binop",
    "transform",
    # A factory is a callable too, and missing it kept `valfilter(predicate, d, factory)`
    # unprovable even once `predicate` was fixed: the third argument came out as `{1: 1}`,
    # a dict INSTANCE, so `factory()` raised "'dict' object is not callable" and all 64
    # calls still failed on both sides.
    "factory",
    "cls",
    "constructor",
)
CALLABLE_POOL = [
    # The type constructors first: they are the usual argument for a `factory` parameter
    # and they are also perfectly good one-argument callables.
    "dict",
    "list",
    "bool",
    "len",
    "str",
    "set",
    "tuple",
    "abs",
    "(lambda *a: True)",
    "(lambda *a: False)",
    "(lambda x: x)",
]

OBSERVED, RECOMBINED, GENERATED = "observed", "recombined", "generated"

PROVENANCE_LABEL = {
    OBSERVED: "a call your tests really made",
    RECOMBINED: "values your tests use, recombined",
    GENERATED: "generated - not a value your tests use; judge whether it is realistic",
}


@dataclass(frozen=True)
class ArgSet:
    """One way to call the function: positional sources, keyword sources, provenance."""

    pos: tuple[str, ...]
    kw: tuple[tuple[str, str], ...] = ()
    provenance: str = GENERATED

    def source(self) -> str:
        """A Python expression evaluating to `(args_tuple, kwargs_dict)`."""
        args = "(" + "".join(p + ", " for p in self.pos) + ")"
        kwargs = "{" + ", ".join(f"{k!r}: {v}" for k, v in self.kw) + "}"
        return f"({args}, {kwargs})"

    def display(self) -> str:
        parts = list(self.pos) + [f"{k}={v}" for k, v in self.kw]
        return "(" + ", ".join(parts) + ")"


def _is_parametrize(node: ast.Call) -> bool:
    f = node.func
    return isinstance(f, ast.Attribute) and f.attr == "parametrize"


def _literal_source(node: ast.AST) -> str | None:
    """Source for a node that is a pure literal, else None."""
    try:
        ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None
    with contextlib.suppress(AttributeError, ValueError):
        return ast.unparse(node)
    return None


def _parametrize_rows(node: ast.Call) -> list[dict[str, str]]:
    """Expand `@pytest.mark.parametrize(names, values)` into one {name: source} per row.

    The decorator's arguments are a table, not a call: the first is a list of column
    names and the second the rows. Reading them as call arguments is exactly how the
    string `'x,exp'` ended up passed to a function under test.
    """
    if len(node.args) < 2:
        return []
    names_node, rows_node = node.args[0], node.args[1]
    try:
        names = ast.literal_eval(names_node)
    except (ValueError, TypeError, SyntaxError):
        return []
    if isinstance(names, str):
        names = [n.strip() for n in names.split(",") if n.strip()]
    if not isinstance(names, list | tuple) or not all(isinstance(n, str) for n in names):
        return []
    if not isinstance(rows_node, ast.List | ast.Tuple):
        return []
    out: list[dict[str, str]] = []
    for row in rows_node.elts:
        # pytest.param(1, 2, id="x") carries its values positionally.
        if isinstance(row, ast.Call) and getattr(row.func, "attr", "") == "param":
            cells = row.args
        elif len(names) == 1:
            cells = [row]
        elif isinstance(row, ast.Tuple | ast.List):
            cells = row.elts
        else:
            continue
        if len(cells) != len(names):
            continue
        got = {}
        for name, cell in zip(names, cells, strict=True):
            src = _literal_source(cell)
            if src is not None:
                got[name] = src
        out.append(got)
    return out


def _literals_from(path: Path, limit: int = 40) -> dict[str, list[str]]:
    """Literals in one test file, indexed by the keyword they were passed as."""
    found: dict[str, list[str]] = {}

    def add(key: str, value: str) -> None:
        bucket = found.setdefault(key, [])
        if value not in bucket and len(bucket) < limit:
            bucket.append(value)

    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    except (SyntaxError, UnicodeDecodeError, OSError, ValueError):
        return found

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _is_parametrize(node):
            for row in _parametrize_rows(node):
                for name, src in row.items():
                    add(name, src)
                    add("*", src)
            continue
        for kw in node.keywords:
            if kw.arg and isinstance(kw.value, ast.Constant):
                add(kw.arg, repr(kw.value.value))
        for arg in node.args:
            # Whole literal collections too, not just scalars: a function taking a list
            # gets nothing usable from a pool of bare ints.
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str | int | float):
                add("*", repr(arg.value))
            elif isinstance(arg, ast.List | ast.Tuple | ast.Dict | ast.Set):
                src = _literal_source(arg)
                if src is not None:
                    add("*", src)
    return found


def harvest_for(repo: Path, covering_tests: list[str]) -> dict[str, list[str]]:
    """Pool the literals from just the test files that cover this function."""
    files = {t.split("::")[0] for t in covering_tests}
    pool: dict[str, list[str]] = {}
    for rel in sorted(files):
        path = repo / rel
        if not path.is_file():
            continue
        for k, vs in _literals_from(path).items():
            bucket = pool.setdefault(k, [])
            for v in vs:
                if v not in bucket:
                    bucket.append(v)
    return pool


def _params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[list[str], list[str]]:
    """(positional parameter names, keyword-only names that have no default)."""
    positional = [a.arg for a in fn.args.posonlyargs + fn.args.args]
    if positional and positional[0] in ("self", "cls"):
        positional = positional[1:]
    required_kw = [
        a.arg for a, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults, strict=True) if d is None
    ]
    return positional, required_kw


def argument_sets(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    pool: dict[str, list[str]],
    cap: int = 64,
    observed: list[dict] | None = None,
) -> list[ArgSet]:
    """Ways to call `fn`: real recorded calls first, then careful variations.

    Variations change one parameter at a time rather than taking a cross product: the
    product explodes, and a witness in which exactly one value differs from a real call
    is immediately readable - that value is the cause.
    """
    positional, required_kw = _params(fn)
    out: list[ArgSet] = []
    seen: set[tuple] = set()

    def push(a: ArgSet) -> bool:
        # Keyed on the call alone: the same call reached twice keeps its first, and
        # strongest, provenance.
        if (a.pos, a.kw) not in seen:
            seen.add((a.pos, a.kw))
            out.append(a)
        return len(out) >= cap

    # 1. Real calls, verbatim.
    real: list[ArgSet] = []
    for call in observed or []:
        a = ArgSet(tuple(call.get("pos", [])), tuple(sorted(call.get("kw", {}).items())), OBSERVED)
        real.append(a)
        if push(a):
            return out

    # 2. Real values, recombined: swap one positional value for one seen in another call
    #    of the same shape. Capped, so the generated boundaries below still get a turn.
    recombined_cap = len(out) + 12
    for base in real:
        if len(out) >= recombined_cap:
            break
        for other in real:
            if other is base or len(other.pos) != len(base.pos):
                continue
            for i, v in enumerate(other.pos):
                if v == base.pos[i]:
                    continue
                pos = list(base.pos)
                pos[i] = v
                if push(ArgSet(tuple(pos), base.kw, RECOMBINED)):
                    return out
                if len(out) >= recombined_cap:
                    break
            if len(out) >= recombined_cap:
                break

    # 3. Generated. Boundary values first - the function's own constants and their
    #    neighbours, and the neighbours of each value the tests passed for this parameter
    #    - because that is where comparison and constant mutants differ from the
    #    original. Then literals from the covering test files, then generic corners.
    #    Without the boundaries, `if w <= 2` vs `if w <= 3` was left unproven because the
    #    only candidate that separates them, 3, never made it into a pool crowded with
    #    another function's parametrize rows.
    boundaries = _boundaries(fn)
    pools: list[list[str]] = []
    for i, name in enumerate(positional):
        near: list[str] = []
        for a in real:
            if i < len(a.pos):
                near += _neighbours(a.pos[i])
        vals: list[str] = []
        # A parameter named for a function goes first to callables: offering it `0` and
        # `""` produces 64 calls that all raise, which is how a provable gap in
        # `valfilter(predicate, d)` came back unproven.
        wants_callable = name.lower() in CALLABLE_NAMES
        for group in (
            CALLABLE_POOL if wants_callable else [],
            boundaries,
            near,
            pool.get(name, []),
            pool.get("*", []),
            GENERIC,
        ):
            vals += [v for v in group[:12] if v not in vals]
        pools.append(vals[:24])

    if real:
        baseline = list(real[0].pos)
        base_kw = real[0].kw
        if len(baseline) != len(positional):
            return out
    else:
        baseline = [p[0] if p else "None" for p in pools]
        base_kw = tuple((k, (pool.get(k) or ["None"])[0]) for k in required_kw)
        if push(ArgSet(tuple(baseline), base_kw, GENERATED)):
            return out

    for i, p in enumerate(pools):
        for value in p:
            if value == baseline[i]:
                continue
            args = list(baseline)
            args[i] = value
            if push(ArgSet(tuple(args), base_kw, GENERATED)):
                return out
    return out


def _neighbours(src: str) -> list[str]:
    """For an integer literal, the values either side of it; otherwise nothing."""
    try:
        v = ast.literal_eval(src)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return []
    if isinstance(v, bool) or not isinstance(v, int):
        return []
    return [repr(v - 1), repr(v + 1)]


def _boundaries(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Numeric constants in the function body, each with its neighbours: v, v-1, v+1."""
    body = fn.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]  # the docstring
    out: list[str] = []
    for stmt in body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Constant) or isinstance(node.value, bool):
                continue
            if isinstance(node.value, int | float):
                for cand in [repr(node.value), *_neighbours(repr(node.value))]:
                    if cand not in out:
                        out.append(cand)
    return out


def _returned_cleanly(side: str) -> bool:
    """Did this side produce a value without raising?

    The `ok:` prefix alone is not enough. A lazy iterator is drained before comparison, and
    a generator that yields nothing and then raises is rendered
    `ok: []...then TypeError: ...` - which starts with `ok` and is an exception.

    Missing this promoted the one and only "unarguable" gap in a real audit to the top of
    the report, when both sides had in fact raised. A tool whose product is graded evidence
    cannot mis-grade its own evidence.
    """
    return side.startswith("ok") and "...then " not in side


def witness_strength(witness: dict | None) -> int:
    """How convincing a witness is. Lower is better.

    0 - both versions returned a value, and the values differ. Unarguable.
    1 - one returned, one raised. Real, and needs the reader to judge the input.
    2 - both raised, differently. Never counted as a gap: it says more about the
        argument than about the suite.
    """
    if not witness:
        return 3
    old_ok = _returned_cleanly(witness.get("old", ""))
    new_ok = _returned_cleanly(witness.get("new", ""))
    if old_ok and new_ok:
        return 0
    if old_ok or new_ok:
        return 1
    return 2


_TYPE_CONFUSION = {"TypeError", "AttributeError"}


def _exception_type(side: str) -> str:
    """`raise: KeyError: 'x'` -> `KeyError`; `ok: [1]...then ValueError: y` -> `ValueError`."""
    tail = side.split("...then ", 1)[1] if "...then " in side else side.partition(": ")[2]
    return tail.split(":", 1)[0].strip()


def eligible(provenance: str, old: str, new: str) -> bool:
    """Whether a disagreement on this input may be offered as proof of a gap.

    - Both sides raising (differently) is never proof.
    - On an input a test really passed, one side returning is enough: the suite made
      that call and did not notice the difference.
    - On any other input the ORIGINAL must return cleanly, which is the only evidence
      available that the input is one the function is meant to accept - and the mutant
      must either return too or fail with something other than a TypeError or
      AttributeError, which on a made-up input mostly mean "wrong type of argument".
    """
    old_ok, new_ok = _returned_cleanly(old), _returned_cleanly(new)
    if not (old_ok or new_ok):
        return False
    if provenance == OBSERVED:
        return True
    if not old_ok:
        return False
    # The mutant raising a type error on an input nobody passed usually means the input
    # is the wrong type for the path the mutant took, not that the mutant is wrong.
    # toolz's `get_exclude_keywords(0, 0)`: the original returns early on the first 0 and
    # never looks at the second; the mutant does not return early and asks the int 0 for
    # `.parameters`. A real signature object would not have noticed the difference.
    return new_ok or _exception_type(new) not in _TYPE_CONFUSION
