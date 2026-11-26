"""Find every function that can start the agent loop, from the source tree.

The loop starts in `run_agent_loop_stream` and `run_chat_stream`. A function that
calls (or passes on) one of those is itself an entry, and so is anything that
calls that function, transitively, in any module. References are resolved through
imports so that `from app.api.routes import chatbot` followed by
`chatbot.askAIStream(...)` is followed, wherever the caller lives.

Followed: imports of every form (relative, dotted, re-exported), module-level
aliases including lambdas and `functools.partial`, `getattr(x, "literal")`,
`Cls().method` and `self.method` inside a class, defs nested in module-level
`if`/`try`/`with`/loop blocks, and BOM or coding-cookie sources. Methods are keyed
by qualified name (`module:Class.method`), so a method never borrows the
guarded status of a same-named route.

Not followed (a static scan cannot see them): `importlib` and other dynamic
imports, `getattr` with a computed name, attribute dispatch through instances
whose class is not visible at the call (`obj.method()` for a parameter `obj`),
code run from strings (`exec`), and code outside `app/**`. A module-level statement
(say `router.add_api_route(path, lambda ...: handler(...))`) is scanned as the
pseudo-function `module:<module>`, so a loop reference in it is reported. The
route-table checks catch a mounted handler on an existing loop path whatever way it
calls in, walk mounted sub-apps, and name any endpoint the scan cannot see into
(a lambda, a `functools.partial`, a closure, a callable object).

The agent loop also refuses at run time when the route guard did not run for the
request (`app.api.middlewares.identified_caller.loop_caller_refusal`), so
what this scan cannot see fails closed instead of running unguarded.
"""

from __future__ import annotations

import ast
import functools
import inspect
from dataclasses import dataclass, field
from pathlib import Path

SEEDS = {"run_agent_loop_stream", "run_chat_stream"}
APP_ROOT = Path(__file__).resolve().parents[3] / "app"
# The pseudo-function standing for a module's own top-level statements.
MODULE_LEVEL = "<module>"
# Set by a main that serves no HTTP routes, with the reason; a main without one must build `app`.
NO_HTTP_APP_MARKER = "NO_HTTP_APP"


def module_name(path: Path, root: Path = APP_ROOT) -> str:
    parts = list(path.relative_to(root.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def service_main_names(root: Path = APP_ROOT) -> list[str]:
    """Every service main under `root`, at any depth, as a dotted name below `app` ('query_main', 'pkg.x_main')."""
    return sorted(
        ".".join(p.relative_to(root).with_suffix("").parts)
        for p in root.rglob("*_main.py")
    )


def read_tree(root: Path = APP_ROOT) -> dict[str, bytes]:
    # Bytes, so that `ast.parse` honours a UTF-8 BOM or a coding cookie exactly as the interpreter does.
    return {module_name(p, root): p.read_bytes() for p in sorted(root.rglob("*.py"))}


@dataclass
class Graph:
    # "module:qualname" -> the "module:qualname" targets it references (resolved through imports)
    references: dict[str, set[str]] = field(default_factory=dict)
    # functions decorated as a route (`@something.get/post/...`)
    decorated_routes: set[str] = field(default_factory=set)
    # "module:name" bound at module level to something else (`from x import f`, `g = f`,
    # `g = lambda: f()`, `g = partial(f)`): followed like a call but never reported itself.
    aliases: set[str] = field(default_factory=set)
    # every def, keyed "module:name" or "module:Class.name"
    functions: set[str] = field(default_factory=set)
    reaching: set[str] = field(default_factory=set)
    # modules that could not be parsed; a caller must treat any entry as a failure
    parse_errors: dict[str, str] = field(default_factory=dict)


ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "api_route", "websocket"}
# Statements whose bodies run at module (or class) level, so a def inside them is still a module member.
_BLOCKS = (
    ast.If,
    ast.Try,
    ast.TryStar,
    ast.With,
    ast.AsyncWith,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Match,
)


def _imports(tree: ast.Module, module: str, known: set[str]) -> dict[str, str]:
    """Local name -> dotted target: a module ('a.b') or a member ('a.b:name')."""
    # A package's `__init__` is named after the package, so `from . import x` there means the package itself.
    is_package = any(other.startswith(f"{module}.") for other in known)
    mapping: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mapping[(alias.asname or alias.name).split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = module.split(".")
                drop = node.level - 1 if is_package else node.level
                anchor = parts[: len(parts) - drop] if drop <= len(parts) else []
                base = (
                    ".".join([*anchor, base]).strip(".") if base else ".".join(anchor)
                )
            for alias in node.names:
                local = alias.asname or alias.name
                mapping[local] = (
                    f"{base}.{alias.name}"
                    if f"{base}.{alias.name}" in known
                    else f"{base}:{alias.name}"
                )
    return mapping


def _block_bodies(node: ast.AST) -> list[list[ast.stmt]]:
    bodies = [getattr(node, name, []) for name in ("body", "orelse", "finalbody")]
    bodies += [handler.body for handler in getattr(node, "handlers", [])]
    bodies += [case.body for case in getattr(node, "cases", [])]
    return bodies


def _members(
    body: list[ast.stmt], prefix: str = ""
) -> tuple[list[tuple[str, ast.AST, str]], list[ast.stmt], list[ast.stmt]]:
    """Defs as (qualname, node, enclosing class qualname), module-level assignments, and module-level
    expression statements (calls run at import), at any block depth."""
    defs: list[tuple[str, ast.AST, str]] = []
    assigns: list[ast.stmt] = []
    exprs: list[ast.stmt] = []
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs.append((f"{prefix}{node.name}", node, prefix.rstrip(".")))
        elif isinstance(node, ast.ClassDef):
            inner, _, _ = _members(node.body, f"{prefix}{node.name}.")
            defs.extend(inner)
        elif isinstance(node, _BLOCKS):
            if not prefix:
                # The block's own header (an `if` test, a `with` item) runs at import too.
                exprs.extend(
                    ast.Expr(value=part)
                    for part in (
                        getattr(node, "test", None),
                        getattr(node, "iter", None),
                        getattr(node, "subject", None),
                    )
                    if part is not None
                )
                exprs.extend(
                    ast.Expr(value=item.context_expr)
                    for item in getattr(node, "items", [])
                )
            for block in _block_bodies(node):
                inner, inner_assigns, inner_exprs = _members(block, prefix)
                defs.extend(inner)
                assigns.extend(inner_assigns)
                exprs.extend(inner_exprs)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and not prefix:
            assigns.append(node)
        elif isinstance(node, ast.Expr) and not prefix:
            exprs.append(node)
    return defs, assigns, exprs


def _dotted(node: ast.AST) -> list[str] | None:
    """`a.b().c` -> ['a', 'b', 'c']: a call in the chain is read as its callee (`Cls().m` is `Cls.m`)."""
    names: list[str] = []
    while True:
        if isinstance(node, ast.Call):
            node = node.func
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
            node = node.value
        else:
            break
    if not isinstance(node, ast.Name):
        return None
    names.append(node.id)
    return names[::-1]


def _getattr_constant(node: ast.AST) -> ast.AST | None:
    """`getattr(x, "name"[, default])` -> the equivalent `x.name`."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        return ast.Attribute(
            value=node.args[0], attr=node.args[1].value, ctx=ast.Load()
        )
    return None


class _Resolver:
    def __init__(
        self, module: str, local: set[str], imports: dict[str, str], known: set[str]
    ) -> None:
        self.module, self.local, self.imports, self.known = (
            module,
            local,
            imports,
            known,
        )

    def targets(self, node: ast.AST, cls: str = "") -> set[str]:
        """Every 'module:qualname' that `name`, `mod.name`, `Cls().m`, `self.m` or `pkg.mod.name` may mean."""
        parts = _dotted(node)
        if not parts:
            return set()
        head, rest = parts[0], parts[1:]
        if head in {"self", "cls"} and cls:
            return self._members_of(self.module, [*cls.split("."), *rest])
        if head in self.local:
            return self._members_of(self.module, [head, *rest])
        target = self.imports.get(head)
        if not target:
            return set()
        if ":" in target:
            owner, name = target.split(":", 1)
            return self._members_of(owner, [name, *rest])
        # `import a.b.c` binds `a`; `a.b.c.f` is member `f` of the longest known module prefix.
        dotted = [*target.split("."), *rest]
        for cut in range(len(dotted) - 1, 0, -1):
            if ".".join(dotted[:cut]) in self.known:
                return self._members_of(".".join(dotted[:cut]), dotted[cut:])
        return self._members_of(target, rest) if rest else set()

    @staticmethod
    def _members_of(owner: str, names: list[str]) -> set[str]:
        return {f"{owner}:{'.'.join(names[:n])}" for n in range(1, len(names) + 1)}

    def references(self, node: ast.AST, cls: str = "") -> set[str]:
        """All targets and seed names referenced anywhere inside `node`."""
        refs: set[str] = set()
        for child in ast.walk(node):
            alias = _getattr_constant(child)
            for candidate in (child, alias) if alias is not None else (child,):
                if isinstance(candidate, (ast.Name, ast.Attribute)):
                    refs |= self.targets(candidate, cls)
                    name = (
                        candidate.id
                        if isinstance(candidate, ast.Name)
                        else candidate.attr
                    )
                    if name in SEEDS:
                        refs.add(f"seed:{name}")
        return refs


def build_graph(sources: dict[str, str | bytes]) -> Graph:
    known = set(sources)
    graph = Graph()
    for module, text in sources.items():
        try:
            tree = ast.parse(
                text.removeprefix("\ufeff") if isinstance(text, str) else text
            )
        except (SyntaxError, ValueError) as exc:
            graph.parse_errors[module] = f"{type(exc).__name__}: {exc}"
            continue
        defs, assigns, exprs = _members(tree.body)
        local = {qualname.split(".")[0] for qualname, _, _ in defs}
        local |= {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
        bound = {
            target.id
            for node in assigns
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            if isinstance(target, ast.Name)
        }
        resolver = _Resolver(
            module, local | bound, _imports(tree, module, known), known
        )
        for name, target in resolver.imports.items():
            if ":" in target and f"{module}:{name}" != target:
                graph.aliases.add(f"{module}:{name}")
                graph.references.setdefault(f"{module}:{name}", set()).add(target)
        for node in assigns:
            if node.value is None:
                continue
            refs = resolver.references(node.value)
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            ):
                if isinstance(target, ast.Name):
                    key = f"{module}:{target.id}"
                    graph.aliases.add(key)
                    graph.references.setdefault(key, set()).update(refs - {key})
        if exprs:
            # Top-level statements run at import, with no request: a loop reference there is an entry.
            key = f"{module}:{MODULE_LEVEL}"
            graph.functions.add(key)
            graph.references.setdefault(key, set()).update(
                ref for node in exprs for ref in resolver.references(node)
            )
        for qualname, fn, cls in defs:
            key = f"{module}:{qualname}"
            graph.functions.add(key)
            # nested defs and lambdas count toward their enclosing function
            graph.references.setdefault(key, set()).update(
                resolver.references(fn, cls) - {key}
            )
            for deco in fn.decorator_list:  # type: ignore[attr-defined]
                if (
                    isinstance(deco, ast.Call)
                    and isinstance(deco.func, ast.Attribute)
                    and deco.func.attr in ROUTE_METHODS
                ):
                    graph.decorated_routes.add(key)
    reaching = {
        k
        for k, refs in graph.references.items()
        if any(r.startswith("seed:") for r in refs)
    }
    changed = True
    while changed:
        changed = False
        for key, refs in graph.references.items():
            if key not in reaching and refs & reaching:
                reaching.add(key)
                changed = True
    graph.reaching = reaching
    return graph


def violations(
    graph: Graph, guarded_endpoints: set[str], allowlist: dict[str, str]
) -> list[str]:
    """Entries that are neither a guarded route nor on the reviewed allowlist."""
    return sorted(
        k
        for k in graph.reaching
        if (k in graph.functions or k not in graph.aliases)
        and k not in guarded_endpoints
        and k not in allowlist
    )


def endpoint_key(endpoint: object) -> tuple[str, bool]:
    """('module:qualname', opaque) for a route endpoint.

    Opaque means the source scan cannot see what it calls: a `functools.partial`
    (keyed by the function it wraps), a lambda, a closure, or a callable object.
    """
    target, opaque = endpoint, False
    while isinstance(target, functools.partial):
        target, opaque = target.func, True
    if not (inspect.isfunction(target) or inspect.ismethod(target)):
        target, opaque = type(target), True
    qualname = getattr(target, "__qualname__", None) or type(target).__qualname__
    module = getattr(target, "__module__", None) or type(target).__module__
    opaque = opaque or "<lambda>" in qualname or "<locals>" in qualname
    return f"{module}:{qualname}", opaque


def _mounted_routes(route: object) -> list | None:
    """The routes of a mounted sub-app (`Mount`, `Host`), looking through ASGI middleware wrappers."""
    from starlette.routing import Host, Mount

    if not isinstance(route, (Mount, Host)):
        return None
    app = getattr(route, "_base_app", None) or route.app
    while not hasattr(app, "routes") and hasattr(app, "app"):
        app = app.app
    return list(getattr(app, "routes", None) or [])


def route_rows(routes: list, prefix: str = "") -> list[dict]:
    """One row per route, mounted sub-apps included: methods, path, endpoint ('module:function'),
    whether the guard applies, and whether the endpoint is opaque to the source scan."""
    from app.api.middlewares.identified_caller import require_identified_caller

    rows = []
    for route in routes:
        children = _mounted_routes(route)
        if children is not None:
            rows.extend(route_rows(children, prefix + getattr(route, "path", "")))
            continue
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        # A plain Starlette route has no dependencies, so it is never guarded.
        pending, calls = (
            [d for d in [getattr(route, "dependant", None)] if d is not None],
            set(),
        )
        while pending:
            dependant = pending.pop()
            calls.add(dependant.call)
            pending.extend(dependant.dependencies)
        key, opaque = endpoint_key(endpoint)
        rows.append(
            {
                "methods": sorted(getattr(route, "methods", None) or ["WEBSOCKET"]),
                "path": prefix + getattr(route, "path", ""),
                "endpoint": key,
                "guarded": require_identified_caller in calls,
                "opaque": opaque,
            }
        )
    return rows


def main_routes(name: str) -> dict:
    """Route rows of one service app, `app/<name>.py` (`name` dotted), as it is built for this edition.

    `has_app` says whether the main defines a module-level FastAPI `app`, so a caller can refuse a
    main that would otherwise drop out of the route checks. `no_http_app` is the reason the main
    gives, in `NO_HTTP_APP`, for serving no HTTP routes at all.
    """
    import importlib

    from fastapi import FastAPI

    module = importlib.import_module(f"app.{name}")
    service = getattr(module, "app", None)
    reason = getattr(module, NO_HTTP_APP_MARKER, None)
    if not isinstance(service, FastAPI):
        return {"has_app": False, "no_http_app": reason, "middleware": [], "rows": []}
    return {
        "has_app": True,
        "no_http_app": reason,
        "middleware": [
            getattr(m.cls, "__name__", repr(m.cls)) for m in service.user_middleware
        ],
        "rows": [{**row, "service": name} for row in route_rows(service.routes)],
    }


if __name__ == "__main__":
    # Run as `python -m tests.unit.api.agent_loop_entry_scan <main> <out.json>`, one main per process:
    # the mains install signal handlers and set log names on import, and one at a time keeps the
    # memory peak to the largest app. The result goes to a file, since importing the apps writes to stdout.
    import json
    import sys

    Path(sys.argv[2]).write_text(json.dumps(main_routes(sys.argv[1])), encoding="utf-8")
