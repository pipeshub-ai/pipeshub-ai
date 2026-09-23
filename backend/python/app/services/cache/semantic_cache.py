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
    corpusRevision: int
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
            
        await self.vector_db.create_index(self.collection_name, "filters_hash", {"type": "keyword"})
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
                    must=[FieldCondition(key="filters_hash", value=filters_hash)]
                ),
                limit=1,
                with_payload=True
            )
            results = await self.vector_db.query_nearest_points(self.collection_name, [req])
            if results and results[0]:
                top_match = results[0][0]
                if top_match.score >= self.threshold:
                    logger.info(f"Semantic cache hit! Score: {top_match.score}")
                    return top_match.payload.get("response_text")
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
        corpus_revision: int
    ) -> None:
        try:
            point = VectorPoint(
                id=str(uuid.uuid4()),
                dense_vector=embedding,
                payload={
                    "query_text": query,
                    "response_text": response_text,
                    "filters_hash": filters_hash,
                    "orgId": org_id,
                    "corpusRevision": corpus_revision,
                    "createdAt": int(time.time()),
                }
            )
            await self.vector_db.upsert_points(self.collection_name, [point])
            logger.info("Saved response to semantic cache.")
            
            # Fire-and-forget stale entry purge
            import asyncio
            asyncio.create_task(self.purge_stale_entries(org_id, corpus_revision))
        except Exception as e:
            logger.error(f"Error writing to semantic cache: {e}", exc_info=True)

    async def purge_stale_entries(self, org_id: str, current_revision: int) -> None:
        try:
            filter_expr = FilterExpression(
                must=[FieldCondition(key="orgId", value=org_id)],
                must_not=[FieldCondition(key="corpusRevision", value=current_revision)]
            )
            await self.vector_db.delete_points(self.collection_name, filter_expr)
            logger.debug(f"Purged stale semantic cache entries for org {org_id}")
        except Exception as e:
            logger.warning(f"Failed to purge stale semantic cache entries: {e}")

def hash_filters(scope: SemanticCacheScope) -> str:
    return hashlib.sha256(scope.model_dump_json(exclude_none=True).encode()).hexdigest()
