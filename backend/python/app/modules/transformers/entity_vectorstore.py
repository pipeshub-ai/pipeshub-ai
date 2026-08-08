"""EntityVectorStore — provider-agnostic indexing of knowledge graph entities.

Entities (categories, topics, departments, people, record groups, connectors)
are stored in a dedicated ``entities`` vector collection, separate from the
``records`` document collection.  Each entity point has:

  page_content  = "<name> [| alias1 | alias2] [description]"
  metadata      = EntityRecord.to_vector_payload()  (slim — see models.entities)

The deterministic point ID is derived from ``orgId:entityType:entityId`` via
UUID5 so that re-upserts are idempotent without a prior delete.

Reference counting / provenance (which connectors reference an entity, how
many records link to it) is owned by the graph DB, not this vector index —
this store is a disposable search projection that can always be rebuilt from
the graph via ``get_entities_for_sync``. Connector-disconnect cleanup here is
therefore a single filtered delete (``metadata.connectorId`` + ``metadata.orgId``),
not a full-collection scroll.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import QdrantCollectionNames
from app.config.constants.service import config_node_constants
from app.exceptions.indexing_exceptions import VectorStoreError
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

# How long a (org, type, id) tuple is considered "freshly synced" and skipped
# by the opportunistic per-record sync path (see ``sync_entities_from_metadata``
# / ``sync_entity_if_stale``). Shared entities like a category or record group
# get referenced by many records in quick succession; without this window each
# reference would re-embed identical text. Explicit repair via
# ``upsert_entity``/``upsert_entities_batch`` (used by entity-sync/trigger)
# always bypasses this cache. Trade-off: a rename picked up mid-window is
# delayed up to this TTL on the opportunistic path — acceptable since the
# admin repair endpoint forces an immediate refresh.
_DEDUP_TTL_SECONDS = 3600
_DEDUP_CACHE_MAX_ENTRIES = 50_000


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

        # Opportunistic-sync dedup cache; see ``_DEDUP_TTL_SECONDS``.
        self._recent_sync_cache: dict[str, float] = {}
        self._recent_sync_lock = asyncio.Lock()

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
        # Create filterable indexes for the fields we query on
        for field, schema in [
            ("metadata.orgId", {"type": "keyword"}),
            ("metadata.entityType", {"type": "keyword"}),
            ("metadata.entityId", {"type": "keyword"}),
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
        self, entities: list[EntityRecord], batch_size: int = 64
    ) -> None:
        """Batch-embed and upsert a list of EntityRecord objects.

        Failures within a batch are logged and skipped rather than aborting
        the entire batch (partial-failure tolerance).
        """
        await self._ensure_initialized()
        if not entities:
            return

        for start in range(0, len(entities), batch_size):
            batch = entities[start : start + batch_size]
            try:
                texts = [e.embedding_text for e in batch]
                dense_vecs = await self._embed(texts)
                sparse_vecs = await self._embed_sparse(texts)

                points: list[VectorPoint] = []
                for entity, dense, sparse in zip(batch, dense_vecs, sparse_vecs):
                    if not entity.name.strip():
                        self.logger.warning(
                            "Skipping entity with empty name: %s / %s",
                            entity.entity_type,
                            entity.entity_id,
                        )
                        continue
                    point_id = self._point_id(
                        entity.org_id, entity.entity_type.value, entity.entity_id
                    )
                    payload = {
                        "page_content": entity.embedding_text,
                        "metadata": entity.to_vector_payload(),
                    }
                    points.append(
                        VectorPoint(
                            id=point_id,
                            dense_vector=dense,
                            sparse_vector=sparse,
                            payload=payload,
                        )
                    )

                if points:
                    await self.vector_db_service.upsert_points(
                        collection_name=self.collection_name, points=points
                    )
                    self.logger.debug(
                        "Upserted %d entity points (batch start=%d)", len(points), start
                    )
            except Exception as exc:
                self.logger.error(
                    "Failed to upsert entity batch starting at %d: %s", start, exc
                )

    # ------------------------------------------------------------------
    # Public API — delete
    # ------------------------------------------------------------------

    async def delete_entity(self, org_id: str, entity_type: str, entity_id: str) -> None:
        """Delete a single entity point from the collection."""
        await self._ensure_initialized()
        try:
            filter_expr = await self.vector_db_service.filter_collection(
                must={"metadata.entityId": entity_id, "metadata.orgId": org_id}
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
        """Remove every entity point scoped to *connector_id* within *org_id*.

        A single server-side filtered delete — entities that are NOT scoped to
        a specific connector (``connectorId`` unset, e.g. cross-connector
        taxonomy entities) are untouched. This intentionally does not scroll
        the collection; the graph DB remains the source of truth for whether a
        connector-scoped entity should still exist, and a full re-sync via
        ``sync_entities_from_metadata``/``get_entities_for_sync`` will restore
        anything still valid.
        """
        await self._ensure_initialized()
        try:
            filter_expr = await self.vector_db_service.filter_collection(
                must={"metadata.orgId": org_id, "metadata.connectorId": connector_id}
            )
            await self.vector_db_service.delete_points(self.collection_name, filter_expr)
            self.logger.info(
                "Deleted connector-scoped entities | org=%s connector=%s",
                org_id, connector_id,
            )
        except Exception as exc:
            self.logger.error(
                "Failed to delete connector-scoped entities (org=%s connector=%s): %s",
                org_id, connector_id, exc,
            )

    # ------------------------------------------------------------------
    # Public API — search
    # ------------------------------------------------------------------

    async def search_entities(
        self,
        query: str,
        org_id: str,
        entity_types: list[str] | None = None,
        top_k: int = 10,
        score_threshold: float = _CONFIDENCE_THRESHOLD,
    ) -> list[dict[str, Any]]:
        """Semantically search for entities matching *query*.

        Returns a list of dicts:
            {entityId, entityType, name, score, parentEntityId, parentEntityType}

        The caller (resolve_entity_filters tool) formats these for the agent.
        """
        await self._ensure_initialized()

        if not query.strip():
            return []

        loop = asyncio.get_running_loop()
        dense_vec = await loop.run_in_executor(
            None, self._dense_embeddings.embed_query, query
        )

        sparse_vec = None
        if self._sparse_embedder:
            sparse_results = await self._sparse_embedder.embed_documents([query])
            sparse_vec = sparse_results[0] if sparse_results else None

        # Build filter: always scope to org; optionally restrict entity types
        must_conditions: dict[str, Any] = {"metadata.orgId": org_id}
        if entity_types:
            must_conditions["metadata.entityType"] = entity_types  # list → "any of" filter

        filter_expr = await self.vector_db_service.filter_collection(
            must=must_conditions
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
            return []

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
                    "parentEntityId": meta.get("parentEntityId"),
                    "parentEntityType": meta.get("parentEntityType"),
                }
            )
        return output

    # ------------------------------------------------------------------
    # Public API — sync helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _dedup_key(org_id: str, entity_type: str, entity_id: str) -> str:
        return f"{org_id}:{entity_type}:{entity_id}"

    async def _filter_freshly_synced(
        self, entities: list[EntityRecord]
    ) -> list[EntityRecord]:
        """Drop entities that were opportunistically synced within the dedup
        TTL, so repeated references from many records don't each re-embed
        identical text. See ``_DEDUP_TTL_SECONDS``.
        """
        now = asyncio.get_running_loop().time()
        fresh: list[EntityRecord] = []
        async with self._recent_sync_lock:
            if len(self._recent_sync_cache) > _DEDUP_CACHE_MAX_ENTRIES:
                self._recent_sync_cache = {
                    key: seen_at
                    for key, seen_at in self._recent_sync_cache.items()
                    if now - seen_at < _DEDUP_TTL_SECONDS
                }
            for entity in entities:
                key = self._dedup_key(
                    entity.org_id, entity.entity_type.value, entity.entity_id
                )
                seen_at = self._recent_sync_cache.get(key)
                if seen_at is not None and now - seen_at < _DEDUP_TTL_SECONDS:
                    continue
                self._recent_sync_cache[key] = now
                fresh.append(entity)
        return fresh

    async def sync_entities_from_metadata(
        self,
        org_id: str,
        new_entities: list[EntityRecord],
    ) -> None:
        """Called from SinkOrchestrator.enrich() with entities discovered during
        a GraphDBTransformer run.  Entities that already exist are upserted
        (idempotent due to UUID5 IDs); genuinely new entities get a fresh point.

        Skips entities synced within the dedup TTL (see ``_DEDUP_TTL_SECONDS``)
        since the same shared taxonomy node is typically touched by many
        records in quick succession.
        """
        if not new_entities:
            return
        fresh = await self._filter_freshly_synced(new_entities)
        if not fresh:
            return
        await self.upsert_entities_batch(fresh)
        self.logger.info(
            "Synced %d entities to vector store for org %s (%d skipped, freshly synced)",
            len(fresh), org_id, len(new_entities) - len(fresh),
        )

    async def sync_entity_if_stale(self, entity: EntityRecord) -> None:
        """Single-entity variant of ``sync_entities_from_metadata`` for call
        sites that discover one shared entity at a time (e.g. a record's
        RecordGroup) rather than a batch.
        """
        fresh = await self._filter_freshly_synced([entity])
        if fresh:
            await self.upsert_entities_batch(fresh)

    # ------------------------------------------------------------------
    # Public API — status
    # ------------------------------------------------------------------

    async def count_org_entities(self, org_id: str, page_cap: int = 20, page_size: int = 100) -> dict[str, Any]:
        """Bounded, org-scoped count for status/health reporting.

        Deliberately does not expose ``get_collection_info().points_count``
        (a global, cross-tenant total) to callers scoped to a single org.
        Scrolls up to ``page_cap`` pages of ``page_size`` so the call stays
        cheap even on very large collections; ``is_estimate`` signals when the
        cap was hit before exhausting the org's points.
        """
        await self._ensure_initialized()
        filter_expr = await self.vector_db_service.filter_collection(
            must={"metadata.orgId": org_id}
        )
        count = 0
        offset: str | None = None
        for _ in range(page_cap):
            result = await self.vector_db_service.scroll(
                collection_name=self.collection_name,
                scroll_filter=filter_expr,
                limit=page_size,
                offset=offset,
                with_payload=False,
            )
            count += len(result.points)
            offset = result.next_offset
            if offset is None:
                return {"count": count, "is_estimate": False}
        return {"count": count, "is_estimate": True}
