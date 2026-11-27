"""A connector's trash listing from the typed graph: ``list_trashed_records`` pages delete batches by ``kg.records`` (``deleted_ms``, ``delete_batch``) instead of reading the
organisation's whole trash.

The batches of the connector, newest first, are one index seek each from the cursor on (``records_trash``, read backwards), and each batch's records are read by
``records_trash_batch``. Which of a batch's records are its roots (connector, organisation, a hierarchy parent record in the same batch, typed as a file for a single-file listing) is
read from their kg rows and the kg rows of their parents (kg.model keeps the file a record is typed as, ``file_kind``), the walk's predicates on those columns; only a page's rows are
read from the graph, shaped as the walk shapes them (``ROWS``), so an answer differs from the walk's only if kg's columns do. The walk answers (``hierarchy.answer``) when the
connector is not served, and when a batch a page passes through is one kg cannot place as the walk does: a record of it in another connector or with no delete time, a batch
whose latest delete time is not that of a root (the walk sorts a batch by its roots' latest time; a batch deleted at several times is read once, at its latest), or, for a
single-file listing, one whose file kg cannot hold (``kb_odd``). The count reads each batch of the connector once (``records_trash_app_batch``) and asks only whether it has a
root, so its cost is a few seeks a batch up to the cap, whatever the batches hold.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.core.db.errors import DbTimeout
from app.services.graph_db.common.utils import TRASH_LIST_OTHER_ROOT_NAMES
from app.services.graph_db.postgres.kg import read as kg_read
from app.services.graph_db.postgres.kg.hierarchy import (
    Fallback,
    _links_up,
    _node,
    _served,
    _sk,
)
from app.services.graph_db.postgres.kg.model import FILE_FILE, LINK_HIERARCHY

if TYPE_CHECKING:
    from app.core.db import Tx

_TRASHED = "k.org_id = $1 AND k.app_sk = $2 AND k.coll = 3 AND k.deleted AND k.deleted_ms IS NOT NULL AND k.delete_batch IS NOT NULL"     # records_trash's predicate
# The same rows with the connector as a range: ordered by (app_sk, delete_batch), only records_trash_app_batch has that order (with ``app_sk = $2`` the planner drops
# app_sk from the order, and on fresh statistics read records_trash_batch, every connector's batches, in batch order).
_TRASHED_BY_BATCH = _TRASHED.replace("k.app_sk = $2", "k.app_sk >= $2 AND k.app_sk <= $2")
_NEXT = f"SELECT k.deleted_ms, k.delete_batch FROM kg.records k WHERE {_TRASHED} AND {{after}} ORDER BY k.deleted_ms DESC, k.delete_batch DESC LIMIT 1"
# A hierarchy parent record of ``m`` in the trash in the same batch (its primary parent and its hierarchy links), each read by its key.
_PARENT_IN_BATCH = f"""(SELECT p.sk, p.parent_sk, p.delete_batch, p.app_sk, p.alien, p.deleted_ms FROM (SELECT m.parent_sk AS sk UNION ALL {_links_up("m.sk", LINK_HIERARCHY)}) q CROSS JOIN LATERAL {_sk("q.sk")} p
        WHERE p.kind = 3 AND p.deleted AND p.delete_batch = m.delete_batch LIMIT 1)"""
# A record of the batch is a root as the walk decides (``TrashMixin.list_trashed_records``), read from its kg row: of the connector ($2) and organisation, deleted at a time,
# with no hierarchy parent record in the trash in the same batch, and (single-file listing, $3) typed as a file. Every read is a seek of a key.
_ROOT = f"""m.app_sk = $2 AND NOT m.alien AND m.deleted_ms IS NOT NULL AND NOT EXISTS {_PARENT_IN_BATCH}
      AND (NOT $3 OR m.file_kind = {FILE_FILE})"""
# A batch's records, of every connector (the batch at ``x``).
_BATCH_ROWS = """(SELECT * FROM kg.records m WHERE m.org_id = $1 AND m.coll = 3 AND m.deleted AND m.delete_batch IS NOT NULL AND m.delete_batch >= {x}
          AND m.delete_batch <= {x} ORDER BY m.delete_batch OFFSET 0)"""
# Two of a batch's records: whether it has more than one.
_BATCH_TWO = """(SELECT * FROM kg.records m WHERE m.org_id = $1 AND m.coll = 3 AND m.deleted AND m.delete_batch IS NOT NULL AND m.delete_batch >= {x}
          AND m.delete_batch <= {x} ORDER BY m.delete_batch LIMIT 2 OFFSET 0)"""
# How far the count climbs a batch's hierarchy to its top; a deeper (or cyclic) batch is read whole.
_UP_STEPS = 64

# The connector's batches newest first after the cursor ($4, $5), one seek each, with each batch's roots (by id), how many of its records are the connector's, and ``odd``:
# a record of it kg cannot place as the walk does (module docstring). A single-file listing skips a batch of more than one record by reading two. A batch with no root is no item. A batch
# deleted at several times is met once per time: only the visit at its latest time is read (one seek of ``records_trash_app_batch`` finds a later one).
# Each record's root test runs once (``rt``): as the FILTER of the ordered aggregate it ran twice per record.
_BATCHES = f"""RECURSIVE b(ms, batch, n) AS (
    (SELECT x.deleted_ms, x.delete_batch, 1 FROM ({_NEXT.format(after="($4::bigint IS NULL OR (k.deleted_ms, k.delete_batch) < ($4::bigint, $5::text))")}) x)
    UNION ALL SELECT x.deleted_ms, x.delete_batch, b.n + 1 FROM b CROSS JOIN LATERAL ({_NEXT.format(after="(k.deleted_ms, k.delete_batch) < (b.ms, b.batch)")}) x),
  s AS (SELECT b.ms, b.batch, b.n, a.odd OR (NOT ($3 AND a.cnt > 1) AND coalesce(a.root_ms <> b.ms, false)) AS odd, a.batch_size,
      CASE WHEN $3 AND a.cnt > 1 THEN '{{}}'::text[] ELSE coalesce(a.roots, '{{}}'::text[]) END AS roots
    FROM b CROSS JOIN LATERAL (
      SELECT count(*) AS cnt, count(*) FILTER (WHERE m.app_sk = $2) AS batch_size, array_agg(m.id ORDER BY m.id) FILTER (WHERE rt.root) AS roots,
        max(m.deleted_ms) FILTER (WHERE rt.root) AS root_ms,
        coalesce(bool_or(m.app_sk IS DISTINCT FROM $2 OR m.deleted_ms IS NULL OR ($3 AND m.kb_odd)), false) AS odd
      FROM {_BATCH_ROWS.format(x="b.batch")} m
        CROSS JOIN LATERAL (SELECT {_ROOT} AS root OFFSET 0) rt) a
    WHERE (SELECT k.deleted_ms FROM kg.records k WHERE {_TRASHED_BY_BATCH} AND (k.delete_batch, k.deleted_ms) > (b.batch, b.ms) AND k.delete_batch <= b.batch
        ORDER BY k.app_sk, k.delete_batch, k.deleted_ms LIMIT 1) IS NULL
      AND NOT ($3 AND (SELECT count(*) FROM {_BATCH_TWO.format(x="b.batch")} m) > 1))"""
# shortcut: a single-file page reads every batch until it has a page of single-file ones (a few pages each, once per delete time of a batch deleted at several times): a
# trash of large mixed-time batches can pass LIMITS and take the walk; index single-record batches if that shows up beyond the gate world.
PAGE = f"""/* pipeshub:kg.trash_page */ WITH {_BATCHES}
SELECT s.n, s.odd, s.batch, s.ms AS deleted_at, s.roots[1] AS first_id, cardinality(s.roots)::bigint AS root_count, s.batch_size,
    ARRAY(SELECT x.doc->>'recordName' FROM unnest(s.roots[2:1 + $7]) WITH ORDINALITY u(id, o) CROSS JOIN LATERAL {_node("'records'", "u.id")} x ORDER BY u.o) AS other_names
  FROM s WHERE cardinality(s.roots) > 0 OR s.odd LIMIT $6"""
# The connector's batches, each once and in batch order (``records_trash_app_batch``), up to the cap ($4): one seek each, and whether a batch has a root. A batch's top, climbed
# to from the record its seek met through parents in the batch, is a root in a batch the product wrote; only a batch whose top is none (of another connector or organisation, or a
# hierarchy deeper than _UP_STEPS or cyclic) is read whole. A single-file listing reads at most two records of a batch. Times play no part: a count is of batches.
_HAS_ROOT = f"""SELECT CASE WHEN $3 THEN coalesce(f.root, false)
      WHEN (WITH RECURSIVE up(sk, parent_sk, delete_batch, app_sk, alien, deleted_ms, d) AS (
          SELECT c.sk, c.parent_sk, c.batch, c.app_sk, c.alien, c.deleted_ms, 0
          UNION ALL SELECT p.*, m.d + 1 FROM up m CROSS JOIN LATERAL {_PARENT_IN_BATCH} p WHERE m.d < {_UP_STEPS})
        SELECT t.d < {_UP_STEPS} AND t.app_sk = $2 AND NOT t.alien AND t.deleted_ms IS NOT NULL FROM up t ORDER BY t.d DESC LIMIT 1) THEN true
      ELSE EXISTS (SELECT 1 FROM {_BATCH_ROWS.format(x="c.batch")} m WHERE {_ROOT}) END AS root, coalesce(f.odd, false) AS odd
    FROM (SELECT count(*) = 1 AND bool_or(rt.root) AS root, count(*) = 1 AND bool_or(m.kb_odd) AS odd FROM {_BATCH_TWO.format(x="c.batch")} m
      CROSS JOIN LATERAL (SELECT {_ROOT} AS root OFFSET 0) rt WHERE $3) f OFFSET 0"""
_ROW = "k.delete_batch, k.sk, k.parent_sk, k.app_sk, k.alien, k.deleted_ms"
COUNT = f"""/* pipeshub:kg.trash_count */ WITH RECURSIVE c(batch, sk, parent_sk, app_sk, alien, deleted_ms) AS (
    (SELECT {_ROW} FROM kg.records k WHERE {_TRASHED_BY_BATCH} ORDER BY k.app_sk, k.delete_batch LIMIT 1)
    UNION ALL SELECT x.* FROM c CROSS JOIN LATERAL (SELECT {_ROW} FROM kg.records k WHERE {_TRASHED_BY_BATCH} AND k.delete_batch > c.batch ORDER BY k.app_sk, k.delete_batch LIMIT 1) x)
SELECT count(*) FILTER (WHERE h.root) AS total, coalesce(bool_or(h.odd), false) AS odd FROM (SELECT h.root, h.odd FROM c CROSS JOIN LATERAL ({_HAS_ROOT}) h WHERE h.root OR h.odd LIMIT $4) h"""

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
    if org_id != org:
        raise Fallback("org")                          # the walk's roots name ``org_id``; kg's rows are of the organisation they were copied for (``alien``)
    if files_only:
        # The single-file rule counts a batch's records across the organisation (the walk's ``cnt``): kg holds them all only for connectors it serves, so a connector it does
        # not (an older copy, a change waiting) could hide a record of the batch and make a batch of two look like one.
        for r in await tx.fetch(ORG_APPS, org, connector_id):
            if not await kg_read.serves(tx, org, r["id"]):
                raise Fallback("org")
    ts, batch = after or (None, None)
    try:
        found = await tx.fetch(PAGE, org, app_sk, files_only, ts, batch, skip + limit, TRASH_LIST_OTHER_ROOT_NAMES)
        counted = await tx.fetchrow(COUNT, org, app_sk, files_only, None if count_cap is None else count_cap + 1)
        if counted["odd"] or any(r["odd"] for r in found):
            raise Fallback("odd")
        shown = [{"o": r["n"], **{c: r[c] for c in ("batch", "deleted_at", "first_id", "root_count", "batch_size", "other_names")}} for r in found[skip:]]
        rows = await tx.fetch(ROWS, org, json.dumps(shown)) if shown else []
    except DbTimeout:
        raise Fallback("slow") from None                # past LIMITS (or a lock wait): the walk's read, an expected outcome and no error
    return [dict(r) for r in rows], counted["total"]
