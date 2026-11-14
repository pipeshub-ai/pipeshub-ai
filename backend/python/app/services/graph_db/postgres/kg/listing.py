"""A connector's flat listing from the typed graph: the structure of the listing (which rows, in which order, how many) read from ``kg.records``; who may see a row is decided by the caller.

Structure may lag the graph by the drain lag (a record moved a moment ago may still list in its old folder); access never does, because the caller asks the access check about every row it
keeps (kg classes for a connector kg serves, the live walk otherwise). A row is a candidate when it is live, no placeholder, not another organisation's, and not hidden by a group that hides
its children (the listing honours that navigation flag, the access check does not). The statements read a connector's rows in the order of the listing by the partial indexes of kg_0009, in
windows, from a boundary (keyset), so a page never scans the connector.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.graph_db.common.name_match import _REPLACEMENTS, name_query_key

# sort field -> (column, whether it is text compared as bytes: the order of the code points)
SORTS: dict[str, tuple[str, str]] = {"name": ("name_key", "name_sort"), "updatedAt": ("updated_ms", "updated_ms"), "createdAt": ("created_ms", "created_ms"), "sizeInBytes": ("size_bytes", "size_bytes")}
LIVE = "r.kind IN (2, 3) AND NOT r.deleted AND NOT r.placeholder AND NOT r.alien"        # as the partial indexes spell it
# The bytes of name_sort the name indexes key on (kg_0009): a name is unbounded and a btree entry is not. Ordering by (prefix, name_sort) is ordering by name_sort.
NAME_PREFIX = 1024
COLS = "r.sk, r.coll, r.id, r.kind, r.folder, r.placeholder, r.hide_children, r.parent_sk, r.group_sk, r.name_key, r.name_sort, r.updated_ms, r.created_ms, r.size_bytes, r.r13, r.path IS NOT NULL AS has_path, r.class_id, r.fence_job IS NOT NULL AS fenced, r.app_sk, r.deleted, r.alien"

# The copy state of a connector: its node key and the mode of its projection ('legacy' is a connector kg has never copied).
# One row, with the connector's document (``app_doc``) beside it; ``sk`` is NULL when kg holds no key for the connector.
STATE = """/* pipeshub:kg.listing_state */ SELECT k.sk, coalesce(s.mode, 'legacy') AS mode, s.code_epoch, s.verified_at, n.doc AS app_doc FROM (SELECT $1::text AS org_id, $2::text AS id) q
    LEFT JOIN kg.node_keys k ON k.org_id = q.org_id AND k.coll = 1 AND k.id = q.id LEFT JOIN kg.projection_state s ON s.org_id = k.org_id AND s.app_sk = k.sk
    LEFT JOIN graph.nodes n ON n.org_id = q.org_id AND n.coll = 'apps' AND n.id = q.id"""

# Rows a group that hides its children hides from a listing: what lies below it by the hierarchy (the primary parent and the non-primary links), and the members of it or of a group below it.
# The hider itself is listed. Empty when no group of the connector hides.
# Every step reads by a key (a lateral read per node, kept one by OFFSET 0), so the plan never joins against the whole organisation's rows or links.
HIDDEN = """/* pipeshub:kg.listing_hidden */ WITH RECURSIVE hid AS (SELECT sk FROM kg.records WHERE org_id = $1 AND app_sk = $2 AND hide_children),
  seed(sk) AS (
    SELECT c.sk FROM hid h CROSS JOIN LATERAL (SELECT c.sk FROM kg.records c WHERE c.org_id = $1 AND c.parent_sk = h.sk OFFSET 0) c
    UNION SELECT l.child_sk FROM hid h CROSS JOIN LATERAL (SELECT l.child_sk FROM kg.links l WHERE l.org_id = $1 AND l.parent_sk = h.sk AND l.kind = 1 OFFSET 0) l),
  below(sk) AS (
    SELECT sk FROM seed
    UNION SELECT x.sk FROM below b CROSS JOIN LATERAL (
      SELECT c.sk FROM kg.records c WHERE c.org_id = $1 AND c.parent_sk = b.sk
      UNION ALL SELECT l.child_sk FROM kg.links l WHERE l.org_id = $1 AND l.parent_sk = b.sk AND l.kind = 1) x),
  scope AS (SELECT sk FROM hid UNION SELECT sk FROM below)
SELECT coalesce(array_agg(x.sk), '{}') AS sks FROM (
  SELECT sk FROM below
  UNION SELECT r.sk FROM scope s CROSS JOIN LATERAL (SELECT r.sk FROM kg.records r WHERE r.org_id = $1 AND r.group_sk = s.sk OFFSET 0) r
  UNION SELECT l.child_sk FROM scope s CROSS JOIN LATERAL (SELECT l.child_sk FROM kg.links l WHERE l.org_id = $1 AND l.parent_sk = s.sk AND l.kind = 2 OFFSET 0) l) x"""
HAS_HIDER = "/* pipeshub:kg.listing_hiders */ SELECT EXISTS (SELECT 1 FROM kg.records WHERE org_id = $1 AND app_sk = $2 AND hide_children)"


class Fallback(Exception):
    """The listing cannot be answered as asked (a sort or filter kg has no column for)."""


@dataclass
class Params:
    values: list[Any] = field(default_factory=list)

    def add(self, value: Any) -> str:  # noqa: ANN401
        self.values.append(value)
        return f"${len(self.values)}"


def name_prefix(alias: str = "r") -> str:
    """The indexed prefix of a row's name key, spelt as the indexes of kg_0009 spell it (the planner matches the expression)."""
    return f"substring({alias}.name_sort FROM 1 FOR {NAME_PREFIX})"


def order(field_: str, sort_dir: str, direction: str, alias: str = "r") -> str:
    """The listing's order as SQL: rows without a key after the rows with one, the key in the sort direction, the id ascending; all three flipped for a previous page."""
    _, col = SORTS[field_]
    rev = direction == "prev"
    keys = [name_prefix(alias), f"{alias}.{col}"] if field_ == "name" else [f"{alias}.{col}"]
    way = f'{"DESC" if (sort_dir == "DESC") != rev else "ASC"} NULLS {"FIRST" if rev else "LAST"}'
    return ", ".join(f"{k} {way}" for k in keys) + f', {alias}.id COLLATE "C" {"DESC" if rev else "ASC"}'


def after(field_: str, sort_dir: str, direction: str, boundary: dict[str, Any], p: Params) -> list[str]:
    """The rows past a boundary (the last row of the page before, or the last row looked at), as disjoint conditions: one statement reads each by a range of the sort index."""
    value_col, col = SORTS[field_]
    text = field_ == "name"
    fwd, asc = direction == "next", sort_dir == "ASC"
    rank, bid = int(boundary["nullRank"]), str(boundary["id"])
    key = f"r.{col}"
    lead = name_prefix() if text else key      # the leading key of the sort index (NULL exactly when the key is), so a test of NULL is a range of it
    idp = p.add(bid)
    id_past = f'r.id COLLATE "C" {">" if fwd else "<"} {idp}::text COLLATE "C"'
    if rank == 1:
        return [f"{lead} IS NULL AND {id_past}"] if fwd else [f"{lead} IS NOT NULL", f"{lead} IS NULL AND {id_past}"]
    bk = boundary.get("sortKey")
    if bk is None or isinstance(bk, bool) or (not text and not isinstance(bk, int)) or (text and not isinstance(bk, str)):
        raise Fallback("cursor")
    past = ">" if asc == fwd else "<"
    if text:      # by the indexed prefix first: a key past the boundary's prefix is past the boundary, and only a key with the boundary's prefix needs the whole value
        raw = bk.encode("utf-16-be", "surrogatepass")
        bkp, pre = f"{p.add(raw)}::bytea", f"{p.add(raw[:NAME_PREFIX])}::bytea"
        branches = [f"{name_prefix()} = {pre} AND {key} = {bkp} AND {id_past}", f"{name_prefix()} = {pre} AND {key} {past} {bkp}", f"{name_prefix()} {past} {pre}"]
    else:
        bkp = f"{p.add(bk)}::bigint"
        branches = [f"{key} = {bkp} AND {id_past}", f"{key} {past} {bkp}"]
    if fwd:
        branches.append(f"{lead} IS NULL")
    return branches


# A browse's sidebar: the groups, the folders and the records with a live hierarchy child, each looked up by the row's own key (the children index, the key of kg.links; OFFSET 0 keeps
# the planner from joining the test against the organisation's rows). Of those records the browse keeps the ones whose arrow is on (``_kh_browse``): a child is no promise the viewer may open it.
WITH_CHILDREN = ("(r.kind = 2 OR r.folder OR EXISTS (SELECT 1 FROM kg.records k WHERE k.org_id = r.org_id AND k.parent_sk = r.sk AND k.kind IN (2, 3) AND NOT k.deleted AND NOT k.alien OFFSET 0) "
                 "OR EXISTS (SELECT 1 FROM kg.links k WHERE k.org_id = r.org_id AND k.parent_sk = r.sk AND k.kind = 1 OFFSET 0))")


def filters_sql(filters: dict[str, Any], p: Params, *, browse: bool = False) -> str:
    """The content filters of a listing as conditions on a row ``r``. ``connector_ids`` and ``origins`` are the connector's, decided by the caller. ``browse``: a browse's, whose
    sidebar (``only_containers``) also reads the records that have a hierarchy child, for the browse to keep those whose arrow is on (``_kh_browse``)."""
    out: list[str] = []
    if q := filters.get("search_query"):
        name = "r.name_key"
        key = name_query_key(q)
        if " " in key or "σ" in key:       # the listing reads the separators of the name as spaces too, as it does of the query
            for old, new in _REPLACEMENTS:
                name = f"replace({name}, '{old}', '{new}')"
        out.append(f"strpos({name}, {p.add(key)}) > 0")
    if nt := filters.get("node_types"):
        n = p.add(list(nt))
        out.append(f"((r.kind = 2 AND 'recordGroup' = ANY({n}::text[])) OR (r.kind = 3 AND r.folder AND 'folder' = ANY({n}::text[])) OR (r.kind = 3 AND NOT r.folder AND 'record' = ANY({n}::text[])))")
    if rt := filters.get("record_types"):
        out.append(f"(r.kind = 3 AND r.record_type = ANY({p.add(list(rt))}::text[]))")
    if st := filters.get("indexing_status"):
        out.append("(r.kind = 3 AND EXISTS (SELECT 1 FROM graph.nodes n WHERE n.org_id = r.org_id AND n.coll = 'records' AND n.id = r.id "
                   f"AND n.doc->>'indexingStatus' = ANY({p.add(list(st))}::text[])))")
    for label, col in (("created_at", "created_ms"), ("updated_at", "updated_ms")):
        w = filters.get(label) or {}
        if w.get("gte") is not None:
            out.append(f"r.{col} >= {p.add(int(w['gte']))}::bigint")
        if w.get("lte") is not None:
            out.append(f"r.{col} <= {p.add(int(w['lte']))}::bigint")
    sz = filters.get("size") or {}
    if sz.get("gte") is not None or sz.get("lte") is not None:      # a record with no recorded size is out of a size window: unknown is not empty
        out.append("r.kind = 3")
        if sz.get("gte") is not None:
            out.append(f"r.size_bytes >= {p.add(int(sz['gte']))}::bigint")
        if sz.get("lte") is not None:
            out.append(f"r.size_bytes <= {p.add(int(sz['lte']))}::bigint")
    if filters.get("only_containers"):      # a flat listing gives a record no arrow, so its sidebar is the groups and the folders
        out.append(WITH_CHILDREN if browse else "(r.kind = 2 OR r.folder)")
    return "".join(f" AND {c}" for c in out)


def page_sql(field_: str, sort_dir: str, direction: str, where: str, branches: list[str] | None, admit: str | None = None, scopes: list[str] | None = None) -> str:
    """The window of the sort order past the boundary, ``$3`` rows; ``where`` is the connector's conditions and the filters (``$1`` the org, ``$2`` the connector). ``admit``, when given, is a condition
    on the rows of the window (alias ``w``: what the viewer's classes open), answered as the column ``ok`` of every row of the window, in the window's order. ``scopes`` replaces the connector's live
    rows with other row sets (each a condition on ``r``, read in its own range of the sort index): a browse's children."""
    o = order(field_, sort_dir, direction)
    base = [f"r.app_sk = $2 AND {LIVE}"] if scopes is None else scopes
    reads = [f"(SELECT {COLS} FROM kg.records r WHERE r.org_id = $1 AND {sc}{where}{f' AND ({b})' if b else ''} ORDER BY {o} LIMIT $3)" for sc in base for b in (branches or [None])]
    if len(reads) == 1 and admit is None:
        return "/* pipeshub:kg.listing_page */ " + reads[0][1:-1]
    inner = reads[0] if len(reads) == 1 else f"SELECT * FROM ({' UNION ALL '.join(reads)}) r ORDER BY {o} LIMIT $3"
    if admit is None:
        return f"/* pipeshub:kg.listing_page */ {inner}"
    ow = order(field_, sort_dir, direction, "w")
    return f"/* pipeshub:kg.listing_page_admitted */ SELECT w.*, ({admit}) AS ok FROM ({inner}) w ORDER BY {ow}"


def scan_sql(where: str, last: str) -> str:
    """The connector's candidates in key order, for the count: ``$3`` rows past the key ``last`` (a parameter)."""
    return f"/* pipeshub:kg.listing_scan */ SELECT r.sk, r.id, r.kind, r.folder FROM kg.records r WHERE r.org_id = $1 AND r.app_sk = $2 AND {LIVE}{where} AND r.sk > {last}::bigint ORDER BY r.sk LIMIT $3"


CLASSES = "/* pipeshub:kg.listing_classes */ SELECT id, clauses FROM kg.scope_classes WHERE org_id = $1 AND app_sk = $2 LIMIT $3"
# What kg itself decides for a row (as ``read.decide``): stamped (a path, a class), not hidden by a pending restamp and not a record of no connector. Every other row is the walk's.
DECIDED = "r.r13 IS FALSE AND r.path IS NOT NULL AND r.class_id IS NOT NULL AND r.fence_job IS NULL"


def undecided_sql(where: str, classes_param: str) -> str:
    """The ids of the rows kg does not decide (or whose class names a literal kg cannot vouch for), for the walk: ``$3`` of them at most."""
    return f"/* pipeshub:kg.listing_undecided */ SELECT r.id FROM kg.records r WHERE r.org_id = $1 AND r.app_sk = $2 AND {LIVE}{where} AND (NOT ({DECIDED}) OR r.class_id = ANY({classes_param}::bigint[])) LIMIT $3"


def count_sql(where: str, plain: str, own: str, granted: str, unvouched: str) -> str:
    """The rows kg admits, by type, each branch stopping at ``$3`` (the cap): a row whose class a clause of the viewer's grants meets with no grant of the row's own, or one that needs it and the row is granted."""
    return (f"/* pipeshub:kg.listing_count */ SELECT t.kind, t.folder, count(*) AS n FROM (SELECT r.kind, r.folder FROM kg.records r WHERE r.org_id = $1 AND r.app_sk = $2 AND {LIVE}{where} AND {DECIDED} "
            f"AND NOT r.class_id = ANY({unvouched}::bigint[]) AND (r.class_id = ANY({plain}::bigint[]) OR (r.class_id = ANY({own}::bigint[]) AND r.id = ANY({granted}::text[]))) LIMIT $3) t GROUP BY t.kind, t.folder")


def class_admit(alias: str, p: Params, viewer: dict[str, Any]) -> str:
    """What the viewer's classes open, as a condition on rows ``alias`` (the stamped, undelayed rows by class, and the few rows no class decides that the access check admitted)."""
    a = alias
    return (f"(({a}.r13 IS FALSE AND {a}.has_path AND {a}.class_id IS NOT NULL AND NOT {a}.fenced AND NOT {a}.class_id = ANY({p.add(viewer['unvouched'])}::bigint[]) "
            f"AND ({a}.class_id = ANY({p.add(viewer['plain'])}::bigint[]) OR ({a}.class_id = ANY({p.add(viewer['own'])}::bigint[]) AND {a}.id = ANY({p.add(viewer['granted'])}::text[])))) "
            f"OR {a}.id = ANY({p.add(sorted(viewer['walked']))}::text[]))")


# The children are read by the node's own ranges and the conditions on what the viewer may see apply on top of them: OFFSET 0 keeps the planner from pushing a class test into the
# read (it would then read every row of the class across the organisation and keep the node's children last).
_FENCE = " OFFSET 0"


def _limit(bound: str) -> str:
    """`` LIMIT <bound>``: built here, so that no bare clause text sits apart from its statement (``test_kg_copied_columns`` reads the module's texts in order)."""
    return f" LIMIT {bound}"


def _linked(parent: str, cols: str, live: str, limit: str = "", *, ordered: bool = False) -> str:
    """The live children of ``parent`` by a hierarchy link, as rows ``c``: the parent's links by the key of kg.links, each child by its own key, filtered before any ``limit`` (so no
    budget of links can stop short of a child that qualifies). OFFSET 0 keeps each read on its node. ``ordered``: the links in key order (the order of their index)."""
    by_key = " ORDER BY l.child_sk" if ordered else ""
    return (f"(SELECT c.* FROM (SELECT l.child_sk FROM kg.links l WHERE l.org_id = $1 AND l.parent_sk = {parent} AND l.kind = 1{by_key} OFFSET 0) l "
            f"CROSS JOIN LATERAL ({cols} WHERE {live} AND c.sk = l.child_sk OFFSET 0) c{limit})")


def undecided_child(p: Params, unvouched: list[int], app_sk: int) -> str:
    """A child (a row ``c`` of :func:`arrows_sql`) that the classes do not decide: no stamp, a pending restamp, a placeholder, a class kg cannot vouch for, or another connector's."""
    return (f"NOT (c.r13 IS FALSE AND c.has_path AND c.class_id IS NOT NULL AND NOT c.fenced AND NOT c.placeholder AND NOT c.class_id = ANY({p.add(unvouched)}::bigint[]) "
            f"AND c.app_sk = {p.add(app_sk)})")


def arrows_sql(admit: str, undecided: str = "false", sample: int = 200) -> str:
    """For each container among ``$2`` (sks): whether it lists something when opened (``lists``): some child by a hierarchy edge (the primary parent or a hierarchy link: what a browse
    of the row lists; a granted record that only belongs to a group is the chain-top walk's, ``HELD``) that is live, not hidden (``$3``) and open to the viewer (``admit``, a condition on a row ``c`` with
    the columns of the listing's window), exactly, with no sample of the children; and whether it has a child, a placeholder included, that ``undecided`` (a condition on ``c``) says
    the classes do not decide among its first ``sample`` children, read in the order :func:`children_of_sql` samples them (``undecided``: the rows whose sample the access check
    has to be asked about; the probe and the sample are the same children)."""
    live = "c.org_id = $1 AND c.kind IN (2, 3) AND NOT c.deleted AND NOT c.alien AND c.sk <> ALL($3::bigint[])"
    cols = "SELECT c.sk, c.id, c.placeholder, c.app_sk, c.r13, c.path IS NOT NULL AS has_path, c.class_id, c.fence_job IS NOT NULL AS fenced FROM kg.records c"
    def some(cond: str, limit: str = _FENCE, *, ordered: bool = False) -> str:      # the row's children first, the condition on them after (never a class read of the organisation)
        order = f' ORDER BY {name_prefix("c")}, c.id COLLATE "C"' if ordered else ""
        return (f"EXISTS (SELECT 1 FROM ({cols} WHERE {live} AND c.parent_sk = p.sk{order}{limit}) c WHERE {cond}) "
                f"OR EXISTS (SELECT 1 FROM {_linked('p.sk', cols, live, limit, ordered=ordered)} c WHERE {cond} OFFSET 0)")
    return (f"/* pipeshub:kg.listing_arrows */ SELECT p.sk, {some(f'NOT c.placeholder AND {admit}')} AS lists, {some(undecided, _limit(str(int(sample))), ordered=True)} AS undecided "
            "FROM unnest($2::bigint[]) AS p(sk)")


# The start of a browse by its id, with the connector it belongs to.
BROWSE_START = f"""/* pipeshub:kg.browse_start */ SELECT {COLS}, a.id AS app_id FROM kg.records r
    LEFT JOIN kg.node_keys a ON a.org_id = r.org_id AND a.sk = r.app_sk WHERE r.org_id = $1 AND r.coll = ANY('{{2,3}}'::smallint[]) AND r.id = $2 LIMIT 3"""
BROWSE_UP = f"/* pipeshub:kg.browse_up */ SELECT {COLS} FROM kg.records r WHERE r.org_id = $1 AND r.sk = ANY($2::bigint[])"
BROWSE_LIVE = "r.kind IN (2, 3) AND NOT r.deleted AND NOT r.alien"        # what a browse reads: placeholders too, for the way they hold to something readable
LINKS_MAX = 5_000       # the linked children of one node a request reads (kg.links rows: a node's non-primary children); past it a browse is refused as too large
# The live linked children of the start ``$2`` outside the hidden rows ``$4``, read once per request and handed to its statements: ``LINKS_MAX + 1`` at most (``$3``), counted
# among the children that qualify (a link to a deleted or hidden row takes nothing from the budget).
LINKED_CHILDREN = ("/* pipeshub:kg.browse_linked */ SELECT c.sk AS child_sk FROM "
                   + _linked("$2", "SELECT c.sk FROM kg.records c", "c.org_id = $1 AND c.kind IN (2, 3) AND NOT c.deleted AND NOT c.alien AND c.sk <> ALL($4::bigint[])", _limit("$3")) + " c")


def browse_scopes(links: str | None) -> list[str]:
    """The children of the start ``$2`` as ranges of the children indexes: by the primary parent, and, when it has linked children (``links``: the parameter that carries their
    keys), those by their keys (never the primary parent again, so no child is read twice)."""
    scopes = [f"r.parent_sk = $2 AND {BROWSE_LIVE}"]
    if links:
        scopes.append(f"r.sk = ANY({links}::bigint[]) AND r.parent_sk IS DISTINCT FROM $2 AND {BROWSE_LIVE}")
    return scopes


def _children(cols: str, where: str, links: str | None, tail: str | None = None) -> str:
    tail = _limit("$3") if tail is None else tail
    return " UNION ALL ".join(f"(SELECT {cols} FROM kg.records r WHERE r.org_id = $1 AND {sc}{where}{tail})" for sc in browse_scopes(links))


def children_sql(where: str, links: str | None) -> str:
    """The children of the start ``$2`` by a hierarchy edge, ``$3`` rows at most, in no order (a browse sorts what it holds)."""
    return f"/* pipeshub:kg.browse_children */ SELECT * FROM ({_children(COLS, where, links)}) r LIMIT $3"


def children_held_sql(where: str, links: str | None) -> str:
    """How many children the start ``$2`` has by a hierarchy edge, counted up to ``$3`` (``n``), and the children themselves when there are fewer than ``$3`` (one row of NULLs
    beside ``n`` when there are none or too many)."""
    return (f"/* pipeshub:kg.browse_children_held */ SELECT n.n, c.* FROM (SELECT count(*) AS n FROM ({_children('1', where, links)}) x) n "
            f"LEFT JOIN LATERAL (SELECT * FROM ({_children(COLS, where, links)}) r LIMIT $3) c ON n.n < $3")


def browse_undecided_sql(where: str, classes_param: str, app_param: str, links: str | None) -> str:
    """The children of the start that kg does not decide (as :func:`undecided_sql`, or of another connector, whose classes are not this one's) or that are placeholders (listed only on
    the way to something readable): ``$3`` of them at most."""
    undecided = f"r.placeholder OR NOT r.r13 IS FALSE OR NOT r.has_path OR r.class_id IS NULL OR r.fenced OR r.class_id = ANY({classes_param}::bigint[]) OR r.app_sk IS DISTINCT FROM {app_param}::bigint"
    return f"/* pipeshub:kg.browse_undecided */ SELECT * FROM ({_children(COLS, where, links, _FENCE)}) r WHERE {undecided} LIMIT $3"


def browse_count_sql(where: str, plain: str, own: str, granted: str, unvouched: str, app: str, links: str | None) -> str:
    """:func:`count_sql` over the children of the start: the ones of the connector ``app`` kg admits by class, by type, ``$3`` at most (another connector's child is the access
    check's, as :func:`browse_undecided_sql` hands it over: a class can be shared by two connectors)."""
    admitted = (f"NOT c.placeholder AND c.r13 IS FALSE AND c.has_path AND c.class_id IS NOT NULL AND NOT c.fenced AND NOT c.class_id = ANY({unvouched}::bigint[]) "
                f"AND c.app_sk = {app}::bigint "
                f"AND (c.class_id = ANY({plain}::bigint[]) OR (c.class_id = ANY({own}::bigint[]) AND c.id = ANY({granted}::text[])))")
    cols = "r.kind, r.folder, r.id, r.placeholder, r.r13, r.path IS NOT NULL AS has_path, r.class_id, r.fence_job IS NOT NULL AS fenced, r.app_sk"
    return (f"/* pipeshub:kg.browse_count */ SELECT t.kind, t.folder, count(*) AS n FROM (SELECT c.kind, c.folder FROM ({_children(cols, where, links, _FENCE)}) c "
            f"WHERE {admitted} LIMIT $3) t GROUP BY t.kind, t.folder")


def children_of_sql(only: str = "", *, total: bool = False) -> str:
    """The children of each of the sks ``$2`` by a hierarchy edge, at most ``$3`` of each, outside the hidden rows ``$4`` (``only``: a further condition on ``r``). With ``total``,
    ``$5`` rows at most in all: the parents in the order of ``$2``, each one's children by the primary parent in the order of the children index, then its linked ones in key order
    (the read stops at the bound, so a cut is the same on every run)."""
    live = f"r.org_id = $1 AND {BROWSE_LIVE} AND r.sk <> ALL($4::bigint[]){only}"
    linked = live.replace("r.", "c.") + " AND c.parent_sk IS DISTINCT FROM l_parent"
    return ("/* pipeshub:kg.browse_children_of */ SELECT p.sk AS parent, c.* FROM unnest($2::bigint[]) AS p(sk) CROSS JOIN LATERAL ("
            f"(SELECT {COLS} FROM kg.records r WHERE {live} AND r.parent_sk = p.sk ORDER BY {name_prefix()}, r.id COLLATE \"C\" LIMIT $3) "
            f"UNION ALL {_linked('p.sk', 'SELECT ' + COLS.replace('r.', 'c.') + ' FROM kg.records c', linked.replace('l_parent', 'p.sk'), _limit('$3'), ordered=True)}) c"
            + (_limit("$5") if total else ""))


# The granted rows of a connector (``$3``) among ids ``$2`` that a browse may list as chain-tops: live, of the organisation, no placeholder, OPEN (``_kh_v3_chain_top_groups``).
# The access rule is the graph's: a copied column kg may hold empty for a row it refused (kg.project).
# Every lookup is a seek by its unique key, as kg.read's (``k >= q.k ORDER BY k LIMIT 1``, read with sorts off: ``read.SORTS_OFF``): written as a join on ``id = ANY($2)`` the
# plan followed the statistics, and on the same rows (600 granted ids of 4,000) swung between probing the ids and reading every record or graph node of the organisation.
CHAIN_GRANTED = """/* pipeshub:kg.chain_granted */ SELECT r.sk, r.id, r.kind, r.parent_sk, r.group_sk FROM unnest($2::text[]) AS q(id)
    CROSS JOIN LATERAL (SELECT * FROM (SELECT * FROM kg.records k WHERE k.org_id = $1 AND k.coll = 2 AND k.id >= q.id ORDER BY k.id LIMIT 1) s WHERE s.id = q.id
        UNION ALL SELECT * FROM (SELECT * FROM kg.records k WHERE k.org_id = $1 AND k.coll = 3 AND k.id >= q.id ORDER BY k.id LIMIT 1) s WHERE s.id = q.id) r
    CROSS JOIN LATERAL (SELECT * FROM (SELECT n.id, n.doc FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = (CASE r.coll WHEN 2 THEN 'recordGroups' ELSE 'records' END)
        AND n.id >= r.id ORDER BY n.id LIMIT 1) s WHERE s.id = r.id OFFSET 0) n
    WHERE r.app_sk = (SELECT a.sk FROM (SELECT * FROM kg.node_keys k WHERE k.org_id = $1 AND k.coll = 1 AND k.id >= $3 ORDER BY k.id LIMIT 1) a WHERE a.id = $3)
      AND NOT r.deleted AND NOT r.placeholder AND NOT r.alien AND coalesce(n.doc->>'accessRule', 'OPEN') = 'OPEN'"""
CHAIN_ROWS = """/* pipeshub:kg.chain_rows */ SELECT r.sk, r.id, r.kind, r.parent_sk, r.group_sk, r.deleted FROM kg.records r
    WHERE r.org_id = $1 AND r.coll = ANY('{2,3}'::smallint[]) AND r.id = ANY($2::text[])"""
CHAIN_BY_SK = "/* pipeshub:kg.chain_by_sk */ SELECT r.sk, r.kind, r.parent_sk, r.deleted, r.alien FROM kg.records r WHERE r.org_id = $1 AND r.sk = ANY($2::bigint[])"
APP_KEY = "/* pipeshub:kg.app_key */ SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = 1 AND id = $2"
# The groups that hold a record among ids ``$2`` (as its group or by a group link) that could be a chain-top: read once per request by key from the grants.
HELD_ANY = """/* pipeshub:kg.listing_held_any */ SELECT DISTINCT g.sk FROM kg.records c
    CROSS JOIN LATERAL (SELECT c.group_sk AS sk UNION ALL SELECT l.parent_sk FROM kg.links l WHERE l.org_id = $1 AND l.child_sk = c.sk AND l.kind = 2) g
    WHERE c.org_id = $1 AND c.coll = 3 AND c.id = ANY($2::text[]) AND NOT c.deleted AND NOT c.placeholder AND NOT c.alien
      AND g.sk IS NOT NULL"""
# The groups among ``$2`` that hold a record among ids ``$3`` (as its group or by a group link) that could be a chain-top: the cheap test behind a group's arrow, settled by the
# request's chain-top placements (``_kh_held_lists``); it never says no to a real chain-top.
HELD = """/* pipeshub:kg.listing_held */ SELECT DISTINCT g.sk FROM kg.records c
    CROSS JOIN LATERAL (SELECT c.group_sk AS sk UNION ALL SELECT l.parent_sk FROM kg.links l WHERE l.org_id = $1 AND l.child_sk = c.sk AND l.kind = 2) g
    WHERE c.org_id = $1 AND c.coll = 3 AND c.id = ANY($3::text[]) AND NOT c.deleted AND NOT c.placeholder AND NOT c.alien
      AND g.sk = ANY($2::bigint[])"""
