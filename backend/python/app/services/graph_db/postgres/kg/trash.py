"""A connector's trash listing from the typed graph: ``list_trashed_records`` pages delete batches by ``kg.records`` (``deleted_ms``, ``delete_batch``) instead of reading the
organisation's whole trash.

Only the order and the grouping are kg's: the batches of the connector, newest first, one index seek each from the cursor on (``records_trash``, read backwards), and each batch's
records by ``records_trash_batch``. Which of a batch's records are its roots (connector, organisation, a parent in the same batch, a file for a single-file listing) is read from
``graph.nodes`` and ``graph.edges`` by the walk's own predicates, and a page's rows are shaped as the walk shapes them (``ROWS``), so an answer differs from the walk's only if kg's
columns do. The walk answers (``hierarchy.answer``) when the connector is not served, and when a batch a read passes through is one kg cannot place as the walk does: a record of
it in another connector or not in the graph, its delete time or batch not the graph's, or records of one batch deleted at different times (the walk sorts a batch by its roots'
latest time, kg by its records').
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.core.db.errors import DbTimeout
from app.services.graph_db.common.utils import TRASH_LIST_OTHER_ROOT_NAMES
from app.services.graph_db.postgres.kg import read as kg_read
from app.services.graph_db.postgres.kg.hierarchy import Fallback, _node, _served

if TYPE_CHECKING:
    from app.core.db import Tx

_TRASHED = "k.org_id = $1 AND k.app_sk = $2 AND k.coll = 3 AND k.deleted AND k.deleted_ms IS NOT NULL AND k.delete_batch IS NOT NULL"     # records_trash's predicate
_NEXT = f"SELECT k.deleted_ms, k.delete_batch FROM kg.records k WHERE {_TRASHED} AND {{after}} ORDER BY k.deleted_ms DESC, k.delete_batch DESC LIMIT 1"
# A record of the batch is a root as the walk decides (``TrashMixin.list_trashed_records``): of the connector ($3) and organisation ($4), stamped, with no hierarchy parent in the
# trash in the same batch, and (single-file listing, $5) a file. Every read is a seek, spelt as hierarchy.py spells them (a key read as a range of one value, in its order).
_ROOT = f"""n.doc->>'connectorId' = $3 AND n.doc->>'deletedAtTimestamp' IS NOT NULL AND n.doc->>'orgId' = $4 AND n.doc->>'deleteBatchId' IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM (SELECT e.from_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'nodeRelations' AND e.to_id >= n.id AND e.to_id <= n.id
          AND e.from_coll = 'records' AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT') ORDER BY e.to_id) e CROSS JOIN LATERAL {_node("'records'", "e.from_id")} p
        WHERE coalesce((p.doc->>'isDeleted')::boolean, false) AND p.doc->>'deleteBatchId' = n.doc->>'deleteBatchId')
      AND (NOT $5 OR EXISTS (SELECT 1 FROM (SELECT e.to_coll, e.to_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'isOfType' AND e.from_id >= n.id AND e.from_id <= n.id
          ORDER BY e.from_id) e CROSS JOIN LATERAL {_node("e.to_coll", "e.to_id")} t WHERE t.doc->>'isFile' = 'true'))"""
# The connector's batches newest first after the cursor ($6, $7), one seek each, with each batch's roots (by id), how many of its records are the connector's, and ``odd``:
# a record of it kg cannot place as the walk does (module docstring). A single-file listing keeps no batch of more than one record. A batch with no root is no item.
_BATCHES = f"""RECURSIVE b(ms, batch, n) AS (
    (SELECT x.deleted_ms, x.delete_batch, 1 FROM ({_NEXT.format(after="($6::bigint IS NULL OR (k.deleted_ms, k.delete_batch) < ($6::bigint, $7::text))")}) x)
    UNION ALL SELECT x.deleted_ms, x.delete_batch, b.n + 1 FROM b CROSS JOIN LATERAL ({_NEXT.format(after="(k.deleted_ms, k.delete_batch) < (b.ms, b.batch)")}) x),
  s AS (SELECT b.ms, b.batch, b.n, a.odd, a.batch_size, CASE WHEN $5 AND a.cnt > 1 THEN '{{}}'::text[] ELSE coalesce(a.roots, '{{}}'::text[]) END AS roots
    FROM b CROSS JOIN LATERAL (
      SELECT count(*) AS cnt, count(*) FILTER (WHERE n.doc->>'connectorId' = $3) AS batch_size, array_agg(m.id ORDER BY m.id) FILTER (WHERE {_ROOT}) AS roots,
        coalesce(bool_or(m.app_sk IS DISTINCT FROM $2 OR m.deleted_ms IS DISTINCT FROM b.ms OR n.id IS NULL OR NOT coalesce((n.doc->>'isDeleted')::boolean, false)
          OR n.doc->>'deleteBatchId' IS DISTINCT FROM b.batch OR (n.doc->>'deletedAtTimestamp')::bigint IS DISTINCT FROM b.ms), false) AS odd
      FROM (SELECT * FROM kg.records m WHERE m.org_id = $1 AND m.coll = 3 AND m.deleted AND m.delete_batch IS NOT NULL AND m.delete_batch >= b.batch
          AND m.delete_batch <= b.batch ORDER BY m.delete_batch OFFSET 0) m LEFT JOIN LATERAL {_node("'records'", "m.id")} n ON true) a)"""
PAGE = f"""/* pipeshub:kg.trash_page */ WITH {_BATCHES}
SELECT s.n, s.odd, s.batch, s.ms AS deleted_at, s.roots[1] AS first_id, cardinality(s.roots)::bigint AS root_count, s.batch_size,
    ARRAY(SELECT x.doc->>'recordName' FROM unnest(s.roots[2:1 + $9]) WITH ORDINALITY u(id, o) CROSS JOIN LATERAL {_node("'records'", "u.id")} x ORDER BY u.o) AS other_names
  FROM s WHERE cardinality(s.roots) > 0 OR s.odd LIMIT $8"""
COUNT = f"""/* pipeshub:kg.trash_count */ WITH {_BATCHES}
SELECT count(*) FILTER (WHERE cardinality(c.roots) > 0) AS total, coalesce(bool_or(c.odd), false) AS odd FROM (SELECT s.roots, s.odd FROM s WHERE cardinality(s.roots) > 0 OR s.odd LIMIT $8) c"""

# A page's rows, shaped as the walk shapes them (``TrashMixin`` in ``postgres/trash.py``, the statement after its ``page``), from the batches this module read
# (``page`` from JSON, with each batch's other root names and size already read). A record's parent edges are a seek on to_id spelt in its order: with sorts off,
# the walk's ``e.to_id = r.id ORDER BY e.from_id LIMIT 1`` is the primary key read in from_id order, every relation of the org up to the parent.
ROWS = """/* pipeshub:kg.trash_rows */ WITH page AS (SELECT * FROM jsonb_to_recordset($2::jsonb) AS x(o int, batch text, deleted_at bigint, first_id text, root_count bigint, batch_size bigint, other_names text[]))
            SELECT page.batch, page.deleted_at, r.doc AS rec, p.id AS parent_id, p.doc->>'recordName' AS parent_name, p.doc->>'isDeleted' AS parent_deleted,
                   ty.doc->>'isFile' AS is_file, ty.doc->>'mimeType' AS file_mime, ty.doc->'sizeInBytes' AS size, page.root_count,
                   page.other_names AS other_names,
                   page.batch_size AS batch_size,
                   CASE WHEN u.id IS NULL THEN NULL WHEN coalesce(u.doc->>'fullName', '') <> '' THEN u.doc->>'fullName'
                        ELSE trim(coalesce(u.doc->>'firstName', '') || ' ' || coalesce(u.doc->>'lastName', '')) END AS user_name, u.doc->>'email' AS user_email
            FROM page CROSS JOIN LATERAL (SELECT x.org_id, x.id, x.doc, x.doc->>'deletedByUserId' AS deleted_by FROM graph.nodes x
              WHERE x.org_id = $1 AND x.coll = 'records' AND x.id = page.first_id LIMIT 1) r
            LEFT JOIN LATERAL (SELECT pn.id, pn.doc FROM (SELECT e.org_id, e.from_coll, e.from_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'nodeRelations'
                AND e.to_id >= r.id AND e.to_id <= r.id AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT') ORDER BY e.to_id) e
              JOIN graph.nodes pn ON pn.org_id = e.org_id AND pn.coll = e.from_coll AND pn.id = e.from_id ORDER BY e.from_id LIMIT 1) p ON true
            LEFT JOIN LATERAL (SELECT t.doc, t.coll AS tcoll FROM (SELECT e.org_id, e.to_coll, e.to_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'isOfType'
                AND e.from_id >= r.id AND e.from_id <= r.id ORDER BY e.from_id) e
              JOIN graph.nodes t ON t.org_id = e.org_id AND t.coll = e.to_coll AND t.id = e.to_id LIMIT 1) ty ON true
            LEFT JOIN graph.nodes u ON u.org_id = $1 AND u.coll = 'users' AND u.id = r.deleted_by
            ORDER BY page.o"""
ORG_APPS = "/* pipeshub:kg.trash_org_apps */ SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = 'apps' AND id <> $2"
# A count asked with no cap reads every batch of the connector: as the access check's reads (``read.PROFILE``), a slow statement hands the read to the walk at once.
LIMITS = "/* pipeshub:kg.trash_limits */ SELECT set_config('statement_timeout', '2s', true)"


async def page(tx: Tx, org: str, connector_id: str, org_id: str, *, files_only: bool, skip: int, limit: int, after: tuple[int, str] | None,
               count_cap: int | None) -> tuple[list[dict[str, Any]], int]:
    """``list_trashed_records``'s rows and total, as the walk's statements return them. Raises Fallback."""
    await tx.fetchval(LIMITS)
    app_sk = await _served(tx, org, connector_id)
    if files_only:
        # The single-file rule counts a batch's records across the organisation (the walk's ``cnt``): kg holds them all only for connectors it serves, so a connector it does
        # not (an older copy, a change waiting) could hide a record of the batch and make a batch of two look like one.
        for r in await tx.fetch(ORG_APPS, org, connector_id):
            if not await kg_read.serves(tx, org, r["id"]):
                raise Fallback("org")
    ts, batch = after or (None, None)
    try:
        found = await tx.fetch(PAGE, org, app_sk, connector_id, org_id, files_only, ts, batch, skip + limit, TRASH_LIST_OTHER_ROOT_NAMES)
        counted = await tx.fetchrow(COUNT, org, app_sk, connector_id, org_id, files_only, None, None, None if count_cap is None else count_cap + 1)
        if counted["odd"] or any(r["odd"] for r in found):
            raise Fallback("odd")
        shown = [{"o": r["n"], **{c: r[c] for c in ("batch", "deleted_at", "first_id", "root_count", "batch_size", "other_names")}} for r in found[skip:]]
        rows = await tx.fetch(ROWS, org, json.dumps(shown)) if shown else []
    except DbTimeout:
        raise Fallback("slow") from None                # past LIMITS (or a lock wait): the walk's read, an expected outcome and no error
    return [dict(r) for r in rows], counted["total"]
