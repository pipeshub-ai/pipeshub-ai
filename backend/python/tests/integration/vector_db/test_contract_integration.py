"""
Shared integration contract tests for all vector DB providers.

Requires: docker compose -f deployment/docker-compose/docker-compose.integration.vector-db.yml up -d
Run: pytest tests/integration/vector_db/test_contract_integration.py -m integration --timeout=120
"""

import pytest

from app.services.vector_db.models import HybridSearchRequest, VectorPoint
from tests.integration.vector_db.conftest import make_collection
from tests.integration.vector_db.helpers import DIM, make_collection_config, make_dense, sample_points

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class _VectorDBContractTests:
    """Provider-agnostic contract. Subclasses inject `vector_service` from a
    module-scoped fixture (qdrant_service / opensearch_service / redis_service)
    so pytest-asyncio never has to `getfixturevalue` a module fixture from a
    running test loop.
    """

    async def test_health_check_passes(self, vector_service):
        from app.services.vector_db.models import HealthStatus

        health = await vector_service.health_check()
        assert health.status in (HealthStatus.HEALTHY, HealthStatus.DEGRADED)

    async def test_upsert_and_dense_query(self, vector_service):
        provider = vector_service.get_service_name()
        col = make_collection(provider)
        cfg = make_collection_config()
        try:
            await vector_service.create_collection(col, cfg)
            points = sample_points("org-contract")
            await vector_service.upsert_points(col, points)
            flt = await vector_service.filter_collection(must={"orgId": "org-contract"})
            results = await vector_service.query_nearest_points(
                col,
                [HybridSearchRequest(dense_query=make_dense([1.0]), filter=flt, limit=3)],
            )
            assert len(results[0]) >= 1
        finally:
            await vector_service.delete_collection(col)

    async def test_delete_points(self, vector_service):
        provider = vector_service.get_service_name()
        col = make_collection(f"{provider}_del")
        cfg = make_collection_config()
        try:
            await vector_service.create_collection(col, cfg)
            await vector_service.upsert_points(col, sample_points("org-del"))
            flt = await vector_service.filter_collection(must={"orgId": "org-del"})
            await vector_service.delete_points(col, flt)
            info = await vector_service.get_collection_info(col)
            assert info.points_count == 0 or info.points_count is None
        finally:
            await vector_service.delete_collection(col)

    async def test_scroll_pagination(self, vector_service):
        provider = vector_service.get_service_name()
        col = make_collection(f"{provider}_scroll")
        cfg = make_collection_config()
        try:
            await vector_service.create_collection(col, cfg)
            many = [
                VectorPoint(
                    id=f"pt-{i}",
                    dense_vector=make_dense([float(i % DIM)]),
                    payload={
                        "page_content": f"chunk {i}",
                        "metadata": {"orgId": "org-scroll", "virtualRecordId": "vr-scroll"},
                    },
                )
                for i in range(12)
            ]
            await vector_service.upsert_points(col, many)
            flt = await vector_service.filter_collection(must={"orgId": "org-scroll"})
            collected = []
            offset = None
            while True:
                page = await vector_service.scroll(col, flt, limit=5, offset=offset)
                collected.extend(page.points)
                if not page.next_offset:
                    break
                offset = page.next_offset
            assert len({p.id for p in collected}) == 12
        finally:
            await vector_service.delete_collection(col)


class TestQdrantContract(_VectorDBContractTests):
    @pytest.fixture
    async def vector_service(self, qdrant_service):
        return qdrant_service


class TestOpenSearchContract(_VectorDBContractTests):
    @pytest.fixture
    async def vector_service(self, opensearch_service):
        return opensearch_service


class TestRedisContract(_VectorDBContractTests):
    @pytest.fixture
    async def vector_service(self, redis_service):
        return redis_service
