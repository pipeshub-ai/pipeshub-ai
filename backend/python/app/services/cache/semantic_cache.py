import hashlib
import json
import logging
import time
import uuid
from typing import Optional

from pydantic import BaseModel

from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.models import (
    FilterExpression,
    HybridSearchRequest,
    VectorPoint,
    FieldCondition,
    CollectionConfig
)
from app.services.vector_db.collections import CollectionType


class SemanticCacheScope(BaseModel):
    orgId: str
    userId: str
    permissionsRevision: str
    corpusRevision: str
    filters: dict | None = None


logger = logging.getLogger(__name__)


class SemanticCacheService:
    def __init__(
        self,
        vector_db_service: IVectorDBService,
    ):
        self.vector_db = vector_db_service
        self.collection_name = CollectionType.SEMANTIC_CACHE.value
        self.threshold = 0.95
        self._initialized = False

    async def initialize(self, embedding_dimension: int) -> None:
        if self._initialized:
            return

        exists = await self.vector_db.collection_exists(self.collection_name)
        if not exists:
            config = CollectionConfig(embedding_size=embedding_dimension)
            await self.vector_db.create_collection(self.collection_name, config)

        # Index on the nested metadata field so filters survive OpenSearch
        # document conversion (top-level payload fields are discarded by the
        # vector_point_to_document adapter; only metadata.* survives).
        await self.vector_db.create_index(
            self.collection_name, "metadata.filters_hash", {"type": "keyword"}
        )

        # Migration: apply non-indexed mapping for large stored-text fields on
        # existing collections.  create_collection only runs when the index is
        # first created, so collections that pre-date this change would still
        # have query_text/response_text mapped as keyword by the dynamic
        # template and risk failing on answers > 32,766 bytes.
        #
        # put_mapping is additive and idempotent in OpenSearch: adding index:false
        # to an existing text field does not require a reindex — only newly
        # indexed documents are affected; existing _source values stay retrievable.
        #
        # If the collection was just created above, this call is a harmless no-op
        # because the explicit static properties already set index:false.
        await self.vector_db.create_index(
            self.collection_name,
            "metadata.query_text",
            {"type": "text", "index": False},
        )
        await self.vector_db.create_index(
            self.collection_name,
            "metadata.response_text",
            {"type": "text", "index": False},
        )
        self._initialized = True

    async def get_cached_response(
        self,
        query: str,
        embedding: list[float],
        filters_hash: str
    ) -> Optional[str]:
        try:
            req = HybridSearchRequest(
                dense_query=embedding,
                filter=FilterExpression(
                    # Use metadata.filters_hash because OpenSearch stores custom
                    # cache fields inside the metadata sub-document.
                    must=[FieldCondition(key="metadata.filters_hash", value=filters_hash)]
                ),
                limit=1,
                with_payload=True
            )
            results = await self.vector_db.query_nearest_points(self.collection_name, [req])
            if results and results[0]:
                top_match = results[0][0]
                if top_match.score >= self.threshold:
                    logger.info(f"Semantic cache hit! Score: {top_match.score}")
                    # response_text lives in metadata.response_text after the
                    # OpenSearch round-trip (hit_to_search_result rebuilds payload
                    # as {"metadata": {...}, "page_content": ...}).
                    return top_match.payload.get("metadata", {}).get("response_text")
        except Exception as e:
            logger.error(f"Error reading from semantic cache: {e}", exc_info=True)
        return None

    async def set_cached_response(
        self,
        query: str,
        response_text: str,
        embedding: list[float],
        filters_hash: str,
        org_id: str,
        corpus_revision: str,
    ) -> None:
        try:
            # Store all cache-specific fields inside the `metadata` sub-document
            # so they survive the OpenSearch adapter's vector_point_to_document /
            # hit_to_search_result round-trip.  Top-level payload fields (other
            # than page_content / connectorIds / recordGroupIds) are dropped by
            # the adapter.
            point = VectorPoint(
                id=str(uuid.uuid4()),
                dense_vector=embedding,
                payload={
                    "page_content": query,
                    "metadata": {
                        "query_text": query,
                        "response_text": response_text,
                        "filters_hash": filters_hash,
                        "orgId": org_id,
                        "corpusRevision": corpus_revision,
                        "createdAt": int(time.time()),
                    },
                }
            )
            await self.vector_db.upsert_points(self.collection_name, [point])
            logger.info("Saved response to semantic cache.")

            # Fire-and-forget stale entry purge
            import asyncio
            asyncio.create_task(self.purge_stale_entries(org_id, corpus_revision))
        except Exception as e:
            logger.error(f"Error writing to semantic cache: {e}", exc_info=True)

    async def purge_stale_entries(self, org_id: str, current_revision: str) -> None:
        try:
            filter_expr = FilterExpression(
                must=[FieldCondition(key="metadata.orgId", value=org_id)],
                must_not=[FieldCondition(key="metadata.corpusRevision", value=current_revision)]
            )
            await self.vector_db.delete_points(self.collection_name, filter_expr)
            logger.debug(f"Purged stale semantic cache entries for org {org_id}")
        except Exception as e:
            logger.warning(f"Failed to purge stale semantic cache entries: {e}")


def hash_filters(scope: SemanticCacheScope) -> str:
    """Produce a stable SHA-256 hex digest for a SemanticCacheScope.

    Keys are sorted recursively (via json.dumps sort_keys=True) so that
    equivalent filter dicts with different insertion orders hash identically.
    None values are excluded via model_dump's exclude_none so they do not
    contribute to the hash.
    """
    canonical = json.dumps(
        scope.model_dump(exclude_none=True),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
