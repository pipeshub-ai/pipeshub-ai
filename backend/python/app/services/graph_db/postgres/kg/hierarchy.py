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
from typing import TYPE_CHECKING, Any

from app.core.org_context import current_or_system
from app.services.graph_db.postgres.kg import read as kg_read
from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.core.db import Tx

logger = logging.getLogger(__name__)
HIERARCHY = METRICS_BACKEND.counter("pipeshub_kg_hierarchy_total", "Descendant and level reads, by who answered and why the walk did", ["what", "answer"])


class Fallback(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# Every read below is a seek on the index of its key, whatever the statistics say (a table not analysed yet after a first large sync): the unit turns sorts off (``PROFILE``), and each
# read asks for its key's order, which only that index gives without a sort (as ``read.BY_ID``). A key read by equality is spelt as a range of one value for the same reason (an
# equality would leave the order to any index on the org). OFFSET 0 keeps each read on its row. JIT is off: the cost a disabled sort adds would compile every statement (half a second).
PROFILE = "/* pipeshub:kg.hier_profile */ SELECT set_config('enable_sort', 'off', true), set_config('enable_bitmapscan', 'off', true), set_config('jit', 'off', true)"


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


APP_KEY = "/* pipeshub:kg.hier_app */ SELECT sk FROM (SELECT * FROM kg.node_keys k WHERE k.org_id = $1 AND k.coll = 1 AND k.id >= $2 ORDER BY k.id LIMIT 1) s WHERE s.id = $2"
NODE = f"""/* pipeshub:kg.hier_node */ SELECT r.sk, a.id AS app_id FROM {_id("$2", "$3")} r LEFT JOIN LATERAL {_sk("r.app_sk", "kg.node_keys")} a ON a.coll = 1"""
GRAPH_RECORDS = f"""/* pipeshub:kg.hier_graph_records */ SELECT EXISTS (SELECT 1 FROM unnest($2::text[]) q(id) CROSS JOIN LATERAL {_node("'records'", "q.id")} n)"""
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
  array(SELECT r.id FROM v CROSS JOIN LATERAL {_node("'records'", "v.id")} r
    WHERE {_DOC.format(c="$5", o="$6", s="$7", x="$8", a="$9")} ORDER BY r.id OFFSET $10 LIMIT $11) AS ids"""

# The record group $2 and the groups that belong to it, $4 levels down (``group_sk`` and the group links), each of the walk's organisation ($3, or none) as the walk reads its document.
NEST = f"""/* pipeshub:kg.hier_nest */ WITH RECURSIVE nest(sk, d) AS (
    SELECT g.sk, 0 FROM {_id("2", "$2")} g CROSS JOIN LATERAL {_node("'recordGroups'", "g.id")} n WHERE n.doc->>'orgId' = $3
    UNION SELECT c.sk, nest.d + 1 FROM nest CROSS JOIN LATERAL ({_below("nest.sk", 2, by="group_sk", link=2)}) x CROSS JOIN LATERAL {_sk("x.sk")} c
      CROSS JOIN LATERAL {_node("'recordGroups'", "c.id")} n
    WHERE nest.d < $4 AND (n.doc->>'orgId' = $3 OR n.doc->>'orgId' IS NULL))
SELECT r.sk, r.id, r.app_sk, r.path IS NULL AS no_path FROM (SELECT DISTINCT sk FROM nest) q CROSS JOIN LATERAL {_sk("q.sk")} r"""

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
UP = f"""/* pipeshub:kg.hier_up */ WITH RECURSIVE up(node, sk, k) AS (
    SELECT r.id, r.sk, 0 FROM unnest($2::text[]) q(id) CROSS JOIN LATERAL {_id("3", "q.id")} r
    UNION SELECT up.node, p.sk, up.k + 1 FROM up CROSS JOIN LATERAL (
      SELECT c.parent_sk AS sk FROM {_sk("up.sk")} c UNION ALL SELECT l.parent_sk FROM {_links_up("up.sk", 1)} l) x
      CROSS JOIN LATERAL {_sk("x.sk")} p
    WHERE up.k < $3 AND p.coll = 3)
SELECT up.node, up.sk, up.k, r.app_sk, r.path IS NULL AS no_path FROM up CROSS JOIN LATERAL {_sk("up.sk")} r"""
# The members among the sks $2 of the group $3 (its group, or a group link).
GROUP_MEMBERS = f"""/* pipeshub:kg.hier_group_members */ SELECT c.sk FROM unnest($2::bigint[]) q(sk) CROSS JOIN LATERAL {_sk("q.sk")} c
    WHERE c.group_sk = $3 OR EXISTS (SELECT 1 FROM {_links_up("c.sk", 2)} l WHERE l.parent_sk = $3)"""
# The members among the sks $2 of a live group of the connector $3, or of the connector itself (a belongs-to edge to the App $4, which kg does not copy).
APP_MEMBERS = f"""/* pipeshub:kg.hier_app_members */ SELECT c.sk FROM unnest($2::bigint[]) q(sk) CROSS JOIN LATERAL {_sk("q.sk")} c WHERE
    EXISTS (SELECT 1 FROM (SELECT c.group_sk AS sk UNION ALL SELECT l.parent_sk FROM {_links_up("c.sk", 2)} l) x CROSS JOIN LATERAL {_sk("x.sk")} g
            WHERE g.kind = 2 AND g.app_sk = $3 AND NOT g.deleted)
    OR EXISTS (SELECT 1 FROM (SELECT e.from_coll, e.to_coll, e.to_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'belongsTo' AND e.from_id >= c.id AND e.from_id <= c.id
               ORDER BY e.from_id) e WHERE e.from_coll = 'records' AND e.to_coll = 'apps' AND e.to_id = $4)"""


async def _served(tx: Tx, org: str, app_id: str | None) -> int:
    """The sk of a connector kg answers for (``read.serves``, in the caller's snapshot). Raises Fallback."""
    if not app_id or not await kg_read.serves(tx, org, app_id):
        raise Fallback("not_served")
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
    return list(row["ids"])


async def nested_groups(tx: Tx, org: str, group_id: str, connector_id: str, org_id: str, bound: int) -> list[tuple[int, str]]:
    """``get_records_by_record_group``'s groups: the group and those below it ``bound`` levels down, as (sk, id). Raises Fallback."""
    app_sk = await _served(tx, org, connector_id)
    rows = await tx.fetch(NEST, org, group_id, org_id, bound)
    if not rows:
        raise Fallback("unknown")
    if any(r["app_sk"] != app_sk or r["no_path"] for r in rows):
        raise Fallback("reach")
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
    missing = set(node_ids) - {r["node"] for r in rows}
    if missing and await tx.fetchval(GRAPH_RECORDS, org, sorted(missing)):
        raise Fallback("not_projected")                 # a record the graph holds and kg does not: the walk may reach it
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


async def answer(provider: Any, what: str, transaction: str | None, fn: Callable[[Tx], Awaitable[Any]]) -> Any:  # noqa: ANN401
    """``fn``'s answer from kg in one snapshot (the page's, or a unit of its own), or None when the walk answers (counted with the reason). A caller's own transaction may hold
    writes kg has not been given, and the system role cannot read kg: both are the walk's."""
    snapshot = bool(transaction) and bool(getattr(provider._txns.get(transaction), "snapshot", False))
    if (transaction and not snapshot) or current_or_system().kind == "system" or not kg_read.enabled():
        HIERARCHY.inc(what, "off")
        return None
    async def profiled(tx: Tx) -> Any:  # noqa: ANN401
        await tx.fetchval(PROFILE)
        return await fn(tx)
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
    return out
