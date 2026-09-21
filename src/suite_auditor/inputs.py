"""Argument values for proving a survivor wrong, taken from the tests that cover it.

The quality of a gap report is the quality of its witness, and a witness is only as good as
the input it rests on. The first version of this tool harvested literals from the whole
repository and produced this:

    GAP toolz/_signatures.py::get_exclude_keywords
        args: (0, 0)
        old: ok: ()
        new: raise: AttributeError: 'int' object has no attribute 'parameters'

That is a genuine behavioural difference and it is worthless. The function expects signature
objects; `(0, 0)` is not an input it will ever see, and a maintainer shown that would close
the report in a second - correctly.

So values come from the **tests that cover this specific function**. Those files contain,
by definition, the values somebody thought were worth calling it with. A repo-wide pool is
kept only as a fallback for functions whose covering tests yield nothing.

The other half of the fix is ranking: a disagreement where both versions *return something*
is worth far more than one where the mutant merely crashes on an argument the original also
had no real use for. Both are reported; only the first kind leads.
"""

from __future__ import annotations

import ast
import contextlib
from pathlib import Path

# Values that exercise the corners most mutation operators live at.
GENERIC = ["0", "1", "-1", "2", '""', '"a"', "[]", "[1, 2]", "None", "True", "False", "{}"]


def _literals_from(path: Path, limit: int = 40) -> dict[str, list[str]]:
    """Literals in one test file, indexed by the keyword they were passed as."""
    found: dict[str, list[str]] = {}

    def add(key: str, value: str) -> None:
        bucket = found.setdefault(key, [])
        if value not in bucket and len(bucket) < limit:
            bucket.append(value)

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return found

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
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
                with contextlib.suppress(AttributeError, ValueError):
                    add("*", ast.unparse(arg))
    return found


def harvest_for(repo: Path, covering_tests: list[str]) -> dict[str, list[str]]:
    """Pool the literals from just the test files that cover this function."""
    files = {t.split("::")[0] for t in covering_tests}
    pool: dict[str, list[str]] = {}
    for rel in files:
        path = repo / rel
        if not path.is_file():
            continue
        for k, vs in _literals_from(path).items():
            bucket = pool.setdefault(k, [])
            for v in vs:
                if v not in bucket:
                    bucket.append(v)
    return pool


def argument_sets(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    pool: dict[str, list[str]],
    cap: int = 40,
) -> list[str]:
    """Source strings for calling `fn`, varying one parameter at a time.

    One-at-a-time rather than a cross product: the product explodes, and a witness in which
    exactly one value differs from the baseline is immediately readable - that value is the
    cause.
    """
    params = [a.arg for a in fn.args.args if a.arg not in ("self", "cls")]
    if not params:
        return ["()"]

    pools: list[list[str]] = []
    for name in params:
        vals = list(pool.get(name, []))
        vals += [v for v in pool.get("*", []) if v not in vals]
        vals += [v for v in GENERIC if v not in vals]
        pools.append(vals[:12])

    baseline = [p[0] if p else "None" for p in pools]
    sets = ["(" + ", ".join(baseline) + ",)"]
    for i, p in enumerate(pools):
        for value in p[1:]:
            args = list(baseline)
            args[i] = value
            sets.append("(" + ", ".join(args) + ",)")
            if len(sets) >= cap:
                return sets
    return sets


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
    2 - both raised, differently. Weakest: often says more about the argument than the bug.
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
