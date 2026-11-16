"""A record's children and descendants from the typed graph: ``get_records_by_parent_record``, ``get_records_by_record_group`` and ``get_node_depths_batch`` read their structure
from ``kg.records`` (``parent_sk``, ``group_sk``) and ``kg.links`` (the non-primary parents and groups) instead of following ``graph.edges``.

Only the edges are kg's. Every filter the walk applies to a document (connector, organisation, deletion, indexing status, file type) is still read from ``graph.nodes``, by the
same predicates, so a page differs from the walk's only if kg's structure does. kg answers for a connector it serves (``read.serves``: verified, enforced, nothing waiting in the
log), in one snapshot with that check. The walk answers instead (counted ``pipeshub_kg_hierarchy_total{what, answer}``) when the switch is off, inside a caller's own transaction, for
the system role, when a read fails, and when a row the read passes through is another connector's (that connector's copy is not the one checked) or has no ``path`` (the stamper
could not place it: a cycle, or a chain deeper than it stores).
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from app.core.org_context import current_org_id, peek
from app.services.graph_db.postgres.kg import net
from app.services.graph_db.postgres.kg import read as kg_read
from app.services.graph_db.postgres.kg.stamp import MAX_DEPTH
from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.core.db import Tx

logger = logging.getLogger(__name__)
_SERVED: ContextVar[list[str] | None] = ContextVar("kg_hierarchy_served", default=None)      # the connectors a read was answered for: what a difference demotes
SAMPLE_WALK = "/* pipeshub:kg.hier_sample_limits */ SELECT set_config('statement_timeout', '10s', true)"
HIERARCHY = METRICS_BACKEND.counter("pipeshub_kg_hierarchy_total", "Descendant and level reads, by who answered and why the walk did", ["what", "answer"])


class Fallback(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# Every read below is a seek on the index of its key, whatever the statistics say (a table not analysed yet after a first large sync): the unit turns sorts off (``PROFILE``), and each
# read asks for its key's order, which only that index gives without a sort (as ``read.BY_ID``). A key read by equality is spelt as a range of one value for the same reason (an
# equality would leave the order to any index on the org). OFFSET 0 keeps each read on its row. JIT is off: the cost a disabled sort adds would compile every statement (half a second).
# Sequential scans are off too: a seek is every read's plan, and a table whose statistics say one key holds most of an org's rows must not tip a read to a scan of it
# (PostgreSQL 18 planned one for the nest after an ANALYZE in CI).
PROFILE = ("/* pipeshub:kg.hier_profile */ SELECT set_config('enable_sort', 'off', true), set_config('enable_bitmapscan', 'off', true), set_config('enable_seqscan', 'off', true), "
           "set_config('jit', 'off', true)")


def _sk(x: str, table: str = "kg.records") -> str:
    """The row of ``table`` whose sk is ``x``."""
    return f"(SELECT * FROM (SELECT * FROM {table} k WHERE k.org_id = $1 AND k.sk >= {x} ORDER BY k.sk LIMIT 1) s WHERE s.sk = {x} OFFSET 0)"


def _id(coll: str, x: str) -> str:
    """The kg.records row of collection code ``coll`` whose id is ``x``."""
    return f"(SELECT * FROM (SELECT * FROM kg.records k WHERE k.org_id = $1 AND k.coll = {coll} AND k.id >= {x} ORDER BY k.id LIMIT 1) s WHERE s.id = {x} OFFSET 0)"


def _node(coll: str, x: str) -> str:
    """The graph.nodes document of collection ``coll`` (an expression) whose id is ``x``."""
    return f"(SELECT * FROM (SELECT * FROM graph.nodes k WHERE k.org_id = $1 AND k.coll = {coll} AND k.id >= {x} ORDER BY k.id LIMIT 1) s WHERE s.id = {x} OFFSET 0)"


def _below(x: str, coll: int, *, by: str = "parent_sk", link: int = 1) -> str:
    """The sks of the rows of collection code ``coll`` under ``x``: by ``by`` (``parent_sk``: the primary hierarchy parent, ``group_sk``: the first group), then by kg.links of kind ``link``."""
    return (f"(SELECT c.sk FROM kg.records c WHERE c.org_id = $1 AND c.{by} >= {x} AND c.{by} <= {x} AND c.coll = {coll} ORDER BY c.{by}, c.sk) "
            f"UNION ALL (SELECT r.sk FROM (SELECT l.child_sk FROM kg.links l WHERE l.org_id = $1 AND (l.parent_sk, l.kind) >= ({x}, {link}) AND (l.parent_sk, l.kind) <= ({x}, {link}) "
            f"ORDER BY l.parent_sk, l.kind, l.child_sk) l CROSS JOIN LATERAL {_sk('l.child_sk')} r WHERE r.coll = {coll})")


def _links_up(x: str, kind: int) -> str:
    """The parents of ``x`` by kg.links of kind ``kind``."""
    return (f"(SELECT l.parent_sk FROM kg.links l WHERE l.org_id = $1 AND (l.child_sk, l.kind) >= ({x}, {kind}) AND (l.child_sk, l.kind) <= ({x}, {kind}) "
            f"ORDER BY l.child_sk, l.kind)")


def _not_held() -> str:
    """Whether kg holds no row for the edge end ``e`` (collection name ``e.oc``, id ``e.oi``): one seek per collection, each on a constant collection code. A code computed
    from the end (a CASE) leaves the collection out of the index bound, and PostgreSQL 18.7 then planned a scan of the organisation's records with a disabled sort."""
    return (f"CASE e.oc WHEN 'records' THEN NOT EXISTS (SELECT 1 FROM {_id('3', 'e.oi')} k) WHEN 'recordGroups' THEN NOT EXISTS (SELECT 1 FROM {_id('2', 'e.oi')} k) "
            f"ELSE NOT EXISTS (SELECT 1 FROM {_id('1', 'e.oi')} k) END")


def _unheld(x: str) -> str:
    """Whether an edge the walk can follow touches the node of id ``x`` and a node kg does not hold: a hierarchy relation or a belongs-to edge, out of it or into it, whose
    other end is a record, a record group or an App kg has no row for (an id longer than its writers take, or a node removed while its edge stays). The walk follows such an
    edge; kg cannot, so the walk must answer. One rule for every read: each visited node is held to it."""
    out = [f"(SELECT e.to_coll AS oc, e.to_id AS oi FROM graph.edges e WHERE e.org_id = $1 AND e.coll = '{c}' AND e.from_id >= {x} AND e.from_id <= {x} ORDER BY e.from_id)"
           for c in ("nodeRelations", "belongsTo")]
    into = [f"(SELECT e.from_coll AS oc, e.from_id AS oi FROM graph.edges e WHERE e.org_id = $1 AND e.coll = '{c}' AND e.to_id >= {x} AND e.to_id <= {x} ORDER BY e.to_id)"
            for c in ("nodeRelations", "belongsTo")]
    return (f"EXISTS (SELECT 1 FROM ({' UNION ALL '.join(out + into)}) e WHERE e.oc IN ('records', 'recordGroups', 'apps') "
            f"AND {_not_held()})")


APP_KEY = "/* pipeshub:kg.hier_app */ SELECT sk FROM (SELECT * FROM kg.node_keys k WHERE k.org_id = $1 AND k.coll = 1 AND k.id >= $2 ORDER BY k.id LIMIT 1) s WHERE s.id = $2"
NODE = f"""/* pipeshub:kg.hier_node */ SELECT r.sk, a.id AS app_id FROM {_id("$2", "$3")} r LEFT JOIN LATERAL {_sk("r.app_sk", "kg.node_keys")} a ON a.coll = 1"""
_DOC = """r.doc->>'connectorId' = {c} AND (r.doc->>'orgId' = {o} OR r.doc->>'orgId' IS NULL) AND coalesce((r.doc->>'isDeleted')::boolean, false) = false
      AND ({s}::text[] IS NULL OR r.doc->>'indexingStatus' = ANY({s}::text[])) AND ({x}::text[] IS NULL OR NOT coalesce(r.doc->>'indexingStatus', '') = ANY({x}::text[]))
      AND ({a}::text IS NULL OR r.id > {a})"""

# The records below the record $2 (an sk), $3 levels down, by every hierarchy parent: the primary one in ``parent_sk``, the others in kg.links. Only records, as the walk follows
# record-to-record edges. ``untrusted``: a row it passes through is not the connector's ($4) or has no path. ``ids``: the walk's page of them (its document filters, its order).
BELOW = f"""/* pipeshub:kg.hier_below */ WITH RECURSIVE w(sk, d) AS (
    SELECT $2::bigint, 0
    UNION SELECT x.sk, w.d + 1 FROM w CROSS JOIN LATERAL ({_below("w.sk", 3)}) x WHERE w.d < $3),
  v AS MATERIALIZED (SELECT r.sk, r.id, r.app_sk, r.path FROM (SELECT DISTINCT sk FROM w) q CROSS JOIN LATERAL {_sk("q.sk")} r)
SELECT (SELECT coalesce(bool_or(v.app_sk IS DISTINCT FROM $4 OR v.path IS NULL), false) FROM v) AS untrusted,
  EXISTS (SELECT 1 FROM v WHERE {_unheld("v.id")}) AS unheld,
  array(SELECT r.id FROM v CROSS JOIN LATERAL {_node("'records'", "v.id")} r
    WHERE {_DOC.format(c="$5", o="$6", s="$7", x="$8", a="$9")} ORDER BY r.id OFFSET $10 LIMIT $11) AS ids"""

# The record group $2 and the groups that belong to it, $4 levels down (``group_sk`` and the group links), each of the walk's organisation ($3, or none) as the walk reads its document.
NEST = f"""/* pipeshub:kg.hier_nest */ WITH RECURSIVE nest(sk, d) AS (
    SELECT g.sk, 0 FROM {_id("2", "$2")} g CROSS JOIN LATERAL {_node("'recordGroups'", "g.id")} n WHERE n.doc->>'orgId' = $3
    UNION SELECT c.sk, nest.d + 1 FROM nest CROSS JOIN LATERAL ({_below("nest.sk", 2, by="group_sk", link=2)}) x CROSS JOIN LATERAL {_sk("x.sk")} c
      CROSS JOIN LATERAL {_node("'recordGroups'", "c.id")} n
    WHERE nest.d < $4 AND (n.doc->>'orgId' = $3 OR n.doc->>'orgId' IS NULL))
SELECT r.sk, r.id, r.app_sk, r.path IS NULL AS no_path, {_unheld("r.id")} AS unheld
FROM (SELECT DISTINCT sk FROM nest) q CROSS JOIN LATERAL {_sk("q.sk")} r"""

# The records that belong to one of the groups $2 (sks) and pass the walk's document filters and its file test (its candidates), then those reachable from a candidate no other
# candidate is a hierarchy parent of, 100 levels down within the candidates (a cycle of candidates with no root is not returned, as by the walk). ``untrusted`` as for BELOW.
GROUP_RECORDS = f"""/* pipeshub:kg.hier_group_records */ WITH RECURSIVE cand AS MATERIALIZED (
    SELECT c.sk, c.id, c.parent_sk, c.app_sk, c.path FROM (SELECT DISTINCT m.sk FROM unnest($2::bigint[]) g(sk) CROSS JOIN LATERAL ({_below("g.sk", 3, by="group_sk", link=2)}) m) m
      CROSS JOIN LATERAL {_sk("m.sk")} c CROSS JOIN LATERAL {_node("'records'", "c.id")} r
      CROSS JOIN LATERAL (SELECT t.doc, t.coll AS tcoll FROM (SELECT e.to_coll, e.to_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'isOfType' AND e.from_id >= r.id
          AND e.from_id <= r.id ORDER BY e.from_id) e CROSS JOIN LATERAL {_node("e.to_coll", "e.to_id")} t LIMIT 1) ty
      WHERE {_DOC.format(c="$3", o="$4", s="$5", x="$6", a="$7")} AND (ty.tcoll <> 'files' OR ty.doc->>'isFile' = 'true')),
  reach(sk, d) AS (
    SELECT c.sk, 0 FROM cand c WHERE NOT EXISTS (SELECT 1 FROM cand p WHERE p.sk = c.parent_sk)
      AND NOT EXISTS (SELECT 1 FROM {_links_up("c.sk", 1)} l JOIN cand p ON p.sk = l.parent_sk)
    UNION SELECT x.sk, reach.d + 1 FROM reach CROSS JOIN LATERAL ({_below("reach.sk", 3)}) x WHERE reach.d < 100 AND x.sk IN (SELECT sk FROM cand))
SELECT (SELECT coalesce(bool_or(cand.app_sk IS DISTINCT FROM $8 OR cand.path IS NULL), false) FROM cand) AS untrusted,
  array(SELECT c.id FROM (SELECT DISTINCT sk FROM reach) q JOIN cand c ON c.sk = q.sk ORDER BY c.id OFFSET $9 LIMIT $10) AS ids"""

# Each asked record ($2) and its record ancestors by every hierarchy parent, $3 levels up: (asked id, ancestor sk, levels up). A level under a parent is read from these.
# Each node on the way up is read, and its edges held to kg (``unheld``), once: asked siblings share their ancestors.
UP = f"""/* pipeshub:kg.hier_up */ WITH RECURSIVE up(node, sk, k) AS (
    SELECT r.id, r.sk, 0 FROM unnest($2::text[]) q(id) CROSS JOIN LATERAL {_id("3", "q.id")} r
    UNION SELECT up.node, p.sk, up.k + 1 FROM up CROSS JOIN LATERAL (
      SELECT c.parent_sk AS sk FROM {_sk("up.sk")} c UNION ALL SELECT l.parent_sk FROM {_links_up("up.sk", 1)} l) x
      CROSS JOIN LATERAL {_sk("x.sk")} p
    WHERE up.k < $3 AND p.coll = 3),
  a AS MATERIALIZED (SELECT r.sk, r.app_sk, r.path IS NULL AS no_path, {_unheld("r.id")} AS unheld FROM (SELECT DISTINCT sk FROM up) q CROSS JOIN LATERAL {_sk("q.sk")} r)
SELECT up.node, up.sk, up.k, a.app_sk, a.no_path, a.unheld FROM up JOIN a ON a.sk = up.sk"""
# The members among the sks $2 of the group $3 (its group, or a group link).
GROUP_MEMBERS = f"""/* pipeshub:kg.hier_group_members */ SELECT c.sk FROM unnest($2::bigint[]) q(sk) CROSS JOIN LATERAL {_sk("q.sk")} c
    WHERE c.group_sk = $3 OR EXISTS (SELECT 1 FROM {_links_up("c.sk", 2)} l WHERE l.parent_sk = $3)"""
# The members among the sks $2 of a live group of the connector $3, or of the connector itself (a belongs-to edge to the App $4, which kg does not copy).
APP_MEMBERS = f"""/* pipeshub:kg.hier_app_members */ SELECT c.sk FROM unnest($2::bigint[]) q(sk) CROSS JOIN LATERAL {_sk("q.sk")} c WHERE
    EXISTS (SELECT 1 FROM (SELECT c.group_sk AS sk UNION ALL SELECT l.parent_sk FROM {_links_up("c.sk", 2)} l) x CROSS JOIN LATERAL {_sk("x.sk")} g
            WHERE g.kind = 2 AND g.app_sk = $3 AND NOT g.deleted)
    OR EXISTS (SELECT 1 FROM (SELECT e.from_coll, e.to_coll, e.to_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'belongsTo' AND e.from_id >= c.id AND e.from_id <= c.id
               ORDER BY e.from_id) e WHERE e.from_coll = 'records' AND e.to_coll = 'apps' AND e.to_id = $4)"""




def _unheld_up(x: str) -> str:
    """Whether an edge the hub's trail follows up from the node of id ``x`` (a hierarchy relation into it, a belongs-to edge out of it) has an end kg does not hold. Only these:
    :func:`_unheld` also reads the edges down, every member of a large group."""
    return (f"EXISTS (SELECT 1 FROM ((SELECT e.from_coll AS oc, e.from_id AS oi FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'nodeRelations' AND e.to_id >= {x} "
            f"AND e.to_id <= {x} AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT') ORDER BY e.to_id) "
            f"UNION ALL (SELECT e.to_coll, e.to_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'belongsTo' AND e.from_id >= {x} AND e.from_id <= {x} ORDER BY e.from_id)) e "
            f"WHERE e.oc IN ('records', 'recordGroups', 'apps') AND {_not_held()})")


# Each asked id's node, a record first, then a group, then an app, with a record's file type. Every read is a seek on one (collection, id) key: the typed graph's trail runs
# this under its profile (sorts, bitmap and sequential scans off), where an id list across several collections would be a scan of the whole organisation.
HUB_NODES = f"""/* pipeshub:graph.hub_node */ SELECT q.id AS key, coalesce(r.coll, g.coll, a.coll) AS coll, coalesce(r.doc, g.doc, a.doc) AS doc,
    (SELECT f.doc->>'isFile' FROM (SELECT e.to_coll, e.to_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'isOfType' AND e.from_id >= r.id AND e.from_id <= r.id
        AND e.from_coll = 'records' ORDER BY e.from_id) e CROSS JOIN LATERAL {_node("'files'", "e.to_id")} f LIMIT 1) AS is_file
  FROM unnest($2::text[]) AS q(id)
  LEFT JOIN LATERAL {_node("'records'", "q.id")} r ON true
  LEFT JOIN LATERAL {_node("'recordGroups'", "q.id")} g ON r.id IS NULL
  LEFT JOIN LATERAL {_node("'apps'", "q.id")} a ON r.id IS NULL AND g.id IS NULL
  WHERE coalesce(r.id, g.id, a.id) IS NOT NULL"""

# One node of the hub's trail ($2, an sk): its row, its record parents (the primary one and the linked ones that are records), its groups (the first and the linked ones) and
# whether an edge the trail follows up from it has an end kg does not hold.
TRAIL_NODE = f"""/* pipeshub:kg.hier_trail_node */ SELECT r.sk, r.coll, r.id, r.app_sk, r.path IS NULL AS no_path,
    array(SELECT p.sk FROM (SELECT r.parent_sk AS sk UNION ALL SELECT l.parent_sk FROM {_links_up("r.sk", 1)} l) x CROSS JOIN LATERAL {_sk("x.sk")} p WHERE p.coll = 3) AS parents,
    array(SELECT x.sk FROM (SELECT r.group_sk AS sk UNION ALL SELECT l.parent_sk FROM {_links_up("r.sk", 2)} l) x WHERE x.sk IS NOT NULL) AS groups,
    {_unheld_up("r.id")} AS unheld
  FROM {_sk("$2")} r"""
# The App the node $3 (of collection $2) belongs to, the lowest id first: kg does not copy that edge, so the trail's last step is read from it, as the walk reads it.
TRAIL_APP = """/* pipeshub:kg.hier_trail_app */ SELECT min(e.to_id) FROM (SELECT e.from_coll, e.to_coll, e.to_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'belongsTo'
    AND e.from_id >= $3 AND e.from_id <= $3 ORDER BY e.from_id) e WHERE e.from_coll = $2 AND e.to_coll = 'apps'"""
_COLL_NAME = {3: "records", 2: "recordGroups"}


async def trail(tx: Tx, org: str, node_id: str) -> list[tuple[int, str]]:
    """The knowledge hub's trail from a node up, as (collection code, id): each step's parent chosen as the walk chooses it (``HubMixin._hub_trail``: a record's record parent,
    else the group it belongs to, else the App it belongs to; a group's group, else its App). Raises Fallback where the walk would choose among several (it takes the lowest
    id; kg keeps its own order), at a row of another connector or with no path (a cycle among them), and where an edge the walk follows up leads to a node kg does not hold."""
    start = None
    for coll in (3, 2, 1):                              # the walk's order when an id names nodes of more than one collection
        if (start := await tx.fetchrow(NODE, org, coll, node_id)) is not None:
            break
    if start is None:
        raise Fallback("unknown")
    app_sk = await _served(tx, org, start["app_id"])
    out: list[tuple[int, str]] = []
    sk = start["sk"]
    while True:
        r = await tx.fetchrow(TRAIL_NODE, org, sk)
        if r is None:
            raise Fallback("unknown")
        if r["app_sk"] != app_sk or (r["no_path"] and r["coll"] != 1) or len(out) > MAX_DEPTH:      # an App's row has no path
            raise Fallback("reach")
        if r["unheld"]:
            raise Fallback("unheld")
        out.append((r["coll"], r["id"]))
        if r["coll"] == 1:
            return out
        up = r["parents"] if r["coll"] == 3 and r["parents"] else r["groups"]
        if len(up) > 1:
            raise Fallback("ambiguous")
        if not up:
            app = await tx.fetchval(TRAIL_APP, org, _COLL_NAME[r["coll"]], r["id"])
            return [*out, (1, app)] if app is not None else out
        sk = up[0]


def _pc_unheld(x: str, *, up: bool) -> str:
    """Whether a PARENT_CHILD relation between records into the record of id ``x`` (``up``) or out of it has an end kg does not hold as a record. The move guards follow
    exactly these relations, to whatever is at their end; kg follows its kind-3 links, which name only the records it holds."""
    end, other = ("to_id", "from_id") if up else ("from_id", "to_id")
    return (f"EXISTS (SELECT 1 FROM (SELECT e.{other} AS oi FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'nodeRelations' AND e.{end} >= {x} AND e.{end} <= {x} "
            f"AND e.from_coll = 'records' AND e.to_coll = 'records' AND e.doc->>'relationshipType' = 'PARENT_CHILD' ORDER BY e.{end}) e "
            f"WHERE NOT EXISTS (SELECT 1 FROM {_id('3', 'e.oi')} k))")


def _pc_up(x: str) -> str:
    """The record parents of ``x`` by a PARENT_CHILD relation (kind-3 links), as rows ``p``."""
    return f"{_links_up(x, 3)} l CROSS JOIN LATERAL {_sk('l.parent_sk')} p"


def _pc_down(x: str) -> str:
    """The record children of ``x`` by a PARENT_CHILD relation (kind-3 links), as rows ``p``."""
    return (f"(SELECT l.child_sk FROM kg.links l WHERE l.org_id = $1 AND (l.parent_sk, l.kind) >= ({x}, 3) AND (l.parent_sk, l.kind) <= ({x}, 3) "
            f"ORDER BY l.parent_sk, l.kind, l.child_sk) l CROSS JOIN LATERAL {_sk('l.child_sk')} p")


# The facts every move guard holds its rows to: a row of another connector than $3 (its copy is not the one checked), and a PARENT_CHILD relation the guard follows from a
# visited record (``v``, with ``id``) to a node kg does not hold.
def _pc_checks(*, up: bool) -> str:
    return (f"(SELECT coalesce(bool_or(v.app_sk IS DISTINCT FROM $3), false) FROM v) AS untrusted, "
            f"EXISTS (SELECT 1 FROM v WHERE {_pc_unheld('v.id', up=up)}) AS unheld")


# The record $2 (an sk) and its ancestors by PARENT_CHILD relations between records, to a fixpoint (a cycle ends where it closes): is the record of id $4 among the ancestors?
PC_ANCESTOR = f"""/* pipeshub:kg.hier_pc_ancestor */ WITH RECURSIVE up(sk) AS (
    SELECT p.sk FROM {_pc_up("$2::bigint")} WHERE p.coll = 3
    UNION SELECT p.sk FROM up CROSS JOIN LATERAL {_pc_up("up.sk")} WHERE p.coll = 3),
  v AS MATERIALIZED (SELECT r.sk, r.id, r.app_sk FROM (SELECT $2::bigint AS sk UNION SELECT sk FROM up) q CROSS JOIN LATERAL {_sk("q.sk")} r)
SELECT {_pc_checks(up=True)}, EXISTS (SELECT 1 FROM up JOIN v ON v.sk = up.sk WHERE v.id = $4) AS found"""

# The longest chain of PARENT_CHILD record parents above the record $2 (an sk) that repeats no record, at most $4 long, as the walk counts it.
PC_DEPTH = f"""/* pipeshub:kg.hier_pc_depth */ WITH RECURSIVE up(sk, depth, seen) AS (
    SELECT $2::bigint, 0, ARRAY[$2::bigint]
    UNION ALL SELECT p.sk, up.depth + 1, up.seen || p.sk FROM up CROSS JOIN LATERAL {_pc_up("up.sk")} WHERE p.coll = 3 AND NOT p.sk = ANY(up.seen) AND up.depth < $4),
  v AS MATERIALIZED (SELECT r.sk, r.id, r.app_sk FROM (SELECT DISTINCT sk FROM up) q CROSS JOIN LATERAL {_sk("q.sk")} r)
SELECT {_pc_checks(up=True)}, (SELECT max(depth) FROM up) AS depth"""

# The longest chain of PARENT_CHILD record children below the record $2 (an sk) that repeats no record, at most $4 long, ending at a record whose document's mimeType is
# one of $5 (a folder): its length, 0 when there is none.
PC_HEIGHT = f"""/* pipeshub:kg.hier_pc_height */ WITH RECURSIVE down(sk, depth, seen) AS (
    SELECT $2::bigint, 0, ARRAY[$2::bigint]
    UNION ALL SELECT p.sk, down.depth + 1, down.seen || p.sk FROM down CROSS JOIN LATERAL {_pc_down("down.sk")} WHERE p.coll = 3 AND NOT p.sk = ANY(down.seen) AND down.depth < $4),
  v AS MATERIALIZED (SELECT r.sk, r.id, r.app_sk FROM (SELECT DISTINCT sk FROM down) q CROSS JOIN LATERAL {_sk("q.sk")} r)
SELECT {_pc_checks(up=False)}, (SELECT coalesce(max(d.depth), 0) FROM down d JOIN v ON v.sk = d.sk CROSS JOIN LATERAL {_node("'records'", "v.id")} n
    WHERE d.depth >= 1 AND n.doc->>'mimeType' = ANY($5::text[])) AS height"""


async def _pc_start(tx: Tx, org: str, record_id: str) -> tuple[int, int]:
    """The record's sk and the sk of the connector kg serves it for. Raises Fallback."""
    start = await tx.fetchrow(NODE, org, 3, record_id)
    if start is None:
        raise Fallback("unknown")                       # the walk still follows the relations into an id kg holds no record for
    return start["sk"], await _served(tx, org, start["app_id"])


def _pc_held(row: Any) -> Any:  # noqa: ANN401
    if row["untrusted"]:
        raise Fallback("reach")
    if row["unheld"]:
        raise Fallback("unheld")
    return row


async def pc_ancestor(tx: Tx, org: str, record_id: str, ancestor_id: str) -> bool:
    """``is_record_descendant_of``: whether ``ancestor_id`` is above the record by PARENT_CHILD relations between records. Raises Fallback."""
    sk, app_sk = await _pc_start(tx, org, record_id)
    return bool(_pc_held(await tx.fetchrow(PC_ANCESTOR, org, sk, app_sk, ancestor_id))["found"])


async def pc_depth(tx: Tx, org: str, folder_id: str, bound: int) -> int:
    """``get_folder_depth``: 1 for a record with no PARENT_CHILD record parent, else one more than the longest chain above it. Raises Fallback."""
    sk, app_sk = await _pc_start(tx, org, folder_id)
    return int(_pc_held(await tx.fetchrow(PC_DEPTH, org, sk, app_sk, bound))["depth"] or 0) + 1


async def pc_height(tx: Tx, org: str, folder_id: str, folder_mime_types: list[str], bound: int) -> int:
    """``get_folder_subtree_height``: how many PARENT_CHILD levels below the record the deepest folder is (0 for none). Raises Fallback."""
    sk, app_sk = await _pc_start(tx, org, folder_id)
    return int(_pc_held(await tx.fetchrow(PC_HEIGHT, org, sk, app_sk, bound, list(folder_mime_types)))["height"])


async def _served(tx: Tx, org: str, app_id: str | None) -> int:
    """The sk of a connector kg answers for (``read.serves``, in the caller's snapshot). Raises Fallback."""
    if not app_id or not await kg_read.serves(tx, org, app_id):
        raise Fallback("not_served")
    if (served := _SERVED.get()) is not None:
        served.append(app_id)
    return await tx.fetchval(APP_KEY, org, app_id)


async def below(tx: Tx, org: str, parent_id: str, connector_id: str, org_id: str, bound: int, status: list[str] | None, exclude: list[str] | None, after: str | None,
                offset: int, limit: int | None) -> list[str]:
    """``get_records_by_parent_record``'s ids: the record and its descendants ``bound`` levels down, of the connector, in id order. Raises Fallback."""
    app_sk = await _served(tx, org, connector_id)
    start = await tx.fetchrow(NODE, org, 3, parent_id)
    if start is None:
        raise Fallback("unknown")
    row = await tx.fetchrow(BELOW, org, start["sk"], bound, app_sk, connector_id, org_id, status, exclude, after, offset, limit)
    if row["untrusted"]:
        raise Fallback("reach")
    if row["unheld"]:
        raise Fallback("unheld")
    return list(row["ids"])


async def nested_groups(tx: Tx, org: str, group_id: str, connector_id: str, org_id: str, bound: int) -> list[tuple[int, str]]:
    """``get_records_by_record_group``'s groups: the group and those below it ``bound`` levels down, as (sk, id). Raises Fallback."""
    app_sk = await _served(tx, org, connector_id)
    rows = await tx.fetch(NEST, org, group_id, org_id, bound)
    if not rows:
        raise Fallback("unknown")
    if any(r["app_sk"] != app_sk or r["no_path"] for r in rows):
        raise Fallback("reach")
    if any(r["unheld"] for r in rows):
        raise Fallback("unheld")                        # a member the walk lists (or a group it follows) that kg does not hold
    return [(r["sk"], r["id"]) for r in rows]


async def group_records(tx: Tx, org: str, groups: list[int], connector_id: str, org_id: str, status: list[str] | None, exclude: list[str] | None, after: str | None,
                        offset: int, limit: int | None) -> list[str]:
    """``get_records_by_record_group``'s ids for the groups ``groups`` (sks), in id order. Raises Fallback."""
    app_sk = await _served(tx, org, connector_id)
    row = await tx.fetchrow(GROUP_RECORDS, org, groups, connector_id, org_id, status, exclude, after, app_sk, offset, limit)
    if row["untrusted"]:
        raise Fallback("reach")
    return list(row["ids"])


async def depths(tx: Tx, org: str, parent_id: str, parent_type: str | None, node_ids: list[str], safe: int, rec_depth: int) -> dict[str, int]:
    """``get_node_depths_batch``: the shallowest level of each record under the parent, read upwards from the records. Under a record its own distance (1..``safe``); under a
    group or a connector, one more than the distance to a member of it (up to ``rec_depth`` + 1). Raises Fallback."""
    members_of = parent_type in ("app", "recordGroup")
    if parent_type == "app":
        app_id = parent_id
    else:
        node = await tx.fetchrow(NODE, org, 2 if parent_type == "recordGroup" else 3, parent_id)
        if node is None:
            raise Fallback("unknown")
        app_id = node["app_id"]
    app_sk = await _served(tx, org, app_id)
    rows = await tx.fetch(UP, org, node_ids, rec_depth if members_of else safe)
    if any(r["app_sk"] != app_sk or r["no_path"] for r in rows):
        raise Fallback("reach")
    if any(r["unheld"] for r in rows):
        raise Fallback("unheld")                        # a parent the walk comes down through that kg does not hold
    if set(node_ids) - {r["node"] for r in rows}:
        raise Fallback("not_projected")                 # an asked id kg holds no record for (not projected, too long, or gone while an edge to it stays): the walk may still reach it
    sks = sorted({r["sk"] for r in rows})
    if parent_type == "app":
        hits = {r["sk"] for r in await tx.fetch(APP_MEMBERS, org, sks, app_sk, parent_id)}
    elif parent_type == "recordGroup":
        hits = {r["sk"] for r in await tx.fetch(GROUP_MEMBERS, org, sks, node["sk"])}
    else:
        hits = {node["sk"]}
    out: dict[str, int] = {}
    for r in rows:
        if r["sk"] in hits and (members_of or r["k"] >= 1):
            lvl = r["k"] + 1 if members_of else r["k"]
            out[r["node"]] = min(out.get(r["node"], lvl), lvl)
    return out


async def answer(provider: Any, what: str, transaction: str | None, fn: Callable[[Tx], Awaitable[Any]],  # noqa: ANN401
                 walk: Callable[[Tx], Awaitable[Any]] | None = None, shape: Callable[[Any], Any] = lambda x: x) -> Any:  # noqa: ANN401
    """``fn``'s answer from kg in one snapshot (the page's, or a unit of its own), or None when the walk answers (counted with the reason). A caller's own transaction may hold
    writes kg has not been given, and the system role cannot read kg: both are the walk's. With ``walk`` (the walk's answer to the same read, in a transaction), a share of
    the answers is read again by both in one snapshot off the caller's path (:func:`net.maybe_compare`): a difference (``shape`` of kg's answer against the walk's) takes the
    connector back to the walk."""
    snapshot = bool(transaction) and bool(getattr(provider._txns.get(transaction), "snapshot", False))
    ctx = peek()
    if (transaction and not snapshot) or ctx is None or ctx.kind == "system" or not kg_read.enabled():
        HIERARCHY.inc(what, "off")
        return None
    apps: list[str] = []

    async def profiled(tx: Tx, served: list[str] = apps) -> Any:  # noqa: ANN401
        token = _SERVED.set(served)
        try:
            await tx.fetchval(PROFILE)
            return await fn(tx)
        finally:
            _SERVED.reset(token)
    try:
        if snapshot:
            out = await provider._q(lambda tx: kg_read.in_savepoint(tx, profiled), transaction)
        else:
            out = await provider._db.run(profiled, isolation="repeatable_read", custom_plan=True)
    except Fallback as e:
        HIERARCHY.inc(what, e.reason)
        return None
    except Exception:
        logger.warning("kg %s failed; the walk answers", what, exc_info=True)
        HIERARCHY.inc(what, "error")
        return None
    HIERARCHY.inc(what, "kg")
    if walk is not None:
        async def differs(tx: Tx) -> bool:
            await tx.fetchval(SAMPLE_WALK)
            walked = await walk(tx)                     # first, with the walk's own plan settings
            try:
                mine = await profiled(tx, [])
            except Fallback:
                return False                            # kg would not answer now: nothing to hold against it
            return shape(mine) != walked
        net.maybe_compare(lambda f: provider._db.run(f, isolation="repeatable_read", custom_plan=True), provider._db, current_org_id(), apps,
                          f"hierarchy_{what}", differs)
    return out
