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
from app.modules.indexing.record_diagnostics import WriteKind, sql_patch, with_run_patch
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.graph_db.common.typed_record import typed_record
from app.services.graph_db.common.utils import (
    RECORD_UPDATE_DROPPED_FIELDS,
    TRASHED_EXTERNAL_ID_PREFIX,
    collapse_repeated_records,
    record_created_at_insert_only,
    record_insert_doc,
)
from app.services.graph_db.interface.graph_db_provider import UNSUPPORTED_PAGE_FIELDS
from app.services.graph_db.postgres.base import (
    _VALIDATOR,
    PgBase,
    _arango,
    _doc,
    _j,
    _node,
    _pgbool,
)
from app.utils import error_class
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


async def _upsert_record_docs(tx: Tx, org: str, docs: list[dict]) -> None:
    """``_upsert_docs`` for the ``records`` collection, with the insert-only fields.

    A new record is written whole, with an incarnation id minted when ``RECORD_INCARNATION_WRITE`` is on. An existing record is
    merged from the document without ``createdAtTimestamp``, ``incarnationId`` and the trash state, so a re-sync keeps its stored
    values and a null cannot remove them. The one exception is a placeholder replaced by a real record (the stored row says
    ``isPlaceholder``, the incoming one does not): its date becomes the earlier of the two positive values, decided here from the
    stored row, so two writers that both read the stub cannot both overwrite it. The id is never touched."""
    by_id: dict[str, dict] = {}
    for d in docs:
        by_id[d["id"]] = {**by_id.get(d["id"], {}), **d}      # callers hand one row per id (collapsed or merged); with the switch off, last wins as before
    if not by_id:
        return
    # One document per row: the update branch derives what an existing record may take from it in SQL (``x2.doc - dropped keys``), so the
    # payload is not doubled and the batch is looked up once per conflicting row (two lookups and two documents per row cost 20-25 % at 500 records per call).
    rows = [{"id": i, "doc": record_insert_doc(by_id[i])} for i in sorted(by_id)]
    insert_only = record_created_at_insert_only()
    dropped = list(RECORD_UPDATE_DROPPED_FIELDS) if insert_only else ["incarnationId"]
    await tx.execute("""/* pipeshub:graph.records_upsert_record_docs */ WITH x AS (SELECT * FROM jsonb_to_recordset($3::jsonb) AS x(id text, doc jsonb))
        INSERT INTO graph.nodes (org_id, coll, id, doc) SELECT $1, $2, x.id, (SELECT coalesce(jsonb_object_agg(e.key, e.value), '{}'::jsonb) FROM jsonb_each(x.doc) e WHERE e.value <> 'null'::jsonb) FROM x ORDER BY x.id
        ON CONFLICT (org_id, coll, id) DO UPDATE SET doc = (SELECT coalesce(jsonb_object_agg(e.key, e.value), '{}'::jsonb) FROM jsonb_each(
            graph.nodes.doc || (SELECT (x2.doc - $4::text[]) || CASE
                WHEN $5::boolean AND coalesce((graph.nodes.doc->>'isPlaceholder')::boolean, false) AND x2.doc->>'isPlaceholder' = 'false'
                     AND jsonb_typeof(x2.doc->'createdAtTimestamp') = 'number' AND (x2.doc->>'createdAtTimestamp')::numeric > 0
                THEN jsonb_build_object('createdAtTimestamp', CASE WHEN jsonb_typeof(graph.nodes.doc->'createdAtTimestamp') = 'number' AND (graph.nodes.doc->>'createdAtTimestamp')::numeric > 0
                    THEN LEAST((graph.nodes.doc->>'createdAtTimestamp')::numeric, (x2.doc->>'createdAtTimestamp')::numeric) ELSE (x2.doc->>'createdAtTimestamp')::numeric END)
                ELSE '{}'::jsonb END FROM x x2 WHERE x2.id = EXCLUDED.id)) e WHERE e.value <> 'null'::jsonb)""",
                     org, _RECORDS, _j(rows), dropped, insert_only)


_RECORD_BY_EXTERNAL_PAGE = 100       # the function's LIMIT
_RECORD_BY_EXTERNAL = "/* pipeshub:graph.record_by_external */ SELECT node_id, doc FROM graph.record_by_external($1, $2)"       # the vetted read of graph_0004


class RecordsMixin(PgBase):
    # ---- single reads ------------------------------------------------------------------------------------------
    async def get_record_by_external_id(self, connector_id: str, external_id: str, transaction: str | None = None,
                                        visibility: RecordVisibility = RecordVisibility.LIVE, *, raise_on_error: bool = False) -> Record | None:
        """None means there is no such record, never that the lookup failed: a lookup that could not be read raises ``GraphQueryError``."""
        org = current_org_id()
        try:
            vis = _vis(visibility)
            rows = None
            if isinstance(connector_id, str) and isinstance(external_id, str) and 0 < len(connector_id) <= 200 and 0 < len(external_id) <= 2000:
                # the vetted read returns every record of the pair, and the visibility is read off the documents; a full page may not be all of them
                rows = await self._q(lambda tx: tx.fetch(_RECORD_BY_EXTERNAL, connector_id, external_id), transaction)
            if rows is not None and len(rows) < _RECORD_BY_EXTERNAL_PAGE:
                d = next((x for x in (_doc(r["doc"]) for r in rows) if vis == 0 or _pgbool(x.get("isDeleted")) == (vis == 2)), None)
            else:
                d = await self._one("""/* pipeshub:graph.get_record_by_external_id */ SELECT doc FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'records'
                    AND n.doc->>'connectorId' = $2 AND n.doc->>'externalRecordId' = $3
                    AND ($4::int = 0 OR coalesce((n.doc->>'isDeleted')::boolean, false) = ($4::int = 2)) LIMIT 1""",
                                    org, connector_id, external_id, vis, transaction=transaction)
            return Record.from_arango_base_record(_arango(d, _RECORDS)) if d else None
        except Exception as e:
            if raise_on_error:
                raise
            raise GraphQueryError(f"Could not look up record {external_id}: {e}") from e

    async def get_record_by_id(self, record_id: str, transaction: str | None = None, *, raise_on_error: bool = False) -> Any:  # noqa: ANN401
        """The typed record (with its type document); None when absent or when it will not build, as the Neo4j provider answers.
        ``raise_on_error`` propagates a failed read instead of answering None."""
        org = current_org_id()
        try:
            r = await self._q(lambda tx: tx.fetchrow("""/* pipeshub:graph.get_record_by_id */ SELECT r.doc AS rec, ty.doc AS typ FROM graph.nodes r
                LEFT JOIN LATERAL (SELECT t.doc FROM graph.edges e JOIN graph.nodes t ON t.org_id = e.org_id AND t.coll = e.to_coll AND t.id = e.to_id
                    WHERE e.org_id = r.org_id AND e.coll = 'isOfType' AND e.from_coll = 'records' AND e.from_id = r.id LIMIT 1) ty ON true
                WHERE r.org_id = $1 AND r.coll = 'records' AND r.id = $2""", org, record_id), transaction)
        except Exception as e:
            if raise_on_error:  # raised before logging, as get_record_by_external_id: the caller that handles it logs it once
                raise
            error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False,
                                    op="get_record_by_id", record_id=record_id)
            return None
        if not r:
            return None
        try:
            return typed_record(_rec(r["rec"]), _arango(_doc(r["typ"]), "") if r["typ"] else None)
        except Exception as e:  # the builder's error quotes the whole type document
            error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False,
                                    op="get_record_by_id", record_id=record_id)
            return None

    async def get_records_by_virtual_record_id(self, virtual_record_id: str, accessible_record_ids: list[str] | None = None, transaction: str | None = None,
                                               *, raise_on_error: bool = False, visibility: RecordVisibility = RecordVisibility.LIVE) -> list[str]:
        """The keys of the records of one virtual record. ``accessible_record_ids`` None filters nothing; an empty list returns nothing (fail closed: on
        Neo4j and ArangoDB an empty list is also no filter, which no caller on main relies on or passes)."""
        org = current_org_id()
        try:
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_records_by_virtual_record_id */ SELECT n.id FROM graph.nodes n
                WHERE n.org_id = $1 AND n.coll = 'records' AND (n.doc->>'virtualRecordId' = $2 OR (n.doc ? 'pendingVirtualRecordId' AND n.doc->>'pendingVirtualRecordId' = $2 AND n.doc->>'indexingStatus' IN ('IN_PROGRESS', 'QUEUED', 'NOT_STARTED')))
                AND ($4::int = 0 OR coalesce((n.doc->>'isDeleted')::boolean, false) = ($4::int = 2))
                AND ($3::text[] IS NULL OR n.id = ANY($3::text[]))""",
                                                      org, virtual_record_id, list(accessible_record_ids) if accessible_record_ids is not None else None, _vis(visibility)),
                                  transaction)
            return [r["id"] for r in rows]
        except Exception as e:
            if raise_on_error:
                raise
            error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False,
                                    op="get_records_by_virtual_record_id")
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
            error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False,
                                    op="get_record_by_external_revision_id")
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
            error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False,
                                    op="get_existing_record_keys")
            return set()

    async def get_records_by_record_ids(self, record_ids: list[str], org_id: str, visibility: RecordVisibility = RecordVisibility.LIVE, *,
                                        raise_on_error: bool = False) -> list[dict[str, Any]]:
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
            if raise_on_error:
                raise
            error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False,
                                    op="get_records_by_record_ids")
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
            error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False,
                                    op="get_typed_records_batch")
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
            error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False,
                                    op="get_virtual_record_ids_for_record_ids")
            return {}

    async def get_virtual_record_ids_in_use(self, virtual_record_ids: list[str], *, visibility: RecordVisibility = RecordVisibility.LIVE,
                                            transaction: str | None = None) -> set[str]:
        org = current_org_id()
        wanted = _uniq(virtual_record_ids)
        if not wanted:
            return set()
        # One row per matched id: a record that holds a vrid through its pending field is reported under that id, not under its committed one.
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_virtual_record_ids_in_use */ SELECT DISTINCT w.v FROM unnest($2::text[]) AS w(v)
            JOIN graph.nodes n ON n.org_id = $1 AND n.coll = 'records' AND (n.doc->>'virtualRecordId' = w.v OR (n.doc ? 'pendingVirtualRecordId' AND n.doc->>'pendingVirtualRecordId' = w.v AND n.doc->>'indexingStatus' IN ('IN_PROGRESS', 'QUEUED', 'NOT_STARTED')))
            AND ($3::int = 0 OR coalesce((n.doc->>'isDeleted')::boolean, false) = ($3::int = 2))""", org, wanted, _vis(visibility)), transaction)
        return {r["v"] for r in rows}

    async def get_virtual_record_holders(self, virtual_record_ids: list[str], org_id: str, transaction: str | None = None) -> dict[str, list[dict]]:
        """Every record of the org holding each virtual record id, trashed ones included; another org's id reads as nobody. A failure raises."""
        org = current_org_id()
        wanted = _uniq(virtual_record_ids)
        if not wanted or org_id != org:
            return {}
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_virtual_record_holders */ SELECT w.v AS v, jsonb_build_object('id', n.id,
              'connectorId', n.doc->'connectorId', 'connectorName', n.doc->'connectorName', 'recordGroupId', n.doc->'recordGroupId', 'recordName', n.doc->'recordName',
              'webUrl', n.doc->'webUrl', 'contentRev', n.doc->'contentRev', 'isDeleted', coalesce(n.doc->'isDeleted' = 'true'::jsonb, false)) AS h
            FROM unnest($2::text[]) AS w(v) JOIN graph.nodes n ON n.org_id = $1 AND n.coll = 'records' AND (n.doc->>'virtualRecordId' = w.v OR (n.doc ? 'pendingVirtualRecordId' AND n.doc->>'pendingVirtualRecordId' = w.v AND n.doc->>'indexingStatus' IN ('IN_PROGRESS', 'QUEUED', 'NOT_STARTED'))) ORDER BY n.id""", org, wanted), transaction)
        holders: dict[str, list[dict]] = {}
        for r in rows:
            holders.setdefault(r["v"], []).append(_doc(r["h"]))
        return holders

    # ---- writes ------------------------------------------------------------------------------------------------
    async def batch_upsert_records(self, records: list, transaction: str | None = None, *, release_trashed_external_ids: bool = False) -> None:
        """Each record's base document, type document and ``isOfType`` edge, set-based per record type, in one transaction.

        ``release_trashed_external_ids``: a record in the trash of the same connector that holds an incoming record's external id gives it up
        (kept in ``trashedExternalRecordId`` behind ``TRASHED_EXTERNAL_ID_PREFIX``), in the same transaction as the write. Every document is
        validated against its schema before the first statement, as in ``batch_upsert_nodes``."""
        org = current_org_id()
        if not records:
            return
        records = collapse_repeated_records(records)          # one row per id, by the rule every store applies
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
            await _upsert_record_docs(tx, org, all_bases)
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

    async def write_attempt_start(self, record_id: str, fields: dict[str, Any], now: int | None = None, transaction: str | None = None) -> bool:
        """See ``IGraphDBProvider.write_attempt_start``: one UPDATE, the run rule read from the stored document."""
        org = current_org_id()
        upd = {k: v for k, v in fields.items() if k not in ("_key", "_id", "id")}
        _VALIDATOR.validate_node_update(_RECORDS, upd)
        rev = upd.get("contentRev")
        run = sql_patch("doc", WriteKind.ATTEMPT_START, now="$4::bigint", rev="$5::text" if rev is not None else None)
        args: list[Any] = [org, record_id, _j(upd), now if now is not None else get_epoch_timestamp_in_ms()]
        if rev is not None:
            args.append(rev)
        row = await self._q(lambda tx: tx.fetchval(with_run_patch(
            """/* pipeshub:graph.write_attempt_start */ WITH u AS (UPDATE graph.nodes SET doc = (SELECT coalesce(jsonb_object_agg(_nv.key, _nv.value), '{}'::jsonb)
            FROM jsonb_each(doc || {RUN_PATCH} || $3::jsonb) AS _nv WHERE _nv.value <> 'null'::jsonb)
            WHERE org_id = $1 AND coll = 'records' AND id = $2 RETURNING 1) SELECT count(*) FROM u""", run), *args), transaction, write=True)
        return bool(row)

    async def compare_and_set_indexing_status(self, record_ids: list[str], expected: str, new_status: str, transaction: str | None = None) -> list[str]:
        """Set ``indexingStatus`` on each id that still holds ``expected``; returns the ids set. One statement: the update re-checks the status of a
        row it waited for, so a status another writer committed meanwhile is read, not overwritten."""
        org = current_org_id()
        ids = _uniq(record_ids)
        if not ids:
            return []
        extra = {"queuedAtTimestamp": get_epoch_timestamp_in_ms()} if new_status == ProgressStatus.QUEUED.value else {}
        _VALIDATOR.validate_node_update(_RECORDS, {"indexingStatus": new_status, **extra})
        run = sql_patch("doc", WriteKind.STATUS_VAR, now="$6::bigint", status="$4::text")
        # A database error raises (Neo4j logs it and returns []): the Kafka handler reads [] as "the record moved on" and discards the message, which
        # would drop the final FAILED marking and leave the record in its old status; raising lets the consumer retry.
        rows = await self._q(lambda tx: tx.fetch(with_run_patch("""/* pipeshub:graph.compare_and_set_indexing_status */ UPDATE graph.nodes
            SET doc = doc || jsonb_build_object('indexingStatus', $4::text) || $5::jsonb || {RUN_PATCH}
            WHERE org_id = $1 AND coll = 'records' AND id = ANY($2::text[]) AND doc->>'indexingStatus' = $3 RETURNING id""", run),
                                              org, ids, expected, new_status, _j(extra), get_epoch_timestamp_in_ms()), transaction, write=True)
        return [r["id"] for r in rows]

    async def list_unsupported_page(self, org_id: str, connector_id: str, after_key: str | None, limit: int) -> list[dict[str, Any]]:
        """A page of the vetted read of graph_0012 (index nodes_record_unsupported), in byte order of the id. Arguments the function refuses take the
        plain statement, which reads the same rows in the same order."""
        org = current_org_id()
        n = max(1, int(limit))
        after = after_key or ""
        if isinstance(connector_id, str) and 0 < len(connector_id) <= 200 and len(after) <= 200 and n <= 1000:
            rows = await self._q(lambda tx: tx.fetch("/* pipeshub:graph.records_unsupported_page */ SELECT node_id AS id, doc FROM graph.records_unsupported_page($1, $2, $3)",
                                                     connector_id, after, n), None)
        else:
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.list_unsupported_page */ SELECT n.id, n.doc FROM graph.nodes n
                WHERE n.org_id = $1 AND n.coll = 'records' AND n.doc->>'indexingStatus' = $2 AND n.doc->>'connectorId' = $3
                  AND ($4::text = '' OR n.id COLLATE ucs_basic > $4::text COLLATE ucs_basic) AND coalesce(n.doc->>'isDeleted', '') <> 'true'
                ORDER BY n.id COLLATE ucs_basic LIMIT $5""", org, ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value, connector_id, after, n), None)
        return [{"_key": r["id"], **{f: _doc(r["doc"]).get(f) for f in UNSUPPORTED_PAGE_FIELDS}} for r in rows]

    async def transition_unsupported(self, record_ids: list[str], to_status: str) -> list[str]:
        """One statement per batch: the update re-reads the status of a row it waited for, so a record another writer moved on is left alone."""
        org = current_org_id()
        ids = _uniq(record_ids)
        if not ids:
            return []
        extra: dict[str, Any] = {"reason": None}
        if to_status == ProgressStatus.QUEUED.value:
            extra["queuedAtTimestamp"] = get_epoch_timestamp_in_ms()
        _VALIDATOR.validate_node_update(_RECORDS, {"indexingStatus": to_status, **{k: v for k, v in extra.items() if v is not None}})
        run = sql_patch("doc", WriteKind.STATUS_VAR, now="$6::bigint", status="$4::text")
        rows = await self._q(lambda tx: tx.fetch(with_run_patch("""/* pipeshub:graph.transition_unsupported */ UPDATE graph.nodes
            SET doc = doc || jsonb_build_object('indexingStatus', $4::text) || $5::jsonb || {RUN_PATCH}
            WHERE org_id = $1 AND coll = 'records' AND id = ANY($2::text[]) AND doc->>'indexingStatus' = $3
              AND coalesce(doc->>'isDeleted', '') <> 'true' RETURNING id""", run),
                                                 org, ids, ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value, to_status, _j(extra), get_epoch_timestamp_in_ms()), None, write=True)
        return [r["id"] for r in rows]

    async def update_indexing_status_for_record_ids(
        self, record_ids: list[str], status: str, *, fields: dict[str, Any] | None = None
    ) -> None:
        """Set the status of each existing record that is neither internal nor a placeholder; a database failure is logged, not raised, but a
        status the schema rejects is (it is validated first, and is an update: a missing record stays missing). ``fields`` are set in the
        same statement (a Force reindex's refresh intent); a null value removes the key."""
        org = current_org_id()
        ids = _uniq(record_ids)
        if not ids:
            return
        _VALIDATOR.validate_node_update(_RECORDS, {"indexingStatus": status, **(fields or {})})
        try:
            run = sql_patch("doc", WriteKind.STATUS_VAR, now="$4::bigint", status="$3::text")
            await self._q(lambda tx: tx.execute(with_run_patch("""/* pipeshub:graph.update_indexing_status_for_record_ids */ UPDATE graph.nodes
                SET doc = doc || jsonb_build_object('indexingStatus', $3::text) || {RUN_PATCH} || $5::jsonb
                WHERE org_id = $1 AND coll = 'records' AND id = ANY($2::text[])
                  AND NOT coalesce((doc->>'isInternal')::boolean, false) AND NOT coalesce((doc->>'isPlaceholder')::boolean, false)""", run),
                                                 org, ids, status, get_epoch_timestamp_in_ms(), _j(fields or {})), None, write=True)
        except Exception as e:
            if fields:
                raise        # with ``fields`` (a Force reindex's request) a lost write must not be read as a request that was made
            error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False, op="update_indexing_status_for_record_ids")

    async def update_record_fields_for_ids(self, record_ids: list[str], fields: dict[str, Any]) -> int:
        """Set ``fields`` on each existing record that is neither internal nor a placeholder, whatever its status; the
        statement writes nothing else. Returns how many were written; a failure raises."""
        org = current_org_id()
        ids = _uniq(record_ids)
        if not ids or not fields:
            return 0
        _VALIDATOR.validate_node_update(_RECORDS, dict(fields))
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.update_record_fields_for_ids */ UPDATE graph.nodes
            SET doc = doc || $3::jsonb
            WHERE org_id = $1 AND coll = 'records' AND id = ANY($2::text[])
              AND NOT coalesce((doc->>'isInternal')::boolean, false) AND NOT coalesce((doc->>'isPlaceholder')::boolean, false)
            RETURNING id""", org, ids, _j(dict(fields))), None, write=True)
        return len(rows or [])

    async def get_records_with_refresh_intent(
        self, org_id: str, *, after_key: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """See :meth:`IGraphDBProvider.get_records_with_refresh_intent`. The partial index on the intent serves the filter;
        the org is the bound one (``org_id`` is the caller's check that it is the org it asked for)."""
        org = current_org_id()
        if org_id and org != org_id:
            raise ValueError("get_records_with_refresh_intent: the bound org is not the org asked for")
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.records_with_refresh_intent */ SELECT n.doc FROM graph.nodes n
            WHERE n.org_id = $1 AND n.coll = 'records' AND n.doc->>'refreshIntentId' IS NOT NULL
              AND coalesce(n.doc->>'isDeleted', '') <> 'true' AND ($2::text IS NULL OR n.id > $2)
            ORDER BY n.id LIMIT $3""", org, after_key, max(1, int(limit))), None)
        return [_arango(_doc(r["doc"]), _RECORDS) for r in rows or []]

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
        except Exception as e:
            if raise_on_error:
                raise
            error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False, op="get_sync_point")
            return None

    async def list_sync_points(self, org_id: str, collection: str, *, after_key: str | None = None, limit: int = 500) -> list[dict]:
        """One org's sync points in syncPointKey order; see the interface. Only the ambient org's: another reads nothing."""
        org = current_org_id()
        if org_id != org:
            return []
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.list_sync_points */ SELECT doc FROM graph.nodes
            WHERE org_id = $1 AND coll = $2 AND doc ? 'syncPointKey' AND ($3::text IS NULL OR doc->>'syncPointKey' > $3)
            ORDER BY doc->>'syncPointKey' LIMIT $4""", org, collection, after_key, limit), None)
        return [_arango(_doc(r["doc"]), collection) for r in rows]

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
