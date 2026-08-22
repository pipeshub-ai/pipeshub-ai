"""
Integration tests for the Qdrant vector DB provider.

Requires: docker compose -f deployment/docker-compose/docker-compose.integration.vector-db.yml up -d
Run: pytest tests/integration/vector_db/test_qdrant_integration.py -m integration --timeout=120
"""

import asyncio
import pytest

from app.services.vector_db.models import (
    FieldCondition,
    FilterExpression,
    HybridSearchRequest,
    SparseVector,
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


def sample_points_with_sparse(org_id: str = "org1"):
    """Sample VectorPoints with sparse vectors for Qdrant."""
    base = sample_points(org_id)
    # Add minimal sparse vectors (SPLADE-like, not real BM25)
    base[0].sparse_vector = SparseVector(indices=[0, 10], values=[1.0, 0.5])
    base[1].sparse_vector = SparseVector(indices=[1, 11], values=[1.0, 0.5])
    base[2].sparse_vector = SparseVector(indices=[2, 12], values=[1.0, 0.5])
    return base


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestQdrantHealth:
    async def test_health_check_passes(self, qdrant_service):
        health = await qdrant_service.health_check()
        from app.services.vector_db.models import HealthStatus
        assert health.status == HealthStatus.HEALTHY

    async def test_capabilities_sparse(self, qdrant_service):
        caps = qdrant_service.get_capabilities()
        assert caps.supports_sparse_vectors is True
        assert caps.supports_server_side_text_search is False


# ---------------------------------------------------------------------------
# Collection lifecycle
# ---------------------------------------------------------------------------

class TestQdrantCollectionLifecycle:
    async def test_create_and_info(self, qdrant_service):
        col = make_collection("qdrant")
        cfg = make_collection_config()
        try:
            await qdrant_service.create_collection(col, cfg)
            info = await qdrant_service.get_collection_info(col)
            assert info.exists
            assert info.dense_dimension == DIM
        finally:
            await qdrant_service.delete_collection(col)

    async def test_collection_exists(self, qdrant_service):
        col = make_collection("qdrant_ex")
        cfg = make_collection_config()
        await qdrant_service.create_collection(col, cfg)
        try:
            assert await qdrant_service.collection_exists(col)
        finally:
            await qdrant_service.delete_collection(col)
            assert not await qdrant_service.collection_exists(col)


# ---------------------------------------------------------------------------
# Upsert and query
# ---------------------------------------------------------------------------

class TestQdrantUpsertQuery:
    async def test_upsert_and_dense_query(self, qdrant_service):
        col = make_collection("qdrant_upsert")
        cfg = make_collection_config()
        try:
            await qdrant_service.create_collection(col, cfg)
            await qdrant_service.upsert_points(col, sample_points("org1"))

            req = HybridSearchRequest(
                dense_query=make_dense([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
                limit=3,
            )
            results = (await qdrant_service.query_nearest_points(col, [req]))[0]
            assert len(results) > 0
            assert results[0].id == "doc-python"
        finally:
            await qdrant_service.delete_collection(col)

    async def test_hybrid_sparse_dense_query(self, qdrant_service):
        """Qdrant RRF prefetch with sparse+dense vectors."""
        col = make_collection("qdrant_hybrid")
        cfg = make_collection_config()
        try:
            await qdrant_service.create_collection(col, cfg)
            await qdrant_service.upsert_points(col, sample_points_with_sparse("org1"))

            sparse_q = SparseVector(indices=[0, 10], values=[1.0, 0.5])
            req = HybridSearchRequest(
                dense_query=make_dense([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
                sparse_query=sparse_q,
                limit=3,
            )
            results = (await qdrant_service.query_nearest_points(col, [req]))[0]
            assert results[0].id == "doc-python"
        finally:
            await qdrant_service.delete_collection(col)

    async def test_filter_isolation(self, qdrant_service):
        col = make_collection("qdrant_filter")
        cfg = make_collection_config()
        try:
            await qdrant_service.create_collection(col, cfg)
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
            await qdrant_service.upsert_points(col, points_a + points_b)

            req = HybridSearchRequest(
                dense_query=make_dense([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
                limit=10,
                filter=org_filter("org-a"),
            )
            results = (await qdrant_service.query_nearest_points(col, [req]))[0]
            ids = {r.id for r in results}
            assert "doc-b1" not in ids
        finally:
            await qdrant_service.delete_collection(col)


# ---------------------------------------------------------------------------
# Delete, scroll, overwrite
# ---------------------------------------------------------------------------

class TestQdrantMutations:
    async def test_delete_points(self, qdrant_service):
        col = make_collection("qdrant_del")
        cfg = make_collection_config()
        try:
            await qdrant_service.create_collection(col, cfg)
            await qdrant_service.upsert_points(col, sample_points("org1"))

            del_filter = FilterExpression(
                must=[FieldCondition(key="metadata.virtualRecordId", value="r1")]
            )
            await qdrant_service.delete_points(col, del_filter)

            req = HybridSearchRequest(
                dense_query=make_dense([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
                limit=5,
            )
            results = (await qdrant_service.query_nearest_points(col, [req]))[0]
            assert not any(r.id == "doc-python" for r in results)
        finally:
            await qdrant_service.delete_collection(col)

    async def test_scroll(self, qdrant_service):
        col = make_collection("qdrant_scroll")
        cfg = make_collection_config()
        try:
            await qdrant_service.create_collection(col, cfg)
            await qdrant_service.upsert_points(col, sample_points("org1"))

            result = await qdrant_service.scroll(col, FilterExpression(), limit=10)
            assert len(result.points) == 3
        finally:
            await qdrant_service.delete_collection(col)

    async def test_overwrite_payload(self, qdrant_service):
        col = make_collection("qdrant_overwrite")
        cfg = make_collection_config()
        try:
            await qdrant_service.create_collection(col, cfg)
            await qdrant_service.upsert_points(col, sample_points("org1"))

            update_filter = FilterExpression(
                must=[FieldCondition(key="metadata.virtualRecordId", value="r1")]
            )
            await qdrant_service.overwrite_payload(
                col, {"status": "archived"}, update_filter
            )
            # No assertion — just verify it doesn't raise
        finally:
            await qdrant_service.delete_collection(col)
