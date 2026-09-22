"""EntityVectorStore — provider-agnostic indexing of knowledge graph entities.

Entities (categories, topics, departments, people, record groups, connectors)
are stored in a dedicated ``entities`` vector collection, separate from the
``records`` document collection.  Each entity point has:

  page_content  = "<name>"  (aliases are payload only — EntityRecord.embedding_text)
  metadata      = EntityRecord.to_vector_payload()  (slim — see models.entities)
  connectorIds / recordGroupIds = top-level payload siblings of metadata, not
                                  nested in it (mirrors the records
                                  collection's VectorChunkPayload) — see
                                  ``upsert_entities_batch``.

The deterministic point ID is derived from ``orgId:entityType:entityId`` via
UUID5 so that re-upserts are idempotent without a prior delete.

Reference counting / provenance (which connectors reference an entity, how
many records link to it) is owned by the graph DB, not this vector index —
this store is a disposable search projection that can be rebuilt from the
graph. Connector-disconnect cleanup here
scrolls only the points scoped to that connector (top-level ``connectorIds``
+ ``metadata.orgId``, the same bound a single filtered delete would use) and
shrinks each one's membership rather than deleting it outright, since a
taxonomy/record-group entity can be shared with another still-live
connector — see ``delete_entities_by_connector``.

Membership arrays (``connectorIds``, ``recordGroupIds``) are merged, not
replaced. The same entity point is shared by every record that references it
(deterministic ID above), but each caller only knows about the one record it
currently has in hand — a plain payload overwrite would leave the point
remembering only the last writer's single group/connector instead of the
union across all of them. See ``_merge_membership`` and the per-entity lock
in ``_entity_lock``.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
import weakref
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import QdrantCollectionNames
from app.config.constants.service import config_node_constants
from app.exceptions.indexing_exceptions import VectorStoreError
from app.services.vector_db.const.const import (
    CONNECTOR_IDS_FIELD,
    RECORD_GROUP_IDS_FIELD,
)
from app.services.vector_db.models import (
    CollectionConfig,
    SearchResult,
    VectorPoint,
)
from app.services.vector_db.sparse_embeddings import SparseEmbedder
from app.utils.aimodels import get_default_embedding_model, get_embedding_model

if TYPE_CHECKING:
    import logging

    from app.config.configuration_service import ConfigurationService
    from app.models.entities import EntityRecord
    from app.services.vector_db.interface.vector_db import IVectorDBService

_ENTITIES_COLLECTION = QdrantCollectionNames.ENTITIES.value

_CONFIDENCE_THRESHOLD = 0.0


class EntityVectorStore:
    """Manages embedding and retrieval of knowledge-graph entities in the
    dedicated ``entities`` vector collection.

    Designed to be a singleton per process (DI Singleton provider) so the
    embedding model and sparse embedder are initialised once and reused.
    """

    def __init__(
        self,
        logger: logging.Logger,
        config_service: ConfigurationService,
        vector_db_service: IVectorDBService,
        collection_name: str = _ENTITIES_COLLECTION,
    ) -> None:
        self.logger = logger
        self.config_service = config_service
        self.vector_db_service = vector_db_service
        self.collection_name = collection_name

        self._capabilities = vector_db_service.get_capabilities()
        self._dense_embeddings = None
        self._sparse_embedder: SparseEmbedder | None = None
        self._sparse_lock: asyncio.Lock | None = None
        self._initialized = False
        self._init_lock = asyncio.Lock()

        # Per-entity locks guarding the membership read-merge-write below; see
        # ``_entity_lock``. Weakly held so the map does not grow with every
        # entity ever touched.
        self._entity_locks: "weakref.WeakValueDictionary[tuple[int, str], asyncio.Lock]" = (
            weakref.WeakValueDictionary()
        )

    # ------------------------------------------------------------------
    # Initialisation (lazy, once per process)
    # ------------------------------------------------------------------

    async def _ensure_initialized(self) -> None:
        """Lazily initialise embeddings and the collection (idempotent)."""
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await self._init_embeddings()
            await self._init_collection()
            self._initialized = True

    async def _init_embeddings(self) -> None:
        ai_models = await self.config_service.get_config(
            config_node_constants.AI_MODELS.value, use_cache=False
        )
        embedding_configs = ai_models.get("embedding", [])
        if not embedding_configs:
            self._dense_embeddings = get_default_embedding_model()
        else:
            config = next(
                (c for c in embedding_configs if c.get("isDefault")), embedding_configs[0]
            )
            self._dense_embeddings = get_embedding_model(config["provider"], config)

        loop = asyncio.get_running_loop()
        sample = await loop.run_in_executor(
            None, self._dense_embeddings.embed_query, "test"
        )
        self._embedding_size = len(sample)

        if self._capabilities.supports_sparse_vectors:
            if self._sparse_lock is None:
                self._sparse_lock = asyncio.Lock()
            async with self._sparse_lock:
                if self._sparse_embedder is None:
                    embedder = SparseEmbedder()
                    await embedder._ensure_initialized()
                    self._sparse_embedder = embedder

    async def _init_collection(self) -> None:
        info = await self.vector_db_service.get_collection_info(self.collection_name)
        if info.exists:
            if info.dense_dimension and info.dense_dimension != self._embedding_size:
                raise VectorStoreError(
                    f"Entity collection dimension mismatch: existing={info.dense_dimension}, "
                    f"model={self._embedding_size}. Re-index by deleting the collection.",
                    details={"collection": self.collection_name},
                )
            self.logger.debug(
                "Entity collection '%s' already exists (dim=%s).",
                self.collection_name,
                self._embedding_size,
            )
            return

        await self.vector_db_service.create_collection(
            collection_name=self.collection_name,
            config=CollectionConfig(
                embedding_size=self._embedding_size,
                enable_sparse=self._capabilities.supports_sparse_vectors,
            ),
        )
        # Create filterable indexes for the fields we query on. connectorIds
        # and recordGroupIds are top-level payload siblings of metadata (not
        # nested in it) — see ``upsert_entities_batch``.
        for field, schema in [
            ("metadata.orgId", {"type": "keyword"}),
            ("metadata.entityType", {"type": "keyword"}),
            ("metadata.entityId", {"type": "keyword"}),
            ("metadata.level", {"type": "keyword"}),
            (CONNECTOR_IDS_FIELD, {"type": "keyword"}),
            (RECORD_GROUP_IDS_FIELD, {"type": "keyword"}),
        ]:
            await self.vector_db_service.create_index(
                collection_name=self.collection_name,
                field_name=field,
                field_schema=schema,
            )
        self.logger.info("✅ Created entity vector collection '%s'", self.collection_name)

    # ------------------------------------------------------------------
    # Deterministic point ID
    # ------------------------------------------------------------------

    @staticmethod
    def _point_id(org_id: str, entity_type: str, entity_id: str) -> str:
        """Derive a stable UUID5 so the same entity always maps to the same point."""
        namespace = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
        return str(uuid.uuid5(namespace, f"{org_id}:{entity_type}:{entity_id}"))

    @staticmethod
    def _entity_key(org_id: str, entity_type: str, entity_id: str) -> str:
        return f"{org_id}:{entity_type}:{entity_id}"

    # ------------------------------------------------------------------
    # Embedding helpers
    # ------------------------------------------------------------------

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._dense_embeddings.embed_documents, texts)

    async def _embed_sparse(self, texts: list[str]) -> list[Any]:
        if not self._sparse_embedder:
            return [None] * len(texts)
        return await self._sparse_embedder.embed_documents(texts)

    # ------------------------------------------------------------------
    # Public API — upsert
    # ------------------------------------------------------------------

    async def upsert_entity(self, entity: EntityRecord) -> None:
        """Embed and upsert a single entity into the entities collection."""
        await self.upsert_entities_batch([entity])

    async def upsert_entities_batch(
        self,
        entities: list[EntityRecord],
        batch_size: int = 64,
        *,
        merge_membership: bool = True,
    ) -> None:
        """Batch-embed and upsert a list of EntityRecord objects.

        Failures within a batch are logged and skipped rather than aborting
        the entire batch (partial-failure tolerance).

        ``connectorIds``/``recordGroupIds`` are merged with whatever is
        already stored for that entity, not replaced — see
        ``_merge_membership``. Every entity in the batch has its lock (see
        ``_entity_lock``) held from the pre-write read through this batch's
        single ``upsert_points`` call, so two concurrent batches touching the
        same shared entity (e.g. two records both tagged "Engineering")
        cannot each merge against a stale read and drop the other's update.

        ``merge_membership=False`` writes ``entity.connector_ids``/
        ``record_group_ids`` as-is instead of unioning with what is already
        stored — used by callers that already read-modify-wrote the full
        membership themselves (e.g. removing one connector from a shared
        entity in ``_shrink_connector_membership``), where merging again
        would silently re-add the membership being removed.
        """
        await self._ensure_initialized()
        if not entities:
            return

        for start in range(0, len(entities), batch_size):
            batch = entities[start : start + batch_size]
            try:
                async with contextlib.AsyncExitStack() as locks:
                    # Sorted, deduped acquisition order: a global lock
                    # ordering rules out circular waits between overlapping
                    # concurrent batches, so no separate deadlock-avoidance
                    # logic is needed.
                    lock_keys = sorted({
                        self._entity_key(
                            e.org_id, e.entity_type.value, e.entity_id
                        )
                        for e in batch
                    })
                    for key in lock_keys:
                        await locks.enter_async_context(self._entity_lock(key))

                    pending: list[tuple[EntityRecord, list[str], list[str]]] = []
                    for entity in batch:
                        if not entity.name.strip():
                            self.logger.warning(
                                "Skipping entity with empty name: %s / %s",
                                entity.entity_type,
                                entity.entity_id,
                            )
                            continue
                        if merge_membership:
                            existing = await self._fetch_existing_state(
                                entity.org_id, entity.entity_type.value, entity.entity_id
                            )
                            connector_ids = self._union_ids(
                                existing["connectorIds"], entity.connector_ids
                            )
                            record_group_ids = self._union_ids(
                                existing["recordGroupIds"], entity.record_group_ids
                            )
                            if self._is_unchanged(
                                existing, entity, connector_ids, record_group_ids
                            ):
                                continue
                        else:
                            connector_ids = list(entity.connector_ids)
                            record_group_ids = list(entity.record_group_ids)
                        pending.append((entity, connector_ids, record_group_ids))

                    if not pending:
                        continue

                    # Embedding happens under the locks so the read above and
                    # the write below stay one atomic read-merge-write per
                    # entity; only entities that actually change are embedded.
                    texts = [entity.embedding_text for entity, _, _ in pending]
                    dense_vecs = await self._embed(texts)
                    sparse_vecs = await self._embed_sparse(texts)

                    points: list[VectorPoint] = []
                    for (entity, connector_ids, record_group_ids), dense, sparse in zip(
                        pending, dense_vecs, sparse_vecs
                    ):
                        payload = {
                            "page_content": entity.embedding_text,
                            "metadata": entity.to_vector_payload(),
                            CONNECTOR_IDS_FIELD: connector_ids,
                            RECORD_GROUP_IDS_FIELD: record_group_ids,
                        }
                        points.append(
                            VectorPoint(
                                id=self._point_id(
                                    entity.org_id, entity.entity_type.value, entity.entity_id
                                ),
                                dense_vector=dense,
                                sparse_vector=sparse,
                                payload=payload,
                            )
                        )

                    await self.vector_db_service.upsert_points(
                        collection_name=self.collection_name, points=points
                    )
                    self.logger.debug(
                        "Upserted %d entity points (batch start=%d, skipped %d unchanged)",
                        len(points), start, len(batch) - len(points),
                    )
            except Exception as exc:
                self.logger.error(
                    "Failed to upsert entity batch starting at %d: %s", start, exc
                )

    def _entity_lock(self, key: str) -> asyncio.Lock:
        """Get-or-create the lock for an entity key on the running event loop.

        No guard lock is needed: there is no ``await`` between the lookup and
        the insert, so this is atomic on a single-threaded event loop. Keyed
        by ``(loop, entity)`` for the same reason as ``membership.py``'s
        ``_vrid_lock`` — indexing runs some work on a worker-thread loop and
        some on the main loop, and a single shared lock per key would turn a
        cross-loop call into a hard error.
        """
        try:
            loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            loop_id = 0
        lock_map_key = (loop_id, key)
        lock = self._entity_locks.get(lock_map_key)
        if lock is None:
            lock = asyncio.Lock()
            self._entity_locks[lock_map_key] = lock
        return lock

    async def _fetch_existing_state(
        self, org_id: str, entity_type: str, entity_id: str
    ) -> dict[str, Any]:
        """Best-effort read of a point's current membership arrays and text.

        ``exists`` is False on lookup failure or first-ever write for this
        entity — a fresh entity or a transient read error must not block the
        upsert; it just starts (or stays) with only what this call
        contributes. Caller must hold the entity's lock (``_entity_lock``)
        across this read and the eventual write, otherwise two concurrent
        writers can each merge against a stale read and one update is lost.
        """
        empty: dict[str, Any] = {
            "exists": False,
            "connectorIds": [],
            "recordGroupIds": [],
            "page_content": None,
            "metadata": None,
        }
        try:
            filter_expr = await self.vector_db_service.filter_collection(
                must={
                    "metadata.orgId": org_id,
                    "metadata.entityType": entity_type,
                    "metadata.entityId": entity_id,
                }
            )
            result = await self.vector_db_service.scroll(
                collection_name=self.collection_name,
                scroll_filter=filter_expr,
                limit=1,
            )
        except Exception as exc:
            self.logger.debug(
                "Membership merge read failed for %s/%s (treating as new): %s",
                entity_type, entity_id, exc,
            )
            return empty
        if not result.points:
            return empty
        payload = result.points[0].payload or {}
        return {
            "exists": True,
            "connectorIds": list(payload.get(CONNECTOR_IDS_FIELD) or []),
            "recordGroupIds": list(payload.get(RECORD_GROUP_IDS_FIELD) or []),
            "page_content": payload.get("page_content"),
            "metadata": payload.get("metadata"),
        }

    @staticmethod
    def _is_unchanged(
        existing: dict[str, Any],
        entity: EntityRecord,
        connector_ids: list[str],
        record_group_ids: list[str],
    ) -> bool:
        """True when writing ``entity`` would store exactly what is already
        there, so the embedding call and the upsert can be skipped."""
        return bool(
            existing.get("exists")
            and existing.get("page_content") == entity.embedding_text
            and existing.get("metadata") == entity.to_vector_payload()
            and list(existing.get("connectorIds") or []) == list(connector_ids)
            and list(existing.get("recordGroupIds") or []) == list(record_group_ids)
        )

    @staticmethod
    def _union_ids(existing: list[str], new: list[str]) -> list[str]:
        """Dedup, order-preserving union."""
        seen: set[str] = set()
        merged: list[str] = []
        for value in (*existing, *new):
            if value and value not in seen:
                seen.add(value)
                merged.append(value)
        return merged

    # ------------------------------------------------------------------
    # Public API — delete
    # ------------------------------------------------------------------

    async def delete_entity(self, org_id: str, entity_type: str, entity_id: str) -> None:
        """Delete a single entity point from the collection.

        Filters on the same ``(orgId, entityType, entityId)`` triple the
        point ID is derived from (see ``_point_id``) — scoping by
        ``entityId``+``orgId`` alone would also match a different-typed
        entity that happened to reuse the same graph-node key.
        """
        await self._ensure_initialized()
        try:
            filter_expr = await self.vector_db_service.filter_collection(
                must={
                    "metadata.entityId": entity_id,
                    "metadata.entityType": entity_type,
                    "metadata.orgId": org_id,
                }
            )
            await self.vector_db_service.delete_points(self.collection_name, filter_expr)
            self.logger.info(
                "Deleted entity %s/%s from vector store", entity_type, entity_id
            )
        except Exception as exc:
            self.logger.error("Failed to delete entity %s: %s", entity_id, exc)

    async def delete_entities_for_org(self, org_id: str) -> None:
        """Remove ALL entity vectors for an organisation (e.g. on org deletion)."""
        await self._ensure_initialized()
        try:
            filter_expr = await self.vector_db_service.filter_collection(
                must={"metadata.orgId": org_id}
            )
            await self.vector_db_service.delete_points(self.collection_name, filter_expr)
            self.logger.info("Deleted all entity vectors for org %s", org_id)
        except Exception as exc:
            self.logger.error("Failed to delete entity vectors for org %s: %s", org_id, exc)

    async def delete_entities_by_connector(
        self,
        org_id: str,
        connector_id: str,
    ) -> None:
        """Remove *connector_id*'s membership from every entity point it
        touches within *org_id*.

        Entities that are NOT scoped to this connector (``connectorIds``
        empty or not containing it) are untouched. Entities that ARE scoped
        to it but have no other membership left afterwards are deleted
        outright; entities still referenced by another connector or record
        group (e.g. a taxonomy entity shared across connectors) instead have
        just this connector's id removed — see
        ``_shrink_connector_membership``. The graph DB remains the source of
        truth for whether a connector-scoped entity should still exist;
        reindexing the affected records restores anything still valid.
        """
        await self._ensure_initialized()
        try:
            await self._shrink_connector_membership(org_id, connector_id)
            self.logger.info(
                "Reconciled connector-scoped entities | org=%s connector=%s",
                org_id, connector_id,
            )
        except Exception as exc:
            self.logger.error(
                "Failed to reconcile connector-scoped entities (org=%s connector=%s): %s",
                org_id, connector_id, exc,
            )

    async def _shrink_connector_membership(
        self, org_id: str, connector_id: str, page_size: int = 100
    ) -> None:
        """Remove *connector_id* from ``connectorIds`` on every point that
        has it, within *org_id*.

        Scrolls only points matching this connector (same filter the old
        hard-delete used, so the bound is unchanged), then per point:
        deletes it outright if removing this connector leaves both
        ``connectorIds`` and ``recordGroupIds`` empty (no membership left at
        all — such a point is also unreachable by ``search_entities``, whose
        Stage-1 filter requires a ``should`` match on one of those arrays);
        otherwise rewrites it with the connector removed, via
        ``upsert_entities_batch(..., merge_membership=False)`` so the
        removed id is not immediately re-unioned back in.

        A per-point read-modify-write, not ``set_payload``: rewriting a point
        touches ``metadata.*`` fields alongside the top-level membership
        arrays (e.g. dropping a malformed ``typeCategory``), and there is no
        representation of a partial-field update that is consistent across
        all vector backends (Qdrant treats a dotted payload key literally;
        Redis flattens nested dicts into ``metadata_x`` hash fields only at
        ``upsert_points`` time, not on a payload-only write). Going through
        the normal upsert path keeps this write consistent with every other
        write to this collection, at the cost of re-embedding the (typically
        few) entities this connector touches.
        """
        from app.models.entities import EntityRecord, EntityType, EntityTypeCategory

        filter_expr = await self.vector_db_service.filter_collection(
            must={"metadata.orgId": org_id, CONNECTOR_IDS_FIELD: connector_id}
        )

        to_delete: list[tuple[str, str]] = []
        to_reupsert: list[EntityRecord] = []
        offset: str | None = None
        while True:
            result = await self.vector_db_service.scroll(
                collection_name=self.collection_name,
                scroll_filter=filter_expr,
                limit=page_size,
                offset=offset,
            )
            for point in result.points:
                meta = point.payload.get("metadata") or {}
                entity_id = meta.get("entityId")
                entity_type = meta.get("entityType")
                if not entity_id or not entity_type:
                    continue
                connector_ids = [
                    c for c in (point.payload.get(CONNECTOR_IDS_FIELD) or [])
                    if c != connector_id
                ]
                record_group_ids = list(point.payload.get(RECORD_GROUP_IDS_FIELD) or [])
                if not connector_ids and not record_group_ids:
                    to_delete.append((entity_type, entity_id))
                    continue
                try:
                    type_category = (
                        EntityTypeCategory(meta["typeCategory"])
                        if meta.get("typeCategory")
                        else EntityTypeCategory.PREDEFINED
                    )
                    to_reupsert.append(
                        EntityRecord(
                            entity_id=entity_id,
                            entity_type=EntityType(entity_type),
                            name=meta.get("name") or "",
                            org_id=org_id,
                            canonical_name=meta.get("canonicalName") or "",
                            aliases=list(meta.get("aliases") or []),
                            domain=meta.get("domain"),
                            type_category=type_category,
                            connector_ids=connector_ids,
                            record_group_ids=record_group_ids,
                        )
                    )
                except Exception as exc:
                    self.logger.warning(
                        "Skipping malformed entity point during connector "
                        "membership shrink (org=%s entityId=%s): %s",
                        org_id, entity_id, exc,
                    )
            offset = result.next_offset
            if offset is None:
                break

        for entity_type, entity_id in to_delete:
            await self.delete_entity(org_id, entity_type, entity_id)
        if to_reupsert:
            await self.upsert_entities_batch(to_reupsert, merge_membership=False)

    # ------------------------------------------------------------------
    # Public API — search
    # ------------------------------------------------------------------

    async def search_entities(
        self,
        query: str,
        org_id: str,
        accessible_record_group_ids: set[str],
        accessible_connector_ids: set[str],
        entity_types: list[str] | None = None,
        top_k: int = 10,
        score_threshold: float = _CONFIDENCE_THRESHOLD,
        *,
        allow_org_wide: bool = False,
    ) -> list[dict[str, Any]]:
        """Semantically search for entities matching *query*, scoped to what
        the caller can reach.

        *accessible_record_group_ids*/*accessible_connector_ids* are
        **required** (plain id sets, not an ACL object — this class stays
        graph-free) so a direct call cannot silently produce an unfiltered,
        org-wide search. Only ``allow_org_wide=True`` drops the membership
        filter, for callers that verify every hit against the graph (see
        ``app.modules.retrieval.entity_permissions``) — stored membership
        can be empty or stale, so it is a recall hint, never an access check.

        Filter shape: ``must={orgId[, entityType]}`` AND
        ``should={recordGroupIds, connectorIds}`` (at least one must match —
        no ``min_should_match``, since KB/Collection records have no record
        group by design and are reachable only via ``connectorIds``; see
        ``services/vector_db/membership.py``).

        Returns a list of dicts:
            {entityId, entityType, name, score, connectorIds, recordGroupIds}

        Raises on a vector DB failure so callers can tell it apart from "no
        match".
        """
        await self._ensure_initialized()

        if not query.strip() or not org_id:
            return []

        if not allow_org_wide and not accessible_record_group_ids and not accessible_connector_ids:
            # Omitting the should-group here would leave only the org/type
            # must-filter, silently widening back to an org-wide search.
            return []

        loop = asyncio.get_running_loop()
        dense_vec = await loop.run_in_executor(
            None, self._dense_embeddings.embed_query, query
        )

        sparse_vec = None
        if self._sparse_embedder:
            sparse_results = await self._sparse_embedder.embed_documents([query])
            sparse_vec = sparse_results[0] if sparse_results else None

        must_conditions: dict[str, Any] = {"metadata.orgId": org_id}
        if entity_types:
            must_conditions["metadata.entityType"] = entity_types  # list → "any of" filter

        should_conditions: dict[str, Any] = {}
        if accessible_record_group_ids:
            should_conditions[RECORD_GROUP_IDS_FIELD] = sorted(accessible_record_group_ids)
        if accessible_connector_ids:
            should_conditions[CONNECTOR_IDS_FIELD] = sorted(accessible_connector_ids)

        filter_expr = await self.vector_db_service.filter_collection(
            must=must_conditions,
            should=should_conditions,
        )

        from app.services.vector_db.models import FusionMethod, HybridSearchRequest

        request = HybridSearchRequest(
            dense_query=dense_vec,
            sparse_query=sparse_vec,
            text_query=query,
            filter=filter_expr,
            limit=top_k,
            fusion_method=FusionMethod.RRF,
            with_payload=True,
        )

        try:
            batch_results: list[list[SearchResult]] = (
                await self.vector_db_service.query_nearest_points(
                    collection_name=self.collection_name,
                    requests=[request],
                )
            )
        except Exception as exc:
            self.logger.error("Entity search failed for query '%s': %s", query, exc)
            raise

        results_for_query = batch_results[0] if batch_results else []
        output: list[dict[str, Any]] = []
        for hit in results_for_query:
            if hit.score < score_threshold:
                continue
            meta = hit.payload.get("metadata", {})
            output.append(
                {
                    "entityId": meta.get("entityId"),
                    "entityType": meta.get("entityType"),
                    "name": meta.get("name", hit.payload.get("page_content", "")),
                    "canonicalName": meta.get("canonicalName"),
                    "aliases": meta.get("aliases") or [],
                    "score": round(hit.score, 4),
                    "connectorIds": hit.payload.get(CONNECTOR_IDS_FIELD) or [],
                    "recordGroupIds": hit.payload.get(RECORD_GROUP_IDS_FIELD) or [],
                }
            )
        return output

    async def find_best_matches(
        self,
        names: list[str],
        org_id: str,
        entity_type: str,
        level: str | None = None,
    ) -> list[dict[str, Any] | None]:
        """The single nearest existing entity for each of ``names``, within
        one org, one entity type and (for subcategories) one level.

        Used by ``app.modules.entity_resolution`` to pick the winner offered
        to the merge-decision model. There is deliberately no score
        threshold: the model decides every pair, so the ranking only has to
        put the best candidate first. Hybrid dense + BM25 with RRF is used
        for that, since lexical near-variants are the common case.

        Returns one entry per input name, ``None`` when the name is blank or
        no point of that type/level exists yet. Raises on a vector DB
        failure so the caller can count it and fall back.
        """
        await self._ensure_initialized()
        cleaned = [(name or "").strip() for name in names]
        results: list[dict[str, Any] | None] = [None] * len(cleaned)
        if not org_id or not entity_type:
            return results
        indices = [i for i, name in enumerate(cleaned) if name]
        if not indices:
            return results

        texts = [cleaned[i] for i in indices]
        dense_vecs = await self._embed(texts)
        sparse_vecs = await self._embed_sparse(texts)

        must_conditions: dict[str, Any] = {
            "metadata.orgId": org_id,
            "metadata.entityType": entity_type,
        }
        if level:
            must_conditions["metadata.level"] = level
        filter_expr = await self.vector_db_service.filter_collection(must=must_conditions)

        from app.services.vector_db.models import FusionMethod, HybridSearchRequest

        requests = [
            HybridSearchRequest(
                dense_query=dense,
                sparse_query=sparse,
                text_query=text,
                filter=filter_expr,
                limit=1,
                fusion_method=FusionMethod.RRF,
                with_payload=True,
            )
            for text, dense, sparse in zip(texts, dense_vecs, sparse_vecs)
        ]
        batch_results = await self.vector_db_service.query_nearest_points(
            collection_name=self.collection_name, requests=requests,
        )

        for position, index in enumerate(indices):
            hits = batch_results[position] if position < len(batch_results) else []
            if not hits:
                continue
            hit = hits[0]
            meta = hit.payload.get("metadata") or {}
            if meta.get("entityType") != entity_type:
                continue
            if (meta.get("level") or None) != (level or None):
                continue
            entity_id = meta.get("entityId")
            if not entity_id:
                continue
            results[index] = {
                "entityId": entity_id,
                "entityType": meta.get("entityType"),
                "name": meta.get("name") or hit.payload.get("page_content") or entity_id,
                "aliases": list(meta.get("aliases") or []),
                "level": meta.get("level"),
                "score": round(hit.score, 4),
            }
        return results
