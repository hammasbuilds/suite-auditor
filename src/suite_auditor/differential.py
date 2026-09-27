"""Run both versions of a function on the same inputs and compare.

Every non-trivial detail here was a bug first, found while building the sibling tool
`repo-surgeon`. They are all the same shape: the comparison produced a *confident wrong
answer* rather than an error, which for a review tool is the worst available failure - a
false accusation costs the reader more than silence would.

- **Separate module namespaces per version.** Defining both in one namespace and taking a
  reference to each breaks recursion: the body's self-call resolves through module globals,
  so after the second `def` shadows the name, the old function recurses into the new one
  and they appear to agree.
- **The same `__name__` for both namespaces.** A class defined inside one reprs as
  `<__name__.Foo object ...>`, so distinct names make every returned instance look like a
  difference.
- **Memory addresses normalised.** `<Foo object at 0x7f...>` reprs differently on every
  allocation; comparing raw makes any function returning a plain object a guaranteed
  "disagreement", reported with the address as proof.
- **Annotations are not evaluated.** Modules routinely annotate with names that only exist
  under `if TYPE_CHECKING:`, and evaluating them raises `NameError` on a function that is
  perfectly callable.
- **The package is importable.** Without it, a module doing `from . import x` cannot load
  at all, and the result reads "could not verify" when the truth is "nothing put the
  package on the path".
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from suite_auditor.inputs import OBSERVED, ArgSet, eligible
from suite_auditor.workspace import Timeout, child_env, run

RUNNER = """
import json, re, sys, types

SYS_PATH = {sys_path!r}
PACKAGE = {package!r}
HEADER = {header!r}
OLD = {old!r}
NEW = {new!r}
NAME = {name!r}

if SYS_PATH:
    sys.path.insert(0, SYS_PATH)

PREAMBLE = "from __future__ import annotations\\n"
ADDR = re.compile(r"0x[0-9a-fA-F]{{4,}}")


def _load(tag, source):
    # Both namespaces take the SAME __name__: they are separate module objects either
    # way, and a class defined inside reprs with its module, so differing names would
    # turn every returned instance into a false disagreement.
    name = (PACKAGE + ".rs_probe") if PACKAGE else "rs_probe"
    mod = types.ModuleType(name)
    mod.__dict__["__name__"] = name
    mod.__dict__["__package__"] = PACKAGE
    exec(compile(PREAMBLE + HEADER + "\\n\\n" + source, "<" + tag + ">", "exec"), mod.__dict__)
    return mod.__dict__[NAME]


for tag, src in (("old", OLD), ("new", NEW)):
    try:
        globals()[tag.upper() + "_FN"] = _load(tag, src)
    except Exception as exc:
        print("__SA_LOAD_FAIL__" + tag + ": " + type(exc).__name__ + ": " + str(exc)[:200])
        raise SystemExit(0)


def _norm(text):
    return ADDR.sub("0x...", text)


MAX_ITEMS = 64


def _render(value):
    # repr, except that a lazy iterator is drained first.
    #
    # `<itertools.islice object at 0x...>` reprs identically no matter what it would
    # yield, so a function returning a generator or an islice compares equal to any
    # other - and a real change in what it produces is invisible. Draining it is the only
    # way to see the behaviour at all.
    #
    # Bounded, because an infinite generator is a perfectly ordinary return value. When
    # the bound is hit that is shown, so a difference past it is not claimed absent.
    # Returns (text, value): the text is compared, the value is what `==` sees.
    if hasattr(value, "__next__") and not isinstance(value, (str, bytes)):
        items = []
        try:
            for i, item in enumerate(value):
                if i >= MAX_ITEMS:
                    return repr(items) + "...(truncated)", _NOEQ
                items.append(item)
        except Exception as exc:
            # Consuming it raised: that is behaviour too, and part of the comparison.
            # Plain concatenation, not an f-string: this whole module is a .format()
            # template, and an f-string's braces would be eaten as placeholders.
            text = repr(items) + "...then " + type(exc).__name__ + ": " + str(exc)[:120]
            return text, _NOEQ
        return repr(items), items
    return repr(value), value


_NOEQ = object()


def _call(fn, args, kwargs):
    try:
        text, value = _render(fn(*args, **kwargs))
        return ("ok", _norm(text)), value
    except Exception as exc:
        return ("raise", _norm(type(exc).__name__ + ": " + str(exc)[:200])), _NOEQ


def _equal(x, y):
    # Two return values that `==` cannot tell apart - `False` and `0`, `1` and `1.0` -
    # are not a difference any `assert result == expected` could catch, so they are
    # not a gap either. repr alone called an equivalent `x < lo` -> `x <= lo` mutant a
    # proven gap because it returned 0 where the original returned False.
    if x is _NOEQ or y is _NOEQ:
        return False
    try:
        return bool(x == y) and bool(y == x)
    except Exception:
        return False


import copy

ARGSETS = {argsets}

rows = []
for args, kwargs in ARGSETS:
    # A copy for each side: a function that mutates its argument would otherwise hand
    # the second version an input the first one already changed.
    a, va = _call(OLD_FN, copy.deepcopy(args), copy.deepcopy(kwargs))
    b, vb = _call(NEW_FN, copy.deepcopy(args), copy.deepcopy(kwargs))
    rows.append({{"old": a, "new": b, "same": a == b or _equal(va, vb)}})

print("__SA_JSON__" + json.dumps(rows))
"""


def compare(
    header: str,
    old: str,
    new: str,
    func: str,
    argsets: list[ArgSet] | list[str],
    timeout: float = 30.0,
    sys_path: str = "",
    package: str = "",
    python: str = "",
) -> dict:
    """Compare two versions. Returns a verdict dict; never raises.

    Statuses: `differs` (with a witness), `agree`, `inconclusive` (nothing was exercised,
    or the only disagreements are on inputs that cannot serve as proof - see
    inputs.eligible), `old_uncallable` / `new_uncallable`, `timeout`, `error`.

    `argsets` may be plain positional-tuple sources such as `"(2,)"`, which are treated
    as observed calls.
    """
    sets = [
        a if isinstance(a, ArgSet) else _legacy(a)  # type: ignore[arg-type]
        for a in argsets
    ]
    if not sets:
        return {"status": "inconclusive", "detail": "no argument sets could be built"}

    script = RUNNER.format(
        sys_path=sys_path,
        package=package,
        header=header,
        old=old,
        new=new,
        name=func,
        argsets="[" + ", ".join(a.source() for a in sets) + "]",
    )

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "compare.py"
        # newline="\n" or Windows rewrites newlines to CR CR LF, breaking any line
        # continuation inside the source under comparison.
        path.write_text(script, encoding="utf-8", newline="\n")
        try:
            _, out = run([python or sys.executable, "-B", str(path)], tmp, child_env(), timeout)
        except Timeout:
            return {"status": "timeout", "detail": f"no result within {timeout}s"}
        except OSError as exc:
            return {"status": "error", "detail": str(exc)[:300]}

    if "__SA_LOAD_FAIL__" in out:
        which = out.split("__SA_LOAD_FAIL__", 1)[1].strip().splitlines()[0]
        side = "old" if which.startswith("old") else "new"
        return {"status": f"{side}_uncallable", "detail": which[:300]}

    marker = out.find("__SA_JSON__")
    if marker < 0:
        err = out.strip().splitlines()
        return {"status": "error", "detail": err[-1][:300] if err else "no output"}

    rows = json.loads(out[marker + len("__SA_JSON__") :].strip().splitlines()[0])
    for row, a in zip(rows, sets, strict=False):
        row["args"] = a.display()
        row["provenance"] = a.provenance
        row["old_s"] = f"{row['old'][0]}: {row['old'][1]}"
        row["new_s"] = f"{row['new'][0]}: {row['new'][1]}"
    disagreements = [r for r in rows if not r["same"]]
    exercised = [r for r in rows if r["old"][0] == "ok" or r["new"][0] == "ok"]

    if not exercised:
        # Every input raised on both sides: the arguments were wrong for this function
        # and the run established nothing. Reporting that as agreement is how an
        # unverified change gets a clean bill of health.
        return {
            "status": "inconclusive",
            "detail": f"all {len(rows)} argument sets raised on both sides",
            "tried": len(rows),
        }

    proof = [r for r in disagreements if eligible(r["provenance"], r["old_s"], r["new_s"])]
    if proof:
        # Pick the most legible disagreement, not the first one found: two differing
        # values over value-vs-exception, and a call the tests really made over a
        # generated one.
        def legibility(row) -> tuple[int, int]:
            both_ok = row["old"][0] == "ok" and row["new"][0] == "ok"
            rank = {"observed": 0, "recombined": 1}.get(row["provenance"], 2)
            return (0 if both_ok else 1, rank)

        w = min(proof, key=legibility)
        return {
            "status": "differs",
            "detail": f"{len(disagreements)} of {len(rows)} inputs disagree",
            "witness": {"args": w["args"], "old": w["old_s"], "new": w["new_s"]},
            "provenance": w["provenance"],
            "tried": len(rows),
            "exercised": len(exercised),
        }

    if disagreements:
        return {
            "status": "inconclusive",
            "detail": (
                f"{len(disagreements)} of {len(rows)} inputs disagree, but only where both "
                "versions raise or where the original rejects the input - not proof"
            ),
            "tried": len(rows),
            "exercised": len(exercised),
        }

    return {
        "status": "agree",
        "detail": f"{len(exercised)} of {len(rows)} inputs exercised it, all agree",
        "tried": len(rows),
        "exercised": len(exercised),
    }


def _legacy(src: str) -> ArgSet:
    """A bare positional tuple source, e.g. `"(2,)"`, as an observed call."""
    import ast

    node = ast.parse(src, mode="eval").body
    elts = node.elts if isinstance(node, ast.Tuple) else [node]
    return ArgSet(tuple(ast.unparse(e) for e in elts), (), OBSERVED)
