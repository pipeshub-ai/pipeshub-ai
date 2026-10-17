"""Every connector type a suite asks for must be one the service registers.

The registry is a plain dict keyed on the name passed to @ConnectorBuilder,
matched exactly, spaces included. A suite that spells it differently gets
"Connector type ... not found in registry" -- but only once it runs, and the
suites that need a real tenant (SharePoint needs Microsoft 365) run only in the
nightly. The SharePoint suite asked for "SharePointOnline" against a connector
registered as "SharePoint Online", and nothing said so until a nightly got far
enough to try.

This reads source rather than importing it: no stack, no connector
dependencies, so it can run on every pull request.
"""

from __future__ import annotations

import ast
import difflib
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SOURCES = _REPO / "backend" / "python" / "app" / "connectors" / "sources"
_APP = _REPO / "backend" / "python" / "app"
_SUITES = _REPO / "integration-tests"


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return None


def _string_constants() -> dict[str, str]:
    """Module-level ``NAME = "..."`` and class-level ``Class.ATTR = "..."``.

    Some connectors register under a named constant rather than a literal
    (``OutlookConnectorNames.TEAM``); resolving those is what lets this tell a
    real registration from a missing one.
    """
    table: dict[str, str] = {}

    def record(target: ast.expr, value: ast.expr, prefix: str) -> None:
        if isinstance(target, ast.Name) and isinstance(value, ast.Constant) and isinstance(value.value, str):
            table[prefix + target.id] = value.value

    for path in _APP.rglob("*.py"):
        tree = _parse(path)
        if tree is None:
            continue
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    record(target, node.value, "")
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                record(node.target, node.value, "")
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.Assign):
                        for target in item.targets:
                            record(target, item.value, node.name + ".")
                    elif isinstance(item, ast.AnnAssign) and item.value is not None:
                        record(item.target, item.value, node.name + ".")
    return table


def _registered_names() -> tuple[set[str], list[str]]:
    """Names passed to ConnectorBuilder, and any this cannot resolve."""
    constants = _string_constants()
    names: set[str] = set()
    unresolved: list[str] = []
    for path in _SOURCES.rglob("*.py"):
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ConnectorBuilder"):
                continue
            arg = node.args[0] if node.args else None
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                names.add(arg.value)
            elif arg is not None and ast.unparse(arg) in constants:
                names.add(constants[ast.unparse(arg)])
            else:
                where = f"{path.relative_to(_REPO)}:{node.lineno}"
                unresolved.append(f"{where}  ConnectorBuilder({ast.unparse(arg) if arg else ''})")
    return names, unresolved


def _module_file(module: str, *, relative_to: Path | None = None, level: int = 0) -> Path | None:
    """The file an import names: ``connectors.notion.constants`` or ``.constants``."""
    if level:
        base = relative_to.parent if relative_to else _SUITES
        for _ in range(level - 1):
            base = base.parent
    else:
        base = _SUITES
    target = base.joinpath(*module.split(".")) if module else base
    for candidate in (target.with_suffix(".py"), target / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


class _ModuleStrings:
    """String constants a module defines or imports, resolved per module.

    Per module on purpose: a table keyed only by the identifier would mix the
    suites up, since ``CONNECTOR_TYPE`` is a different connector in each.
    """

    def __init__(self) -> None:
        self._cache: dict[Path, dict[str, str]] = {}

    def of(self, path: Path, _seen: frozenset[Path] = frozenset()) -> dict[str, str]:
        if path in self._cache:
            return self._cache[path]
        if path in _seen:
            return {}
        tree = _parse(path)
        strings: dict[str, str] = {}
        if tree is not None:
            for node in tree.body:
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            strings[target.id] = node.value.value
                elif (
                    isinstance(node, ast.AnnAssign)
                    and isinstance(node.target, ast.Name)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    strings[node.target.id] = node.value.value
                elif isinstance(node, ast.ImportFrom):
                    source = _module_file(node.module or "", relative_to=path, level=node.level)
                    if source is None:
                        continue
                    imported = self.of(source, _seen | {path})
                    for alias in node.names:
                        if alias.name in imported:
                            strings[alias.asname or alias.name] = imported[alias.name]
        self._cache[path] = strings
        return strings


def _suite_calls(tree: ast.Module):
    """Each ``connector_type=`` value with the parameter names in scope for it."""

    def walk(node: ast.AST, params: frozenset[str]):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                a = child.args
                names = {x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
                names |= {x.arg for x in (a.vararg, a.kwarg) if x is not None}
                yield from walk(child, params | names)
                continue
            if isinstance(child, ast.Call):
                for kw in child.keywords:
                    if kw.arg == "connector_type":
                        yield kw.value, params
            yield from walk(child, params)

    yield from walk(tree, frozenset())


def _requested_types() -> tuple[list[tuple[str, str]], list[str]]:
    """``connector_type=`` values the suites pass, and any this cannot resolve.

    A value is a literal or a string constant, defined in the same file or
    followed through its import. One made only of the enclosing function's own
    parameters is being forwarded, and the caller that supplied it is counted
    instead. Anything else is reported rather than skipped: a guard that
    quietly checks less is how the SharePoint name went unnoticed.

    Only ``integration-tests/connectors`` is read. ``helper/`` passes along
    whatever a suite gave it and never names a connector type itself.
    """
    strings = _ModuleStrings()
    requested: list[tuple[str, str]] = []
    unresolved: list[str] = []
    for path in sorted(_SUITES.joinpath("connectors").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = _parse(path)
        if tree is None:
            continue
        known = strings.of(path)
        for value, params in _suite_calls(tree):
            where = f"{path.relative_to(_REPO)}:{value.lineno}"
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                requested.append((value.value, where))
            elif isinstance(value, ast.Name) and value.id in known:
                requested.append((known[value.id], where))
            elif (names := {n.id for n in ast.walk(value) if isinstance(n, ast.Name)}) and names <= params:
                continue
            else:
                unresolved.append(f"{where}  connector_type={ast.unparse(value)}")
    return requested, unresolved


def test_every_registration_resolves_to_a_name() -> None:
    _, unresolved = _registered_names()
    assert not unresolved, (
        "These @ConnectorBuilder registrations pass something this guard cannot "
        "read as a string, so it cannot check the suites against them. Pass a "
        "string literal, or a module- or class-level string constant:\n  "
        + "\n  ".join(unresolved)
    )


def test_every_suite_connector_type_resolves_to_a_string() -> None:
    _, unresolved = _requested_types()
    assert not unresolved, (
        "These suites pass a connector_type this guard cannot read as a string, "
        "so it cannot check them. Pass a string literal, or a string constant "
        "defined in the file or imported from one:\n  " + "\n  ".join(unresolved)
    )


def test_every_requested_connector_type_is_registered() -> None:
    registered, _ = _registered_names()
    requested, _ = _requested_types()
    assert requested, "found no connector_type= in any suite -- this guard is reading the wrong place"

    unknown = [(name, where) for name, where in requested if name not in registered]
    lines = []
    for name, where in unknown:
        close = difflib.get_close_matches(name, registered, n=1, cutoff=0.6)
        hint = f' -- did you mean "{close[0]}"?' if close else ""
        lines.append(f'{where}  "{name}"{hint}')
    assert not unknown, (
        "These suites ask for a connector type the service does not register. "
        "The registry matches the @ConnectorBuilder name exactly, spaces "
        "included, so each would fail with \"not found in registry\" the first "
        "time it ran:\n  " + "\n  ".join(lines)
    )
