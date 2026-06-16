"""
Shared integration contract tests for all vector DB providers.

Requires: docker compose -f deployment/docker-compose/docker-compose.integration.vector-db.yml up -d
Run: pytest tests/integration/vector_db/test_contract_integration.py -m integration --timeout=120
"""

import pytest

from app.services.vector_db.models import FilterExpression, HybridSearchRequest, VectorPoint
from tests.integration.vector_db.conftest import make_collection
from tests.integration.vector_db.helpers import DIM, make_collection_config, make_dense, org_filter, sample_points

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(params=["qdrant", "opensearch", "redis"])
async def vector_service(request):
    """Yield the connected provider service for contract integration tests."""
    return await request.getfixturevalue(f"{request.param}_service")


class TestVectorDBContractIntegration:
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
            many = []
            for i in range(12):
                many.append(
                    VectorPoint(
                        id=f"pt-{i}",
                        dense_vector=make_dense([float(i % DIM)]),
                        payload={
                            "page_content": f"chunk {i}",
                            "metadata": {"orgId": "org-scroll", "virtualRecordId": "vr-scroll"},
                        },
                    )
                )
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
