"""Every statement of the PostgreSQL graph provider that reads or writes ``graph.nodes`` / ``graph.edges`` binds the org of each table it names:
``org_id = $n``, or ``org_id`` equal to the ``org_id`` of a table that is bound.

Row-level security would hide a statement that forgot it, so no integration test can tell the filter from the policy; this reads the statements.
It is weaker than ``sql_lint.org_predicate_violations`` (which wants the predicate in each scope and qualified): a table is bound by any equality in the statement."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytest.importorskip("sqlglot")
import re

from sqlglot import exp, parse_one

PROVIDER = Path(__file__).resolve().parents[4] / "app" / "services" / "graph_db" / "postgres"
MODULES = [p.name for p in sorted(PROVIDER.glob("*.py")) if p.name not in ("__init__.py", "provider.py")]      # every module that holds statements
PARTITIONED = {"graph.nodes", "graph.edges"}
CALLS = {"fetch", "fetchrow", "fetchval", "execute", "_qrows", "_lrows", "_drows", "_rows", "_one", "_id_rows"}      # the helpers pass their SQL on


def unbound_tables(sql: str) -> list[str]:
    """Aliases of partitioned tables that have no ``org_id = $n`` (or correlation to a table that has) in their own SELECT / UPDATE / DELETE."""
    tree = parse_one(re.sub(r"\$(\d+)", r":p\1", sql).replace("~>=~", ">=").replace("~<~", "<"), read="postgres")
    inserted = {t for ins in tree.find_all(exp.Insert) for t in ins.this.find_all(exp.Table)}      # an insert sets org_id itself (WITH CHECK holds it)
    param_bound = {c.table for e in tree.find_all(exp.EQ) for c in (e.left, e.right) if isinstance(c, exp.Column) and c.name == "org_id"
                   and any(isinstance(x, (exp.Placeholder, exp.Parameter)) for x in (e.left, e.right))}
    pairs = [(e.left.table, e.right.table) for e in tree.find_all(exp.EQ)
             if isinstance(e.left, exp.Column) and isinstance(e.right, exp.Column) and e.left.name == e.right.name == "org_id"]
    for _ in pairs:          # a table correlated to a bound one is bound too
        param_bound |= {x for a, b in pairs for x in (a, b) if a in param_bound or b in param_bound}
    bad = []
    for t in tree.find_all(exp.Table):
        if not t.db or f"{t.db}.{t.name}" not in PARTITIONED or t in inserted:
            continue
        alias = t.alias_or_name
        scope = t.find_ancestor(exp.Select, exp.Delete, exp.Update)
        target = isinstance(scope, (exp.Delete, exp.Update)) and scope.this is t
        eqs = list(scope.find_all(exp.EQ)) if scope is not None else []
        bound = False
        for e in eqs:
            cols = [c for c in (e.left, e.right) if isinstance(c, exp.Column) and c.name == "org_id"]
            param = any(isinstance(x, (exp.Placeholder, exp.Parameter)) for x in (e.left, e.right))
            if cols and param and (cols[0].table == alias or (not cols[0].table and (alias == t.name or target))):
                bound = True
            elif len(cols) == 2 and alias in {c.table for c in cols}:
                bound |= next(c.table for c in cols if c.table != alias) in param_bound      # correlated to a table that is bound (e.g. the outer query)
        if not bound:
            bad.append(alias)
    return sorted(bad)


def _text(node: ast.expr) -> str | None:
    """The literal text of a string or of an f-string (its formatted parts left out), else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
    return None


def statements(path: Path) -> list[tuple[int, str]]:
    """The SQL passed to a call: a literal at the call site, or a module constant passed by name."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    consts = {t.id: n.value for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in CALLS and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Name) and arg.id in consts:
                arg = consts[arg.id]
            text = _text(arg)
            if text is not None and "graph." in text:
                out.append((node.lineno, text))
    return out


@pytest.mark.parametrize("module", MODULES)
def test_every_statement_binds_the_org_in_its_own_scope(module: str) -> None:
    found = statements(PROVIDER / module)
    bad = [(line, aliases) for line, sql in found if (aliases := unbound_tables(sql))]
    assert bad == [], f"{module}: statements with a graph table and no org predicate in their scope: {bad}"


def test_the_scan_finds_the_statements_of_the_provider() -> None:
    assert sum(len(statements(PROVIDER / m)) for m in MODULES) > 50, "the scan finds almost nothing: it is broken"


def test_the_scan_catches_a_statement_that_forgot_the_org() -> None:
    sql = "WITH d AS (DELETE FROM graph.nodes n WHERE n.coll = ANY($2::text[]) AND n.id = ANY($3::text[]) RETURNING 1) SELECT count(*) FROM d"
    assert unbound_tables(sql) == ["n"]
    assert unbound_tables(sql.replace("WHERE n.coll", "WHERE n.org_id = $1 AND n.coll")) == []
    assert unbound_tables("DELETE FROM graph.nodes WHERE coll = $2") == ["nodes"]


@pytest.mark.parametrize("module", [p.name for p in sorted(PROVIDER.glob("*.py"))])
def test_no_statement_of_the_provider_is_built_by_formatting(module: str) -> None:
    """The runtime SQL guard sees only calls named fetch/execute; the provider's own helpers pass their SQL on, so they are read here."""
    built = []
    tree = ast.parse((PROVIDER / module).read_text(encoding="utf-8"))
    consts = {t.id: n.value for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in CALLS and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Name) and arg.id in consts:
                arg = consts[arg.id]
            text = "".join(v.value for v in getattr(arg, "values", []) if isinstance(v, ast.Constant) and isinstance(v.value, str))
            if (isinstance(arg, ast.JoinedStr) and any(isinstance(v, ast.FormattedValue) for v in arg.values) and "graph." in text) or isinstance(arg, ast.BinOp):
                built.append(node.lineno)
    assert built == [], f"{module}: statements built by formatting at lines {built}"
