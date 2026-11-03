"""Trash, the knowledge-base push claims and reindex.

A record in the trash has ``isDeleted`` true and the delete batch it went with (``deleteBatchId``); a restore, a listing and the purge work on a batch.
Every method resolves the ambient org before anything else (and before any ``try``); every statement is a literal with bound parameters.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
    ProgressStatus,
)
from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.core.org_context import current_org_id
from app.modules.code_graph.edge_build_trigger import is_code_record
from app.services.graph_db.common.utils import (
    TRASH_LIST_OTHER_ROOT_NAMES,
    TRASH_STATE_FIELDS,
    TRASHED_EXTERNAL_ID_PREFIX,
    cleans_taxonomy_points,
    deleted_record_taxonomy_refs,
    empty_soft_delete_result,
    record_taxonomy_refs,
    restore_items,
    soft_delete_result,
    trash_purge_row,
)
from app.services.graph_db.entity_index_queries import APP_STATUS_DELETING
from app.services.graph_db.postgres.base import PgBase, _doc, _j
from app.services.graph_db.postgres.hierarchy import _EDGE_COLLS, _remove_records
from app.services.graph_db.postgres.records import _rec, _uniq
from app.services.graph_db.push_claim import (
    KB_DELETING_STATUS,
    PushClaim,
    PushExpect,
    PushSet,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.utils.user_messages import action_failed

if TYPE_CHECKING:
    from fastapi import Request

    from app.core.db import Tx

_ORG_FIELD = "orgId"  # the org property of a graph document
MAX_REINDEX_DEPTH = 100  # the Neo4j provider's cap on an unlimited reindex walk


async def _sweep_edges(tx: Tx, org: str, edge_colls: list[str], node_ids: list[str]) -> int:
    """Delete the edges of ``edge_colls`` touching ``'<coll>/<key>'`` node ids, in either direction (``nodeRelations`` is the hierarchy and the links)."""
    by_coll: dict[str, list[str]] = {}
    for nid in node_ids:
        if "/" in nid:
            c, k = nid.split("/", 1)
            by_coll.setdefault(c, []).append(k)
    if not by_coll or not edge_colls:
        return 0
    colls = list(dict.fromkeys(c for ec in edge_colls for c in (["nodeRelations", "recordLinks"] if ec == "nodeRelations" else [ec])))
    n = 0
    for nc, keys in by_coll.items():
        n += await tx.fetchval("""/* pipeshub:graph.sweep_edges_from */ WITH d AS (DELETE FROM graph.edges WHERE org_id = $1 AND coll = ANY($2::text[])
            AND from_coll = $3 AND from_id = ANY($4::text[]) RETURNING 1) SELECT count(*) FROM d""", org, colls, nc, keys)
        n += await tx.fetchval("""/* pipeshub:graph.sweep_edges_to */ WITH d AS (DELETE FROM graph.edges WHERE org_id = $1 AND coll = ANY($2::text[])
            AND to_coll = $3 AND to_id = ANY($4::text[]) RETURNING 1) SELECT count(*) FROM d""", org, colls, nc, keys)
    return n


class TrashMixin(PgBase):
    async def _qrows(self, sql: str, *args: Any, transaction: str | None = None, write: bool = False) -> list:  # noqa: ANN401
        return await self._q(lambda tx: tx.fetch(sql, *args), transaction, write=write)

    async def soft_delete_records(self, record_ids: list[str], connector_id: str, *, delete_source: str, batch_id: str, deleted_by_user_id: str | None = None,
                                  follow: tuple[str, ...] = ("PARENT_CHILD", "ATTACHMENT"), transaction: str | None = None, within_folder_id: str | None = None,
                                  include_trashed_roots: bool = False) -> dict:
        """Move records and the subtrees under them (along ``follow``, within the connector) to the trash: one statement marks them all."""
        if not record_ids:
            return empty_soft_delete_result(batch_id)
        org = current_org_id()

        async def go(tx: Tx) -> dict:
            roots, docs, _ = await self._walk_subtree(tx, record_ids, connector_id, list(follow), within_folder_id, include_trashed=include_trashed_roots, live_only=True)
            marked = await tx.fetch("""/* pipeshub:graph.soft_delete_records */ UPDATE graph.nodes v SET doc = (v.doc - 'deletedByUserId') || jsonb_strip_nulls(jsonb_build_object('isDeleted', true,
                    'deletedAtTimestamp', $3::bigint, 'deleteSource', $4::text, 'deleteBatchId', $5::text, 'deletedByUserId', $6::text))
                WHERE v.org_id = $1 AND v.coll = 'records' AND v.id = ANY($2::text[]) AND coalesce((v.doc->>'isDeleted')::boolean, false) = false
                RETURNING v.id, v.doc->>'recordName' AS name, v.doc->>'virtualRecordId' AS vrid, v.doc->>'orgId' AS oid""",
                                    org, list(docs), get_epoch_timestamp_in_ms(), delete_source, batch_id, deleted_by_user_id)
            return {"root_keys": roots, "marked": [{"id": m["id"], "name": m["name"], "vrid": m["vrid"], "orgId": m["oid"]} for m in marked]}

        try:
            row = await self._q(go, transaction, write=True)
        except Exception as e:
            self.logger.error(f"❌ Failed to move records to the trash: {e}")
            raise
        return soft_delete_result(record_ids, list(row["root_keys"]), row["marked"], batch_id)

    async def restore_records(self, restores: list[dict[str, Any]], batch_id: str | None, transaction: str | None = None, *, connector_id: str | None = None,
                              require_live_parent: bool = False) -> list[str]:
        """Restore trashed records of one delete batch: all or nothing. Any item missing from the batch, an external id taken by another
        record, or (``require_live_parent``) a parent still in the trash refuses the whole restore. Holders of a reclaimed id give it up."""
        if not restores:
            return []
        items, reclaims = restore_items(restores, connector_id)
        items = list({i["id"]: i for i in items}.values())      # an item named twice is restored once (Neo4j's UNWIND finds it twice and restores it)
        ids = [i["id"] for i in items]
        org = current_org_id()

        async def go(tx: Tx) -> list[str]:
            found = await tx.fetch("""/* pipeshub:graph.restore_records_lock */ SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = 'records' AND id = ANY($2::text[])
                AND coalesce((doc->>'isDeleted')::boolean, false) AND doc->>'deleteBatchId' IS NOT DISTINCT FROM $3::text ORDER BY id FOR UPDATE""", org, ids, batch_id)
            if len(found) != len(ids):
                return []
            if reclaims and await tx.fetchval("""/* pipeshub:graph.restore_records_taken */ SELECT 1 FROM jsonb_to_recordset($2::jsonb) c(id text, ext text)
                JOIN graph.nodes t ON t.org_id = $1 AND t.coll = 'records' AND t.doc->>'externalRecordId' = c.ext AND t.doc->>'connectorId' = $3 AND t.id <> c.id
                WHERE NOT coalesce((t.doc->>'isDeleted')::boolean, false) OR t.id = ANY($4::text[]) LIMIT 1""", org, _j(reclaims), connector_id, ids):
                return []
            if require_live_parent and await tx.fetchval("""/* pipeshub:graph.restore_records_parent */ SELECT 1 FROM graph.edges e JOIN graph.nodes p
                ON p.org_id = e.org_id AND p.coll = 'records' AND p.id = e.from_id WHERE e.org_id = $1 AND e.coll = 'nodeRelations' AND e.from_coll = 'records'
                AND e.to_id = ANY($2::text[]) AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT') AND coalesce((p.doc->>'isDeleted')::boolean, false)
                AND NOT p.id = ANY($2::text[]) LIMIT 1""", org, ids):
                return []
            if reclaims:
                await tx.execute("""/* pipeshub:graph.restore_records_release */ UPDATE graph.nodes h SET doc = h.doc || jsonb_build_object('trashedExternalRecordId',
                    h.doc->>'externalRecordId', 'externalRecordId', $4::text || h.id) FROM jsonb_to_recordset($2::jsonb) c(id text, ext text)
                    WHERE h.org_id = $1 AND h.coll = 'records' AND h.doc->>'externalRecordId' = c.ext AND h.doc->>'connectorId' = $3
                      AND coalesce((h.doc->>'isDeleted')::boolean, false) AND NOT h.id = ANY($5::text[])""", org, _j(reclaims), connector_id, TRASHED_EXTERNAL_ID_PREFIX, ids)
            rows = await tx.fetch("""/* pipeshub:graph.restore_records */ UPDATE graph.nodes n SET doc = jsonb_strip_nulls(((n.doc - $3::text[]) ||
                  jsonb_build_object('isDeleted', false, 'updatedAtTimestamp', $4::bigint)) || x.set)
                FROM jsonb_to_recordset($2::jsonb) x(id text, set jsonb) WHERE n.org_id = $1 AND n.coll = 'records' AND n.id = x.id RETURNING n.id""",
                                  org, _j(items), list(TRASH_STATE_FIELDS), get_epoch_timestamp_in_ms())
            return [r["id"] for r in rows]
        return await self._q(go, transaction, write=True)

    async def get_records_in_delete_batch(self, batch_id: str, org_id: str, transaction: str | None = None) -> list[dict[str, Any]]:
        if not batch_id or not org_id:
            return []
        rows = await self._qrows("""/* pipeshub:graph.get_records_in_delete_batch */ SELECT r.doc AS rec, p.id AS pid, p.doc AS pdoc, pe.rt, ty.doc->>'isFile' AS is_file, ty.doc->>'mimeType' AS mime
            FROM graph.nodes r
            LEFT JOIN LATERAL (SELECT e.from_id, e.doc->>'relationshipType' AS rt FROM graph.edges e WHERE e.org_id = r.org_id AND e.coll = 'nodeRelations' AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT') AND e.from_coll = 'records' AND e.to_coll = 'records'
              AND e.to_id = r.id ORDER BY e.from_id LIMIT 1) pe ON true
            LEFT JOIN graph.nodes p ON p.org_id = r.org_id AND p.coll IN ('records') AND p.id = pe.from_id
            LEFT JOIN LATERAL (SELECT t.doc, t.coll AS tcoll FROM graph.edges e JOIN graph.nodes t ON t.org_id = e.org_id AND t.coll = e.to_coll AND t.id = e.to_id
    WHERE e.org_id = r.org_id AND e.coll = 'isOfType' AND e.from_id = r.id LIMIT 1) ty ON true
            WHERE r.org_id = $1 AND r.coll = 'records' AND r.doc->>'deleteBatchId' = $2 AND coalesce((r.doc->>'isDeleted')::boolean, false) AND r.doc->>'orgId' = $3""",
                                current_org_id(), batch_id, org_id, transaction=transaction)
        items = []
        for r in rows:
            p = _doc(r["pdoc"]) if r["pdoc"] else None
            items.append({"record": _rec(r["rec"]), "parentId": p.get("id") if p else None, "parentRelation": r["rt"] if p else None,
                          "parentIsDeleted": (p.get("isDeleted") is True) if p else None, "parentBatchId": p.get("deleteBatchId") if p else None,
                          "parentName": p.get("recordName") if p else None,
                          "isFile": None if r["is_file"] is None else r["is_file"] == "true", "fileMimeType": r["mime"]})
        return items

    async def list_trashed_records(self, connector_id: str, org_id: str, *, skip: int = 0, limit: int = 25, single_file_batches_only: bool = False,
                                   transaction: str | None = None) -> dict[str, Any]:
        """One row per delete batch of a connector's trash (a batch restores whole), newest first."""
        if not connector_id or not org_id or limit <= 0:
            return {"items": [], "total": 0}
        org = current_org_id()
        rows = await self._qrows("""/* pipeshub:graph.list_trashed_records */ WITH tr AS MATERIALIZED (SELECT n.id, n.doc FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'records' AND coalesce((n.doc->>'isDeleted')::boolean, false)),
            cnt AS (SELECT doc->>'deleteBatchId' AS batch, count(*) AS c FROM tr GROUP BY 1),
            roots AS (SELECT r.id, r.doc->>'deleteBatchId' AS batch, (r.doc->>'deletedAtTimestamp')::bigint AS ts, r.doc->>'recordName' AS name FROM tr r
              WHERE r.doc->>'connectorId' = $2 AND r.doc->>'deletedAtTimestamp' IS NOT NULL AND r.doc->>'orgId' = $3 AND r.doc->>'deleteBatchId' IS NOT NULL
                AND CASE WHEN $4 AND (SELECT c FROM cnt WHERE cnt.batch = r.doc->>'deleteBatchId') > 1 THEN false
                         WHEN EXISTS (SELECT 1 FROM graph.edges e JOIN tr p ON p.id = e.from_id WHERE e.org_id = $1 AND e.coll = 'nodeRelations' AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT') AND e.from_coll = 'records'
                                      AND e.to_id = r.id AND p.doc->>'deleteBatchId' = r.doc->>'deleteBatchId') THEN false
                         ELSE NOT $4 OR EXISTS (SELECT 1 FROM graph.edges ie JOIN graph.nodes t ON t.org_id = ie.org_id AND t.coll = ie.to_coll AND t.id = ie.to_id
                                                WHERE ie.org_id = $1 AND ie.coll = 'isOfType' AND ie.from_id = r.id AND t.doc->>'isFile' = 'true') END),
            page AS (SELECT batch, max(ts) AS deleted_at, min(id) AS first_id, count(*) AS root_count FROM roots GROUP BY batch
                     ORDER BY deleted_at DESC, batch DESC OFFSET $5 LIMIT $6)
            SELECT r.doc AS rec, p.id AS parent_id, p.doc->>'recordName' AS parent_name, p.doc->>'isDeleted' AS parent_deleted,
                   ty.doc->>'isFile' AS is_file, ty.doc->>'mimeType' AS file_mime, ty.doc->'sizeInBytes' AS size, page.root_count,
                   CASE WHEN page.root_count < 2 THEN '{}'::text[] ELSE ARRAY(SELECT x.name FROM roots x WHERE x.batch = page.batch AND x.id <> r.id
                        ORDER BY x.id LIMIT $7) END AS other_names,
                   (SELECT count(*) FROM tr x WHERE x.doc->>'deleteBatchId' = page.batch AND x.doc->>'connectorId' = $2) AS batch_size,
                   CASE WHEN u.id IS NULL THEN NULL WHEN coalesce(u.doc->>'fullName', '') <> '' THEN u.doc->>'fullName'
                        ELSE trim(coalesce(u.doc->>'firstName', '') || ' ' || coalesce(u.doc->>'lastName', '')) END AS user_name, u.doc->>'email' AS user_email
            FROM page JOIN graph.nodes r ON r.org_id = $1 AND r.coll = 'records' AND r.id = page.first_id
            LEFT JOIN LATERAL (SELECT pn.id, pn.doc FROM graph.edges e JOIN graph.nodes pn ON pn.org_id = e.org_id AND pn.coll = e.from_coll AND pn.id = e.from_id
              WHERE e.org_id = $1 AND e.coll = 'nodeRelations' AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT') AND e.to_id = r.id ORDER BY e.from_id LIMIT 1) p ON true
            LEFT JOIN LATERAL (SELECT t.doc, t.coll AS tcoll FROM graph.edges e JOIN graph.nodes t ON t.org_id = e.org_id AND t.coll = e.to_coll AND t.id = e.to_id
    WHERE e.org_id = r.org_id AND e.coll = 'isOfType' AND e.from_id = r.id LIMIT 1) ty ON true
            LEFT JOIN graph.nodes u ON u.org_id = $1 AND u.coll = 'users' AND u.id = r.doc->>'deletedByUserId'
            ORDER BY page.deleted_at DESC, page.batch DESC""", org, connector_id, org_id, single_file_batches_only, max(skip, 0), limit,
                                TRASH_LIST_OTHER_ROOT_NAMES, transaction=transaction)
        total = await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.list_trashed_records_total */ WITH tr AS MATERIALIZED (SELECT n.id, n.doc FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'records' AND coalesce((n.doc->>'isDeleted')::boolean, false)),
            cnt AS (SELECT doc->>'deleteBatchId' AS batch, count(*) AS c FROM tr GROUP BY 1),
            roots AS (SELECT r.id, r.doc->>'deleteBatchId' AS batch, (r.doc->>'deletedAtTimestamp')::bigint AS ts, r.doc->>'recordName' AS name FROM tr r
              WHERE r.doc->>'connectorId' = $2 AND r.doc->>'deletedAtTimestamp' IS NOT NULL AND r.doc->>'orgId' = $3 AND r.doc->>'deleteBatchId' IS NOT NULL
                AND CASE WHEN $4 AND (SELECT c FROM cnt WHERE cnt.batch = r.doc->>'deleteBatchId') > 1 THEN false
                         WHEN EXISTS (SELECT 1 FROM graph.edges e JOIN tr p ON p.id = e.from_id WHERE e.org_id = $1 AND e.coll = 'nodeRelations' AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT') AND e.from_coll = 'records'
                                      AND e.to_id = r.id AND p.doc->>'deleteBatchId' = r.doc->>'deleteBatchId') THEN false
                         ELSE NOT $4 OR EXISTS (SELECT 1 FROM graph.edges ie JOIN graph.nodes t ON t.org_id = ie.org_id AND t.coll = ie.to_coll AND t.id = ie.to_id
                                                WHERE ie.org_id = $1 AND ie.coll = 'isOfType' AND ie.from_id = r.id AND t.doc->>'isFile' = 'true') END) SELECT count(DISTINCT batch) FROM roots""",
                                                      org, connector_id, org_id, single_file_batches_only), transaction)
        items = [{"record": _rec(r["rec"]), "parentId": r["parent_id"], "parentName": r["parent_name"],
                  "parentIsDeleted": (r["parent_deleted"] == "true") if r["parent_id"] else None,
                  "isFile": None if r["is_file"] is None else r["is_file"] == "true", "fileMimeType": r["file_mime"],
                  "sizeInBytes": json.loads(r["size"]) if r["size"] else None, "rootCount": r["root_count"] or 1,
                  "otherRootNames": [n for n in r["other_names"] or [] if n is not None], "batchSize": r["batch_size"] or 0,
                  "deletedByName": r["user_name"] or None, "deletedByEmail": r["user_email"]} for r in rows]
        return {"items": items, "total": total or 0}

    async def _trash_purge_row(self, rec: dict, type_doc: dict | None, taxonomy_refs: list[dict[str, str]] | None = None) -> dict:
        payload = await self._create_deleted_record_event_payload(rec, type_doc)
        if payload and taxonomy_refs:
            payload["taxonomyRefs"] = taxonomy_refs
        return trash_purge_row(rec["id"], rec, type_doc, payload)

    async def get_purgeable_trashed_records(self, org_id: str, deleted_before: int, *, after: tuple[int, str] | None = None, limit: int = 500,
                                            max_attempts: int = 5, transaction: str | None = None) -> dict[str, Any]:
        """A page of the org's trash due for the purge, in (deletedAtTimestamp, id) order after the cursor ``after``."""
        org = current_org_id()
        if limit <= 0:
            return {"records": [], "held": 0, "next": None}
        after_ts, after_key = after if after else (None, None)
        lower = after_ts if after_ts is not None else -1
        rows = await self._qrows("""/* pipeshub:graph.get_purgeable_trashed_records */ WITH c AS (
              SELECT r.id, r.doc, (r.doc->>'deletedAtTimestamp')::bigint AS ts FROM graph.nodes r WHERE r.org_id = $1 AND r.coll = 'records' AND r.doc->>'orgId' = $2 AND coalesce((r.doc->>'isDeleted')::boolean, false) AND (r.doc->>'isDeleted') = 'true'
                AND coalesce((r.doc->>'purgeAttempts')::int, 0) < $3 AND r.doc->>'deletedAtTimestamp' IS NOT NULL
                AND (((r.doc->>'deletedAtTimestamp')::bigint = $4 AND r.id > $5) OR ((r.doc->>'deletedAtTimestamp')::bigint > $6 AND (r.doc->>'deletedAtTimestamp')::bigint <= $7))
              ORDER BY (r.doc->>'deletedAtTimestamp')::bigint, r.id LIMIT $8)
            SELECT c.doc AS rec, ty.doc AS typ, coalesce(app.doc->>'status', '') = $9 AS skip,
                   EXISTS (SELECT 1 FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'nodeRelations' AND e.from_coll = 'records' AND e.from_id = c.id
                           AND e.to_coll = 'records' AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT')) AS held
            FROM c LEFT JOIN graph.nodes app ON app.org_id = $1 AND app.coll = 'apps' AND app.id = c.doc->>'connectorId'
            LEFT JOIN LATERAL (SELECT t.doc FROM graph.edges e JOIN graph.nodes t ON t.org_id = e.org_id AND t.coll = e.to_coll AND t.id = e.to_id
              WHERE e.org_id = $1 AND e.coll = 'isOfType' AND e.from_id = c.id ORDER BY e.to_id LIMIT 1) ty ON true
            ORDER BY c.ts, c.id""", org, org_id, max_attempts, after_ts if after_ts is not None else lower - 1, after_key or "", lower, deleted_before,
                                limit, APP_STATUS_DELETING, transaction=transaction)
        listed = [r for r in rows if not r["skip"] and not r["held"]]
        refs = await deleted_record_taxonomy_refs(self, [_doc(r["rec"]) for r in listed], self.logger)
        records = [await self._trash_purge_row(_doc(r["rec"]), _doc(r["typ"]) if r["typ"] else None, refs.get(_doc(r["rec"])["id"])) for r in listed]
        last = _doc(rows[-1]["rec"]) if len(rows) >= limit else None
        return {"records": records, "held": sum(1 for r in rows if r["held"] and not r["skip"]),
                "next": (last["deletedAtTimestamp"], last["id"]) if last else None}

    async def is_trash_walk_index_ready(self) -> bool:
        return True  # the walk needs no index that is built apart from the schema

    async def get_trash_purge_stats(self, org_id: str, max_attempts: int = 5, transaction: str | None = None) -> dict[str, Any]:
        rows = await self._qrows("""/* pipeshub:graph.get_trash_purge_stats */ SELECT count(*) AS trashed,
              count(*) FILTER (WHERE coalesce((n.doc->>'purgeAttempts')::int, 0) >= $3) AS stuck,
              min((n.doc->>'deletedAtTimestamp')::bigint) FILTER (WHERE coalesce((n.doc->>'purgeAttempts')::int, 0) < $3) AS oldest
            FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'records' AND n.doc->>'orgId' = $2 AND coalesce((n.doc->>'isDeleted')::boolean, false) AND n.doc->>'deletedAtTimestamp' IS NOT NULL
              AND (n.doc->>'deletedAtTimestamp')::bigint >= 0""", current_org_id(), org_id, max_attempts, transaction=transaction)
        row = rows[0] if rows else {}
        return {"trashed": int(row.get("trashed") or 0), "stuck": int(row.get("stuck") or 0), "oldestDeletedAt": row.get("oldest")}

    async def record_purge_failure(self, record_ids: list[str], org_id: str, error: str, transaction: str | None = None) -> int:
        if not record_ids:
            return 0
        rows = await self._qrows("""/* pipeshub:graph.record_purge_failure */ UPDATE graph.nodes n SET doc = n.doc || jsonb_build_object(
              'purgeAttempts', coalesce((n.doc->>'purgeAttempts')::int, 0) + 1, 'purgeLastError', $4::text)
            WHERE n.org_id = $1 AND n.coll = 'records' AND n.id = ANY($2::text[]) AND n.doc->>'orgId' = $3 AND coalesce((n.doc->>'isDeleted')::boolean, false) RETURNING n.id""",
                                current_org_id(), _uniq(record_ids), org_id, error, transaction=transaction, write=True)
        return len(rows)

    async def purge_trashed_records(self, record_ids: list[str], org_id: str, deleted_before: int, *, max_attempts: int = 5,
                                    transaction: str | None = None) -> dict[str, Any]:
        """Delete the trashed records that are due; the rest are ``kept``. The rows are locked first, so a restore that commits meanwhile is seen
        and one still running is waited for; a record with any child, trashed or not, is kept."""
        if not record_ids:
            return {"purged": [], "kept": []}
        keys = _uniq(record_ids)
        org = current_org_id()
        refs = await record_taxonomy_refs(self, keys, self.logger)

        async def go(tx: Tx) -> list[dict]:
            await tx.fetch("""/* pipeshub:graph.purge_trashed_records_lock */ SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = 'records' AND id = ANY($2::text[])
                AND doc->>'orgId' = $3 ORDER BY id FOR UPDATE""", org, keys, org_id)
            rows = await tx.fetch("""/* pipeshub:graph.purge_trashed_records_due */ SELECT r.id, r.doc AS rec, ty.doc AS typ, (
                  coalesce((r.doc->>'isDeleted')::boolean, false) AND r.doc->>'deletedAtTimestamp' IS NOT NULL AND (r.doc->>'deletedAtTimestamp')::bigint <= $4 AND coalesce((r.doc->>'purgeAttempts')::int, 0) < $5
                  AND coalesce(app.doc->>'status', '') <> $6
                  AND NOT EXISTS (SELECT 1 FROM graph.edges e WHERE e.org_id = r.org_id AND e.coll = 'nodeRelations' AND e.from_coll = 'records' AND e.from_id = r.id
                                  AND e.to_coll = 'records' AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT'))) AS due
                FROM graph.nodes r LEFT JOIN graph.nodes app ON app.org_id = r.org_id AND app.coll = 'apps' AND app.id = r.doc->>'connectorId'
                LEFT JOIN LATERAL (SELECT t.doc FROM graph.edges e JOIN graph.nodes t ON t.org_id = e.org_id AND t.coll = e.to_coll AND t.id = e.to_id
                  WHERE e.org_id = r.org_id AND e.coll = 'isOfType' AND e.from_id = r.id LIMIT 1) ty ON true
                WHERE r.org_id = $1 AND r.coll = 'records' AND r.id = ANY($2::text[]) AND r.doc->>'orgId' = $3""",
                                  org, keys, org_id, deleted_before, max_attempts, APP_STATUS_DELETING)
            await _remove_records(tx, org, [r["id"] for r in rows if r["due"]])
            return rows
        rows = await self._q(go, transaction, write=True)
        purged = [await self._trash_purge_row(_doc(r["rec"]), _doc(r["typ"]) if r["typ"] else None,
                                              refs.get(r["id"]) if cleans_taxonomy_points(_doc(r["rec"])) else None) for r in rows if r["due"]]
        return {"purged": purged, "kept": [r["id"] for r in rows if not r["due"]]}

    async def purge_trash_kept_record_groups(self, org_id: str, *, limit: int = 100, transaction: str | None = None) -> list[str]:
        """Delete the record groups marked deleted at source that nothing belongs to any more (locked first, then looked at again)."""
        org = current_org_id()
        async def go(tx: Tx) -> list[str]:
            cand = [r["id"] for r in await tx.fetch("""/* pipeshub:graph.purge_kept_record_groups_lock */ SELECT g.id FROM graph.nodes g
                LEFT JOIN graph.nodes app ON app.org_id = g.org_id AND app.coll = 'apps' AND app.id = g.doc->>'connectorId'
                WHERE g.org_id = $1 AND g.coll = 'recordGroups' AND coalesce((g.doc->>'isDeletedAtSource')::boolean, false)
                  AND (g.doc->>'orgId' = $2 OR (coalesce(g.doc->>'orgId', '') = '' AND app.doc->>'orgId' = $2)) AND coalesce(app.doc->>'status', '') <> $3
                  AND NOT EXISTS (SELECT 1 FROM graph.edges m WHERE m.org_id = g.org_id AND m.coll IN ('belongsTo', 'inheritPermissions') AND m.to_coll = 'recordGroups'
                       AND m.to_id = g.id AND m.from_coll IN ('records', 'recordGroups'))
                   AND NOT EXISTS (SELECT 1 FROM graph.nodes r WHERE r.org_id = g.org_id AND r.coll = 'records' AND r.doc->>'connectorId' = g.doc->>'connectorId'
                       AND r.doc->>'recordGroupId' = g.id) ORDER BY g.id LIMIT $4 FOR UPDATE OF g""", org, org_id, APP_STATUS_DELETING, limit)]
            if not cand:
                return []
            due = [r["id"] for r in await tx.fetch("""/* pipeshub:graph.purge_kept_record_groups_due */ SELECT g.id FROM graph.nodes g
                WHERE g.org_id = $1 AND g.coll = 'recordGroups' AND g.id = ANY($2::text[]) AND coalesce((g.doc->>'isDeletedAtSource')::boolean, false) AND NOT EXISTS (SELECT 1 FROM graph.edges m WHERE m.org_id = g.org_id AND m.coll IN ('belongsTo', 'inheritPermissions') AND m.to_coll = 'recordGroups'
                       AND m.to_id = g.id AND m.from_coll IN ('records', 'recordGroups'))
                   AND NOT EXISTS (SELECT 1 FROM graph.nodes r WHERE r.org_id = g.org_id AND r.coll = 'records' AND r.doc->>'connectorId' = g.doc->>'connectorId'
                       AND r.doc->>'recordGroupId' = g.id)""", org, cand)]
            if due:
                await _sweep_edges(tx, org, _EDGE_COLLS, [f"recordGroups/{i}" for i in due])
                await tx.execute("/* pipeshub:graph.purge_kept_record_groups_delete */ DELETE FROM graph.nodes WHERE org_id = $1 AND coll = 'recordGroups' AND id = ANY($2::text[])", org, due)
            return due
        return await self._q(go, transaction, write=True)

    async def take_back_kept_record_group(self, group_id: str, transaction: str | None = None) -> bool:
        """Clear a group's deleted-at-source mark; True when it is still there (a mark is only purged under the same lock, so one still here keeps)."""
        rows = await self._qrows("""/* pipeshub:graph.take_back_kept_record_group */ UPDATE graph.nodes SET doc = (doc - 'deletedAtSourceTimestamp') || '{"isDeletedAtSource": false}'::jsonb
            WHERE org_id = $1 AND coll = 'recordGroups' AND id = $2 RETURNING id""", current_org_id(), group_id, transaction=transaction, write=True)
        return bool(rows)

    async def claim_push_record(self, *, kb_id: str, org_id: str, key: str, expected: PushExpect | None, new: PushSet,
                                transaction: str | None = None) -> PushClaim:
        """The conditional first write of a push. A create takes the knowledge base's row lock, so two creates of one key serialise and the second
        sees the first's record; an update changes the record only while it still holds ``expected``."""
        org = current_org_id()

        async def go(tx: Tx) -> PushClaim:
            if expected is None:      # a create takes the knowledge base's row lock, so two creates of one key serialise
                kb = await tx.fetchrow("""/* pipeshub:graph.claim_push_record_kb_lock */ SELECT kb.doc->>'orgId' AS org, kb.doc->>'status' AS status FROM graph.nodes kb
                    WHERE kb.org_id = $1 AND kb.coll = 'apps' AND kb.id = $2 AND kb.doc->>'type' = 'KB' FOR NO KEY UPDATE""", org, kb_id)
            else:
                kb = await tx.fetchrow("""/* pipeshub:graph.claim_push_record_kb */ SELECT kb.doc->>'orgId' AS org, kb.doc->>'status' AS status FROM graph.nodes kb
                    WHERE kb.org_id = $1 AND kb.coll = 'apps' AND kb.id = $2 AND kb.doc->>'type' = 'KB'""", org, kb_id)
            if not kb or kb["org"] != org_id:
                return PushClaim.KB_MISSING
            if kb["status"] == KB_DELETING_STATUS:
                return PushClaim.KB_DELETING
            if expected is None:   # a second statement: it reads what a creator we waited for committed
                taken = await tx.fetchval("/* pipeshub:graph.claim_push_record_free */ SELECT EXISTS (SELECT 1 FROM graph.nodes WHERE org_id = $1 AND coll = 'records' AND id = $2)", org, key)
                return PushClaim.KEY_TAKEN if taken else PushClaim.CLAIMED
            n = await tx.fetchval("""/* pipeshub:graph.claim_push_record */ WITH u AS (UPDATE graph.nodes r
                SET doc = r.doc || jsonb_build_object('externalRecordId', $8::text, 'version', $9::bigint)
                WHERE r.org_id = $1 AND r.coll = 'records' AND r.id = $2 AND r.doc->>'orgId' = $3 AND r.doc->>'connectorId' = $4
                  AND coalesce((r.doc->>'isDeleted')::boolean, false) = false AND r.doc->>'externalRecordId' = $5 AND r.doc->>'externalRevisionId' = $6
                  AND r.doc->'version' = to_jsonb($7::bigint) RETURNING 1) SELECT count(*) FROM u""",
                                  org, key, org_id, kb_id, expected.document_id, expected.revision, expected.version, new.document_id, new.version)
            return PushClaim.CLAIMED if n == 1 else PushClaim.LOST
        return await self._q(go, transaction, write=True)

    async def retag_push_record(self, *, key: str, kb_id: str, org_id: str, expected_revision: str, new_revision: str,
                                transaction: str | None = None) -> bool:
        org = current_org_id()
        n = await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.retag_push_record */ WITH u AS (UPDATE graph.nodes r
            SET doc = r.doc || jsonb_build_object('externalRevisionId', $5::text)
            WHERE r.org_id = $1 AND r.coll = 'records' AND r.id = $2 AND r.doc->>'orgId' = $3 AND r.doc->>'connectorId' = $4
              AND coalesce((r.doc->>'isDeleted')::boolean, false) = false AND r.doc->>'externalRevisionId' = $6 RETURNING 1) SELECT count(*) FROM u""",
                                                 org, key, org_id, kb_id, new_revision, expected_revision), transaction, write=True)
        return n == 1

    async def _create_reindex_event_payload(self, record: dict, file_record: dict | None, user_id: str | None = None, request: Request | None = None, record_id: str | None = None) -> dict:
        """Create reindex event payload"""
        try:
            # Handle both translated (_key -> id) and untranslated document formats
            record_key = record.get('_key') or record.get('id') or record_id or ''
            # Get extension and mimeType from file record
            extension = ""
            mime_type = ""
            if file_record:
                extension = file_record.get("extension", "")
                mime_type = file_record.get("mimeType", "")

            # Fallback: check if mimeType is in the record itself (for WebpageRecord, CommentRecord, etc.)
            if not mime_type:
                mime_type = record.get("mimeType", "")

            config = getattr(self, "config_service", None)  # none on a bare provider: the default endpoints
            endpoints = await config.get_config(config_node_constants.ENDPOINTS.value) if config else None
            signed_url_route = ""
            file_content = ""

            if record.get("origin") == OriginTypes.UPLOAD.value:
                storage_url = (endpoints or {}).get("storage", {}).get("endpoint", DefaultEndpoints.STORAGE_ENDPOINT.value)
                signed_url_route = f"{storage_url}/api/v1/document/internal/{record['externalRecordId']}/download"
            else:
                connector_url = (endpoints or {}).get("connectors", {}).get("endpoint", DefaultEndpoints.CONNECTOR_ENDPOINT.value)
                signed_url_route = f"{connector_url}/api/v1/{record.get(_ORG_FIELD)}/{user_id}/{record.get('connectorName', '').lower()}/record/{record_key}/signedUrl"

                if record.get("recordType") == "MAIL":
                    mime_type = "text/gmail_content"
                    try:
                        # Return early for MAIL records with special payload
                        return {
                            "orgId": record.get(_ORG_FIELD),
                            "recordId": record_key,
                            "recordName": record.get("recordName", ""),
                            "recordType": record.get("recordType", ""),
                            "version": record.get("version", 1),
                            "origin": record.get("origin", ""),
                            "extension": extension,
                            "mimeType": mime_type,
                            "body": file_content,
                            "connectorId": record.get("connectorId", ""),
                            "createdAtTimestamp": str(record.get("createdAtTimestamp") or get_epoch_timestamp_in_ms()),
                            "updatedAtTimestamp": str(get_epoch_timestamp_in_ms()),
                            "sourceCreatedAtTimestamp": str(
                                record.get("sourceCreatedAtTimestamp")
                                or record.get("createdAtTimestamp")
                                or get_epoch_timestamp_in_ms()
                            )
                        }
                    except Exception as decode_error:
                        self.logger.warning(f"Failed to decode file content as UTF-8: {str(decode_error)}")

            # Standard payload for non-MAIL records
            return {
                "orgId": record.get(_ORG_FIELD),
                "recordId": record_key,
                "recordName": record.get("recordName", ""),
                "recordType": record.get("recordType", ""),
                "version": record.get("version", 1),
                "signedUrlRoute": signed_url_route,
                "origin": record.get("origin", ""),
                "extension": extension,
                "mimeType": mime_type,
                "body": file_content,
                "connectorId": record.get("connectorId", ""),
                "createdAtTimestamp": str(record.get("createdAtTimestamp") or get_epoch_timestamp_in_ms()),
                "updatedAtTimestamp": str(get_epoch_timestamp_in_ms()),
                "sourceCreatedAtTimestamp": str(
                    record.get("sourceCreatedAtTimestamp")
                    or record.get("createdAtTimestamp")
                    or get_epoch_timestamp_in_ms()
                )
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to create reindex event payload: {str(e)}")
            raise

    async def reindex_single_record(
        self,
        record_id: str,
        user_id: str,
        org_id: str,
        request: Request | None = None,
        depth: int = 0,
        status_filters: list[str] | None = None,
    ) -> dict:
        """
        Reindex a single record with permission checks and event publishing.
        Depth comes from caller: 0 = only this record (record-details, collections/KB);
        >0 = include children (e.g. all-records tree uses 100).
        - KB (UPLOAD): depth > 0 uses sync-events; depth == 0 uses record-events.
        - CONNECTOR: uses sync-events and honors depth.

        Args:
            record_id: Record ID to reindex
            user_id: External user ID doing the reindex
            org_id: Organization ID
            request: FastAPI request object
            depth: Depth for children (0 = only this record; -1 or >MAX = normalized to MAX_REINDEX_DEPTH)

        Returns:
            Dict: success, recordId, recordName, connector, eventPublished, userRole; or error code/reason
        """
        current_org_id()      # resolved before the try: a missing org raises, it is not a failed reindex
        try:
            self.logger.debug(f"🔄 Starting reindex for record {record_id} by user {user_id} with depth {depth}")

            # Normalize depth only when including children (-1 or >MAX -> MAX_REINDEX_DEPTH); depth 0 stays 0
            if depth != 0 and (depth == -1 or depth > MAX_REINDEX_DEPTH):
                depth = MAX_REINDEX_DEPTH

            # Get record to determine connector type
            record = await self.get_document(record_id, CollectionNames.RECORDS.value)
            if not record:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"Record not found: {record_id}"
                }

            if record.get("isDeleted"):
                return {
                    "success": False,
                    "code": 400,
                    "reason": "Cannot reindex deleted record"
                }

            if is_code_record(record):
                return {
                    "success": False,
                    "code": 400,
                    "reason": (
                        "Records from GitHub and GitLab cannot be reindexed individually "
                        "because they belong to a repository-wide code graph. "
                        "Run a connector sync instead."
                    ),
                }

            connector_name = record.get("connectorName", "")
            connector_id = record.get("connectorId", "")
            origin = record.get("origin", "")

            self.logger.debug(f"📋 Record details - Origin: {origin}, Connector: {connector_name}, ConnectorId: {connector_id}")

            # Get user
            user = await self.get_user_by_user_id(user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found: {user_id}"
                }

            user_key = user.get('_key') or user.get('id')
            if not user_key:
                return {"success": False, "code": 404, "reason": "User key not found"}

            user_role = None

            # Check permissions based on origin type
            if origin == OriginTypes.UPLOAD.value:
                # KB record - check KB permissions
                kb_context = await self._get_kb_context_for_record(record_id)
                if not kb_context:
                    return {
                        "success": False,
                        "code": 404,
                        "reason": f"Knowledge base context not found for record {record_id}"
                    }

                user_role = await self.get_user_kb_permission(kb_context.get("kb_id") or kb_context.get("id"), user_key)
                if not user_role:
                    return {
                        "success": False,
                        "code": 403,
                        "reason": "Insufficient KB permissions. Required: OWNER, WRITER, READER"
                    }

            elif origin == OriginTypes.CONNECTOR.value:
                # Anyone who may access the record may reindex it: connector
                # items carry no role.
                if record_id not in (await self.check_access(
                    user_key, org_id, node_ids=[record_id],
                )).node_ids:
                    return {
                        "success": False,
                        "code": 403,
                        "reason": "You do not have access to this record"
                    }

                # Check if connector is enabled before allowing reindex
                if connector_id:
                    connector_doc = await self.get_document(connector_id, CollectionNames.APPS.value)
                    if connector_doc and not connector_doc.get("isActive", False):
                        display_name = connector_doc.get("name", "connector")
                        return {
                            "success": False,
                            "code": 409,
                            "reason": f"The connector '{display_name}' is currently disabled. Enable it from Connector Settings and try again."
                        }
            else:
                return {
                    "success": False,
                    "code": 400,
                    "reason": f"Unsupported record origin: {origin}"
                }


            # Create event data for router to publish
            try:
                if origin == OriginTypes.UPLOAD.value and depth > 0:
                    # KB folder reindex with children: use sync-events (same as connectors)
                    # Consumer will find children via get_records_by_parent_record(depth)
                    connector_for_event = connector_name.replace(" ", "").lower() if connector_name else "kb"
                    event_type = f"{connector_for_event}.reindex"

                    payload = {
                        "orgId": org_id,
                        "recordId": record_id,
                        "depth": depth,
                        "connectorId": connector_id,
                        "connector": connector_for_event,
                        "userKey": user_key,
                    }
                    if status_filters:
                        payload["statusFilters"] = status_filters

                    event_data = {
                        "eventType": event_type,
                        "topic": "sync-events",
                        "payload": payload
                    }
                elif origin == OriginTypes.UPLOAD.value:
                    # Single KB file reindex: use record-events
                    file_record = None
                    if record.get("recordType") == "FILE":
                        file_record = await self.get_document(record_id, CollectionNames.FILES.value)

                    payload = await self._create_reindex_event_payload(record, file_record, user_id, request, record_id=record_id)
                    event_data = {
                        "eventType": "newRecord",
                        "topic": "record-events",
                        "payload": payload
                    }
                    # Clear any terminal status so the consumer's COMPLETED gate does not
                    # skip the event. The router promotes this to QUEUED once it publishes.
                    await self.update_indexing_status_for_record_ids(
                        [record_id], ProgressStatus.NOT_STARTED.value
                    )
                else:
                    # For connector records, use sync-events with connector reindex event
                    connector_for_event = connector_name.replace(" ", "").lower() if connector_name else "unknown"
                    event_type = f"{connector_for_event}.reindex"

                    payload = {
                        "orgId": org_id,
                        "recordId": record_id,
                        "depth": depth,
                        "connectorId": connector_id,
                        "connector": connector_for_event,  # Add connector field for consumer fallback
                        "userKey": user_key,
                    }
                    if status_filters:
                        payload["statusFilters"] = status_filters

                    event_data = {
                        "eventType": event_type,
                        "topic": "sync-events",
                        "payload": payload
                    }

                return {
                    "success": True,
                    "recordId": record_id,
                    "recordName": record.get("recordName"),
                    "connector": connector_name if origin == OriginTypes.CONNECTOR.value else Connectors.KNOWLEDGE_BASE.value,
                    "userRole": user_role,
                    "eventData": event_data,
                    "useBatchReindex": origin != OriginTypes.UPLOAD.value  # KB records don't use batch reindex
                }

            except Exception as event_error:
                self.logger.error(f"❌ Failed to create reindex event data: {str(event_error)}")
                # Return success but indicate event data creation failed
                return {
                    "success": True,
                    "recordId": record_id,
                    "recordName": record.get("recordName"),
                    "connector": connector_name if origin == OriginTypes.CONNECTOR.value else Connectors.KNOWLEDGE_BASE.value,
                    "userRole": user_role,
                    "eventData": None,
                    "eventError": str(event_error)
                }

        except Exception as e:
            self.logger.error(f"❌ Failed to reindex record {record_id}: {str(e)}")
            return {"success": False, "code": 500, "reason": str(e)}

    async def reindex_record_group_records(
        self,
        record_group_id: str,
        depth: int,
        user_id: str,
        org_id: str
    ) -> dict:
        """
        Get record group data and validate permissions for reindexing.
        Does NOT publish events - that should be done by the caller (router).

        Args:
            record_group_id: Record group ID
            depth: Depth for traversing children (0 = only direct records)
            user_id: External user ID doing the reindex
            org_id: Organization ID

        Returns:
            Dict: Result with success status and connector information
        """
        current_org_id()      # resolved before the try: a missing org raises, it is not a failed reindex
        try:
            self.logger.debug(f"🔄 Validating record group reindex for {record_group_id} with depth {depth} by user {user_id}")

            # Handle negative depth: -1 means unlimited (set to MAX_REINDEX_DEPTH), other negatives are invalid (set to 0)
            if depth == -1:
                depth = MAX_REINDEX_DEPTH
                self.logger.debug(f"Depth was -1 (unlimited), setting to maximum limit: {depth}")
            elif depth < 0:
                self.logger.warning(f"Invalid negative depth {depth}, setting to 0 (direct records only)")
                depth = 0

            # Get record group
            record_group = await self.get_document(record_group_id, CollectionNames.RECORD_GROUPS.value)
            if not record_group:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"Record group not found: {record_group_id}"
                }

            connector_id = record_group.get("connectorId", "")
            connector_name = record_group.get("connectorName", "")
            if not connector_id or not connector_name:
                return {
                    "success": False,
                    "code": 400,
                    "reason": "Record group does not have a connector id or name"
                }

            # Get user
            user = await self.get_user_by_user_id(user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found: {user_id}"
                }

            user_key = user.get('_key') or user.get('id')
            if not user_key:
                return {"success": False, "code": 404, "reason": "User key not found"}

            try:
                allowed = record_group_id in (await self.check_access(
                    user_key, org_id, node_ids=[record_group_id],
                )).node_ids
            except Exception:
                self.logger.exception("❌ Access check failed for record group %s", record_group_id)
                return {"success": False, "code": 500, "reason": action_failed("check access to this record group")}
            if not allowed:
                return {
                    "success": False,
                    "code": 403,
                    "reason": "You do not have access to this record group"
                }

            # After the access check: the 409 names the connector.
            connector_doc = await self.get_document(connector_id, CollectionNames.APPS.value)
            if connector_doc and not connector_doc.get("isActive", False):
                display_name = connector_doc.get("name", "connector")
                return {
                    "success": False,
                    "code": 409,
                    "reason": f"The connector '{display_name}' is currently disabled. Enable it from Connector Settings and try again."
                }

            # Return success with connector information (caller will publish event)
            return {
                "success": True,
                "connectorId": connector_id,
                "connectorName": connector_name,
                "depth": depth,
                "recordGroupId": record_group_id,
                "userKey": user_key
            }

        except Exception as e:
            self.logger.error("❌ Failed to validate record group reindex: %s", str(e))
            return {"success": False, "code": 500, "reason": str(e)}
