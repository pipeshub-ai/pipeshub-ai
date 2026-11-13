"""No reader takes the copied columns of ``kg.records`` (rule, model, origin, vrid, external_id, record_type) from kg.

The projector writes a row whose value the records writer refuses with that column empty (``kg.project``) and takes only the row's connector off the readers. That is safe only while
no access check or listing reads those columns from kg: the stamps are compiled from the graph, and the one reader of a copied column, the listing's ``record_type`` filter, reads
only connectors kg serves.

This test parses every statement that names ``kg.records`` (sqlglot, the PostgreSQL dialect, as ``app.core.db.sql_lint`` does) and follows each column to the table it comes from:
a copied column (or the whole row) counts when it reaches what a statement returns or a ``WHERE``, ``JOIN``, ``GROUP BY``, ``HAVING`` or ``ORDER BY``; a subquery's column that
nothing outside it reads does not. The statements are those of the application's modules (a string, an f-string whose holes are module constants, a concatenation of those; a
fragment that continues a statement is joined to it), the statements the listing composes (rendered by its builders), the migrations' SQL files and the vetted manifests' bodies.
A statement that does not parse fails the test unless :data:`UNPARSED` names it with a reason; a statement composed at run time must be one of the rendered ones.
"""
from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest
import sqlglot
import yaml
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, build_scope

# sqlglot is a dev dependency: imported, not skipped, so the guard cannot go missing
BACKEND = Path(__file__).resolve().parents[5]
TABLE = "kg.records"
COPIED = frozenset({"rule", "model", "origin", "vrid", "external_id", "record_type"})
LISTING = "python/app/services/graph_db/postgres/kg/listing.py"

# (source, what): the reads that may take a copied column ("*": the whole row, or anything of the source)
ALLOWED = {
    (LISTING, "record_type"),                                                       # the listing's filter: a connector kg serves only
    ("python/app/services/graph_db/postgres/kg/verify.py", "*"),                    # the verify compares the whole row with the graph: what must see the difference
}
# (source, tag or start of the statement): statements that name kg.records and that sqlglot does not parse, with the reason they need no reading
UNPARSED: dict[tuple[str, str], str] = {}

_VERB = re.compile(r"(?:SELECT|WITH|INSERT|UPDATE|DELETE)\b", re.I)
_CONTINUES = re.compile(r"^\s*(?:FROM|WHERE|AND|OR|JOIN|LEFT|RIGHT|INNER|CROSS|FULL|ON|ORDER|GROUP|HAVING|LIMIT|OFFSET|RETURNING|USING)\b", re.I)
_TAG = re.compile(r"/\*\s*pipeshub:([\w.]+)\s*\*/")


def _starts_statement(text: str) -> bool:
    """The text starts a statement, after any leading ``/* ... */`` comments. A scan, not a regex: a run of comments takes linear time whatever it holds."""
    s = text.lstrip()
    while s.startswith("/*"):
        end = s.find("*/", 2)
        if end < 0:
            return False
        s = s[end + 2:].lstrip()
    return _VERB.match(s) is not None


class Unparsed(Exception):
    def __init__(self, sql: str) -> None:
        super().__init__(sql)
        self.key = _key(sql)


def _key(sql: str) -> str:
    m = _TAG.search(sql)
    return m.group(1) if m else " ".join(sql.split())[:60]


def _parse(sql: str) -> list[exp.Expression]:
    """Every statement of a text (``a; b`` is two)."""
    try:
        return [t for t in sqlglot.parse(re.sub(r"\$(\d+)", r":p\1", sql), read="postgres") if t is not None]
    except Exception as e:      # any parse error: the statement is named
        raise Unparsed(sql) from e


# --- lineage --------------------------------------------------------------------------------------------------------------------------------------------------------------------


def _is_table(src: object) -> bool:
    return isinstance(src, exp.Table) and f"{src.db}.{src.name}".lower() == TABLE


def _own_columns(node: exp.Expression) -> list[exp.Column]:
    """The columns of ``node`` that are not inside a query nested in it (those are their own scope's). Read from the tree, not from ``Scope.columns``, which leaves out a
    bare column it takes for a possible alias (``HAVING max(record_type) = ...``)."""
    out = []
    for c in node.find_all(exp.Column):
        a = c
        while a is not node and a is not None and not isinstance(a, (exp.Select, exp.SetOperation)):
            a = a.parent
        if a is node or a is None:          # no query lies between the column and the node
            out.append(c)
    return out


def _projections(scope: Scope) -> list[exp.Expression]:
    e = scope.expression
    return list(e.expressions) if isinstance(e, exp.Select) else []


def _stars(scope: Scope, node: exp.Expression) -> list[str | None]:
    """The stars ``node`` selects in ``scope``: None for ``*``, the table for ``t.*``."""
    if isinstance(node, exp.Star):
        return [None]
    if isinstance(node, exp.Column) and isinstance(node.this, exp.Star):
        return [node.table]
    return []


def _returns_read(scope: Scope) -> bool:
    """Whether what a scope returns is read as such: the statement's, a subquery in an expression, and a union branch when its union's is. A derived table's, a CTE's or a
    lateral subquery's columns are read only through what reads them."""
    parent = scope.parent
    if parent is None:
        return True
    if scope.is_union:          # a branch: as visible as the union it is part of (the parent scope holds the union)
        return _returns_read(parent)
    return not (scope.is_derived_table or scope.is_cte or (scope.is_subquery and parent.is_udtf))


def _children(scope: Scope) -> list[Scope]:
    return [*scope.union_scopes, *scope.subquery_scopes, *scope.derived_table_scopes, *scope.udtf_scopes]


def _leaves(scope: Scope) -> list[Scope]:
    """The SELECTs of a union, nested unions included; the scope itself otherwise."""
    return [leaf for u in scope.union_scopes for leaf in _leaves(u)] if scope.union_scopes else [scope]


def _exported(scope: Scope) -> list[str]:
    """The column names a derived table, CTE or union exports: its alias list, else its first SELECT's names (a star exports nothing it can name)."""
    first = _projections(_leaves(scope)[0])
    return list(scope.outer_columns) or [p.alias_or_name for p in first if not _stars(scope, p)]


def _target(tree: exp.Expression) -> exp.Table | None:
    """The table an INSERT, UPDATE or DELETE writes."""
    t = tree.this
    return t.this if isinstance(t, exp.Schema) else t if isinstance(t, exp.Table) else None


_CLAUSES = ("where", "group", "having", "order", "qualify", "distinct", "windows")
_QUERY = (exp.Select, exp.SetOperation)
_INCOMING = "excluded"      # ON CONFLICT DO UPDATE: the row an INSERT proposes, not a stored one


class _Walker:
    """Follows the columns a statement reads to the tables they come from, collecting the copied columns of kg.records (and "*" for its whole row). ``outer`` names the
    sources of a write (its table, its FROM or USING) for the queries nested in it, whose correlated columns name them."""

    def __init__(self) -> None:
        self.found: set[str] = set()
        self.outer: dict[str, object] = {}
        self.work: list[tuple[Scope | None, exp.Expression]] = []
        self.seen: set[tuple[int, int]] = set()
        self.followed: set[tuple[int, str | None]] = set()

    def sources(self, scope: Scope | None, alias: str) -> list[object]:
        """What ``alias`` names in ``scope``, a scope around it (a correlated column) or the write around them; every source of the nearest level for a column with no table."""
        s = scope
        while s is not None:
            if alias and alias in s.sources:
                return [s.sources[alias]]
            if not alias:
                return list(s.sources.values())
            s = s.parent
        if not alias:
            return [v for k, v in self.outer.items() if k != _INCOMING]
        return [self.outer[alias]] if alias in self.outer and alias != _INCOMING else []

    def whole_row(self, scope: Scope | None, c: exp.Column) -> bool:
        """A bare name that is the alias (or the name) of kg.records is the whole row as a value (``SELECT r``, ``to_jsonb(r)``, ``RETURNING kg_row``), unless a column of
        that name is in reach first. Only the columns of a derived table or CTE are known here: beside a stored table whose columns are not, the name is taken as the row."""
        if c.table:
            return False
        s = scope
        while s is not None:
            if any(c.name in _exported(x) for x in s.sources.values() if isinstance(x, Scope) and isinstance(x.expression, _QUERY)):
                return False
            if c.name in s.sources:
                return _is_table(s.sources[c.name])
            s = s.parent
        if any(c.name in _exported(x) for x in self.outer.values() if isinstance(x, Scope) and isinstance(x.expression, _QUERY)):
            return False
        return c.name in self.outer and _is_table(self.outer[c.name])

    def add_query(self, root: Scope, *, returned: bool) -> None:
        """Every scope of a query: its clauses are read, and what it returns when ``returned`` (else only through what reads it)."""
        for scope in root.traverse():
            e = scope.expression
            if not isinstance(e, exp.Select):
                continue
            for k in _CLAUSES:          # a clause is one node or, for WINDOW, a list of them
                v = e.args.get(k)
                self.work += [(scope, n) for n in (v if isinstance(v, list) else [v]) if n is not None]
            for j in e.args.get("joins") or []:
                if j.args.get("on") is not None:
                    self.work.append((scope, j.args["on"]))
                for u in j.args.get("using") or []:         # USING (a): the column a of both sides
                    for x in scope.sources.values():
                        self.follow(x, u.name)
            if _returns_read(scope) and (returned or scope is not root):
                self.work += [(scope, p) for p in _projections(scope)]

    def follow(self, src: object, name: str | None) -> None:
        """A column ``name`` (None: every column) of ``src`` is read."""
        if (id(src), name) in self.followed:      # a recursive CTE reads itself
            return
        self.followed.add((id(src), name))
        if _is_table(src):
            if name is None:
                self.found.add("*")
            elif name in COPIED:
                self.found.add(name)
            return
        if not isinstance(src, Scope):
            return
        if not isinstance(src.expression, _QUERY):     # a lateral: its subquery makes the columns
            for c in _children(src):
                self.follow(c, name)
            return
        leaves = _leaves(src)
        first = _projections(leaves[0])
        if any(_stars(leaves[0], p) for p in first):         # the columns come from a star: by name, through it
            for b in leaves:
                for p in _projections(b):
                    for t in _stars(b, p):
                        for x in self.sources(b, t or ""):
                            self.follow(x, None if src.outer_columns else name)
                    if not _stars(b, p) and (name is None or p.alias_or_name == name):
                        self.work.append((b, p))
            return
        # the names the scope exports (its alias list, ``c(v)`` or ``x(v)``, else its first SELECT's), and the position each has in every SELECT of it
        names = list(src.outer_columns) or [p.alias_or_name for p in first]
        positions = range(len(names)) if name is None else [i for i, n in enumerate(names) if n == name]
        for b in leaves:
            projs = _projections(b)
            self.work.extend((b, projs[i]) for i in positions if i < len(projs))

    def drain(self) -> set[str]:
        while self.work:
            scope, node = self.work.pop()
            if (id(scope), id(node)) in self.seen:
                continue
            self.seen.add((id(scope), id(node)))
            for t in _stars(scope, node):
                for x in self.sources(scope, t or ""):
                    self.follow(x, None)
            for c in _own_columns(node):
                if isinstance(c.this, exp.Star):
                    if c.table != _INCOMING:
                        for x in self.sources(scope, c.table):
                            self.follow(x, None)
                    continue
                if self.whole_row(scope, c):
                    self.found.add("*")
                for x in self.sources(scope, c.table):
                    self.follow(x, c.name)
        return self.found


def _write_sources(tree: exp.Expression) -> list[exp.Expression]:
    """The tables and subqueries a write reads besides its own table: UPDATE's FROM (and its joins), DELETE's USING."""
    out: list[exp.Expression] = []
    frm = tree.args.get("from")
    if frm is not None:
        out += [frm.this, *frm.expressions]
    out += [j.this for j in tree.args.get("joins") or []]
    out += list(tree.args.get("using") or [])
    return [x for x in out if x is not None]


def _top_queries(node: exp.Expression) -> list[exp.Expression]:
    """The queries under ``node`` that no other query of it holds."""
    out = []
    for q in node.find_all(*_QUERY):
        a = q.parent
        while a is not None and a is not node and not isinstance(a, _QUERY):
            a = a.parent
        if a is node or a is None:
            out.append(q)
    return out


def copied_reads(tree: exp.Expression) -> set[str]:
    """The copied columns of kg.records (and "*" for the whole row) that a statement reads: into what it returns (a query's columns, a RETURNING list), or into a condition,
    a join (ON or USING), a grouping, a window, a DISTINCT ON or an order; for a write also the values it sets (SET, ON CONFLICT DO UPDATE) and what it inserts from a query."""
    w = _Walker()
    if not isinstance(tree, (exp.Insert, exp.Update, exp.Delete)):
        root = build_scope(tree)
        if root is not None:
            w.add_query(root, returned=True)
        return w.drain()
    target = _target(tree)
    if target is not None:
        w.outer[target.alias_or_name] = target
        w.outer.setdefault(target.name, target)
    nested_from: set[int] = set()
    using: list[str] = []

    def register(x: exp.Expression) -> None:
        """A source of the write, and the sources sqlglot hangs on it as its joins (``FROM a JOIN b ON ...``, ``FROM a, b``, the same in USING)."""
        inner = x.this if isinstance(x, exp.Lateral) else x
        if isinstance(inner, exp.Subquery):
            root = build_scope(inner.this)
            if root is not None:
                w.outer[x.alias_or_name or inner.alias_or_name] = root
                w.add_query(root, returned=False)          # a FROM subquery: its columns are read through what reads them
                nested_from.add(id(inner.this))
        elif isinstance(inner, exp.Table):
            w.outer[inner.alias_or_name] = inner
        for j in x.args.get("joins") or []:
            register(j.this)
            if j.args.get("on") is not None:
                w.work.append((None, j.args["on"]))
            using.extend(u.name for u in j.args.get("using") or [])

    for x in _write_sources(tree):
        register(x)
    for name in using:          # USING (a): the column a of both sides, once every source is known
        for src in w.sources(None, ""):
            w.follow(src, name)
    # what the write itself reads, at its own level: its condition, the values it sets (a column set is written, not read), its RETURNING, its ON CONFLICT DO UPDATE
    conflict = tree.args.get("conflict")
    read = [tree.args.get("where"), tree.args.get("returning")]
    read += [a.expression for a in (tree.expressions if isinstance(tree, exp.Update) else []) if isinstance(a, exp.EQ)]
    if conflict is not None:
        read += [a.expression for a in conflict.expressions if isinstance(a, exp.EQ)] + [conflict.args.get("where")]
    for n in read:
        if n is not None:
            w.work.append((None, n))
    ret = tree.args.get("returning")
    for r in ret.expressions if ret is not None else []:
        if isinstance(r, exp.Star):
            w.follow(target, None)
    # every query nested in the write (a subquery of its condition, its values, an INSERT's SELECT): what it returns is read, and its correlated columns name the write's sources
    for q in _top_queries(tree):
        if id(q) not in nested_from:
            root = build_scope(q)
            if root is not None:
                w.add_query(root, returned=True)
    return w.drain()


# --- statements -----------------------------------------------------------------------------------------------------------------------------------------------------------------


def reads(texts: list[str], consts: dict[str, str] | None = None) -> set[str]:
    """The copied columns read by the statements of one source, given as its SQL texts in order: ``{NAME}`` is the constant ``NAME``, a text that continues a statement
    (``FROM ...``, ``AND ...``) is joined to the one before it, a statement that does not parse raises :class:`Unparsed`."""
    stmts: list[str] = []
    for text in texts:
        t = re.sub(r"\{(\w+)\}", lambda m: consts.get(m.group(1), m.group(0)), text) if consts else text
        if _starts_statement(t):
            stmts.append(t)
        elif stmts and _CONTINUES.match(t):
            stmts[-1] += t
    found: set[str] = set()
    for s in stmts:
        if TABLE in s.lower():
            for tree in _parse(s):
                found |= copied_reads(tree)
    return found


def _constants(tree: ast.Module) -> dict[str, str]:
    """The module's string constants, by name (a concatenation or an f-string of other constants included)."""
    out: dict[str, str] = {}
    for _ in range(3):           # constants built from constants defined above them
        for n in tree.body:
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                v = _literal(n.value, out, {})
                if v is not None:
                    out[n.targets[0].id] = v
    return out


def _modules(tree: ast.Module) -> dict[str, str]:
    """Names the module imports application modules under (``from app.x import listing as kgl``)."""
    out = {}
    for n in tree.body:
        if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("app."):
            for a in n.names:
                out[a.asname or a.name] = f"{n.module}.{a.name}"
    return out


def _literal(node: ast.expr, consts: dict[str, str], mods: dict[str, str]) -> str | None:
    """The text of a string, an f-string or a concatenation whose parts are all known; None when a part is computed."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in mods:
        try:
            v = getattr(importlib.import_module(mods[node.value.id]), node.attr, None)
        except ImportError:
            return None
        return v if isinstance(v, str) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = _literal(node.left, consts, mods), _literal(node.right, consts, mods)
        return a + b if a is not None and b is not None else None
    if isinstance(node, ast.JoinedStr):
        parts = [_literal(v.value, consts, mods) if isinstance(v, ast.FormattedValue) and not v.format_spec and v.conversion == -1 else _literal(v, consts, mods)
                 for v in node.values]
        return "".join(parts) if all(p is not None for p in parts) else None
    return None


def _shape(node: ast.expr, consts: dict[str, str], mods: dict[str, str]) -> str:
    """The text of a composed string with ``{}`` for each computed part."""
    if (v := _literal(node, consts, mods)) is not None:
        return v
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _shape(node.left, consts, mods) + _shape(node.right, consts, mods)
    if isinstance(node, ast.JoinedStr):
        return "".join(_shape(v.value if isinstance(v, ast.FormattedValue) else v, consts, mods) for v in node.values)
    return "{}"


def python_statements(path: Path) -> tuple[list[str], list[str]]:
    """A module's SQL texts in order, and the statements it composes at run time (by tag or start)."""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    consts, mods = _constants(tree), _modules(tree)
    docs = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    inner = {id(c) for n in ast.walk(tree) if isinstance(n, (ast.JoinedStr, ast.BinOp, ast.FormattedValue)) for c in ast.iter_child_nodes(n)}
    nodes = sorted((n for n in ast.walk(tree) if isinstance(n, (ast.Constant, ast.JoinedStr, ast.BinOp)) and id(n) not in inner and id(n) not in docs),
                   key=lambda n: (n.lineno, n.col_offset))
    texts, composed = [], []
    for n in nodes:
        if isinstance(n, ast.Constant) and not isinstance(n.value, str):
            continue
        v = _literal(n, consts, mods)
        if v is not None:
            texts.append(v)
            continue
        shape = _shape(n, consts, mods)
        if (_starts_statement(shape) or _TAG.match(shape.lstrip())) and (TABLE in shape.lower() or "{}" in shape):
            composed.append(_key(shape))
    return texts, composed


def _body_statements(body: str) -> list[str]:
    """The statements of a SQL file or a PL/pgSQL body that read or write rows (control lines, declarations and DDL dropped)."""
    body = re.sub(r"--[^\n]*", "", body)
    out = []
    for s in body.split(";"):
        # a statement starts a line of its chunk (after the control lines of PL/pgSQL, a CREATE FUNCTION's header, a RETURN QUERY)
        m = re.search(r"(?im)^[ \t]*(?:RETURN\s+QUERY\s+)?((?:SELECT|WITH|INSERT|UPDATE|DELETE)\b)", s)
        if m:
            out.append(s[m.start(1):].strip())
    return out


def listing_statements() -> list[str]:
    """The statements the listing composes, rendered by its builders with every sort, direction, filter and boundary it takes."""
    kgl = importlib.import_module("app.services.graph_db.postgres.kg.listing")
    viewer = {"unvouched": [1], "plain": [2], "own": [3], "granted": ["a"], "walked": ["b"]}
    filters = {"search_query": "a b", "node_types": ["record"], "record_types": ["FILE"], "indexing_status": ["COMPLETED"], "created_at": {"gte": 1, "lte": 2},
               "updated_at": {"gte": 1, "lte": 2}, "size": {"gte": 1, "lte": 2}, "only_containers": True}
    out = []
    for field_ in kgl.SORTS:
        for sort_dir in ("ASC", "DESC"):
            for direction in ("next", "prev"):
                for boundary in (None, {"nullRank": 0, "sortKey": "a" if field_ == "name" else 5, "id": "x"}, {"nullRank": 1, "id": "x"}):
                    p = kgl.Params([None, None, None])
                    where = kgl.filters_sql(filters, p)
                    branches = kgl.after(field_, sort_dir, direction, boundary, p) if boundary else None
                    out.append(kgl.page_sql(field_, sort_dir, direction, where, branches))
                    out.append(kgl.page_sql(field_, sort_dir, direction, where, branches, kgl.class_admit("w", p, viewer)))
    p = kgl.Params([None, None, None])
    where = kgl.filters_sql(filters, p)
    out += [kgl.scan_sql(where, "$4"), kgl.undecided_sql(where, "$4"), kgl.count_sql(where, "$4", "$5", "$6", "$7"), kgl.arrows_sql(kgl.class_admit("c", p, viewer))]
    return out


RENDERED = {LISTING: listing_statements}


def sources() -> dict[str, tuple[list[str], list[str]]]:
    """Every source of SQL that names kg.records: (its statements' texts, the statements it composes at run time)."""
    out: dict[str, tuple[list[str], list[str]]] = {}
    for p in sorted((BACKEND / "python" / "app").rglob("*.py")):
        if TABLE in p.read_text(encoding="utf-8-sig"):
            key = str(p.relative_to(BACKEND))
            texts, composed = python_statements(p)
            out[key] = (texts + (RENDERED[key]() if key in RENDERED else []), composed)
    for p in sorted((BACKEND / "db" / "versions").rglob("*.sql")):
        if "vetted" not in p.parts and TABLE in (text := p.read_text(encoding="utf-8")):
            out[str(p.relative_to(BACKEND))] = (_body_statements(text), [])
    for p in sorted((BACKEND / "db" / "versions").rglob("manifest.yaml")):
        for fn in yaml.safe_load(p.read_text(encoding="utf-8")).get("functions", []):
            if TABLE in (body := fn.get("body") or ""):
                out[f"{p.relative_to(BACKEND)}#{fn['name']}"] = (_body_statements(body), [])
    return out


def _scan(src: str, texts: list[str]) -> set[str]:
    try:
        return reads(texts)
    except Unparsed as e:
        if (src, e.key) in UNPARSED:
            return set()
        raise AssertionError(f"{src}: a statement that names {TABLE} does not parse ({e.key!r}): make it parse, or name it in UNPARSED with the reason it reads no copied column") from e


# --- tests ----------------------------------------------------------------------------------------------------------------------------------------------------------------------


def test_no_reader_takes_a_copied_column_of_kg_records_from_kg() -> None:
    found = {(src, what) for src, (texts, _) in sources().items() for what in _scan(src, texts)}
    allowed = {(s, w) for s, w in found if (s, w) in ALLOWED or (s, "*") in ALLOWED}
    assert found - allowed == set(), "a read of a column the projector may leave empty for a connector kg no longer serves: read it from the graph, or serve it only for a served connector"


def test_every_statement_composed_at_run_time_is_rendered_here() -> None:
    """A statement built from computed parts cannot be read from the source: its module's builders render it above, or it is named in UNPARSED."""
    missing = []
    for src, (texts, composed) in sources().items():
        rendered = {_key(t) for t in texts}
        # a tagged statement must be among the rendered ones; an untagged piece (a part of a tagged one) needs its module rendered
        missing += [(src, k) for k in composed if (src, k) not in UNPARSED and (k not in rendered if " " not in k else src not in RENDERED)]
    assert missing == [], f"statements composed at run time that the test does not render: {missing}"


def test_the_scan_finds_the_sources_and_statements_it_must_read() -> None:
    srcs = sources()
    assert {LISTING, "python/app/services/graph_db/postgres/kg/read.py", "python/app/services/graph_db/postgres/kh_listing.py",
            "python/app/services/graph_db/postgres/kg/stamp.py", "db/versions/kg/vetted/manifest.yaml#kg.w_listing_set"} <= set(srcs)
    assert sum(len(t) for t, _ in srcs.values()) > 200, "the scan finds almost no statements: it is broken"
    assert (LISTING, "record_type") in {(s, w) for s, (t, _) in srcs.items() for w in _scan(s, t)}


@pytest.mark.parametrize("texts, found", [
    (["SELECT r.sk, r.rule FROM kg.records r WHERE r.org_id = $1"], {"rule"}),
    (["SELECT x.origin FROM unnest($2::text[]) q CROSS JOIN LATERAL (SELECT * FROM kg.records k WHERE k.coll = ANY('{1,2}'::smallint[]) LIMIT 3) x"], {"origin"}),
    (["SELECT * FROM kg.records WHERE org_id = $1"], {"*"}),
    (["SELECT sk, external_id FROM kg.records WHERE org_id = $1"], {"external_id"}),
    (["SELECT r.sk FROM kg.records r WHERE r.org_id = $1", " AND r.vrid = $2"], {"vrid"}),                     # a query composed from two texts
    (["SELECT external_id", " FROM kg.records WHERE org_id = $1"], {"external_id"}),                         # a bare column in another text than the table
    (["SELECT x.* FROM (SELECT * FROM kg.records) x"], {"*"}),                                               # the whole row through a subquery's alias
    (["SELECT * FROM (SELECT * FROM kg.records WHERE org_id = $1) x"], {"*"}),
    (["SELECT r.* FROM kg.records r"], {"*"}),
    (["SELECT r.id FROM unnest($2::text[]) q CROSS JOIN LATERAL (SELECT * FROM kg.records k WHERE k.id = q.id LIMIT 3) r"], set()),   # as read.py: only safe columns of the row
    (["SELECT x.id FROM (SELECT k.* FROM kg.records k) x"], set()),                                          # a qualified star under a safe outer projection
    (["SELECT y.rule FROM (SELECT x.* FROM (SELECT * FROM kg.records) x) y"], {"rule"}),                     # a row wrapped twice
    (["SELECT y.id FROM (SELECT x.* FROM (SELECT * FROM kg.records) x) y WHERE y.record_type = $1"], {"record_type"}),
    (["origin", "SELECT r.sk FROM kg.records r"], set()),                                                   # a dict key of the source is no SQL
    (["SELECT r.sk, r.name_key FROM kg.records AS r WHERE r.org_id = $1"], set()),
    (["SELECT n.doc->>'origin' AS o FROM graph.nodes n JOIN kg.records r ON r.id = n.id"], set()),
    (["SELECT x.model FROM retrieval.spaces x"], set()),
    (["SELECT model FROM retrieval.spaces", "SELECT r.sk FROM kg.records r WHERE r.org_id = $1"], set()),     # another table's column of that name, in the same source
    (["SELECT r.sk FROM kg.records r WHERE r.org_id = $1 ORDER BY r.origin"], {"origin"}),
    (["SELECT 1 FROM graph.nodes n WHERE EXISTS (SELECT 1 FROM kg.records r WHERE r.id = n.id AND r.model = $1)"], {"model"}),
    (["UPDATE kg.records SET rule = $1 WHERE org_id = $2"], set()),                                         # a column set is written, not read
    (["UPDATE kg.records SET name = origin WHERE vrid = $2"], {"origin", "vrid"}),
    (["SELECT id FROM kg.records GROUP BY id HAVING max(record_type) = 'FILE'"], {"record_type"}),           # a bare column in an aggregate of HAVING
    (["SELECT DISTINCT ON (record_type) id FROM kg.records ORDER BY record_type, id"], {"record_type"}),
    (["SELECT DISTINCT ON (r.rule) r.id FROM kg.records r"], {"rule"}),
    (["SELECT sk FROM kg.records; SELECT origin FROM kg.records"], {"origin"}),                               # every statement of a text
    (["SELECT x.sk FROM (SELECT sk, rule FROM kg.records UNION ALL SELECT sk, rule FROM kg.records) x"], set()),   # a union's column the outer query does not read
    (["SELECT x.rule FROM (SELECT sk, rule FROM kg.records UNION ALL SELECT sk, rule FROM kg.records) x"], {"rule"}),
    (["SELECT x.v FROM (SELECT sk AS v FROM kg.records UNION ALL SELECT origin FROM kg.records) x"], {"origin"}),   # by position, in every branch
    (["SELECT sk FROM kg.records UNION SELECT model FROM kg.records"], {"model"}),
    (["UPDATE kg.records SET sk = sk WHERE org_id = $1 RETURNING rule"], {"rule"}),                         # RETURNING is what a statement returns
    (["UPDATE kg.records SET sk = sk WHERE org_id = $1 RETURNING *"], {"*"}),
    (["DELETE FROM kg.records WHERE org_id = $1 RETURNING origin"], {"origin"}),
    (["INSERT INTO kg.records (org_id, sk) VALUES ($1, $2) RETURNING vrid"], {"vrid"}),
    (["UPDATE kg.records SET sk = sk WHERE org_id = $1 RETURNING sk, id"], set()),
    (["SELECT x.v FROM (SELECT sk AS v FROM kg.records INTERSECT SELECT origin FROM kg.records) x"], {"origin"}),   # INTERSECT and EXCEPT as UNION
    (["SELECT x.v FROM (SELECT sk AS v FROM kg.records EXCEPT SELECT origin FROM kg.records) x"], {"origin"}),
    (["SELECT x.sk FROM (SELECT sk, rule FROM kg.records INTERSECT SELECT sk, rule FROM kg.records) x"], set()),
    (["UPDATE kg.records SET name = 'x' WHERE id IN (SELECT id FROM graph.nodes WHERE rule = 'a')"], set()),   # an inner query's columns are its own
    (["DELETE FROM kg.records WHERE id IN (SELECT n.id FROM graph.nodes n WHERE n.records IS NOT NULL)"], set()),
    (["DELETE FROM kg.records WHERE id IN (SELECT id FROM graph.nodes WHERE records IS NOT NULL)"], {"*"}),       # a correlated bare name of the written table: its row
    (["UPDATE kg.records SET name = 'x' WHERE rule = 'a' AND id IN (SELECT id FROM graph.nodes)"], {"rule"}),
    (["UPDATE graph.nodes SET doc = $1 WHERE coll = 'records' AND id IN (SELECT id FROM kg.records WHERE origin = $2)"], {"origin"}),
    (["UPDATE graph.nodes SET doc = $1 WHERE rule = $2 AND id IN (SELECT id FROM kg.records)"], set()),
    (["UPDATE kg.records r SET name = 'x' WHERE EXISTS (SELECT 1 FROM graph.nodes x WHERE x.id = r.origin)"], {"origin"}),   # correlated to the write
    (["DELETE FROM kg.records r WHERE EXISTS (SELECT 1 FROM graph.nodes x WHERE x.id = r.origin)"], {"origin"}),
    (["SELECT r.sk FROM kg.records r WHERE EXISTS (SELECT 1 FROM graph.nodes x WHERE x.id = r.origin)"], {"origin"}),
    (["UPDATE kg.records r SET name = 'x' WHERE EXISTS (SELECT 1 FROM graph.nodes r WHERE r.origin = 'a')"], set()),   # the inner alias hides the write's
    (["INSERT INTO kg.records (org_id, sk, name) VALUES ($1, $2, $3) ON CONFLICT (org_id, sk) DO UPDATE SET name = kg.records.origin"], {"origin"}),
    (["INSERT INTO kg.records AS k (org_id, sk, name) VALUES ($1, $2, $3) ON CONFLICT (org_id, sk) DO UPDATE SET name = excluded.name WHERE k.rule = 'OPEN'"], {"rule"}),
    (["INSERT INTO kg.records (org_id, sk, rule) VALUES ($1, $2, $3) ON CONFLICT (org_id, sk) DO UPDATE SET rule = excluded.rule"], set()),   # the incoming row
    (["SELECT r.sk, row_number() OVER (PARTITION BY r.origin ORDER BY r.sk) FROM kg.records r"], {"origin"}),
    (["SELECT r.sk, row_number() OVER w FROM kg.records r WINDOW w AS (PARTITION BY r.model ORDER BY r.sk)"], {"model"}),
    (["SELECT count(*) FILTER (WHERE r.vrid IS NULL) FROM kg.records r"], {"vrid"}),
    (["UPDATE graph.nodes n SET doc = $1 FROM kg.records r WHERE r.id = n.id AND r.record_type = $2"], {"record_type"}),
    (["UPDATE graph.nodes n SET doc = to_jsonb(r.origin) FROM kg.records r WHERE r.id = n.id"], {"origin"}),
    (["UPDATE graph.nodes n SET doc = $1 FROM LATERAL (SELECT r.rule FROM kg.records r WHERE r.id = n.id) x WHERE x.rule = 'a'"], {"rule"}),
    (["UPDATE graph.nodes n SET doc = $1 FROM (SELECT r.id, r.rule FROM kg.records r) x WHERE x.id = n.id"], set()),
    (["DELETE FROM graph.nodes n USING kg.records r WHERE r.id = n.id AND r.external_id = $1"], {"external_id"}),
    (["UPDATE graph.nodes n SET doc = 1 FROM kg.records r JOIN graph.edges e ON e.src = r.origin WHERE n.id = r.id"], {"origin"}),   # a write source's joins
    (["UPDATE graph.nodes n SET doc = 1 FROM graph.edges e, kg.records r WHERE r.rule = 'a'"], {"rule"}),
    (["DELETE FROM graph.nodes n USING kg.records r JOIN graph.edges e ON e.src = r.origin WHERE n.id = r.id"], {"origin"}),
    (["DELETE FROM graph.nodes n USING graph.edges e, kg.records r WHERE r.rule = 'a'"], {"rule"}),
    (["UPDATE graph.nodes n SET doc = 1 FROM graph.edges e JOIN kg.records r USING (external_id) WHERE n.id = e.id"], {"external_id"}),
    (["UPDATE graph.nodes n SET doc = 1 FROM graph.edges e JOIN kg.records r ON r.id = e.dst WHERE n.id = e.id"], set()),
    (["SELECT v.a FROM (VALUES (1)) v(a) JOIN kg.records r ON r.origin = 'x'"], {"origin"}),
    (["SELECT r.sk FROM kg.records r TABLESAMPLE SYSTEM (10) WHERE r.rule = 'OPEN'"], {"rule"}),
    (["INSERT INTO graph.nodes (id) SELECT r.origin FROM kg.records r"], {"origin"}),                       # what an INSERT inserts from a query
    (["INSERT INTO graph.nodes (id, doc) VALUES ((SELECT r.vrid FROM kg.records r LIMIT 1), $1)"], {"vrid"}),
    (["UPDATE kg.records AS kg_row SET name = 'x' RETURNING kg_row"], {"*"}),                              # a bare alias is the whole row as a value
    (["SELECT r FROM kg.records r"], {"*"}),
    (["SELECT to_jsonb(r) FROM kg.records r WHERE r.org_id = $1"], {"*"}),
    (["SELECT row_to_json(records) FROM kg.records"], {"*"}),
    (["SELECT 1 FROM kg.records r WHERE r IS NOT NULL"], {"*"}),
    (["SELECT r FROM (SELECT n.id AS r FROM graph.nodes n) x JOIN kg.records r ON r.id = x.r"], set()),       # a column of that name comes first
    (["SELECT r.id FROM kg.records r"], set()),
    (["SELECT n.id FROM graph.nodes n JOIN kg.records r USING (external_id)"], {"external_id"}),            # USING reads the column of both sides
    (["SELECT r.sk FROM kg.records r JOIN kg.records s USING (external_id)"], {"external_id"}),
    (["SELECT r.sk FROM kg.records r JOIN kg.records s USING (sk)"], set()),
    (["WITH c(v) AS (SELECT rule FROM kg.records) SELECT v FROM c"], {"rule"}),                             # a CTE's alias list, by position
    (["WITH c(v, w) AS (SELECT sk, rule FROM kg.records) SELECT v FROM c"], set()),
    (["SELECT x.v FROM (SELECT rule FROM kg.records) x(v)"], {"rule"}),                                     # a derived table's alias list
    (["SELECT x.v FROM (SELECT sk, rule FROM kg.records) x(v, w)"], set()),
    (["WITH RECURSIVE c AS (SELECT r.sk FROM kg.records r UNION SELECT x.sk FROM c JOIN kg.records x ON x.parent_sk = c.sk) SELECT c.sk FROM c"], set()),
])
def test_the_scan_names_each_way_of_reading_a_copied_column(texts: list[str], found: set[str]) -> None:
    assert reads(texts) == found


def test_a_column_list_kept_apart_from_its_statement_is_read_in_it() -> None:
    assert reads(["SELECT {COLS} FROM kg.records r"], {"COLS": "r.sk, r.id, r.vrid"}) == {"vrid"}


def test_a_statement_that_does_not_parse_is_named_not_skipped() -> None:
    with pytest.raises(Unparsed):
        reads(["/* pipeshub:kg.broken */ SELECT r.rule FROM kg.records r WHERE ((("])


def test_a_module_s_constants_fill_its_f_strings_and_a_computed_statement_is_named(tmp_path: Path) -> None:
    mod = tmp_path / "m.py"
    mod.write_text('COLS = "r.sk, r.rule"\nA = f"/* pipeshub:kg.a */ SELECT {COLS} FROM kg.records r"\n\n\ndef b(cond):\n'
                   '    return f"/* pipeshub:kg.b */ SELECT r.sk FROM kg.records r WHERE {cond}"\n', encoding="utf-8")
    texts, composed = python_statements(mod)
    assert reads(texts) == {"rule"} and composed == ["kg.b"]


def test_a_run_of_comments_is_scanned_in_linear_time() -> None:
    import time
    t0 = time.perf_counter()
    assert not _starts_statement("/*" + "*//*" * 5000) and not reads(["/*" + "*//*" * 5000])
    assert _starts_statement("/* a */ /* b */ SELECT 1") and not _starts_statement("/* never closed SELECT 1")
    assert time.perf_counter() - t0 < 1.0
