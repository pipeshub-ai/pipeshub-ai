"""The schema a Redis vector index holds in the provider's unit tests, whose client is a mock with no FT.INFO."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

if TYPE_CHECKING:
    from app.services.vector_db.redis.redis_vector import RedisVectorService

INDEXED_FIELDS = frozenset({
    "page_content", "dense_embedding",
    "metadata_orgId", "metadata_virtualRecordId", "metadata_blockId", "metadata_contentRev", "metadata_embedKey",
    "connectorIds", "recordGroupIds", "rootRecordGroupIds",
    "pathIds", "aclScope", "scopeStamp", "scopeFieldsVersion", "scopeGeneration", "scopeFence",
})


def with_schema(svc: RedisVectorService, fields: frozenset[str] = INDEXED_FIELDS) -> RedisVectorService:
    """Answer the provider's schema lookup from ``fields`` instead of an FT.INFO the mock cannot serve."""
    svc._indexed_fields = AsyncMock(return_value=fields)  # type: ignore[method-assign]
    svc._require_settled = AsyncMock(return_value=False)  # type: ignore[method-assign]  # settled, no wait
    return svc
