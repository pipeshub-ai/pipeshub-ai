"""Static SQL checks for code that talks to the database through ``KernelDB``.

* :func:`scan_file` finds, in string literals passed to ``fetch`` / ``fetchrow`` / ``fetchval`` / ``execute`` /
  ``executemany``: session-level SQL that survives past a transaction, string-built SQL, and untagged statements.
* :func:`org_predicate_violations` (needs ``sqlglot``) requires ``<alias>.org_id = $n`` on every reference to an
  org-partitioned table, in the same scope. A correlation (``c.org_id = p.org_id``) does not count.

The runtime module itself is exempt: it is the only place allowed to set a role or a setting.
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
_SQL_START = re.compile(r"\s*(?:/\*(?:[^*]|\*(?!/))*\*/\s*)*(select|insert|update|delete|with|merge|set|reset|declare|create|listen|unlisten|"
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


def _sql_text(node: ast.expr) -> tuple[str | None, bool]:
    """(constant text or None, is_string_built)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, False
    if isinstance(node, ast.JoinedStr):
        text = "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
        return text, any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        left, _ = _sql_text(node.left)
        return left, True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        text, _ = _sql_text(node.func.value)
        return text, True
    return None, False


def scan_source(source: str, path: str = "<src>", *, require_tag: bool = True) -> list[Finding]:
    found: list[Finding] = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _CALLS and node.args):
            continue
        text, built = _sql_text(node.args[0])
        if text is None or not (_SQL_START.match(text) or (built and _KEYWORDS.search(text))):
            continue  # not SQL (an HTTP client's execute, a Redis call, ...)
        if built:
            found.append(Finding(path, node.lineno, "string-built SQL (use bound parameters)"))
        found += [Finding(path, node.lineno, r) for r in sql_rules(text)]
        if require_tag and not built and not _TAG.search(text):
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
