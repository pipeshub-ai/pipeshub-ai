"""Parametrized contract tests for IVectorDBService implementations.

Provider-specific behaviour (Qdrant MinShould, OpenSearch pipelines, Redis
FT.HYBRID) stays in the per-provider test modules.  This file covers the
shared interface contract that all three providers must honour.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.vector_db.models import FilterExpression, VectorPoint

pytest.importorskip("qdrant_client", reason="qdrant_client not installed")


def _make_connected_qdrant():
    from app.services.vector_db.qdrant.config import QdrantConfig
    from app.services.vector_db.qdrant.qdrant import QdrantService

    svc = QdrantService(
        QdrantConfig("localhost", 6333, "", True, False, 180)
    )
    svc.client = AsyncMock()
    return svc


def _make_connected_opensearch():
    pytest.importorskip("opensearchpy", reason="opensearch-py not installed")
    from app.services.vector_db.opensearch.config import OpenSearchConfig
    from app.services.vector_db.opensearch.opensearch import OpenSearchService

    svc = OpenSearchService(OpenSearchConfig())
    svc.client = AsyncMock()
    return svc


def _make_connected_redis():
    pytest.importorskip("redis", reason="redis package not installed")
    from app.services.vector_db.redis.config import RedisVectorConfig
    from app.services.vector_db.redis.redis_vector import RedisVectorService

    svc = RedisVectorService(RedisVectorConfig())
    mock_client = AsyncMock()
    mock_pipeline = MagicMock()
    mock_pipeline.execute_command = MagicMock()
    mock_pipeline.execute = AsyncMock(return_value=[b"OK"])
    mock_client.pipeline = MagicMock(return_value=mock_pipeline)
    svc.client = mock_client
    return svc


def _available_providers():
    providers = ["qdrant"]
    try:
        __import__("opensearchpy")
        providers.append("opensearch")
    except ImportError:
        pass
    try:
        __import__("redis")
        providers.append("redis")
    except ImportError:
        pass
    return providers


PROVIDER_FACTORIES = {
    "qdrant": _make_connected_qdrant,
    "opensearch": _make_connected_opensearch,
    "redis": _make_connected_redis,
}


@pytest.fixture(params=_available_providers())
def connected_service(request):
    return PROVIDER_FACTORIES[request.param]()


class TestDeletePointsContract:
    @pytest.mark.asyncio
    async def test_empty_filter_raises(self, connected_service):
        with pytest.raises(ValueError, match="empty"):
            await connected_service.delete_points("records", FilterExpression())


class TestFilterCollectionContract:
    @pytest.mark.asyncio
    async def test_must_filter_builds_conditions(self, connected_service):
        expr = await connected_service.filter_collection(
            must={"orgId": "org-1", "virtualRecordId": "vr-1"}
        )
        assert len(expr.must) == 2

    @pytest.mark.asyncio
    async def test_invalid_mode_raises(self, connected_service):
        with pytest.raises(ValueError, match="Invalid mode"):
            await connected_service.filter_collection(filter_mode="bogus", orgId="x")


class TestUpsertPointsContract:
    @pytest.mark.asyncio
    async def test_upsert_delegates_to_client(self, connected_service):
        points = [
            VectorPoint(id="p1", dense_vector=[0.1, 0.2], payload={"page_content": "hi"})
        ]
        await connected_service.upsert_points("records", points)
        # Each provider calls its underlying client — just verify no exception


class TestServiceMetadataContract:
    def test_service_name_is_string(self, connected_service):
        assert isinstance(connected_service.get_service_name(), str)

    def test_capabilities_exposed(self, connected_service):
        caps = connected_service.get_capabilities()
        assert caps.supported_fusion_methods
