"""Find every route that checks for an org admin, by reading the source.

The admin-route suite keeps its own table of routes and how to call each one.
This module is what checks that table against the code: it reads the Express
routers under ``backend/nodejs/apps/src``, finds each route whose handler chain
includes an admin middleware (or that is registered after a
``router.use(<admin middleware>)``), and joins its path to the prefix
``app.ts`` mounts the router at. A new admin route that nobody added to the
table then fails a test, instead of shipping with no check that members are
refused.

The Python services have no admin middleware: a handler calls a check such as
``_validate_admin_only`` or ``fetch_caller_role(...).is_admin`` itself, and
some only refuse members for certain inputs. So the Python side is found as
"every route handler that reaches an admin check", and the suite's table has
to say of each one whether it is admin-only (and tested) or conditional.

Reading source rather than an OpenAPI spec: the spec is written by hand and
does not mark which routes need an admin, so it cannot answer the question.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NODE_SRC = REPO / "backend" / "nodejs" / "apps" / "src"
PYTHON_APP = REPO / "backend" / "python" / "app"

# Every middleware that refuses a signed-in member for not being an org admin.
# userAdminOrSelfCheck is left out on purpose: members pass it for their own id.
ADMIN_MIDDLEWARES = frozenset({"userAdminCheck", "adminValidator"})

_METHODS = ("get", "post", "put", "patch", "delete")
_ROUTE_CALL = re.compile(r"\brouter\s*\.\s*(get|post|put|patch|delete|use)\s*\(")
_FACTORY = re.compile(r"export\s+(?:function\s+|const\s+)(create\w+Router)\b")
_MOUNT = re.compile(r"this\.app\.use\(\s*'([^']+)'\s*,\s*(create\w+Router)\s*\(", re.S)
_STRING = re.compile(r"""^\s*(['"`])(.*?)\1\s*$""", re.S)


@dataclass(frozen=True)
class DiscoveredRoute:
    method: str
    path: str
    source: str
    # Middleware that runs before the admin check. A validator here means a
    # member's request must be well formed to reach the check at all.
    before_admin: tuple[str, ...]
    # Middleware between the admin check and the handler. A validator here is
    # what stops an admin's deliberately malformed request before any write.
    after_admin: tuple[str, ...] = ()


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), text, flags=re.S)
    return re.sub(r"(?<![:'\"`])//[^\n]*", lambda m: " " * len(m.group(0)), text)


def _call_arguments(text: str, open_paren: int) -> list[str]:
    """Top-level arguments of the call whose ``(`` is at ``open_paren``."""
    depth = 0
    args: list[str] = []
    start = open_paren + 1
    i = open_paren
    quote: str | None = None
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                args.append(text[start:i])
                return [a.strip() for a in args if a.strip()]
        elif ch == "," and depth == 1:
            args.append(text[start:i])
            start = i + 1
        i += 1
    raise ValueError(f"unbalanced call at offset {open_paren}")


def _middleware_name(arg: str) -> str:
    """``userAdminCheck`` for ``userAdminCheck``, ``ValidationMiddleware.validate``
    for ``ValidationMiddleware.validate(schema)``, the whole text otherwise."""
    head = arg.split("(", 1)[0].strip()
    return re.sub(r"\.bind$", "", head)


def _is_admin(arg: str) -> bool:
    return _middleware_name(arg) in ADMIN_MIDDLEWARES


def router_mounts(node_src: Path = NODE_SRC) -> dict[str, str]:
    """Router factory name -> the prefix ``app.ts`` mounts it at."""
    app_ts = (node_src / "app.ts").read_text(encoding="utf-8")
    return {factory: prefix for prefix, factory in _MOUNT.findall(app_ts)}


def _join(prefix: str, path: str) -> str:
    joined = prefix.rstrip("/") + ("" if path == "/" else "/" + path.lstrip("/"))
    return joined or "/"


def admin_routes_in_file(path: Path, prefix: str) -> list[DiscoveredRoute]:
    text = _strip_comments(path.read_text(encoding="utf-8"))
    found: list[DiscoveredRoute] = []
    # Express applies router.use() middleware to the routes registered after it.
    admin_from_here = False
    used_before: list[str] = []
    for match in _ROUTE_CALL.finditer(text):
        method = match.group(1)
        args = _call_arguments(text, match.end() - 1)
        if method == "use":
            if args and _STRING.match(args[0]):
                continue  # a sub-path mount, not a middleware for every route
            if any(_is_admin(a) for a in args):
                admin_from_here = True
            elif not admin_from_here:
                used_before.extend(n for n in map(_middleware_name, args) if n)
            continue
        if not args:
            continue
        literal = _STRING.match(args[0])
        if not literal:
            continue
        chain = args[1:]
        admin_at = next((i for i, a in enumerate(chain) if _is_admin(a)), None)
        if admin_at is None and not admin_from_here:
            continue
        before = tuple(used_before) + tuple(
            _middleware_name(a) for a in (chain[:admin_at] if admin_at is not None else ())
        )
        # The last argument is the handler itself.
        after = chain[(admin_at + 1 if admin_at is not None else 0):-1]
        found.append(DiscoveredRoute(
            method=method.upper(),
            path=_join(prefix, literal.group(2)),
            source=str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path),
            before_admin=before,
            after_admin=tuple(_middleware_name(a) for a in after),
        ))
    return found


def discover_admin_routes(node_src: Path = NODE_SRC) -> list[DiscoveredRoute]:
    """Every admin-gated route the Node API serves, with its full path."""
    mounts = router_mounts(node_src)
    routes: list[DiscoveredRoute] = []
    unmounted: list[str] = []
    for path in sorted(node_src.rglob("*.ts")):
        if "/tests/" in str(path) or path.name.endswith((".test.ts", ".spec.ts")):
            continue
        text = path.read_text(encoding="utf-8")
        if not any(name in text for name in ADMIN_MIDDLEWARES):
            continue
        factories = _FACTORY.findall(text)
        if not factories or not _ROUTE_CALL.search(text):
            continue
        prefixes = {mounts[f] for f in factories if f in mounts}
        if len(prefixes) != 1:
            unmounted.append(f"{path.relative_to(REPO)} (factories {factories})")
            continue
        routes.extend(admin_routes_in_file(path, prefixes.pop()))
    if unmounted:
        raise AssertionError(
            "Router files that use an admin middleware but whose mount prefix "
            "could not be read from app.ts: " + ", ".join(unmounted)
        )
    return routes


# Calls that ask whether the caller is an org admin in the Python services.
PYTHON_ADMIN_CHECKS = frozenset({
    "is_request_admin",
    "fetch_caller_role",
    "check_user_is_admin",
    "_check_user_is_admin",
    "_validate_admin_only",
    "_require_admin_for_builtin_availability",
    "_assert_admin_owns_shared_credential",
})
_HTTP_DECORATORS = frozenset(_METHODS)


def _names_called(node: ast.AST) -> set[str]:
    called: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                called.add(func.id)
            elif isinstance(func, ast.Attribute):
                called.add(func.attr)
    return called


def _route_decorator(fn: ast.AST) -> tuple[str, str] | None:
    for deco in getattr(fn, "decorator_list", []):
        if (
            isinstance(deco, ast.Call)
            and isinstance(deco.func, ast.Attribute)
            and deco.func.attr in _HTTP_DECORATORS
            and deco.args
            and isinstance(deco.args[0], ast.Constant)
            and isinstance(deco.args[0].value, str)
        ):
            return deco.func.attr.upper(), deco.args[0].value
    return None


def discover_python_admin_handlers(app_root: Path = PYTHON_APP) -> dict[str, str]:
    """``"<file under app>::<handler>"`` -> ``"<METHOD> <path>"`` for every
    FastAPI route handler that reaches an admin check.

    "Reaches" follows calls to other functions in the same module, since the
    check often sits in a shared helper (``_accept_vector_store_job``,
    ``get_validated_connector_instance``) rather than in the handler.
    """
    found: dict[str, str] = {}
    for path in sorted(app_root.rglob("*.py")):
        if "/tests/" in str(path):
            continue
        text = path.read_text(encoding="utf-8")
        if not any(name in text for name in PYTHON_ADMIN_CHECKS):
            continue
        tree = ast.parse(text)
        functions = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        calls = {name: _names_called(fn) for name, fn in functions.items()}
        checking: set[str] = set()
        grew = True
        while grew:
            grew = False
            for name, called in calls.items():
                if name not in checking and called & (PYTHON_ADMIN_CHECKS | checking):
                    checking.add(name)
                    grew = True
        for name, fn in functions.items():
            route = _route_decorator(fn)
            if route and name in checking:
                key = f"{path.relative_to(app_root).as_posix()}::{name}"
                found[key] = f"{route[0]} {route[1]}"
    return found
