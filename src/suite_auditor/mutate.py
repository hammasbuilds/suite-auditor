"""Single-point mutations, and the functions worth applying them to.

A mutant is a deliberate small wrong version of a function. If a suite cannot tell it from
the original, the suite has a gap there - subject to the mutant not being equivalent, which
is what the differential stage settles later.

The operators are chosen to be *plausible mistakes* rather than maximally destructive ones.
Deleting a function body produces a mutant every suite kills and teaches nothing. A `<`
that should be `<=` is the off-by-one somebody actually writes, and in `mbpp-false-accepts`
that operator survived three-assert suites 25.9% of the time - by far the highest of any.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

from suite_auditor.types import Target
from suite_auditor.workspace import JUNK_DIRS, is_test_path

SKIP_DIRS = JUNK_DIRS


# Every operator name, in one place. Two tests hardcoded this list separately and one
# of them broke the moment an operator was added - which is the harmless version of the
# problem. The harmful one is a report grouping results by operator and silently
# dropping a name nothing knows about.
OPERATORS = frozenset(
    {
        "compare",
        "binop",
        "boolop",
        "const",
        "negate_if",
        "drop_return",
        "drop_not",
        "swallow_except",
        "drop_assert",
        "drop_raise",
        "slice_lower",
        "slice_upper",
    }
)


class _Mutator(ast.NodeTransformer):
    """Applies exactly one change, selected by index, and names the operator used."""

    COMPARE = {
        ast.Lt: ast.LtE,
        ast.LtE: ast.Lt,
        ast.Gt: ast.GtE,
        ast.GtE: ast.Gt,
        ast.Eq: ast.NotEq,
        ast.NotEq: ast.Eq,
        # Membership and identity. `x in seen` guarding a cache and `x is None`
        # guarding a default are both decisions, and flipping either changes what the
        # function does - but neither was reachable before.
        ast.In: ast.NotIn,
        ast.NotIn: ast.In,
        ast.Is: ast.IsNot,
        ast.IsNot: ast.Is,
    }
    BINOP = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.FloorDiv}
    BOOLOP = {ast.And: ast.Or, ast.Or: ast.And}

    def __init__(self, target: int) -> None:
        self.target, self.seen, self.applied = target, 0, False
        self.kind = ""

    def _take(self, kind: str) -> bool:
        hit = self.seen == self.target
        self.seen += 1
        if hit:
            self.applied, self.kind = True, kind
        return hit

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        self.generic_visit(node)
        if node.ops and type(node.ops[0]) in self.COMPARE and self._take("compare"):
            node.ops[0] = self.COMPARE[type(node.ops[0])]()
        return node

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        self.generic_visit(node)
        if type(node.op) in self.BINOP and self._take("binop"):
            node.op = self.BINOP[type(node.op)]()
        return node

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
        self.generic_visit(node)
        if type(node.op) in self.BOOLOP and self._take("boolop"):
            node.op = self.BOOLOP[type(node.op)]()
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        # Booleans are ints in Python, and flipping True to 2 is not a plausible mistake.
        if isinstance(node.value, int) and not isinstance(node.value, bool) and self._take("const"):
            return ast.Constant(value=node.value + 1)
        return node

    def visit_If(self, node: ast.If) -> ast.AST:
        self.generic_visit(node)
        if self._take("negate_if"):
            node.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
        return node

    # --- operators added after the survey ---------------------------------------------
    #
    # The five above cover comparisons, arithmetic, boolean joins, integer constants and
    # branch polarity. Every one of these is a mistake a person actually makes and none
    # of them was reachable before. Recall goes up and precision does not move: a
    # survivor still has to fail the differential proof before it is called a gap, so a
    # generated mutant that no input can distinguish stays `unproven` and never reaches
    # the headline.

    def visit_Return(self, node: ast.Return) -> ast.AST:
        self.generic_visit(node)
        # `return x` -> `return None`. The commonest way a refactor loses a value, and
        # invisible to any test that only checks the function does not raise.
        returns_none = isinstance(node.value, ast.Constant) and node.value.value is None
        if node.value is not None and not returns_none and self._take("drop_return"):
            return ast.Return(value=ast.Constant(value=None))
        return node

    def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.AST:
        self.generic_visit(node)
        # Remove a `not`. negate_if only reaches `if` tests; this reaches every other
        # place a negation decides something - a while, a comprehension filter, a
        # returned predicate.
        if isinstance(node.op, ast.Not) and self._take("drop_not"):
            return node.operand
        return node

    def visit_Try(self, node: ast.Try) -> ast.AST:
        self.generic_visit(node)
        # Swallow the exception: replace each handler's body with `pass`. A suite that
        # only checks the happy path cannot tell the difference, and the failure this
        # hides in production is an error that silently became a success.
        if node.handlers and self._take("swallow_except"):
            for handler in node.handlers:
                handler.body = [ast.Pass()]
        return node

    def visit_Assert(self, node: ast.Assert) -> ast.AST:
        self.generic_visit(node)
        # Delete a guard. An `assert` inside library code is a contract; removing it
        # should change behaviour on the input that violates it, and if nothing notices,
        # nothing is testing the contract.
        if self._take("drop_assert"):
            return ast.Pass()
        return node

    def visit_Raise(self, node: ast.Raise) -> ast.AST:
        self.generic_visit(node)
        if self._take("drop_raise"):
            return ast.Pass()
        return node

    def visit_Slice(self, node: ast.Slice) -> ast.AST:
        self.generic_visit(node)
        # Off-by-one at a boundary: `a[1:]` -> `a[2:]`, `a[:n]` -> `a[:n - 1]`. The
        # single most common real bug in code that walks a sequence.
        if node.lower is not None and self._take("slice_lower"):
            node.lower = ast.BinOp(left=node.lower, op=ast.Add(), right=ast.Constant(1))
        elif node.upper is not None and self._take("slice_upper"):
            node.upper = ast.BinOp(left=node.upper, op=ast.Sub(), right=ast.Constant(1))
        return node


MAX_SITES = 600


def _every_mutant(source: str) -> list[tuple[str, str]]:
    """Every distinct single-point mutant of this function, in AST walk order."""
    try:
        base = ast.unparse(ast.parse(source))
    except SyntaxError:
        return []

    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for i in range(MAX_SITES):
        m = _Mutator(i)
        try:
            tree = m.visit(ast.parse(source))
        except (SyntaxError, RecursionError):
            continue
        if not m.applied:
            if i > m.seen:  # every site has been tried
                break
            continue
        ast.fix_missing_locations(tree)
        try:
            text = ast.unparse(tree)
        except (AttributeError, ValueError):
            continue
        if text != base and text not in seen:
            seen.add(text)
            out.append((text, m.kind))
    return out


def mutants(source: str, cap: int = 8) -> list[tuple[str, str]]:
    """Up to `cap` mutants, drawn round-robin across operators.

    The selection used to be "the first `cap` found in AST walk order", and that is a
    biased sample of the thing being measured. A function with many integer constants
    spends its whole budget on `const`; one with a long chain of `if`s spends it on
    `negate_if`. Measured across three repositories at the default cap:

        blast-radius     slice_upper  0 sampled of 11 available
                         slice_lower  0 of 6
                         drop_raise   0 of 1
        cartographer     const        34.0% of the sample, 27.4% of what exists
                         negate_if    14.0% of the sample, 21.5% of what exists

    Three operators were never sampled at all, so the kill rate said nothing about
    them - and it mattered, because operators do not have similar kill rates.
    `mbpp-false-accepts` measured a boundary comparison surviving three-assert suites
    25.9% of the time, by far the highest of any operator. A sample whose composition
    drifts with the shape of the code drags the headline number with it.

    Round-robin rather than proportional, deliberately. Proportional sampling would
    reproduce the natural mix and keep rare operators rare; round-robin guarantees every
    kind of mistake present in the function is represented. That changes what the kill
    rate means - "across the kinds of mistake available here, how many would the suite
    catch" rather than "weighted by how often each kind appears in this code" - and the
    first question is the one worth answering, because the second is dominated by
    whatever the file happens to contain most of.
    """
    everything = _every_mutant(source)
    if len(everything) <= cap:
        return everything

    by_kind: dict[str, list[tuple[str, str]]] = {}
    for item in everything:
        by_kind.setdefault(item[1], []).append(item)

    # Rarest operator first within each round, so a kind with one site is not crowded
    # out by one with forty when the cap falls mid-round.
    order = sorted(by_kind, key=lambda k: (len(by_kind[k]), k))
    out: list[tuple[str, str]] = []
    depth = 0
    while len(out) < cap:
        progressed = False
        for kind in order:
            bucket = by_kind[kind]
            if depth < len(bucket):
                out.append(bucket[depth])
                progressed = True
                if len(out) >= cap:
                    break
        if not progressed:
            break
        depth += 1
    return out


def package_context(path: Path) -> tuple[str, str]:
    """(sys.path entry, dotted package) so a function's own imports resolve."""
    parts: list[str] = []
    current = path.parent
    while (current / "__init__.py").is_file():
        parts.append(current.name)
        if current.parent == current:
            break
        current = current.parent
    return str(current.resolve()), ".".join(reversed(parts))


def _bound(node: ast.stmt) -> set[str]:
    out: set[str] = set()
    if isinstance(node, ast.Import | ast.ImportFrom):
        for a in node.names:
            out.add(a.asname or a.name.split(".")[0])
    elif isinstance(node, ast.Assign):
        for t in node.targets:
            out |= {n.id for n in ast.walk(t) if isinstance(n, ast.Name)}
    return out


def free_names(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {
        n.value.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
    }
    return names | attrs


def header_for(tree: ast.Module, source: str, needed: set[str]) -> str:
    """Only the imports and literal constants the function reads."""
    lines = source.splitlines()
    kept: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Import | ast.ImportFrom | ast.Assign):
            continue
        if isinstance(node, ast.Assign) and not isinstance(
            node.value, ast.Constant | ast.Tuple | ast.List | ast.Dict | ast.Set
        ):
            continue
        if not (_bound(node) & needed):
            continue
        kept.append("\n".join(lines[node.lineno - 1 : node.end_lineno]))
    return "\n".join(kept)


def _target(ctx: tuple, node, name: str) -> Target:
    rel_s, tree, src, lines, sys_path, package = ctx
    start = min([node.lineno] + [d.lineno for d in node.decorator_list])
    end = node.end_lineno or node.lineno
    body = "\n".join(lines[start - 1 : end])
    return Target(
        path=rel_s,
        name=name,
        source=body,
        header=header_for(tree, src, free_names(body)),
        sys_path=sys_path,
        package=package,
        lineno=start,
        end_lineno=end,
    )


def _python_files(repo: Path) -> list[Path]:
    """Every .py file worth considering, without descending into environments or caches.

    os.walk with pruning rather than rglob: a virtual environment inside the project
    holds tens of thousands of files, and walking it only to discard them was most of
    the time `coverage` spent before running anything.
    """
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo):
        here = Path(dirpath)
        keep = []
        for d in dirnames:
            if d in SKIP_DIRS or d.endswith(".egg-info"):
                continue
            if here == repo and d in ("build", "dist", "examples", "docs", "doc"):
                continue
            if (here / d / "pyvenv.cfg").is_file():
                continue
            keep.append(d)
        dirnames[:] = sorted(keep)
        found += [here / f for f in sorted(filenames) if f.endswith(".py")]
    return found


def find_targets(repo: Path, include_methods: bool = True) -> list[Target]:
    """Every function in the package that could be mutated, methods included."""
    out: list[Target] = []
    for p in _python_files(repo):
        rel = p.relative_to(repo)
        # By pytest's conventions, not by substring: "test" in the name used to skip
        # latest.py, contest.py and attestation.py as though they were tests.
        if is_test_path(rel.as_posix()):
            continue
        # Only shipped package code. `examples/` and `docs/` are not the library, and a
        # gap reported in one of them is noise in the report.
        #
        # Two shapes count as shipped. A file whose directory has __init__.py is part
        # of a package. A file directly under src/ is a single-module distribution -
        # `src/drift.py` with no __init__.py anywhere - which the __init__ rule alone
        # skipped entirely. docstring-drift is exactly that shape, and auditing it
        # produced 0 mutants and a kill rate of None: indistinguishable in the report
        # from a library with nothing worth mutating.
        in_package = (p.parent / "__init__.py").is_file()
        single_module = p.parent.name == "src" and p.parent.parent == repo
        if not (in_package or single_module):
            continue
        try:
            src = p.read_text(encoding="utf-8-sig")
            tree = ast.parse(src)
        except (SyntaxError, UnicodeDecodeError, OSError, ValueError):
            continue

        lines = src.splitlines()
        rel_s = str(rel).replace("\\", "/")
        sys_path, package = package_context(p)

        ctx = (rel_s, tree, src, lines, sys_path, package)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                out.append(_target(ctx, node, node.name))
            elif include_methods and isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, ast.FunctionDef | ast.AsyncFunctionDef):
                        out.append(_target(ctx, sub, f"{node.name}.{sub.name}"))
    return out
