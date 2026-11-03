"""Static SQL checks for code that talks to the database through ``KernelDB``.

* :func:`scan_source` finds, in string literals passed to (or assigned to a local name that is passed to) ``fetch`` / ``fetchrow`` / ``fetchval`` / ``execute`` /
  ``executemany``: session-level SQL that survives past a transaction, string-built SQL, and untagged statements.
* :func:`org_predicate_violations` (needs ``sqlglot``) requires ``<alias>.org_id = $n`` on every reference to an
  org-partitioned table, in the same scope. A correlation (``c.org_id = p.org_id``) does not count.

The runtime module itself is exempt: it is the only place allowed to set a role or a setting.

String building is followed where it is visible in one function: an f-string, ``%``, ``.format``, ``+`` with a non-constant operand,
``q += <non-constant>``, a conditional expression with a built branch, the argument or a ``query=`` / ``sql=`` keyword. Joining pure
constants (``"SELECT ..." + "WHERE a = $1"``, ``q += " AND b = $2"``) is not building. Known limits (not seen): statements built with
``str.join``, through an alias (``r = q``), in a module-level f-string constant, in another function that returns the text, or by a wrapper
helper such as ``_q(tx, f"...")``; names that arrive as arguments. The pin tests of the modules that build statements cover those.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

_CALLS = {"fetch", "fetchrow", "fetchval", "execute", "executemany"}
_SQL_START = re.compile(r"\s*(?:(?:/\*(?:[^*]|\*(?!/))*\*/|--[^\n]*)\s*)*(select|insert|update|delete|with|merge|set|reset|declare|create|listen|unlisten|"
                        r"prepare|execute|deallocate|discard|close|fetch|move|savepoint|release|rollback|commit|begin|end|abort|start)\b", re.I | re.S)
_TAG = re.compile(r"/\*\s*pipeshub:[A-Za-z0-9_.]{1,80}\s*\*/")
_COMMENTS = re.compile(r"/\*.*?\*/|--[^\n]*", re.S)
_STARTS = {
    "session SET / RESET": re.compile(r"^(set|reset)\b"),
    "DECLARE (cursor)": re.compile(r"^declare\b"),
    "LISTEN / UNLISTEN": re.compile(r"^(listen|unlisten)\b"),
    "SQL PREPARE / EXECUTE / DEALLOCATE": re.compile(r"^(prepare|execute|deallocate)\b"),
    "DISCARD": re.compile(r"^discard\b"),
    "CLOSE / MOVE cursor": re.compile(r"^(close|move)\b"),
    "transaction control or savepoint outside tx.savepoint()": re.compile(
        r"^(savepoint|release|rollback|commit|begin|end|abort|start\s+transaction)\b"),
}
_ANYWHERE = {
    "set_config(..., false)": re.compile(r"set_config\s*\([^;]*,\s*(false|0|'f'|'false')\s*\)", re.I),
    "re-binding set_config('app.*' | 'role' | 'session_authorization', ...)": re.compile(
        r"set_config\s*\(\s*'(app\.|role|session_authorization)", re.I),
    "WITH HOLD": re.compile(r"\bwith\s+hold\b", re.I),
    "advisory lock class reserved to the kernel (7340, 7341, 7350)": re.compile(r"\bpg_(try_)?advisory_xact_lock(_shared)?\s*\(\s*(7340|7341|7350)\b", re.I),
    "session advisory lock": re.compile(r"\bpg_(try_)?advisory_(lock|unlock)(_shared|_all)?\s*\(", re.I),
}
_TEMP = re.compile(r"^create\s+(global\s+|local\s+)?(temp|temporary)\s+table\b")
_KEYWORDS = re.compile(r"\b(select|insert\s+into|update|delete\s+from|with)\b", re.I)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}"


def sql_rules(sql: str) -> list[str]:
    """Rules a SQL text breaks (empty when clean). Tags and comments are ignored."""
    out = [name for name, rx in _ANYWHERE.items() if rx.search(_COMMENTS.sub(" ", sql))]
    for stmt in _COMMENTS.sub(" ", sql).split(";"):
        s = " ".join(stmt.lower().split())
        out += [name for name, rx in _STARTS.items() if rx.match(s)]
        if _TEMP.match(s) and "on commit drop" not in s:
            out.append("CREATE TEMP TABLE without ON COMMIT DROP")
    return out


_MAX_CANDIDATES = 16
_ALT = "\0"      # separates the texts of the alternatives (branches, assignments) that may reach one call
_Env = dict[str, list[tuple[ast.expr, bool]]]       # name -> (assigned value, assigned by ``+=``) for each assignment
_STATEMENT_KEYWORDS = {"query", "sql", "statement", "stmt"}


class _Unresolvable(Exception):
    """The text a call is given is assembled in a way this module does not model exactly."""


def _bounded(texts: Iterable[str]) -> list[str]:
    out = list(dict.fromkeys(texts))
    if len(out) > _MAX_CANDIDATES:
        raise _Unresolvable
    return out


def _pure(node: ast.expr, env: _Env, seen: tuple[str, ...] = ()) -> str | None:
    """The text of a pure string constant: a literal, a ``+`` of pure constants, or a name assigned exactly once to one. Else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and all(isinstance(v, ast.Constant) for v in node.values):
        return "".join(str(v.value) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _pure(node.left, env, seen), _pure(node.right, env, seen)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.Name) and node.id not in seen and len(env.get(node.id, [])) == 1:
        value, augmented = env[node.id][0]
        return None if augmented else _pure(value, env, (*seen, node.id))
    return None


def _sql_text(node: ast.expr, env: _Env | None = None) -> tuple[str | None, bool]:
    """(constant text or None, is_string_built). Joining pure constants (``"a" + "b"``, ``PREFIX + "..."``) is not building."""
    env = env or {}
    pure = _pure(node, env)
    if pure is not None:
        return pure, False
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)), True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Mod):
        left, _ = _sql_text(node.left, env)
        right, _ = _sql_text(node.right, env) if isinstance(node.op, ast.Add) else (None, False)
        both = _bounded(a + b for a in (left or "").split(_ALT) for b in (right or "").split(_ALT))      # every pairing of the alternatives
        return (_ALT.join(both) or None), True
    if isinstance(node, ast.IfExp):
        (a, a_built), (b, b_built) = _sql_text(node.body, env), _sql_text(node.orelse, env)
        both = [t for t in (a, b) if t is not None]
        return (_ALT.join(both) or None), a_built or b_built        # every branch is checked: a clean branch must not hide the other
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        text, _ = _sql_text(node.func.value, env)
        return text, True
    return None, False


_BRANCHING = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.Match)
_LOOPS = (ast.For, ast.AsyncFor, ast.While)


def _local_assignments(call: ast.Call, parents: dict[ast.AST, ast.AST]) -> tuple[_Env, set[str], set[str]]:
    """The values assigned to plain local names in the functions around ``call`` (``q = ...``, ``q: str = ...``, ``q += ...``), and the
    module's own plain assignments (for names of constants). Only simple assignments are followed: not names that arrive as arguments, and not
    aliases, joins or wrapper helpers (see the module docstring). The third result is the names bound in a way that is not modelled exactly
    (a ``+=`` inside a branch or loop, an assignment in a loop, a tuple, ``for``, ``with`` or walrus target, another augmented operator)."""
    out: _Env = {}
    odd: set[str] = set()

    def within(n: ast.AST, scope: ast.AST, kinds: tuple[type, ...]) -> bool:
        while (n := parents.get(n)) is not None and n is not scope:
            if isinstance(n, kinds):
                return True
        return False

    def own(scope: ast.AST) -> Iterable[ast.AST]:
        """The nodes of a function body, without those of the functions nested in it (they have their own names)."""
        todo = list(ast.iter_child_nodes(scope))
        while todo:
            n = todo.pop()
            yield n
            if not isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                todo.extend(ast.iter_child_nodes(n))

    def collect(scope: ast.AST, *, walk: bool) -> None:
        for n in (sorted(own(scope), key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0))) if walk else getattr(scope, "body", [])):
            if walk and isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and not isinstance(parents.get(n), ast.Assign | ast.AnnAssign | ast.AugAssign):
                odd.add(n.id)
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        out.setdefault(t.id, []).append((n.value, False))
                        if walk and within(n, scope, _LOOPS):
                            odd.add(t.id)
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None:
                out.setdefault(n.target.id, []).append((n.value, False))
            elif walk and isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name):
                out.setdefault(n.target.id, []).append((n.value, True))
                if not isinstance(n.op, ast.Add | ast.Mod) or within(n, scope, _BRANCHING):
                    odd.add(n.target.id)

    node: ast.AST | None = call
    scopes: list[ast.AST] = []
    while node is not None:
        node = parents.get(node)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            scopes.append(node)
        elif isinstance(node, ast.Module):
            collect(node, walk=False)      # module-level constants first: a local name of the same spelling replaces them below
    module = dict(out)
    out.clear()
    for scope in reversed(scopes):         # outermost first
        a = scope.args
        for param in (*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg):
            if param:
                out.pop(param.arg, None)   # a parameter shadows what an enclosing function assigned to that name
                odd.discard(param.arg)
        collect(scope, walk=True)
    return {**module, **out}, set(out) | odd, odd


def _statement(arg: ast.expr, call: ast.Call, parents: dict[ast.AST, ast.AST]) -> tuple[str | None, bool]:
    """(constant text or None, is_string_built) of the statement a call is given: the argument itself, or what the local name it passes
    was assigned (built when any assignment builds it; ``q += x`` builds unless ``x`` is a pure constant)."""
    env, local, odd = _local_assignments(call, parents)
    if isinstance(arg, ast.Name):
        if arg.id in odd:
            raise _Unresolvable
        if arg.id not in local:
            return None, False           # a module constant or an argument: not followed
    text, built = _sql_text(arg, env)
    if text is not None or not isinstance(arg, ast.Name):
        return text, built
    built, cands = False, []
    for value, augmented in env.get(arg.id, []):
        value_text, value_built = _sql_text(value, env)
        built = built or value_built or (augmented and _pure(value, env) is None)
        if augmented:                      # a ``+=`` extends every candidate assigned before it, for every alternative it has
            cands = _bounded(c + t for c in cands or [""] for t in (value_text or "").split(_ALT))
        elif value_text is not None:
            cands = _bounded([*cands, *value_text.split(_ALT)])     # a plain assignment adds candidates
        elif not isinstance(value, ast.Constant):
            raise _Unresolvable          # an alias, call, join or anything else not modelled
    return (_ALT.join(cands) or None), built


def _statement_argument(node: ast.Call) -> ast.expr | None:
    if node.args:
        return node.args[0]
    return next((k.value for k in node.keywords if k.arg in _STATEMENT_KEYWORDS), None)


def scan_source(source: str, path: str = "<src>", *, require_tag: bool = True) -> list[Finding]:
    found: list[Finding] = []
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _CALLS):
            continue
        arg = _statement_argument(node)
        if arg is None:
            continue
        try:
            text, built = _statement(arg, node, parents)
        except _Unresolvable:
            found.append(Finding(path, node.lineno, "statement not resolvable (use a literal with bound parameters)"))
            continue
        if text is None:
            continue
        alts = [a for a in text.split(_ALT) if _SQL_START.match(a) or (built and _KEYWORDS.search(a))]   # the rest are not SQL (an HTTP client's execute, ...)
        if not alts:
            continue
        if built:
            found.append(Finding(path, node.lineno, "string-built SQL (use bound parameters)"))
        for a in alts:         # each alternative is checked on its own: a tagged one must not vouch for an untagged one
            found += [Finding(path, node.lineno, r) for r in sql_rules(a)]
            if require_tag and not built and not _TAG.search(a):
                found.append(Finding(path, node.lineno, "statement without a /* pipeshub:<module>.<fn> */ tag"))
    return found


def scan_paths(paths: Iterable[Path], *, exempt: Iterable[Path] = (), require_tag: bool = True) -> list[Finding]:
    skip = [Path(e).resolve() for e in exempt]
    out: list[Finding] = []
    for p in paths:
        if any(s == p.resolve() or s in p.resolve().parents for s in skip):   # a directory or a single file
            continue
        out += scan_source(p.read_text(encoding="utf-8"), str(p), require_tag=require_tag)
    return out


def org_predicate_violations(sql: str, partitioned: set[str]) -> list[str]:
    """Aliases of org-partitioned table references without ``<alias>.org_id = $n`` in their own scope."""
    import sqlglot
    from sqlglot import exp

    tree = sqlglot.parse_one(re.sub(r"\$(\d+)", r":p\1", sql), read="postgres")
    bad: list[str] = []
    for t in tree.find_all(exp.Table):
        if (f"{t.db}.{t.name}" if t.db else t.name) not in partitioned:
            continue
        alias = t.alias_or_name
        scope = t.find_ancestor(exp.Select)
        conds = [scope.args.get("where")] + [j.args.get("on") for j in scope.args.get("joins") or []] if scope else []
        ok = False
        for c in conds:
            for eq in c.find_all(exp.EQ) if c else []:
                sides = (eq.left, eq.right)
                col = [s for s in sides if isinstance(s, exp.Column) and s.name == "org_id" and s.table == alias]
                par = [s for s in sides if isinstance(s, (exp.Placeholder, exp.Parameter))]
                ok |= bool(col and par)
        if not ok:
            bad.append(alias)
    return bad
