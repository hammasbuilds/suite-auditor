"""Trivial Compiler Equivalence, for Python.

Papadakis, Jia, Harman and Le Traon (ICSE 2015) observed that if a mutant compiles to
exactly the same binary as the original, it cannot behave differently, and if two mutants
compile to the same binary as each other, one of them is redundant. No analysis is needed:
the compiler has already proved it. On C they discarded over 7% of mutants as equivalent
and over 21% as duplicates; on Java the equivalent-detection rate against a hand-analysed
ground truth was about 54% against about 30% for C, and they attribute the gap to mutation
characteristics being language-dependent.

Nobody has applied it to Python, where the usual objection - that CPython has no
optimising compiler, so there is nothing to collapse two different programs into one form
- is exactly the hypothesis worth testing rather than assuming. CPython does fold
constants, does rewrite some jumps, and `compile()` is deterministic for a fixed
interpreter, so the method transfers directly: compile, canonicalise away everything
positional, and compare.

**Soundness.** A mutant whose canonical form equals the original's executes the identical
sequence of instructions over the identical constants and names. It is equivalent, not
merely suspected of it. The converse does not hold - this finds *trivial* equivalence
only - so a mutant that survives this filter is still just a mutant, and the differential
stage remains the thing that decides whether a survivor is a gap.

**What is stripped, and why each one is safe.** Filenames and line numbers do not affect
execution. Line tables and column tables are debug metadata. Docstrings are *not*
stripped: `__doc__` is observable. Exception tables are *not* stripped: on 3.11+ they
carry the try/except ranges, which are semantics. Function and class names are not
stripped: they are observable through `__name__` and in tracebacks.
"""

from __future__ import annotations

import dis
import hashlib
import sys
from types import CodeType

# Attributes of a code object that execution depends on. Deliberately a list of names
# rather than "everything except a deny-list": a new CPython release that adds a
# semantic field would silently fall out of a deny-list and be ignored, which is the
# direction that produces a false claim of equivalence.
_SEMANTIC = (
    "co_argcount",
    "co_posonlyargcount",
    "co_kwonlyargcount",
    "co_flags",
    "co_name",
    "co_qualname",
)

# Deliberately absent from the list above, and the reason this module does not simply
# hash `co_code`:
#
#   co_consts, co_names, co_varnames  - index tables, compared through the instruction
#                                       stream instead. CPython folds `60 * 60` to 3600
#                                       and then *leaves the unused 60 in co_consts*,
#                                       so the folded mutant and the literal original
#                                       have the same instructions over different
#                                       tables and different operands. Comparing
#                                       co_code bytes calls them different, which is
#                                       the textbook trivially-equivalent mutant missed.
#   co_nlocals, co_stacksize          - derived from the above.
#   co_firstlineno, co_linetable,     - where the source was written.
#   co_qualname's module prefix

# Present from 3.11. Carries the try/except ranges, so it is semantics, not debug data.
_EXCEPTION_TABLE = "co_exceptiontable"


class Unfingerprintable(Exception):
    """The source does not compile, or holds a constant with no canonical form.

    Raised rather than returning None so that a caller cannot mistake "could not decide"
    for "not equivalent". Every caller here treats it as "do not filter this mutant",
    which is the conservative direction: an undetected equivalent mutant costs a little
    accuracy, a wrongly discarded non-equivalent one costs a result.
    """


def _canonical_const(value: object) -> str:
    """One constant, as text, with nested code objects expanded.

    `repr` is used for everything else because the constant table holds only immutable
    literals - numbers, strings, bytes, tuples and frozensets of those, and None,
    True, False and Ellipsis - whose reprs round-trip. The type name is included
    because `1`, `1.0` and `True` have reprs that do not distinguish them in a tuple,
    and they are three different constants to the interpreter.
    """
    if isinstance(value, CodeType):
        return _canonical_code(value)
    if isinstance(value, tuple):
        return "(" + ",".join(_canonical_const(v) for v in value) + ")"
    if isinstance(value, frozenset):
        return "{" + ",".join(sorted(_canonical_const(v) for v in value)) + "}"
    literal = bool | int | float | complex | str | bytes
    if value is None or value is Ellipsis or isinstance(value, literal):
        return f"{type(value).__name__}:{value!r}"
    raise Unfingerprintable(f"constant of type {type(value).__name__} has no canonical form")


def _canonical_code(code: CodeType) -> str:
    """A code object as text: its header, its instruction stream, its exception ranges.

    The instruction stream rather than `co_code`, because an operand is an index into a
    table whose contents depend on what the compiler happened to leave behind.
    `dis.get_instructions` resolves each operand to the value it names - the constant,
    the variable name, the comparison - so two programs that execute the same operations
    over the same values read the same here whatever their tables look like.

    Jump targets are rewritten from byte offsets to positions in this instruction list,
    so a jump over a region whose encoding changed length still matches.
    """
    instructions = [i for i in dis.get_instructions(code) if i.opname != "CACHE"]
    index_of = {i.offset: n for n, i in enumerate(instructions)}

    stream = []
    for item in instructions:
        if item.argval is None and item.arg is None:
            stream.append(item.opname)
            continue
        if isinstance(item.argval, CodeType):
            stream.append(f"{item.opname}({_canonical_code(item.argval)})")
            continue
        if item.opname in dis.hasjump or item.opname in getattr(dis, "hasjrel", ()):
            target = index_of.get(item.argval)
            stream.append(f"{item.opname}(->{target if target is not None else item.argval})")
            continue
        # argrepr distinguishes the operands dis encodes as opaque integers - BINARY_OP
        # 0 is `+` and 10 is `-`, COMPARE_OP 2 is `<` and 42 is `<=` - and argval
        # carries the value for everything else.
        stream.append(f"{item.opname}({_canonical_const_or_repr(item.argval)}|{item.argrepr})")

    parts = [f"{name}={getattr(code, name)!r}" for name in _SEMANTIC if hasattr(code, name)]
    # The docstring is the one constant no instruction loads: CPython puts it in
    # co_consts[0] and the interpreter reads it from there to build `__doc__`. Comparing
    # only the instruction stream lost it, and `__doc__` is observable - two functions
    # differing in their docstring are two programs, and calling them equivalent would
    # have been the first unsound answer this module gave.
    first = code.co_consts[0] if code.co_consts else None
    if isinstance(first, str) or first is None:
        parts.append(f"doc={first!r}")
    parts.append("ops=[" + ";".join(stream) + "]")
    # Raw, and compared rather than decoded: its format is a CPython implementation
    # detail. Two programs whose handler ranges encode differently are simply not
    # declared equivalent, which is the conservative direction.
    table = getattr(code, _EXCEPTION_TABLE, None)
    if table:
        parts.append(f"{_EXCEPTION_TABLE}={table.hex()}")
    return "<" + "|".join(parts) + ">"


def _canonical_const_or_repr(value: object) -> str:
    """A constant's canonical form, or its repr for an operand that is not one."""
    try:
        return _canonical_const(value)
    except Unfingerprintable:
        return f"{type(value).__name__}:{value!r}"


def fingerprint(source: str) -> str:
    """A digest of what `source` compiles to, ignoring where it was written.

    Two sources with the same fingerprint are the same program. The interpreter version
    is mixed in, because a fingerprint computed under one CPython says nothing about
    another and a cached one must not be reused across an upgrade.
    """
    try:
        code = compile(source, "<tce>", "exec")
    except (SyntaxError, ValueError, MemoryError, RecursionError) as exc:
        raise Unfingerprintable(str(exc)) from exc
    body = _canonical_code(code)
    marker = f"py{sys.version_info.major}.{sys.version_info.minor}|"
    return hashlib.sha256((marker + body).encode("utf-8", "surrogatepass")).hexdigest()


def partition(original: str, candidates: list[tuple[str, str]]) -> dict:
    """Split mutants into equivalent, duplicate and distinct, by what they compile to.

    `candidates` is the `(source, operator)` list this package's mutate.mutants returns.
    The return carries all three groups and the undecidable ones, because a filter that
    reports only what it kept cannot be checked - and the counts are the measurement:
    the share of a mutant set that TCE removes is the quantity Papadakis et al. report
    per language, and the Python number is not in the literature.

    The first mutant of a duplicate group is kept, so the result is deterministic in the
    order mutate.mutants produced - which is round-robin across operators, so the kept
    representative is not systematically drawn from the most common operator.
    """
    try:
        base = fingerprint(original)
    except Unfingerprintable:
        # Without the original's fingerprint nothing can be called equivalent, and
        # calling the whole set distinct is the conservative answer.
        return {
            "kept": list(candidates),
            "equivalent": [],
            "duplicate": [],
            "undecidable": [],
            "base": None,
        }

    kept: list[tuple[str, str]] = []
    equivalent: list[tuple[str, str]] = []
    duplicate: list[tuple[str, str, str]] = []
    undecidable: list[tuple[str, str]] = []
    seen: dict[str, str] = {}

    for source, operator in candidates:
        try:
            digest = fingerprint(source)
        except Unfingerprintable:
            undecidable.append((source, operator))
            kept.append((source, operator))
            continue
        if digest == base:
            equivalent.append((source, operator))
            continue
        if digest in seen:
            duplicate.append((source, operator, seen[digest]))
            continue
        seen[digest] = operator
        kept.append((source, operator))

    return {
        "kept": kept,
        "equivalent": equivalent,
        "duplicate": duplicate,
        "undecidable": undecidable,
        "base": base,
    }


def summarise(partitioned: dict) -> dict:
    """Counts only, for a report: what was planned, and what TCE removed."""
    kept = len(partitioned["kept"])
    equivalent = len(partitioned["equivalent"])
    duplicate = len(partitioned["duplicate"])
    planned = kept + equivalent + duplicate
    return {
        "planned": planned,
        "kept": kept,
        "equivalent": equivalent,
        "duplicate": duplicate,
        "undecidable": len(partitioned["undecidable"]),
        "equivalent_share": round(equivalent / planned, 6) if planned else None,
        "duplicate_share": round(duplicate / planned, 6) if planned else None,
    }
