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
- **A single call cannot tell a mutant apart from a coin flip.** `random`, `uuid4`,
  `time.time` and similar disagree with *themselves* from one call to the next on
  byte-identical code; comparing one call per side reported that as a confident,
  "unarguable" proven gap, differently on every run. Each disagreement is now checked
  against a second call to the same side before it is trusted - see `nondeterministic`
  below.
"""

from __future__ import annotations

import ast
import json
import sys
import tempfile
from pathlib import Path

from suite_auditor.inputs import OBSERVED, ArgSet, eligible
from suite_auditor.workspace import Timeout, child_env, run

# Modules and attributes whose result is not a function of their arguments. A call to
# any of these anywhere in a function's own source is a stronger, source-level
# guarantee than the runtime repeat check below: it catches a narrow value space
# (random.randint(1, 6) can coincide with itself by chance) that a repeat cannot rule
# out with certainty, and it costs nothing extra at audit time.
_NONDETERMINISTIC_CALLS = {
    "random", "uuid", "uuid1", "uuid3", "uuid4", "uuid5",
    "time", "monotonic", "perf_counter", "process_time",
    "urandom", "token_bytes", "token_hex", "token_urlsafe",
}  # fmt: skip
_NONDETERMINISTIC_ATTRS = {"now", "utcnow", "today"}  # datetime.now(), date.today(), ...


def _looks_nondeterministic(source: str) -> bool:
    """Does this function's own source call something whose result is not a function
    of its arguments (random, uuid, a clock)?

    A name match, not a data-flow analysis: `random` shadowed by a local variable of
    the same name would false-positive, and a nondeterministic call reached only
    through another function this one calls would false-negative (the runtime repeat
    check in RUNNER is what catches that case). Both are the safe direction to be
    wrong in for a tool whose job is to not claim more than it can prove.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name in _NONDETERMINISTIC_CALLS or name in _NONDETERMINISTIC_ATTRS:
                return True
    return False


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


class _SideEffect(BaseException):
    # BaseException, not Exception: a function that wraps its own file write in
    # `except Exception` would otherwise swallow the block and the run would report a
    # value as if the call had been clean.
    pass


_GUARD = [False]

# Audit events that leave the process: anything that writes, deletes, renames, spawns or
# connects. Blocking them is the whole point - the proof step only needs a return value,
# and it has no business changing the machine to get one.
_BLOCKED = (
    "os.remove", "os.unlink", "os.rename", "os.replace", "os.rmdir", "os.mkdir",
    "os.truncate", "os.chmod", "os.chown", "os.link", "os.symlink", "os.system",
    "os.exec", "os.spawn", "os.posix_spawn", "os.fork", "os.forkpty", "os.kill",
    "os.putenv", "os.unsetenv", "os.scandir",
    "shutil.", "subprocess.", "socket.connect", "socket.bind", "socket.sendto",
    "urllib.Request", "ftplib.", "smtplib.", "webbrowser.open", "sqlite3.connect",
    "winreg.", "ctypes.dlopen", "ctypes.call_function",
)


def _audit(event, args):
    if not _GUARD[0]:
        return
    if event == "open":
        # args is (path, mode, flags); reading is fine, writing is not.
        mode = str(args[1]) if len(args) > 1 and args[1] else ""
        if any(ch in mode for ch in "wax+"):
            raise _SideEffect("open(mode=" + mode + ")")
        return
    for prefix in _BLOCKED:
        if event == prefix or event.startswith(prefix):
            raise _SideEffect(event)


sys.addaudithook(_audit)


def _call(fn, args, kwargs):
    # The guard is on ONLY for the call itself. Proving a mutant by running the real
    # function used to mean running its side effects too: auditing a file tool whose
    # test passes a real path deleted that path, and the report still said "no provable
    # gap found" - a clean bill of health and a lost file in the same run. A survivor
    # that cannot be proven without touching the machine stays unproven instead.
    _GUARD[0] = True
    try:
        text, value = _render(fn(*args, **kwargs))
        return ("ok", _norm(text)), value
    except _SideEffect as effect:
        return ("impure", _norm(str(effect))), _NOEQ
    except Exception as exc:
        return ("raise", _norm(type(exc).__name__ + ": " + str(exc)[:200])), _NOEQ
    finally:
        _GUARD[0] = False


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
    same = a == b or _equal(va, vb)
    # A single call cannot tell "the mutant changed behaviour" apart from "this
    # function is not deterministic" - random.randint, uuid4, time.time and similar
    # disagree with THEMSELVES from one call to the next, on byte-identical code, and
    # a disagreement like that is not proof of anything. Before trusting a
    # disagreement, call each side twice more: a function narrow enough to coincide
    # with itself once by chance (random.randint(1, 6) agrees 1 time in 6) is most
    # unlikely to coincide on both repeats, so this is not a single coin flip.
    nondeterministic = False
    if not same:
        old_repeats = [_call(OLD_FN, copy.deepcopy(args), copy.deepcopy(kwargs)) for _ in range(2)]
        new_repeats = [_call(NEW_FN, copy.deepcopy(args), copy.deepcopy(kwargs)) for _ in range(2)]
        old_stable = all(a == a2 or _equal(va, va2) for a2, va2 in old_repeats)
        new_stable = all(b == b2 or _equal(vb, vb2) for b2, vb2 in new_repeats)
        nondeterministic = not (old_stable and new_stable)
    rows.append({{"old": a, "new": b, "same": same, "nondeterministic": nondeterministic}})

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
    # A source-level guarantee, stronger than the runtime repeat check inside RUNNER:
    # a function whose own body calls random/uuid/a clock cannot produce proof no
    # matter how many repeats happen to agree by chance (random.randint(1, 6) has a
    # 1-in-6 shot at coinciding with itself twice running).
    if _looks_nondeterministic(old) or _looks_nondeterministic(new):
        for row in rows:
            row["nondeterministic"] = True
    for row, a in zip(rows, sets, strict=False):
        row["args"] = a.display()
        row["provenance"] = a.provenance
        row["old_s"] = f"{row['old'][0]}: {row['old'][1]}"
        row["new_s"] = f"{row['new'][0]}: {row['new'][1]}"
    disagreements = [r for r in rows if not r["same"]]
    # A disagreement where either side did not even reproduce on its own second call is
    # not evidence of anything the mutation did - see the nondeterministic check above.
    unstable = [r for r in disagreements if r.get("nondeterministic")]
    disagreements = [r for r in disagreements if not r.get("nondeterministic")]
    exercised = [r for r in rows if r["old"][0] == "ok" or r["new"][0] == "ok"]

    # A call blocked for touching the filesystem, the network or another process proves
    # nothing either way: the two versions never got to return a value to compare. It is
    # not agreement and it is not a gap, and it must not be read as either.
    blocked = [r for r in rows if r["old"][0] == "impure" or r["new"][0] == "impure"]
    if blocked and not exercised:
        effects = sorted({r["old"][1] if r["old"][0] == "impure" else r["new"][1]
                          for r in blocked})
        return {
            "status": "inconclusive",
            "detail": "every call tried to leave the process ("
                      + ", ".join(effects[:3])
                      + "); proving this would mean running its side effects",
            "tried": len(rows),
        }
    rows = [r for r in rows if r not in blocked]
    disagreements = [r for r in disagreements if r not in blocked]

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
            "nondeterministic": len(unstable),
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
            "nondeterministic": len(unstable),
        }

    if unstable:
        # Every disagreement found was against the function's own repeat, not against
        # the mutant: this function is not deterministic (random/uuid/time and
        # similar), and nothing here can be reported as agreement OR as a gap.
        return {
            "status": "inconclusive",
            "detail": (
                f"{len(unstable)} of {len(rows)} inputs disagreed, but only because the "
                "function did not agree with its own repeat - not deterministic, so "
                "nothing here can be trusted as proof"
            ),
            "tried": len(rows),
            "exercised": len(exercised),
            "nondeterministic": len(unstable),
        }

    return {
        "status": "agree",
        "detail": f"{len(exercised)} of {len(rows)} inputs exercised it, all agree",
        "tried": len(rows),
        "exercised": len(exercised),
        "nondeterministic": 0,
    }


def _legacy(src: str) -> ArgSet:
    """A bare positional tuple source, e.g. `"(2,)"`, as an observed call."""
    import ast

    node = ast.parse(src, mode="eval").body
    elts = node.elts if isinstance(node, ast.Tuple) else [node]
    return ArgSet(tuple(ast.unparse(e) for e in elts), (), OBSERVED)
