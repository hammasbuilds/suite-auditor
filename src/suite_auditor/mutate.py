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
from pathlib import Path

from suite_auditor.types import Target

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".tox",
    ".nox",
    ".mypy_cache",
    ".pytest_cache",
    ".eggs",
    ".ruff_cache",
    "node_modules",
    "site-packages",
}


class _Mutator(ast.NodeTransformer):
    """Applies exactly one change, selected by index, and names the operator used."""

    COMPARE = {
        ast.Lt: ast.LtE,
        ast.LtE: ast.Lt,
        ast.Gt: ast.GtE,
        ast.GtE: ast.Gt,
        ast.Eq: ast.NotEq,
        ast.NotEq: ast.Eq,
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


def mutants(source: str, cap: int = 8) -> list[tuple[str, str]]:
    """[(mutant source, operator)] - distinct, and never equal to the original."""
    try:
        base = ast.unparse(ast.parse(source))
    except SyntaxError:
        return []

    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for i in range(cap * 4):
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
        if len(out) >= cap:
            break
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


def find_targets(repo: Path, include_methods: bool = True) -> list[Target]:
    """Every function in the package that could be mutated, methods included."""
    out: list[Target] = []
    for p in sorted(repo.rglob("*.py")):
        rel = p.relative_to(repo)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if "test" in p.name or "tests" in rel.parts:
            continue
        # Only shipped package code. `examples/` and `docs/` are not the library, and a
        # gap reported in one of them is noise in the report.
        if not (p.parent / "__init__.py").is_file():
            continue
        try:
            src = p.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except (SyntaxError, UnicodeDecodeError, OSError):
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
