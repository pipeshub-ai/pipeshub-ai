import hashlib
import json
import logging
import uuid
from typing import Optional

from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.models import (
    FilterExpression,
    HybridSearchRequest,
    VectorPoint,
    FieldCondition
)
from app.services.vector_db.collections import CollectionType

logger = logging.getLogger(__name__)

class SemanticCacheService:
    def __init__(
        self,
        vector_db_service: IVectorDBService,
    ):
        self.vector_db = vector_db_service
        self.collection_name = CollectionType.SEMANTIC_CACHE.value
        self.threshold = 0.95

    async def initialize(self) -> None:
        exists = await self.vector_db.collection_exists(self.collection_name)
        if not exists:
            await self.vector_db.create_collection(self.collection_name)

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
        filters_hash: str
    ) -> None:
        try:
            point = VectorPoint(
                id=str(uuid.uuid4()),
                dense_vector=embedding,
                payload={
                    "query_text": query,
                    "response_text": response_text,
                    "filters_hash": filters_hash,
                }
            )
            await self.vector_db.upsert_points(self.collection_name, [point])
            logger.info("Saved response to semantic cache.")
        except Exception as e:
            logger.error(f"Error writing to semantic cache: {e}", exc_info=True)

def hash_filters(filters: dict | None) -> str:
    if not filters:
        return "none"
    return hashlib.sha256(json.dumps(filters, sort_keys=True).encode()).hexdigest()
