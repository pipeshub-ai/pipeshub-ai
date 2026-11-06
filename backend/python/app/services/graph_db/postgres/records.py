"""Records: single reads, lookups, the upsert with its type document, indexing status, and sync points.

A record is a ``records`` document, its type document (``files``, ``tickets``, ...) and an ``isOfType`` edge between them. Documents are
Arango-shaped. Every method resolves the ambient org before anything else (and before any ``try``), so a missing org raises instead of being
taken for a failed read. Every statement is a literal with bound parameters; the visibility of a record (live, trashed, both) is a bound code
(``_vis``), never text.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import (
    RECORD_TYPE_COLLECTION_MAPPING,
    CollectionNames,
    ProgressStatus,
)
from app.core.org_context import current_org_id
from app.exceptions.graph_db_exceptions import GraphQueryError
from app.models.entities import Record
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.graph_db.common.typed_record import typed_record
from app.services.graph_db.common.utils import TRASHED_EXTERNAL_ID_PREFIX
from app.services.graph_db.postgres.base import (
    _VALIDATOR,
    PgBase,
    _arango,
    _doc,
    _j,
    _node,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.core.db import Tx

_RECORDS = CollectionNames.RECORDS.value
_TYPE_COLLS = sorted(set(RECORD_TYPE_COLLECTION_MAPPING.values()))
_VIS = {RecordVisibility.ALL: 0, RecordVisibility.LIVE: 1, RecordVisibility.DELETED: 2}


def _vis(v: RecordVisibility) -> int:
    """The bound code of a visibility: 0 both, 1 live, 2 trashed. A record with no ``isDeleted`` is live (the shared rule)."""
    return _VIS[RecordVisibility(v)]


def _uniq(xs: Any) -> list[str]:  # noqa: ANN401
    return [x for x in dict.fromkeys(xs or []) if isinstance(x, str) and x]


def _rec(raw: Any) -> dict:  # noqa: ANN401
    return _arango(_doc(raw), _RECORDS)


async def _upsert_docs(tx: Tx, org: str, coll: str, docs: list[dict]) -> None:
    """Upsert documents in id order (concurrent batches lock in one order); a field set to null is removed (``SET n += map``), the rest merge.
    Only top-level nulls go: a null inside a nested value is kept, as a property of a node is flat on Neo4j and whatever it holds is stored as given.
    A repeated id applies its writes in order."""
    by_id: dict[str, dict] = {}
    for d in docs:
        by_id[d["id"]] = {**by_id.get(d["id"], {}), **d}
    if not by_id:
        return
    await tx.execute("""/* pipeshub:graph.records_upsert_docs */ WITH x AS (SELECT * FROM jsonb_to_recordset($3::jsonb) AS x(id text, doc jsonb))
        INSERT INTO graph.nodes (org_id, coll, id, doc) SELECT $1, $2, x.id, (SELECT coalesce(jsonb_object_agg(e.key, e.value), '{}'::jsonb) FROM jsonb_each(x.doc) e WHERE e.value <> 'null'::jsonb) FROM x ORDER BY x.id
        ON CONFLICT (org_id, coll, id) DO UPDATE SET doc = (SELECT coalesce(jsonb_object_agg(e.key, e.value), '{}'::jsonb) FROM jsonb_each(graph.nodes.doc || (SELECT x2.doc FROM x x2 WHERE x2.id = EXCLUDED.id)) e WHERE e.value <> 'null'::jsonb)""",
                     org, coll, _j([{"id": i, "doc": by_id[i]} for i in sorted(by_id)]))


class RecordsMixin(PgBase):
    # ---- single reads ------------------------------------------------------------------------------------------
    async def get_record_by_external_id(self, connector_id: str, external_id: str, transaction: str | None = None,
                                        visibility: RecordVisibility = RecordVisibility.LIVE, *, raise_on_error: bool = False) -> Record | None:
        """None means there is no such record, never that the lookup failed: a lookup that could not be read raises ``GraphQueryError``."""
        org = current_org_id()
        try:
            d = await self._one("""/* pipeshub:graph.get_record_by_external_id */ SELECT doc FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'records'
                AND n.doc->>'connectorId' = $2 AND n.doc->>'externalRecordId' = $3
                AND ($4::int = 0 OR coalesce((n.doc->>'isDeleted')::boolean, false) = ($4::int = 2)) LIMIT 1""",
                                org, connector_id, external_id, _vis(visibility), transaction=transaction)
            return Record.from_arango_base_record(_arango(d, _RECORDS)) if d else None
        except Exception as e:
            if raise_on_error:
                raise
            self.logger.error("Get record by external ID failed: %s", e)
            raise GraphQueryError(f"Could not look up record {external_id}: {e}") from e

    async def get_record_by_id(self, record_id: str, transaction: str | None = None) -> Any:  # noqa: ANN401
        """The typed record (with its type document); None when absent or when it will not build, as the Neo4j provider answers."""
        org = current_org_id()
        try:
            r = await self._q(lambda tx: tx.fetchrow("""/* pipeshub:graph.get_record_by_id */ SELECT r.doc AS rec, ty.doc AS typ FROM graph.nodes r
                LEFT JOIN LATERAL (SELECT t.doc FROM graph.edges e JOIN graph.nodes t ON t.org_id = e.org_id AND t.coll = e.to_coll AND t.id = e.to_id
                    WHERE e.org_id = r.org_id AND e.coll = 'isOfType' AND e.from_coll = 'records' AND e.from_id = r.id LIMIT 1) ty ON true
                WHERE r.org_id = $1 AND r.coll = 'records' AND r.id = $2""", org, record_id), transaction)
            if not r:
                return None
            return typed_record(_rec(r["rec"]), _arango(_doc(r["typ"]), "") if r["typ"] else None)
        except Exception as e:
            self.logger.error("Failed to retrieve record for id %s: %s", record_id, e)
            return None

    async def get_records_by_virtual_record_id(self, virtual_record_id: str, accessible_record_ids: list[str] | None = None, transaction: str | None = None,
                                               *, raise_on_error: bool = False, visibility: RecordVisibility = RecordVisibility.LIVE) -> list[str]:
        """The keys of the records of one virtual record. ``accessible_record_ids`` None filters nothing; an empty list returns nothing (fail closed: on
        Neo4j and ArangoDB an empty list is also no filter, which no caller on main relies on or passes)."""
        org = current_org_id()
        try:
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_records_by_virtual_record_id */ SELECT n.id FROM graph.nodes n
                WHERE n.org_id = $1 AND n.coll = 'records' AND n.doc->>'virtualRecordId' = $2
                AND ($4::int = 0 OR coalesce((n.doc->>'isDeleted')::boolean, false) = ($4::int = 2))
                AND ($3::text[] IS NULL OR n.id = ANY($3::text[]))""",
                                                      org, virtual_record_id, list(accessible_record_ids) if accessible_record_ids is not None else None, _vis(visibility)),
                                  transaction)
            return [r["id"] for r in rows]
        except Exception as e:
            self.logger.error("Error finding records with virtualRecordId %s: %s", virtual_record_id, e)
            if raise_on_error:
                raise
            return []

    async def page_record_ids_by_type(self, org_id: str, record_types: list[str], *, after_key: str | None = None, limit: int = 500) -> list[str]:
        org = current_org_id()
        if limit < 0:
            raise ValueError("limit must not be negative")
        if not org_id or not record_types or limit == 0 or org_id != org:  # an org argument is only ever the ambient org
            return []
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.page_record_ids_by_type */ SELECT n.id FROM graph.nodes n
            WHERE n.org_id = $1 AND n.coll = 'records' AND n.id > $2 AND n.doc->>'recordType' = ANY($3::text[])
            AND NOT coalesce((n.doc->>'isDeleted')::boolean, false) ORDER BY n.id LIMIT $4""",
                                                  org, after_key or "", list(record_types), limit), None)
        return [r["id"] for r in rows]

    async def get_records_by_parent(self, connector_id: str, parent_external_record_id: str, record_type: str | None = None,
                                    transaction: str | None = None, visibility: RecordVisibility = RecordVisibility.LIVE) -> list[Record]:
        org = current_org_id()
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_records_by_parent */ SELECT n.doc FROM graph.nodes n
            WHERE n.org_id = $1 AND n.coll = 'records' AND n.doc->>'connectorId' = $2 AND n.doc->>'externalParentId' = $3
            AND ($4::text IS NULL OR n.doc->>'recordType' = $4)
            AND ($5::int = 0 OR coalesce((n.doc->>'isDeleted')::boolean, false) = ($5::int = 2))""",
                                                  org, connector_id, parent_external_record_id, record_type or None, _vis(visibility)), transaction)
        return [Record.from_arango_base_record(_rec(r["doc"])) for r in rows]

    async def get_record_by_external_revision_id(self, connector_id: str, external_revision_id: str, transaction: str | None = None, *,
                                                 visibility: RecordVisibility = RecordVisibility.LIVE) -> Record | None:
        org = current_org_id()
        try:
            d = await self._one("""/* pipeshub:graph.get_record_by_external_revision_id */ SELECT doc FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'records'
                AND n.doc->>'connectorId' = $2 AND n.doc->>'externalRevisionId' = $3
                AND ($4::int = 0 OR coalesce((n.doc->>'isDeleted')::boolean, false) = ($4::int = 2)) LIMIT 1""",
                                org, connector_id, external_revision_id, _vis(visibility), transaction=transaction)
            return Record.from_arango_base_record(_arango(d, _RECORDS)) if d else None
        except Exception as e:
            self.logger.error("Get record by external revision ID failed: %s", e)
            return None

    # ---- lookups by id -----------------------------------------------------------------------------------------
    async def get_existing_record_keys(self, record_ids: list[str], transaction: str | None = None) -> set[str]:
        org = current_org_id()
        ids = _uniq(record_ids)
        if not ids:
            return set()
        try:
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_existing_record_keys */ SELECT id FROM graph.nodes
                WHERE org_id = $1 AND coll = 'records' AND id = ANY($2::text[])""", org, ids), transaction)
            return {r["id"] for r in rows}
        except Exception as e:
            self.logger.error("Failed to check record existence: %s", e)
            return set()

    async def get_records_by_record_ids(self, record_ids: list[str], org_id: str, visibility: RecordVisibility = RecordVisibility.LIVE) -> list[dict[str, Any]]:
        org = current_org_id()
        if not record_ids:
            return []
        try:
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_records_by_record_ids */ SELECT n.doc FROM graph.nodes n
                WHERE n.org_id = $1 AND n.coll = 'records' AND n.id = ANY($2::text[]) AND n.doc->>'orgId' = $3
                AND ($4::int = 0 OR coalesce((n.doc->>'isDeleted')::boolean, false) = ($4::int = 2))""",
                                                      org, list(record_ids), org_id, _vis(visibility)), None)
            return [_rec(r["doc"]) for r in rows]
        except Exception as e:
            self.logger.error("Failed to fetch records by record IDs: %s", e)
            return []

    async def get_typed_records_batch(self, record_ids: list[str], *, raise_on_error: bool = False) -> dict[str, Record]:
        """Typed records by id; an id that is absent, or will not build, is left out."""
        org = current_org_id()
        if not record_ids:
            return {}
        try:
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_typed_records_batch */ SELECT r.id, r.doc AS rec, ty.doc AS typ FROM graph.nodes r
                LEFT JOIN LATERAL (SELECT t.doc FROM graph.edges e JOIN graph.nodes t ON t.org_id = e.org_id AND t.coll = e.to_coll AND t.id = e.to_id
                    WHERE e.org_id = r.org_id AND e.coll = 'isOfType' AND e.from_coll = 'records' AND e.from_id = r.id LIMIT 1) ty ON true
                WHERE r.org_id = $1 AND r.coll = 'records' AND r.id = ANY($2::text[])""", org, list(record_ids)), None)
        except Exception as e:
            if raise_on_error:
                raise
            self.logger.error("get_typed_records_batch failed: %s", e)
            return {}
        typed: dict[str, Record] = {}
        for r in rows:
            try:
                typed[r["id"]] = typed_record(_rec(r["rec"]), _arango(_doc(r["typ"]), "") if r["typ"] else None)
            except Exception:
                continue
        return typed

    async def get_virtual_record_ids_for_record_ids(self, record_ids: list[str], transaction: str | None = None) -> dict[str, str]:
        org = current_org_id()
        if not record_ids:
            return {}
        try:
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_virtual_record_ids_for_record_ids */ SELECT id, doc->>'virtualRecordId' AS v
                FROM graph.nodes WHERE org_id = $1 AND coll = 'records' AND id = ANY($2::text[]) AND doc->>'virtualRecordId' IS NOT NULL""",
                                                      org, list(record_ids)), transaction)
            return {r["id"]: r["v"] for r in rows}
        except Exception as e:
            self.logger.warning("Failed to get virtual_record_ids for record_ids: %s", e)
            return {}

    async def get_virtual_record_ids_in_use(self, virtual_record_ids: list[str], *, visibility: RecordVisibility = RecordVisibility.LIVE,
                                            transaction: str | None = None) -> set[str]:
        org = current_org_id()
        wanted = _uniq(virtual_record_ids)
        if not wanted:
            return set()
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_virtual_record_ids_in_use */ SELECT DISTINCT n.doc->>'virtualRecordId' AS v FROM graph.nodes n
            WHERE n.org_id = $1 AND n.coll = 'records' AND n.doc->>'virtualRecordId' = ANY($2::text[])
            AND ($3::int = 0 OR coalesce((n.doc->>'isDeleted')::boolean, false) = ($3::int = 2))""", org, wanted, _vis(visibility)), transaction)
        return {r["v"] for r in rows}

    # ---- writes ------------------------------------------------------------------------------------------------
    async def batch_upsert_records(self, records: list, transaction: str | None = None, *, release_trashed_external_ids: bool = False) -> None:
        """Each record's base document, type document and ``isOfType`` edge, set-based per record type, in one transaction.

        ``release_trashed_external_ids``: a record in the trash of the same connector that holds an incoming record's external id gives it up
        (kept in ``trashedExternalRecordId`` behind ``TRASHED_EXTERNAL_ID_PREFIX``), in the same transaction as the write. Every document is
        validated against its schema before the first statement, as in ``batch_upsert_nodes``."""
        org = current_org_id()
        if not records:
            return
        by_type: dict[str | None, list] = {}
        for rec in records:
            by_type.setdefault(RECORD_TYPE_COLLECTION_MAPPING.get(rec.record_type), []).append(rec)

        bases_by_type = {coll: [_node(r.to_arango_base_record(), _RECORDS) for r in group] for coll, group in by_type.items()}
        types_by_type = {coll: [_node(r.to_arango_record(), coll) for r in by_type[coll]] for coll in by_type if coll}
        all_bases = [b for bases in bases_by_type.values() for b in bases]
        await self._validate_upserts(_RECORDS, all_bases, transaction)
        for coll in sorted(types_by_type):
            await self._validate_upserts(coll, types_by_type[coll], transaction)

        async def go(tx: Tx) -> None:
            # Row locks are taken in one order whatever the mix of types: every base document by id, then each type's documents by id
            # (collections in name order), then the edges. Two concurrent batches over the same records cannot wait on each other.
            now = get_epoch_timestamp_in_ms()
            if release_trashed_external_ids:
                await tx.execute("""/* pipeshub:graph.release_trashed_external_ids */ UPDATE graph.nodes h
                    SET doc = h.doc || jsonb_build_object('trashedExternalRecordId', h.doc->>'externalRecordId', 'externalRecordId', $3::text || h.id)
                    FROM jsonb_to_recordset($2::jsonb) AS x(id text, ext text, cid text)
                    WHERE h.org_id = $1 AND h.coll = 'records' AND x.ext IS NOT NULL AND h.doc->>'connectorId' = x.cid AND h.doc->>'externalRecordId' = x.ext
                      AND h.id <> x.id AND coalesce((h.doc->>'isDeleted')::boolean, false)""",
                                 org, _j([{"id": b["id"], "ext": b.get("externalRecordId"), "cid": b.get("connectorId")} for b in all_bases]), TRASHED_EXTERNAL_ID_PREFIX)
            await _upsert_docs(tx, org, _RECORDS, all_bases)
            for coll in sorted(c for c in by_type if c):
                types = types_by_type[coll]
                await _upsert_docs(tx, org, coll, types)
                edges = {b["id"]: {"fi": b["id"], "ti": t["id"], "doc": {"createdAtTimestamp": now, "updatedAtTimestamp": now}}
                         for b, t in zip(bases_by_type[coll], types, strict=True)}
                await tx.execute("""/* pipeshub:graph.records_upsert_type_edges */ INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
                    SELECT $1, 'isOfType', 'records', x.fi, $2, x.ti, '', x.doc FROM jsonb_to_recordset($3::jsonb) AS x(fi text, ti text, doc jsonb)
                    ORDER BY x.fi ON CONFLICT (org_id, coll, from_coll, from_id, to_coll, to_id, ekey) DO UPDATE SET doc = EXCLUDED.doc""",
                                 org, coll, _j([edges[i] for i in sorted(edges)]))
        await self._q(go, transaction, write=True)

    async def create_record_relation(self, from_record_id: str, to_record_id: str, relation_type: str, transaction: str | None = None) -> None:
        now = get_epoch_timestamp_in_ms()
        await self.batch_create_edges([{"from_id": from_record_id, "from_collection": _RECORDS, "to_id": to_record_id, "to_collection": _RECORDS,
                                        "relationshipType": relation_type, "createdAtTimestamp": now, "updatedAtTimestamp": now}],
                                      CollectionNames.NODE_RELATIONS.value, transaction)

    async def compare_and_set_indexing_status(self, record_ids: list[str], expected: str, new_status: str, transaction: str | None = None) -> list[str]:
        """Set ``indexingStatus`` on each id that still holds ``expected``; returns the ids set. One statement: the update re-checks the status of a
        row it waited for, so a status another writer committed meanwhile is read, not overwritten."""
        org = current_org_id()
        ids = _uniq(record_ids)
        if not ids:
            return []
        extra = {"queuedAtTimestamp": get_epoch_timestamp_in_ms()} if new_status == ProgressStatus.QUEUED.value else {}
        _VALIDATOR.validate_node_update(_RECORDS, {"indexingStatus": new_status, **extra})
        # A database error raises (Neo4j logs it and returns []): the Kafka handler reads [] as "the record moved on" and discards the message, which
        # would drop the final FAILED marking and leave the record in its old status; raising lets the consumer retry.
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.compare_and_set_indexing_status */ UPDATE graph.nodes
            SET doc = doc || jsonb_build_object('indexingStatus', $4::text) || $5::jsonb
            WHERE org_id = $1 AND coll = 'records' AND id = ANY($2::text[]) AND doc->>'indexingStatus' = $3 RETURNING id""",
                                              org, ids, expected, new_status, _j(extra)), transaction, write=True)
        return [r["id"] for r in rows]

    async def update_indexing_status_for_record_ids(self, record_ids: list[str], status: str) -> None:
        """Set the status of each existing record that is neither internal nor a placeholder; a database failure is logged, not raised, but a
        status the schema rejects is (it is validated first, and is an update: a missing record stays missing)."""
        org = current_org_id()
        ids = _uniq(record_ids)
        if not ids:
            return
        _VALIDATOR.validate_node_update(_RECORDS, {"indexingStatus": status})
        try:
            await self._q(lambda tx: tx.execute("""/* pipeshub:graph.update_indexing_status_for_record_ids */ UPDATE graph.nodes
                SET doc = doc || jsonb_build_object('indexingStatus', $3::text)
                WHERE org_id = $1 AND coll = 'records' AND id = ANY($2::text[])
                  AND NOT coalesce((doc->>'isInternal')::boolean, false) AND NOT coalesce((doc->>'isPlaceholder')::boolean, false)""",
                                                 org, ids, status), None, write=True)
        except Exception as e:
            self.logger.error("Failed to update records to %s: %s", status, e)

    async def has_records_indexed_since(self, connector_id: str, since_ms: int, *, transaction: str | None = None) -> bool:
        org = current_org_id()
        return bool(await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.has_records_indexed_since */ SELECT EXISTS (SELECT 1 FROM graph.nodes r
            WHERE r.org_id = $1 AND r.coll = 'records' AND r.doc->>'connectorId' = $2 AND r.doc->>'indexingStatus' = $3
              AND jsonb_typeof(r.doc->'lastIndexTimestamp') = 'number' AND (r.doc->>'lastIndexTimestamp')::numeric > $4)""",
                                                         org, connector_id, ProgressStatus.COMPLETED.value, since_ms), transaction))

    # ---- sync points -------------------------------------------------------------------------------------------
    async def get_sync_point(self, key: str, collection: str, transaction: str | None = None, *, raise_on_error: bool = False) -> dict | None:
        org = current_org_id()
        try:
            d = await self._one("""/* pipeshub:graph.get_sync_point */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND doc->>'syncPointKey' = $3
                ORDER BY id LIMIT 1""", org, collection, key, transaction=transaction)
            return _arango(d, collection) if d else None
        except Exception:
            self.logger.exception("Get sync point failed")
            if raise_on_error:
                raise
            return None

    async def upsert_sync_point(self, sync_point_key: str, sync_point_data: dict, collection: str, transaction: str | None = None) -> bool:
        """Create or merge the point keyed by ``syncPointKey`` (a null clears a property). Like the Neo4j provider it stamps the caller's dict."""
        org, now = current_org_id(), get_epoch_timestamp_in_ms()
        sync_point_data["syncPointKey"] = sync_point_key
        sync_point_data["updatedAtTimestamp"] = now
        data = {k: v for k, v in sync_point_data.items() if k not in ("_key", "_id", "id")}

        async def go(tx: Tx) -> None:
            # Update the point wherever it is stored (an older row may carry another id), otherwise insert it under id = key: the key is the
            # primary key, so two first upserts converge on one row.
            n = await tx.fetchval("""/* pipeshub:graph.upsert_sync_point_update */ WITH u AS (UPDATE graph.nodes SET doc = (SELECT coalesce(jsonb_object_agg(e.key, e.value), '{}'::jsonb) FROM jsonb_each(doc || $4::jsonb) e WHERE e.value <> 'null'::jsonb)
                WHERE org_id = $1 AND coll = $2 AND doc->>'syncPointKey' = $3 RETURNING 1) SELECT count(*) FROM u""", org, collection, sync_point_key, _j(data))
            if not n:
                await tx.execute("""/* pipeshub:graph.upsert_sync_point_insert */ INSERT INTO graph.nodes (org_id, coll, id, doc)
                    VALUES ($1, $2, $3, (SELECT coalesce(jsonb_object_agg(e.key, e.value), '{}'::jsonb) FROM jsonb_each($4::jsonb || jsonb_build_object('id', $3::text, 'createdAtTimestamp', $5::bigint)) e WHERE e.value <> 'null'::jsonb))
                    ON CONFLICT (org_id, coll, id) DO UPDATE SET doc = (SELECT coalesce(jsonb_object_agg(e.key, e.value), '{}'::jsonb) FROM jsonb_each(graph.nodes.doc || $4::jsonb) e WHERE e.value <> 'null'::jsonb)""", org, collection, sync_point_key, _j(data), now)
        try:
            await self._q(go, transaction, write=True)
        except Exception:
            self.logger.exception("Upsert sync point failed")
            raise
        return True

    async def remove_sync_point(self, key: str, collection: str, transaction: str | None = None) -> None:
        org = current_org_id()
        await self._q(lambda tx: tx.execute("""/* pipeshub:graph.remove_sync_point */ DELETE FROM graph.nodes
            WHERE org_id = $1 AND coll = $2 AND doc->>'syncPointKey' = $3""", org, collection, key), transaction, write=True)

    # ---- delete ------------------------------------------------------------------------------------------------
    async def _delete_records_with_their_types(self, record_ids: list[str], transaction: str | None = None) -> int:
        """Delete records with their type documents (the ``isOfType`` targets, and a type document that shares the record's id) and every edge of
        any of them, in one unit, so a failure deletes nothing. Blocks are not touched (``delete_blocks_for_records``). Returns the records deleted."""
        org = current_org_id()
        ids = _uniq(record_ids)
        if not ids:
            return 0

        async def go(tx: Tx) -> int:
            # The records are locked before their type documents are read: a writer that attaches a type to one of them holds that row until it commits,
            # so the read below (a statement of its own, with a fresh snapshot) sees what it wrote instead of leaving a type document no record owns.
            await tx.execute("""/* pipeshub:graph.delete_records_lock */ SELECT 1 FROM graph.nodes WHERE org_id = $1 AND coll = 'records' AND id = ANY($2::text[])
                ORDER BY id FOR UPDATE""", org, ids)
            return await tx.fetchval("""/* pipeshub:graph.delete_records_with_their_types */ WITH typed AS (
                  SELECT e.to_coll AS coll, e.to_id AS id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'isOfType' AND e.from_coll = 'records'
                    AND e.from_id = ANY($2::text[]) AND e.to_coll = ANY($3::text[])
                  UNION SELECT n.coll, n.id FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = ANY($3::text[]) AND n.id = ANY($2::text[])),
                gone AS (SELECT 'records' AS coll, i AS id FROM unnest($2::text[]) i UNION SELECT coll, id FROM typed),
                edges_from AS (DELETE FROM graph.edges e USING gone g WHERE e.org_id = $1 AND e.from_coll = g.coll AND e.from_id = g.id RETURNING 1),
                edges_to AS (DELETE FROM graph.edges e USING gone g WHERE e.org_id = $1 AND e.to_coll = g.coll AND e.to_id = g.id RETURNING 1),
                d AS (DELETE FROM graph.nodes n USING gone g WHERE n.org_id = $1 AND n.coll = g.coll AND n.id = g.id RETURNING n.coll)
                SELECT count(*) FILTER (WHERE coll = 'records') FROM d""", org, ids, _TYPE_COLLS)
        return int(await self._q(go, transaction, write=True) or 0)
