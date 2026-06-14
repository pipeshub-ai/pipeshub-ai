"""
Integration tests for the OpenSearch vector DB provider.

Requires: docker compose -f deployment/docker-compose/docker-compose.integration.vector-db.yml up -d
Run: pytest tests/integration/vector_db/test_opensearch_integration.py -m integration --timeout=120
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


async def _refresh(svc, col: str) -> None:
    """Force an OpenSearch index refresh so documents are immediately searchable."""
    try:
        await svc.client.indices.refresh(index=col)
    except Exception:
        await asyncio.sleep(1.0)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestOpenSearchHealth:
    async def test_health_check_passes(self, opensearch_service):
        health = await opensearch_service.health_check()
        from app.services.vector_db.models import HealthStatus
        assert health.status == HealthStatus.HEALTHY

    async def test_capabilities(self, opensearch_service):
        caps = opensearch_service.get_capabilities()
        assert caps.supports_server_side_text_search is True
        assert caps.supports_sparse_vectors is False


# ---------------------------------------------------------------------------
# Collection lifecycle
# ---------------------------------------------------------------------------

class TestOpenSearchCollectionLifecycle:
    async def test_create_and_info(self, opensearch_service):
        col = make_collection("os")
        cfg = make_collection_config()
        try:
            await opensearch_service.create_collection(col, cfg)
            info = await opensearch_service.get_collection_info(col)
            assert info.exists
            assert info.dense_dimension == DIM
        finally:
            await opensearch_service.delete_collection(col)

    async def test_create_idempotent(self, opensearch_service):
        col = make_collection("os_idem")
        cfg = make_collection_config()
        try:
            await opensearch_service.create_collection(col, cfg)
            await opensearch_service.create_collection(col, cfg)  # should not raise
        finally:
            await opensearch_service.delete_collection(col)

    async def test_collection_exists(self, opensearch_service):
        col = make_collection("os_exists")
        cfg = make_collection_config()
        await opensearch_service.create_collection(col, cfg)
        try:
            assert await opensearch_service.collection_exists(col)
        finally:
            await opensearch_service.delete_collection(col)
            assert not await opensearch_service.collection_exists(col)


# ---------------------------------------------------------------------------
# Upsert and hybrid query
# ---------------------------------------------------------------------------

class TestOpenSearchUpsertQuery:
    async def test_upsert_and_dense_query(self, opensearch_service):
        col = make_collection("os_upsert")
        cfg = make_collection_config()
        try:
            await opensearch_service.create_collection(col, cfg)
            await opensearch_service.upsert_points(col, sample_points("org1"))
            await _refresh(opensearch_service, col)

            req = HybridSearchRequest(
                dense_query=make_dense([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
                limit=3,
            )
            results = (await opensearch_service.query_nearest_points(col, [req]))[0]
            assert len(results) > 0
            assert results[0].id == "doc-python"
        finally:
            await opensearch_service.delete_collection(col)

    async def test_hybrid_rrf_query(self, opensearch_service):
        """RRF pipeline should fuse BM25 + KNN legs; doc matching both legs ranks higher."""
        col = make_collection("os_rrf")
        cfg = make_collection_config()
        try:
            await opensearch_service.create_collection(col, cfg)
            await opensearch_service.upsert_points(col, sample_points("org1"))
            await _refresh(opensearch_service, col)

            req = HybridSearchRequest(
                dense_query=make_dense([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
                text_query="Python programming language",
                limit=3,
            )
            results = (await opensearch_service.query_nearest_points(col, [req]))[0]
            ids = [r.id for r in results]
            # doc-python matches both dense and text, should be first
            assert ids[0] == "doc-python"
        finally:
            await opensearch_service.delete_collection(col)

    async def test_filter_isolation(self, opensearch_service):
        """orgId filter must isolate results per tenant."""
        col = make_collection("os_filter")
        cfg = make_collection_config()
        try:
            await opensearch_service.create_collection(col, cfg)
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
            await opensearch_service.upsert_points(col, points_a + points_b)
            await _refresh(opensearch_service, col)

            req = HybridSearchRequest(
                dense_query=make_dense([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
                limit=10,
                filter=org_filter("org-a"),
            )
            results = (await opensearch_service.query_nearest_points(col, [req]))[0]
            ids = {r.id for r in results}
            assert "doc-b1" not in ids
        finally:
            await opensearch_service.delete_collection(col)

    async def test_should_filter_with_accessible_ids(self, opensearch_service):
        """should filter with virtualRecordId list (accessible-ID tenant separation precursor)."""
        col = make_collection("os_should")
        cfg = make_collection_config()
        try:
            await opensearch_service.create_collection(col, cfg)
            await opensearch_service.upsert_points(col, sample_points("org1"))
            await _refresh(opensearch_service, col)

            # Only allow r1, r2
            filter_expr = FilterExpression(
                must=[FieldCondition(key="metadata.orgId", value="org1")],
                should=[
                    FieldCondition(key="metadata.virtualRecordId", value="r1"),
                    FieldCondition(key="metadata.virtualRecordId", value="r2"),
                ],
            )
            req = HybridSearchRequest(
                dense_query=make_dense([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
                limit=10,
                filter=filter_expr,
            )
            results = (await opensearch_service.query_nearest_points(col, [req]))[0]
            ids = {r.id for r in results}
            assert "doc-cooking" not in ids
        finally:
            await opensearch_service.delete_collection(col)


# ---------------------------------------------------------------------------
# Delete, scroll, overwrite
# ---------------------------------------------------------------------------

class TestOpenSearchMutations:
    async def test_delete_points(self, opensearch_service):
        col = make_collection("os_del")
        cfg = make_collection_config()
        try:
            await opensearch_service.create_collection(col, cfg)
            await opensearch_service.upsert_points(col, sample_points("org1"))
            await _refresh(opensearch_service, col)

            del_filter = FilterExpression(
                must=[FieldCondition(key="metadata.virtualRecordId", value="r1")]
            )
            await opensearch_service.delete_points(col, del_filter)
            await _refresh(opensearch_service, col)

            req = HybridSearchRequest(
                dense_query=make_dense([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
                limit=5,
            )
            results = (await opensearch_service.query_nearest_points(col, [req]))[0]
            assert not any(r.id == "doc-python" for r in results)
        finally:
            await opensearch_service.delete_collection(col)

    async def test_scroll(self, opensearch_service):
        col = make_collection("os_scroll")
        cfg = make_collection_config()
        try:
            await opensearch_service.create_collection(col, cfg)
            await opensearch_service.upsert_points(col, sample_points("org1"))
            await _refresh(opensearch_service, col)

            result = await opensearch_service.scroll(col, FilterExpression(), limit=10)
            assert len(result.points) == 3
        finally:
            await opensearch_service.delete_collection(col)

    async def test_overwrite_payload(self, opensearch_service):
        col = make_collection("os_overwrite")
        cfg = make_collection_config()
        try:
            await opensearch_service.create_collection(col, cfg)
            await opensearch_service.upsert_points(col, sample_points("org1"))
            await _refresh(opensearch_service, col)

            update_filter = FilterExpression(
                must=[FieldCondition(key="metadata.virtualRecordId", value="r1")]
            )
            await opensearch_service.overwrite_payload(
                col, {"metadata.status": "archived"}, update_filter
            )
        finally:
            await opensearch_service.delete_collection(col)


# ---------------------------------------------------------------------------
# get_collection_info — dimension
# ---------------------------------------------------------------------------

class TestOpenSearchCollectionInfo:
    async def test_points_count(self, opensearch_service):
        col = make_collection("os_info")
        cfg = make_collection_config()
        try:
            await opensearch_service.create_collection(col, cfg)
            await opensearch_service.upsert_points(col, sample_points("org1"))
            await _refresh(opensearch_service, col)

            info = await opensearch_service.get_collection_info(col)
            assert info.exists
            assert info.points_count == 3
            assert info.dense_dimension == DIM
        finally:
            await opensearch_service.delete_collection(col)
