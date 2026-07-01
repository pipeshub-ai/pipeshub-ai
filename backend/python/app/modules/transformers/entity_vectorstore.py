"""EntityVectorStore — provider-agnostic indexing of knowledge graph entities.

Entities (categories, topics, departments, people, record groups, connectors)
are stored in a dedicated ``entities`` vector collection, separate from the
``records`` document collection.  Each entity point has:

  page_content  = "<name> [| alias1 | alias2] [summary/description]"
  metadata      = EntityRecord.to_vector_payload()

The deterministic point ID is derived from ``orgId:entityType:entityId`` via
UUID5 so that re-upserts are idempotent without a prior delete.

Cross-connector sharing safety
--------------------------------
``metadata.sourceConnectors`` tracks which connector instances reference each
entity.  An entity is only removed from the vector store when *all* source
connectors have been removed AND its ``entityCount`` (number of linked records)
has reached zero.  Call :meth:`remove_connector_reference` from the connector
disconnect handler; it handles the safe-delete logic internally.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, List, Optional

from app.config.constants.arangodb import QdrantCollectionNames
from app.exceptions.indexing_exceptions import EmbeddingError, VectorStoreError
from app.models.entities import EntityRecord, EntityType
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.models import (
    CollectionConfig,
    FieldCondition,
    FilterExpression,
    SearchResult,
    VectorPoint,
)
from app.services.vector_db.sparse_embeddings import SparseEmbedder
from app.utils.aimodels import get_default_embedding_model, get_embedding_model
from app.config.constants.service import config_node_constants

_ENTITIES_COLLECTION = QdrantCollectionNames.ENTITIES.value

_ENTITY_SEARCH_BATCH = 100
_CONFIDENCE_THRESHOLD = 0.0


class EntityVectorStore:
    """Manages embedding and retrieval of knowledge-graph entities in the
    dedicated ``entities`` vector collection.

    Designed to be a singleton per process (DI Singleton provider) so the
    embedding model and sparse embedder are initialised once and reused.
    """

    def __init__(
        self,
        logger,
        config_service,
        vector_db_service: IVectorDBService,
        collection_name: str = _ENTITIES_COLLECTION,
    ) -> None:
        self.logger = logger
        self.config_service = config_service
        self.vector_db_service = vector_db_service
        self.collection_name = collection_name

        self._capabilities = vector_db_service.get_capabilities()
        self._dense_embeddings = None
        self._sparse_embedder: Optional[SparseEmbedder] = None
        self._sparse_lock: Optional[asyncio.Lock] = None
        self._initialized = False
        self._init_lock = asyncio.Lock()

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

    async def _embed(self, texts: List[str]) -> List[List[float]]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._dense_embeddings.embed_documents, texts)

    async def _embed_sparse(self, texts: List[str]):
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
        self, entities: List[EntityRecord], batch_size: int = 64
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

                points: List[VectorPoint] = []
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
        point_id = self._point_id(org_id, entity_type, entity_id)
        try:
            # Delete by the exact point id using an entityId filter
            filter_expr = await self.vector_db_service.filter_collection(
                must={"entityId": entity_id, "orgId": org_id}
            )
            # Remap to metadata-nested keys (provider normalises internally)
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

    async def remove_connector_reference(
        self,
        org_id: str,
        entity_type: str,
        entity_id: str,
        connector_id: str,
    ) -> None:
        """Remove a connector from an entity's sourceConnectors list.

        If sourceConnectors becomes empty **and** entityCount == 0, the entity
        is removed from the vector store.  If entityCount > 0 or other
        connectors still reference it, the point is merely updated.
        """
        await self._ensure_initialized()
        point_id = self._point_id(org_id, entity_type, entity_id)
        # Retrieve the current payload
        try:
            filter_expr = await self.vector_db_service.filter_collection(
                must={"metadata.entityId": entity_id, "metadata.orgId": org_id}
            )
            from app.services.vector_db.models import ScrollResult
            result: ScrollResult = await self.vector_db_service.scroll(
                collection_name=self.collection_name,
                scroll_filter=filter_expr,
                limit=1,
            )
        except Exception as exc:
            self.logger.error("Failed to fetch entity %s for connector removal: %s", entity_id, exc)
            return

        if not result.points:
            self.logger.debug("Entity %s not found in vector store; skipping removal", entity_id)
            return

        point = result.points[0]
        metadata: Dict[str, Any] = point.payload.get("metadata", {})

        source_connectors: List[str] = list(metadata.get("sourceConnectors", []))
        if connector_id in source_connectors:
            source_connectors.remove(connector_id)

        entity_count: int = int(metadata.get("entityCount", 0))

        if not source_connectors and entity_count == 0:
            await self.delete_entity(org_id, entity_type, entity_id)
            self.logger.info(
                "Removed entity %s/%s: last connector disconnected and no linked records.",
                entity_type,
                entity_id,
            )
            return

        # Update in place — overwrite just the metadata field
        updated_metadata = {**metadata, "sourceConnectors": source_connectors}
        try:
            update_filter = await self.vector_db_service.filter_collection(
                must={"metadata.entityId": entity_id, "metadata.orgId": org_id}
            )
            await self.vector_db_service.overwrite_payload(
                collection_name=self.collection_name,
                payload={"metadata": updated_metadata},
                points=update_filter,
            )
        except Exception as exc:
            self.logger.error(
                "Failed to update sourceConnectors for entity %s: %s", entity_id, exc
            )

    # ------------------------------------------------------------------
    # Public API — search
    # ------------------------------------------------------------------

    async def search_entities(
        self,
        query: str,
        org_id: str,
        entity_types: Optional[List[str]] = None,
        top_k: int = 10,
        score_threshold: float = _CONFIDENCE_THRESHOLD,
    ) -> List[Dict[str, Any]]:
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
        must_conditions: Dict[str, Any] = {"metadata.orgId": org_id}
        if entity_types:
            must_conditions["metadata.entityType"] = entity_types  # list → "any of" filter

        filter_expr = await self.vector_db_service.filter_collection(
            must=must_conditions
        )

        from app.services.vector_db.models import HybridSearchRequest, FusionMethod

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
            batch_results: List[List[SearchResult]] = (
                await self.vector_db_service.query_nearest_points(
                    collection_name=self.collection_name,
                    requests=[request],
                )
            )
        except Exception as exc:
            self.logger.error("Entity search failed for query '%s': %s", query, exc)
            return []

        results_for_query = batch_results[0] if batch_results else []
        output: List[Dict[str, Any]] = []
        for hit in results_for_query:
            if hit.score < score_threshold:
                continue
            meta = hit.payload.get("metadata", {})
            output.append(
                {
                    "entityId": meta.get("entityId"),
                    "entityType": meta.get("entityType"),
                    "name": meta.get("name", hit.payload.get("page_content", "")),
                    "score": round(hit.score, 4),
                    "parentEntityId": meta.get("parentEntityId"),
                    "parentEntityType": meta.get("parentEntityType"),
                }
            )
        return output

    async def remove_all_connector_references(
        self,
        org_id: str,
        connector_id: str,
        batch_size: int = 100,
    ) -> None:
        """Remove *connector_id* from every entity belonging to *org_id*.

        Called when a connector is disconnected.  For each entity that had
        *connector_id* in its ``sourceConnectors`` list, the reference is
        removed.  Entities whose ``sourceConnectors`` becomes empty **and**
        whose ``entityCount`` is zero are permanently deleted; entities that
        are still referenced by other connectors are merely updated in place.
        """
        await self._ensure_initialized()

        from app.services.vector_db.models import ScrollResult

        offset: str | None = None
        removed_count = 0
        deleted_count = 0

        while True:
            try:
                filter_expr = await self.vector_db_service.filter_collection(
                    must={"metadata.orgId": org_id}
                )
                result: ScrollResult = await self.vector_db_service.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=filter_expr,
                    limit=batch_size,
                    offset=offset,
                    with_payload=True,
                )
            except Exception as exc:
                self.logger.error(
                    "Scroll failed during connector reference removal (org=%s connector=%s): %s",
                    org_id, connector_id, exc,
                )
                break

            if not result.points:
                break

            for point in result.points:
                meta: Dict[str, Any] = point.payload.get("metadata", {})
                source_connectors: List[str] = list(meta.get("sourceConnectors") or [])
                if connector_id not in source_connectors:
                    continue

                entity_id: str = meta.get("entityId", "")
                entity_type: str = meta.get("entityType", "")
                if not entity_id or not entity_type:
                    continue

                removed_count += 1
                updated_connectors = [c for c in source_connectors if c != connector_id]
                entity_count: int = int(meta.get("entityCount", 0))

                if not updated_connectors and entity_count == 0:
                    await self.delete_entity(org_id, entity_type, entity_id)
                    deleted_count += 1
                    self.logger.debug(
                        "Deleted entity %s/%s (last connector removed, entityCount=0)",
                        entity_type, entity_id,
                    )
                else:
                    updated_meta = {**meta, "sourceConnectors": updated_connectors}
                    try:
                        update_filter = await self.vector_db_service.filter_collection(
                            must={"metadata.entityId": entity_id, "metadata.orgId": org_id}
                        )
                        await self.vector_db_service.overwrite_payload(
                            collection_name=self.collection_name,
                            payload={"metadata": updated_meta},
                            points=update_filter,
                        )
                    except Exception as exc:
                        self.logger.error(
                            "Failed to update sourceConnectors for entity %s: %s",
                            entity_id, exc,
                        )

            offset = result.next_offset
            if offset is None:
                break

        self.logger.info(
            "Connector reference removal complete | org=%s connector=%s | "
            "entities_updated=%d entities_deleted=%d",
            org_id, connector_id, removed_count - deleted_count, deleted_count,
        )

    # ------------------------------------------------------------------
    # Public API — sync helpers
    # ------------------------------------------------------------------

    async def sync_entities_from_metadata(
        self,
        org_id: str,
        new_entities: List[EntityRecord],
    ) -> None:
        """Called from SinkOrchestrator.enrich() with entities discovered during
        a GraphDBTransformer run.  Entities that already exist are upserted
        (idempotent due to UUID5 IDs); genuinely new entities get a fresh point.
        """
        if not new_entities:
            return
        await self.upsert_entities_batch(new_entities)
        self.logger.info(
            "Synced %d entities to vector store for org %s", len(new_entities), org_id
        )
