"""
End-to-end integration tests for the image-embedding indexing path:

  VectorStore._process_image_embeddings() -> MultimodalEmbeddingFactory
  -> VectorPoint construction -> real vector DB upsert -> query_nearest_points

A deterministic FakeMultimodalProvider stands in for the real network-calling
providers (Cohere/Gemini/etc.) so the test is reproducible and needs no API
keys, while everything else — dimension validation, blockType/isImage
metadata, page_content handling, and storage/retrieval — runs through the
real ``VectorStore`` and a real vector DB backend.

Requires: docker compose -f deployment/docker-compose/docker-compose.integration.vector-db.yml up -d
Run: pytest tests/integration/test_multimodal_indexing.py -m integration --timeout=120

These tests skip automatically when Docker vector DB services aren't
reachable (same convention as tests/integration/vector_db/).
"""

import hashlib
import math
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.integration.vector_db.conftest import (  # noqa: F401  (fixtures)
    make_collection,
    qdrant_service,
    redis_service,
)
from tests.integration.vector_db.helpers import DIM

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

from app.models.blocks import BlockType  # noqa: E402
from app.services.embeddings.multimodal.interface import (  # noqa: E402
    IMultimodalEmbeddingProvider,
    ImageEmbeddingResult,
)


class FakeMultimodalProvider(IMultimodalEmbeddingProvider):
    """Deterministic stand-in for a real image-embedding provider.

    Maps each base64 string to a stable unit vector (hash-based, like the
    FakeEmbedder used for text in test_e2e_pipeline.py) so retrieval
    assertions are reproducible without any network calls.
    """

    def __init__(self, dim: int = DIM, bad_dim_indices: frozenset = frozenset()):
        self.dim = dim
        self._bad_dim_indices = bad_dim_indices

    async def embed_images(self, image_base64s: List[str]) -> List[ImageEmbeddingResult]:
        results = []
        for i, b64 in enumerate(image_base64s):
            vec = self._embed(b64)
            if i in self._bad_dim_indices:
                vec = vec[:-1]  # deliberately wrong dimension
            results.append(ImageEmbeddingResult(index=i, embedding=vec))
        return results

    def supports_multimodal(self) -> bool:
        return True

    @property
    def provider_name(self) -> str:
        return "fake-multimodal"

    def _embed(self, text: str) -> List[float]:
        h = int(hashlib.md5(text.encode()).hexdigest(), 16)
        vec = [float((h >> (i * 2)) & 0xFF) for i in range(self.dim)]
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


def _make_vector_store(vector_db_service):
    from app.modules.transformers.vectorstore import VectorStore
    from app.services.vector_db.models import VectorDBCapabilities

    vector_db_service.get_capabilities = MagicMock(return_value=VectorDBCapabilities())

    graph_provider = AsyncMock()
    graph_provider.get_document = AsyncMock(return_value={"_key": "rec-1"})

    vs = VectorStore(
        logger=MagicMock(),
        config_service=AsyncMock(),
        graph_provider=graph_provider,
        collection_name="",  # set per-test via vs.collection_name
        vector_db_service=vector_db_service,
    )
    vs.embedding_provider = "cohere"
    vs.model_name = "embed-v4.0"
    vs.embedding_size = DIM
    return vs


def _image_chunk(description: str, uri: str) -> dict:
    return {
        "image_uri": uri,
        "description": description,
        "metadata": {
            "virtualRecordId": "vr-1",
            "orgId": "org-e2e-multimodal",
            "isBlock": True,
            "isBlockGroup": False,
            "blockType": BlockType.IMAGE.value,
            "isImage": True,
        },
    }


async def _index_and_upsert_images(vs, vector_db_service, collection: str, chunks, uris):
    with patch(
        "app.modules.transformers.vectorstore.MultimodalEmbeddingFactory.create",
        return_value=FakeMultimodalProvider(dim=DIM),
    ):
        points = await vs._process_image_embeddings(chunks, uris, record_id="rec-1")
    await vector_db_service.upsert_points(collection, points)
    return points


class TestQdrantMultimodalIndexing:
    async def test_image_points_stored_with_blocktype_metadata_and_retrievable(self, qdrant_service):
        pytest.importorskip("qdrant_client", reason="qdrant_client not installed")
        from app.services.vector_db.models import CollectionConfig, DistanceMetric, HybridSearchRequest

        col = make_collection("e2e_mm_qdrant")
        vs = _make_vector_store(qdrant_service)
        vs.collection_name = col
        cfg = CollectionConfig(embedding_size=DIM, distance_metric=DistanceMetric.COSINE)

        chunks = [
            _image_chunk("A network architecture diagram", "data:image/png;base64,AAA"),
            _image_chunk("A flowchart of the deployment process", "data:image/png;base64,BBB"),
        ]
        uris = [c["image_uri"] for c in chunks]

        try:
            await qdrant_service.create_collection(col, cfg)
            points = await _index_and_upsert_images(vs, qdrant_service, col, chunks, uris)

            assert len(points) == 2
            for point in points:
                meta = point.payload["metadata"]
                assert meta["blockType"] == BlockType.IMAGE.value
                assert meta["isImage"] is True
                # The raw base64 URI must never end up in page_content.
                assert point.payload["page_content"] not in ("data:image/png;base64,AAA", "data:image/png;base64,BBB")

            fake_provider = FakeMultimodalProvider(dim=DIM)
            query_vec = fake_provider._embed("data:image/png;base64,AAA")
            req = HybridSearchRequest(dense_query=query_vec, limit=5)
            results = (await qdrant_service.query_nearest_points(col, [req]))[0]

            assert len(results) > 0
            assert results[0].payload["metadata"]["blockType"] == BlockType.IMAGE.value
            assert results[0].payload["page_content"] == "A network architecture diagram"
        finally:
            await qdrant_service.delete_collection(col)

    async def test_dimension_mismatch_dropped_before_upsert(self, qdrant_service):
        """An image embedding whose dimension doesn't match the collection
        must never reach upsert_points — it would corrupt cosine similarity
        for the whole collection or be rejected outright by the DB."""
        pytest.importorskip("qdrant_client", reason="qdrant_client not installed")
        from app.services.vector_db.models import CollectionConfig, DistanceMetric

        col = make_collection("e2e_mm_qdrant_baddim")
        vs = _make_vector_store(qdrant_service)
        vs.collection_name = col
        cfg = CollectionConfig(embedding_size=DIM, distance_metric=DistanceMetric.COSINE)

        chunks = [
            _image_chunk("Good embedding", "data:image/png;base64,AAA"),
            _image_chunk("Bad embedding", "data:image/png;base64,BBB"),
        ]
        uris = [c["image_uri"] for c in chunks]

        try:
            await qdrant_service.create_collection(col, cfg)
            with patch(
                "app.modules.transformers.vectorstore.MultimodalEmbeddingFactory.create",
                return_value=FakeMultimodalProvider(dim=DIM, bad_dim_indices=frozenset({1})),
            ):
                points = await vs._process_image_embeddings(chunks, uris, record_id="rec-1")

            assert len(points) == 1
            assert points[0].payload["page_content"] == "Good embedding"
            vs.logger.error.assert_called()

            await qdrant_service.upsert_points(col, points)
        finally:
            await qdrant_service.delete_collection(col)


class TestRedisMultimodalIndexing:
    async def test_image_points_stored_with_blocktype_metadata_and_retrievable(self, redis_service):
        import asyncio

        from app.services.vector_db.models import CollectionConfig, DistanceMetric, HybridSearchRequest

        col = make_collection("e2e_mm_redis")
        vs = _make_vector_store(redis_service)
        vs.collection_name = col
        cfg = CollectionConfig(embedding_size=DIM, distance_metric=DistanceMetric.COSINE)

        chunks = [
            _image_chunk("A network architecture diagram", "data:image/png;base64,AAA"),
            _image_chunk("A flowchart of the deployment process", "data:image/png;base64,BBB"),
        ]
        uris = [c["image_uri"] for c in chunks]

        try:
            await redis_service.create_collection(col, cfg)
            points = await _index_and_upsert_images(vs, redis_service, col, chunks, uris)
            await asyncio.sleep(0.5)  # wait for Redis index

            assert len(points) == 2
            for point in points:
                assert point.payload["metadata"]["blockType"] == BlockType.IMAGE.value
                assert point.payload["metadata"]["isImage"] is True

            fake_provider = FakeMultimodalProvider(dim=DIM)
            query_vec = fake_provider._embed("data:image/png;base64,AAA")
            req = HybridSearchRequest(dense_query=query_vec, limit=5)
            results = (await redis_service.query_nearest_points(col, [req]))[0]

            assert len(results) > 0
            assert results[0].payload["metadata"]["blockType"] == BlockType.IMAGE.value
        finally:
            await redis_service.delete_collection(col)
