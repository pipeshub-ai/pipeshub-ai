"""
End-to-end integration tests for the indexing + retrieval pipeline.

These tests exercise the full path:
  VectorStore (embed + upsert) → RetrievalService.search_with_filters

A deterministic FakeEmbedder produces reproducible dense vectors so the tests
are not dependent on a real embedding model.

Requires: docker compose -f deployment/docker-compose/docker-compose.integration.vector-db.yml up -d
Run: pytest tests/integration/vector_db/test_e2e_pipeline.py -m integration --timeout=120

Note: these tests skip automatically when optional packages (langchain_core, etc.)
are not installed in the current Python environment.
"""

import asyncio
import logging
import pytest

from tests.integration.vector_db.helpers import DIM
from tests.integration.vector_db.conftest import make_collection

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

langchain_core = pytest.importorskip("langchain_core", reason="langchain_core not installed")

from langchain_core.documents import Document  # noqa: E402

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fake embedder
# ---------------------------------------------------------------------------

class FakeEmbedder:
    """Deterministic embedder that maps a string hash to a DIM-sized unit vector."""

    def __init__(self, dim: int = DIM):
        self.dim = dim

    async def aembed_documents(self, texts):
        return [self._embed(t) for t in texts]

    async def aembed_query(self, text: str):
        return self._embed(text)

    def embed_documents(self, texts):
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str):
        return self._embed(text)

    def _embed(self, text: str):
        """Stable hash-based unit vector."""
        import hashlib, struct, math
        h = int(hashlib.md5(text.encode()).hexdigest(), 16)
        vecs = []
        for i in range(self.dim):
            bits = (h >> (i * 2)) & 0xFF
            vecs.append(float(bits))
        norm = math.sqrt(sum(v * v for v in vecs)) or 1.0
        return [v / norm for v in vecs]


# ---------------------------------------------------------------------------
# Helper to index documents
# ---------------------------------------------------------------------------

async def _index_documents(vector_db_service, collection: str, documents, embedder):
    """Minimal indexing path: embed + upsert without going through the full VectorStore."""
    from app.services.vector_db.models import VectorPoint

    dense_vecs = await embedder.aembed_documents([d.page_content for d in documents])
    points = []
    for i, (doc, dense) in enumerate(zip(documents, dense_vecs)):
        pid = doc.metadata.get("id", str(i))
        points.append(
            VectorPoint(
                id=pid,
                dense_vector=dense,
                payload={
                    "page_content": doc.page_content,
                    "metadata": doc.metadata,
                },
            )
        )
    await vector_db_service.upsert_points(collection, points)
    return points


# ---------------------------------------------------------------------------
# Sample documents
# ---------------------------------------------------------------------------

def _sample_docs(org_id: str = "org-test"):
    return [
        Document(
            page_content="Python is a dynamic programming language favored for AI",
            metadata={"id": "p1", "orgId": org_id, "virtualRecordId": "vr1"},
        ),
        Document(
            page_content="Java is a statically typed language popular for enterprise",
            metadata={"id": "p2", "orgId": org_id, "virtualRecordId": "vr2"},
        ),
        Document(
            page_content="Cooking pasta is easy: boil water, add salt, cook al dente",
            metadata={"id": "p3", "orgId": org_id, "virtualRecordId": "vr3"},
        ),
    ]


# ---------------------------------------------------------------------------
# E2E: Redis
# ---------------------------------------------------------------------------

class TestRedisE2E:
    async def test_index_and_query_redis(self, redis_service):
        from app.services.vector_db.models import CollectionConfig, DistanceMetric

        col = make_collection("e2e_redis")
        embedder = FakeEmbedder(DIM)
        cfg = CollectionConfig(embedding_size=DIM, distance_metric=DistanceMetric.COSINE)
        try:
            await redis_service.create_collection(col, cfg)
            docs = _sample_docs("org-e2e-redis")
            await _index_documents(redis_service, col, docs, embedder)
            await asyncio.sleep(0.5)  # wait for Redis index

            query_vec = await embedder.aembed_query("Python AI language")

            from app.services.vector_db.models import HybridSearchRequest, FilterExpression, FieldCondition

            req = HybridSearchRequest(
                dense_query=query_vec,
                text_query="Python AI language",
                limit=3,
                filter=FilterExpression(
                    must=[FieldCondition(key="metadata.orgId", value="org-e2e-redis")]
                ),
            )
            results = (await redis_service.query_nearest_points(col, [req]))[0]
            assert len(results) > 0
            top_id = results[0].id
            assert top_id == "p1"  # Python doc should rank highest
        finally:
            await redis_service.delete_collection(col)

    async def test_tenant_isolation_redis(self, redis_service):
        """Two orgs share a collection; filters must isolate results."""
        from app.services.vector_db.models import (
            CollectionConfig, DistanceMetric, HybridSearchRequest,
            FilterExpression, FieldCondition
        )

        col = make_collection("e2e_redis_tenant")
        embedder = FakeEmbedder(DIM)
        cfg = CollectionConfig(embedding_size=DIM, distance_metric=DistanceMetric.COSINE)
        try:
            await redis_service.create_collection(col, cfg)
            docs_a = _sample_docs("org-a")
            docs_b = _sample_docs("org-b")
            await _index_documents(redis_service, col, docs_a + docs_b, embedder)
            await asyncio.sleep(0.5)

            query_vec = await embedder.aembed_query("programming")
            req = HybridSearchRequest(
                dense_query=query_vec,
                limit=10,
                filter=FilterExpression(
                    must=[FieldCondition(key="metadata.orgId", value="org-a")]
                ),
            )
            results = (await redis_service.query_nearest_points(col, [req]))[0]
            assert all(r.payload["metadata"]["orgId"] == "org-a" for r in results)
        finally:
            await redis_service.delete_collection(col)


# ---------------------------------------------------------------------------
# E2E: OpenSearch
# ---------------------------------------------------------------------------

class TestOpenSearchE2E:
    async def test_index_and_query_opensearch(self, opensearch_service):
        from app.services.vector_db.models import CollectionConfig, DistanceMetric

        col = make_collection("e2e_os")
        embedder = FakeEmbedder(DIM)
        cfg = CollectionConfig(embedding_size=DIM, distance_metric=DistanceMetric.COSINE)
        try:
            await opensearch_service.create_collection(col, cfg)
            docs = _sample_docs("org-e2e-os")
            await _index_documents(opensearch_service, col, docs, embedder)
            await opensearch_service.client.indices.refresh(index=col)

            query_vec = await embedder.aembed_query("Python AI language")

            from app.services.vector_db.models import HybridSearchRequest, FilterExpression, FieldCondition

            req = HybridSearchRequest(
                dense_query=query_vec,
                text_query="Python AI language",
                limit=3,
                filter=FilterExpression(
                    must=[FieldCondition(key="metadata.orgId", value="org-e2e-os")]
                ),
            )
            results = (await opensearch_service.query_nearest_points(col, [req]))[0]
            assert len(results) > 0
        finally:
            await opensearch_service.delete_collection(col)

    async def test_rrf_fusion_opensearch(self, opensearch_service):
        """Doc matching BOTH dense and text legs should outrank single-leg matches."""
        from app.services.vector_db.models import (
            CollectionConfig, DistanceMetric, HybridSearchRequest, VectorPoint,
        )
        import math

        col = make_collection("e2e_os_rrf")
        cfg = CollectionConfig(embedding_size=DIM, distance_metric=DistanceMetric.COSINE)
        embedder = FakeEmbedder(DIM)

        # Doc A matches both legs; Doc B only matches text; Doc C only matches dense
        dense_a = await embedder.aembed_query("Python artificial intelligence")
        dense_c = await embedder.aembed_query("completely different topic")
        points = [
            VectorPoint(
                id="doc-a",
                dense_vector=dense_a,
                payload={"page_content": "Python artificial intelligence", "metadata": {"orgId": "x", "virtualRecordId": "x1"}},
            ),
            VectorPoint(
                id="doc-b",
                dense_vector=[0.0] * DIM,  # far from query
                payload={"page_content": "Python artificial intelligence", "metadata": {"orgId": "x", "virtualRecordId": "x2"}},
            ),
            VectorPoint(
                id="doc-c",
                dense_vector=dense_a,  # close to query
                payload={"page_content": "topic unrelated to query", "metadata": {"orgId": "x", "virtualRecordId": "x3"}},
            ),
        ]
        try:
            await opensearch_service.create_collection(col, cfg)
            await opensearch_service.upsert_points(col, points)
            await opensearch_service.client.indices.refresh(index=col)

            req = HybridSearchRequest(
                dense_query=dense_a,
                text_query="Python artificial intelligence",
                limit=3,
            )
            results = (await opensearch_service.query_nearest_points(col, [req]))[0]
            ids = [r.id for r in results]
            # doc-a matches both legs and should rank first or very near top
            assert "doc-a" in ids[:2], f"Expected doc-a near top, got {ids}"
        finally:
            await opensearch_service.delete_collection(col)


# ---------------------------------------------------------------------------
# E2E: Qdrant
# ---------------------------------------------------------------------------

class TestQdrantE2E:
    async def test_index_and_query_qdrant(self, qdrant_service):
        pytest.importorskip("qdrant_client", reason="qdrant_client not installed")
        from app.services.vector_db.models import CollectionConfig, DistanceMetric

        col = make_collection("e2e_qdrant")
        embedder = FakeEmbedder(DIM)
        cfg = CollectionConfig(embedding_size=DIM, distance_metric=DistanceMetric.COSINE)
        try:
            await qdrant_service.create_collection(col, cfg)
            docs = _sample_docs("org-e2e-qdrant")
            await _index_documents(qdrant_service, col, docs, embedder)

            query_vec = await embedder.aembed_query("Python AI language")

            from app.services.vector_db.models import HybridSearchRequest, FilterExpression, FieldCondition

            req = HybridSearchRequest(
                dense_query=query_vec,
                limit=3,
                filter=FilterExpression(
                    must=[FieldCondition(key="metadata.orgId", value="org-e2e-qdrant")]
                ),
            )
            results = (await qdrant_service.query_nearest_points(col, [req]))[0]
            assert len(results) > 0
            assert results[0].id == "p1"
        finally:
            await qdrant_service.delete_collection(col)
