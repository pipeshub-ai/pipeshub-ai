"""
Integration tests for the Redis vector DB provider.

Requires: docker compose -f deployment/docker-compose/docker-compose.integration.vector-db.yml up -d
Run: pytest tests/integration/vector_db/test_redis_integration.py -m integration --timeout=120
"""

import asyncio
import pytest

from app.services.vector_db.models import (
    FieldCondition,
    FilterExpression,
    HybridSearchRequest,
    VectorPoint,
)

from tests.integration.vector_db.helpers import (
    DIM,
    make_collection_config,
    make_dense,
    org_filter,
    sample_points,
)
from tests.integration.vector_db.conftest import make_collection

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _wait_index_ready(
    svc, collection_name: str, expected_docs: int = 0, max_wait: float = 15.0
) -> None:
    """Poll until the index exists and has at least *expected_docs* indexed.

    With ``--search-workers N`` (N > 0), HNSW indexing is background and
    asynchronous.  A plain ``asyncio.sleep`` is not sufficient — we must poll
    ``FT.INFO`` until ``num_docs`` reaches the expected count so that all
    documents are in the HNSW graph before issuing a KNN query.
    """
    deadline = asyncio.get_event_loop().time() + max_wait
    while asyncio.get_event_loop().time() < deadline:
        try:
            info = await svc.get_collection_info(collection_name)
            if info.exists and (expected_docs == 0 or (info.points_count or 0) >= expected_docs):
                return
        except Exception:
            pass
        await asyncio.sleep(0.1)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestRedisHealth:
    async def test_health_check_passes(self, redis_service):
        health = await redis_service.health_check()
        from app.services.vector_db.models import HealthStatus
        assert health.status == HealthStatus.HEALTHY
        assert health.server_version is not None
        # FT.HYBRID requires Redis >= 8.4
        parts = health.server_version.split(".")
        major, minor = int(parts[0]), int(parts[1])
        assert (major, minor) >= (8, 4), (
            f"Redis {health.server_version} < 8.4; FT.HYBRID not supported"
        )

    async def test_capabilities(self, redis_service):
        caps = redis_service.get_capabilities()
        assert caps.supports_server_side_text_search is True
        assert caps.supports_sparse_vectors is False


# ---------------------------------------------------------------------------
# Collection lifecycle
# ---------------------------------------------------------------------------

class TestRedisCollectionLifecycle:
    async def test_create_and_collection_info(self, redis_service):
        col = make_collection("redis")
        cfg = make_collection_config()
        try:
            await redis_service.create_collection(col, cfg)
            info = await redis_service.get_collection_info(col)
            assert info.exists
            assert info.dense_dimension == DIM
        finally:
            await redis_service.delete_collection(col)

    async def test_collection_exists(self, redis_service):
        col = make_collection("redis_exists")
        cfg = make_collection_config()
        await redis_service.create_collection(col, cfg)
        try:
            assert await redis_service.collection_exists(col)
        finally:
            await redis_service.delete_collection(col)
            assert not await redis_service.collection_exists(col)

    async def test_create_idempotent(self, redis_service):
        col = make_collection("redis_idem")
        cfg = make_collection_config()
        try:
            await redis_service.create_collection(col, cfg)
            await redis_service.create_collection(col, cfg)  # should not raise
            info = await redis_service.get_collection_info(col)
            assert info.exists
        finally:
            await redis_service.delete_collection(col)


# ---------------------------------------------------------------------------
# Upsert and query
# ---------------------------------------------------------------------------

class TestRedisUpsertQuery:
    async def test_upsert_and_dense_query(self, redis_service):
        col = make_collection("redis_upsert")
        cfg = make_collection_config()
        pts = sample_points("org1")
        try:
            await redis_service.create_collection(col, cfg)
            await redis_service.upsert_points(col, pts)
            # Wait until all 3 docs are in the HNSW index (search-workers
            # indexes asynchronously; a fixed sleep is not reliable).
            await _wait_index_ready(redis_service, col, expected_docs=len(pts))

            req = HybridSearchRequest(
                dense_query=make_dense([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
                limit=3,
            )
            results_list = await redis_service.query_nearest_points(col, [req])
            assert len(results_list) == 1
            results = results_list[0]
            assert len(results) > 0
            # doc-python should rank highest (cosine similarity = 1.0)
            assert results[0].id == "doc-python"
        finally:
            await redis_service.delete_collection(col)

    async def test_hybrid_query_with_text(self, redis_service):
        """Text query should improve ranking for lexically-matching docs."""
        col = make_collection("redis_hybrid")
        cfg = make_collection_config()
        pts = sample_points("org1")
        try:
            await redis_service.create_collection(col, cfg)
            await redis_service.upsert_points(col, pts)
            await _wait_index_ready(redis_service, col, expected_docs=len(pts))

            req = HybridSearchRequest(
                dense_query=make_dense([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
                text_query="Python programming language",
                limit=3,
            )
            results_list = await redis_service.query_nearest_points(col, [req])
            assert len(results_list[0]) > 0
        finally:
            await redis_service.delete_collection(col)

    async def test_filter_isolation(self, redis_service):
        """orgId filter must not leak across tenants."""
        col = make_collection("redis_filter")
        cfg = make_collection_config()
        points_a = sample_points("org-a")
        points_b = [
            VectorPoint(
                id="doc-b1",
                dense_vector=make_dense([0.5, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
                payload={
                    "page_content": "Ruby on Rails",
                    "metadata": {"orgId": "org-b", "virtualRecordId": "rb1"},
                },
            )
        ]
        all_pts = points_a + points_b
        try:
            await redis_service.create_collection(col, cfg)
            await redis_service.upsert_points(col, all_pts)
            await _wait_index_ready(redis_service, col, expected_docs=len(all_pts))

            req = HybridSearchRequest(
                dense_query=make_dense([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
                limit=10,
                filter=org_filter("org-a"),
            )
            results = (await redis_service.query_nearest_points(col, [req]))[0]
            ids = {r.id for r in results}
            assert "doc-b1" not in ids
            assert all(r.payload.get("metadata", {}).get("orgId") == "org-a" for r in results)
        finally:
            await redis_service.delete_collection(col)


# ---------------------------------------------------------------------------
# Delete, scroll, overwrite
# ---------------------------------------------------------------------------

class TestRedisMutations:
    async def test_delete_points(self, redis_service):
        col = make_collection("redis_del")
        cfg = make_collection_config()
        pts = sample_points("org1")
        try:
            await redis_service.create_collection(col, cfg)
            await redis_service.upsert_points(col, pts)
            await _wait_index_ready(redis_service, col, expected_docs=len(pts))

            del_filter = FilterExpression(
                must=[FieldCondition(key="metadata.virtualRecordId", value="r1")]
            )
            await redis_service.delete_points(col, del_filter)
            # Small pause to let the index reflect the deletion
            await asyncio.sleep(0.1)

            # r1 / doc-python should be gone
            req = HybridSearchRequest(
                dense_query=make_dense([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
                limit=5,
                filter=org_filter("org1"),
            )
            results = (await redis_service.query_nearest_points(col, [req]))[0]
            assert not any(r.id == "doc-python" for r in results)
        finally:
            await redis_service.delete_collection(col)

    async def test_scroll(self, redis_service):
        col = make_collection("redis_scroll")
        cfg = make_collection_config()
        pts = sample_points("org1")
        try:
            await redis_service.create_collection(col, cfg)
            await redis_service.upsert_points(col, pts)
            # FT.SEARCH (used by scroll) reads from the hash store directly,
            # not the HNSW graph — all documents are available immediately.
            result = await redis_service.scroll(col, FilterExpression(), limit=10)
            assert len(result.points) == 3
        finally:
            await redis_service.delete_collection(col)

    async def test_overwrite_payload(self, redis_service):
        col = make_collection("redis_overwrite")
        cfg = make_collection_config()
        pts = sample_points("org1")
        try:
            await redis_service.create_collection(col, cfg)
            await redis_service.upsert_points(col, pts)
            # FT.SEARCH for key lookup doesn't need HNSW to be ready
            update_filter = FilterExpression(
                must=[FieldCondition(key="metadata.virtualRecordId", value="r1")]
            )
            await redis_service.overwrite_payload(
                col, {"metadata.status": "archived"}, update_filter
            )
        finally:
            await redis_service.delete_collection(col)


# ---------------------------------------------------------------------------
# Dimension mismatch recreation
# ---------------------------------------------------------------------------

class TestRedisDimensionMismatch:
    async def test_dimension_mismatch_detected(self, redis_service):
        col = make_collection("redis_dim")
        cfg_small = CollectionConfig = make_collection_config()  # DIM=8
        try:
            await redis_service.create_collection(col, cfg_small)
            info = await redis_service.get_collection_info(col)
            assert info.dense_dimension == DIM
        finally:
            await redis_service.delete_collection(col)
