"""
Shared integration contract tests for all vector DB providers.

Requires: docker compose -f deployment/docker-compose/docker-compose.integration.vector-db.yml up -d
Run: pytest tests/integration/vector_db/test_contract_integration.py -m integration --timeout=120
"""

import pytest

from app.services.vector_db.models import HybridSearchRequest, VectorPoint
from tests.integration.vector_db.conftest import make_collection
from tests.integration.vector_db.helpers import (
    DIM,
    make_collection_config,
    make_dense,
    point_id,
    sample_points,
    wait_for,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

# OpenSearch indexes are created with a 30s refresh interval, so a write can
# take that long to become searchable; the other backends answer at once.
VISIBILITY_TIMEOUT = 45.0


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

            async def query() -> list:
                return (await vector_service.query_nearest_points(
                    col,
                    [HybridSearchRequest(dense_query=make_dense([1.0]), filter=flt, limit=3)],
                ))[0]

            results = await wait_for(query, timeout=VISIBILITY_TIMEOUT)
            assert len(results) >= 1
        finally:
            await vector_service.delete_collection(col)

    async def test_delete_points(self, vector_service):
        provider = vector_service.get_service_name()
        col = make_collection(f"{provider}_del")
        cfg = make_collection_config()
        try:
            await vector_service.create_collection(col, cfg)
            points = sample_points("org-del")
            await vector_service.upsert_points(col, points)
            flt = await vector_service.filter_collection(must={"orgId": "org-del"})

            async def points_count_is(n: int) -> bool:
                return (await vector_service.get_collection_info(col)).points_count == n

            # Deleting before the points are searchable would match nothing on
            # OpenSearch and the zero count below would prove nothing.
            await wait_for(lambda: points_count_is(len(points)), timeout=VISIBILITY_TIMEOUT)
            await vector_service.delete_points(col, flt)
            await wait_for(lambda: points_count_is(0), timeout=VISIBILITY_TIMEOUT)
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
                    id=point_id(f"pt-{i}"),
                    # Never all zeros: cosine is undefined for a zero vector
                    # and OpenSearch rejects the document.
                    dense_vector=make_dense([float(i % DIM) + 1.0]),
                    payload={
                        "page_content": f"chunk {i}",
                        "metadata": {"orgId": "org-scroll", "virtualRecordId": "vr-scroll"},
                    },
                )
                for i in range(12)
            ]
            await vector_service.upsert_points(col, many)
            flt = await vector_service.filter_collection(must={"orgId": "org-scroll"})

            async def scroll_all_ids() -> set | None:
                collected = []
                offset = None
                while True:
                    page = await vector_service.scroll(col, flt, limit=5, offset=offset)
                    collected.extend(page.points)
                    if not page.next_offset:
                        break
                    offset = page.next_offset
                ids = {p.id for p in collected}
                return ids if len(ids) == 12 else None

            assert len(await wait_for(scroll_all_ids, timeout=VISIBILITY_TIMEOUT)) == 12
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
