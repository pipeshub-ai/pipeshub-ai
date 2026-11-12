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
COLS = "r.sk, r.coll, r.id, r.kind, r.folder, r.hide_children, r.parent_sk, r.group_sk, r.name_key, r.name_sort, r.updated_ms, r.created_ms, r.size_bytes, r.r13, r.path IS NOT NULL AS has_path, r.class_id, r.fence_job IS NOT NULL AS fenced"

# The copy state of a connector: its node key and the mode of its projection ('legacy' is a connector kg has never copied).
STATE = """/* pipeshub:kg.listing_state */ SELECT k.sk, coalesce(s.mode, 'legacy') AS mode, s.code_epoch, s.verified_at FROM kg.node_keys k
    LEFT JOIN kg.projection_state s ON s.org_id = k.org_id AND s.app_sk = k.sk WHERE k.org_id = $1 AND k.coll = 1 AND k.id = $2"""

# Rows a group that hides its children hides from a listing: what lies below it by the hierarchy (the primary parent and the non-primary links), and the members of it or of a group below it.
# The hider itself is listed. Empty when no group of the connector hides.
HIDDEN = """/* pipeshub:kg.listing_hidden */ WITH RECURSIVE hid AS (SELECT sk FROM kg.records WHERE org_id = $1 AND app_sk = $2 AND hide_children),
  seed(sk) AS (
    SELECT c.sk FROM hid h JOIN kg.records c ON c.org_id = $1 AND c.parent_sk = h.sk
    UNION SELECT l.child_sk FROM hid h JOIN kg.links l ON l.org_id = $1 AND l.parent_sk = h.sk AND l.kind = 1),
  below(sk) AS (
    SELECT sk FROM seed
    UNION SELECT x.sk FROM below b CROSS JOIN LATERAL (
      SELECT c.sk FROM kg.records c WHERE c.org_id = $1 AND c.parent_sk = b.sk
      UNION ALL SELECT l.child_sk FROM kg.links l WHERE l.org_id = $1 AND l.parent_sk = b.sk AND l.kind = 1) x),
  scope AS (SELECT sk FROM hid UNION SELECT sk FROM below)
SELECT coalesce(array_agg(x.sk), '{}') AS sks FROM (
  SELECT sk FROM below
  UNION SELECT r.sk FROM kg.records r WHERE r.org_id = $1 AND r.group_sk IN (SELECT sk FROM scope)
  UNION SELECT l.child_sk FROM kg.links l WHERE l.org_id = $1 AND l.kind = 2 AND l.parent_sk IN (SELECT sk FROM scope)) x"""
HAS_HIDER = "/* pipeshub:kg.listing_hiders */ SELECT EXISTS (SELECT 1 FROM kg.records WHERE org_id = $1 AND app_sk = $2 AND hide_children)"


class Fallback(Exception):
    """The listing cannot be answered as asked (a sort or filter kg has no column for)."""


@dataclass
class Params:
    values: list[Any] = field(default_factory=list)

    def add(self, value: Any) -> str:  # noqa: ANN401
        self.values.append(value)
        return f"${len(self.values)}"


def order(field_: str, sort_dir: str, direction: str, alias: str = "r") -> str:
    """The listing's order as SQL: rows without a key after the rows with one, the key in the sort direction, the id ascending; all three flipped for a previous page."""
    _, col = SORTS[field_]
    rev = direction == "prev"
    key = f"{alias}.{col}"
    return f'{key} {"DESC" if (sort_dir == "DESC") != rev else "ASC"} NULLS {"FIRST" if rev else "LAST"}, {alias}.id COLLATE "C" {"DESC" if rev else "ASC"}'


def after(field_: str, sort_dir: str, direction: str, boundary: dict[str, Any], p: Params) -> list[str]:
    """The rows past a boundary (the last row of the page before, or the last row looked at), as disjoint conditions: one statement reads each by a range of the sort index."""
    value_col, col = SORTS[field_]
    text = field_ == "name"
    fwd, asc = direction == "next", sort_dir == "ASC"
    rank, bid = int(boundary["nullRank"]), str(boundary["id"])
    key = f"r.{col}"
    idp = p.add(bid)
    id_past = f'r.id COLLATE "C" {">" if fwd else "<"} {idp}::text COLLATE "C"'
    if rank == 1:
        return [f"r.{col} IS NULL AND {id_past}"] if fwd else [f"r.{col} IS NOT NULL", f"r.{col} IS NULL AND {id_past}"]
    bk = boundary.get("sortKey")
    if bk is None or isinstance(bk, bool) or (not text and not isinstance(bk, int)) or (text and not isinstance(bk, str)):
        raise Fallback("cursor")
    bkp = f"{p.add(bk.encode('utf-16-be', 'surrogatepass'))}::bytea" if text else f"{p.add(bk)}::bigint"
    branches = [f"{key} = {bkp} AND {id_past}", f"{key} {'>' if asc == fwd else '<'} {bkp}"]
    if fwd:
        branches.append(f"r.{col} IS NULL")
    return branches


def filters_sql(filters: dict[str, Any], p: Params) -> str:
    """The content filters of a listing as conditions on a row ``r``. ``connector_ids`` and ``origins`` are the connector's, decided by the caller."""
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
    if filters.get("only_containers"):
        out.append("(r.kind = 2 OR r.folder)")
    return "".join(f" AND {c}" for c in out)


def page_sql(field_: str, sort_dir: str, direction: str, where: str, branches: list[str] | None, admit: str | None = None) -> str:
    """The window of the sort order past the boundary, ``$3`` rows; ``where`` is the connector's conditions and the filters (``$1`` the org, ``$2`` the connector). ``admit``, when given, is a condition
    on the rows of the window (alias ``w``: what the viewer's classes open), answered as the column ``ok`` of every row of the window, in the window's order."""
    o = order(field_, sort_dir, direction)
    reads = [f"(SELECT {COLS} FROM kg.records r WHERE r.org_id = $1 AND r.app_sk = $2 AND {LIVE}{where}{f' AND ({b})' if b else ''} ORDER BY {o} LIMIT $3)" for b in (branches or [None])]
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


def arrows_sql(admit: str) -> str:
    """The containers among ``$2`` (sks) that list something when opened: some child (by the hierarchy parent, the group, or a link) that is live, not hidden (``$3``) and open to the viewer
    (``admit``, a condition on a row ``c`` with the columns of the listing's window). Exact: no sample of the children."""
    live = "c.org_id = $1 AND c.kind IN (2, 3) AND NOT c.deleted AND NOT c.placeholder AND NOT c.alien AND c.sk <> ALL($3::bigint[])"
    cols = "SELECT c.sk, c.id, c.r13, c.path IS NOT NULL AS has_path, c.class_id, c.fence_job IS NOT NULL AS fenced FROM kg.records c"
    def one(where: str) -> str:
        return f"EXISTS (SELECT 1 FROM ({cols} WHERE {live} AND {where}) c WHERE {admit})"
    return ("/* pipeshub:kg.listing_arrows */ SELECT p.sk FROM unnest($2::bigint[]) AS p(sk) WHERE "
            + one("c.parent_sk = p.sk") + " OR " + one("c.group_sk = p.sk")
            + " OR " + one("c.sk IN (SELECT l.child_sk FROM kg.links l WHERE l.org_id = $1 AND l.parent_sk = p.sk)"))
