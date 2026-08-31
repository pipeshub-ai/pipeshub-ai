"""Unit tests for vector DB models, CollectionType, and name sanitization."""

import pytest

from app.services.vector_db.collections import (
    sanitize_collection_name,
)
from app.services.vector_db.models import (
    FieldCondition,
    FilterExpression,
    FusionMethod,
    HybridSearchRequest,
    SparseVector,
    VectorChunkMetadata,
    VectorChunkPayload,
    VectorDBCapabilities,
    to_generic_sparse_vector,
)

# ---------------------------------------------------------------------------
# VectorChunkMetadata / VectorChunkPayload — blockType / isImage fields
# ---------------------------------------------------------------------------


class TestVectorChunkMetadata:
    def test_defaults_are_none(self):
        meta = VectorChunkMetadata()
        assert meta.blockType is None
        assert meta.isImage is None

    def test_blocktype_and_isimage_roundtrip_through_payload(self):
        payload = VectorChunkPayload(
            page_content="",
            metadata=VectorChunkMetadata(
                orgId="org-1",
                virtualRecordId="vr-1",
                blockId="block-1",
                blockIndex=0,
                blockType="image",
                isImage=True,
            ),
        )
        as_dict = payload.to_dict()
        assert as_dict["metadata"]["blockType"] == "image"
        assert as_dict["metadata"]["isImage"] is True

        restored = VectorChunkPayload.from_dict(as_dict)
        assert restored.metadata.blockType == "image"
        assert restored.metadata.isImage is True

    def test_from_dict_missing_blocktype_defaults_to_none(self):
        """Points written before blockType existed must not error on read."""
        legacy_data = {
            "page_content": "hello",
            "metadata": {"orgId": "org-1", "virtualRecordId": "vr-1"},
        }
        restored = VectorChunkPayload.from_dict(legacy_data)
        assert restored.metadata.blockType is None
        assert restored.metadata.isImage is None

    def test_to_dict_omits_none_fields(self):
        payload = VectorChunkPayload(metadata=VectorChunkMetadata(blockType="text"))
        as_dict = payload.to_dict()
        assert as_dict["metadata"] == {"blockType": "text"}


# ---------------------------------------------------------------------------
# sanitize_collection_name
# ---------------------------------------------------------------------------


class TestSanitizeCollectionName:
    def test_lowercases(self):
        assert sanitize_collection_name("MyCollection") == "mycollection"

    def test_replaces_illegal_chars_with_underscore(self):
        assert sanitize_collection_name("google-drive.records!") == "google_drive_records_"

    def test_strips_disallowed_leading_chars(self):
        assert sanitize_collection_name("_-+records") == "records"

    def test_all_disallowed_leading_chars_falls_back_to_placeholder(self):
        assert sanitize_collection_name("___").startswith("collection_")

    def test_empty_string_falls_back_to_placeholder(self):
        assert sanitize_collection_name("").startswith("collection_")

    def test_placeholder_fallback_keeps_distinct_inputs_distinct(self):
        """Two names that sanitize away entirely must not share a collection."""
        assert sanitize_collection_name("___") != sanitize_collection_name("+++")

    def test_is_idempotent(self):
        """The dedup path compares a fresh name against an already-resolved
        one; a second pass that changed the answer would let a record be
        skipped as a duplicate of something in a collection it never reached."""
        for raw in ("Records", "___", "", "a" * 500, "Google Drive/Records", "+x-y"):
            once = sanitize_collection_name(raw)
            assert sanitize_collection_name(once) == once

    def test_within_length_limit_is_unchanged(self):
        name = "a" * 50
        assert sanitize_collection_name(name) == name

    def test_over_length_name_is_truncated_with_hash_suffix(self):
        name = "a" * 250
        result = sanitize_collection_name(name)
        assert len(result) <= 200
        assert result.startswith("a" * 10)
        assert "_" in result

    def test_over_length_truncation_avoids_collisions(self):
        """Two different over-length names must not truncate to the same result."""
        name_a = "a" * 250
        name_b = "a" * 249 + "b"
        assert sanitize_collection_name(name_a) != sanitize_collection_name(name_b)

    def test_idempotent(self):
        """Sanitizing an already-sanitized name is a no-op."""
        once = sanitize_collection_name("Google_Drive-Records")
        twice = sanitize_collection_name(once)
        assert once == twice


# ---------------------------------------------------------------------------
# FilterExpression helpers
# ---------------------------------------------------------------------------


class TestFilterExpression:
    def test_empty_expression(self):
        f = FilterExpression()
        assert f.is_empty()

    def test_non_empty_must(self):
        f = FilterExpression(must=[FieldCondition(key="k", value="v")])
        assert not f.is_empty()

    def test_non_empty_should(self):
        f = FilterExpression(should=[FieldCondition(key="k", values=["a", "b"])])
        assert not f.is_empty()


# ---------------------------------------------------------------------------
# to_generic_sparse_vector
# ---------------------------------------------------------------------------


class TestToGenericSparseVector:
    def test_passthrough(self):
        sv = SparseVector(indices=[1, 2], values=[0.5, 0.3])
        result = to_generic_sparse_vector(sv)
        assert result is sv

    def test_from_object_with_attributes(self):
        class FakeEmbed:
            indices = [1, 2]
            values = [0.5, 0.3]

        result = to_generic_sparse_vector(FakeEmbed())
        assert result.indices == [1, 2]
        assert result.values == [0.5, 0.3]

    def test_from_dict(self):
        result = to_generic_sparse_vector({"indices": [0], "values": [1.0]})
        assert result.indices == [0]
        assert result.values == [1.0]

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            to_generic_sparse_vector("not a sparse vector")


# ---------------------------------------------------------------------------
# VectorDBCapabilities
# ---------------------------------------------------------------------------


class TestVectorDBCapabilities:
    def test_defaults(self):
        caps = VectorDBCapabilities()
        assert caps.supports_sparse_vectors is False
        assert caps.supports_server_side_text_search is False
        assert FusionMethod.RRF in caps.supported_fusion_methods

    def test_qdrant_caps(self):
        caps = VectorDBCapabilities(
            supports_sparse_vectors=True,
            supports_server_side_text_search=False,
        )
        assert caps.supports_sparse_vectors is True

    def test_redis_caps(self):
        caps = VectorDBCapabilities(
            supports_sparse_vectors=False,
            supports_server_side_text_search=True,
        )
        assert caps.supports_server_side_text_search is True

    def test_multi_collection_defaults(self):
        """New capability fields default to permissive values so a provider
        that hasn't declared them yet is not artificially restricted."""
        caps = VectorDBCapabilities()
        assert caps.supports_multi_collection is True
        assert caps.max_recommended_collections is None

    def test_provider_can_declare_a_recommended_ceiling(self):
        caps = VectorDBCapabilities(max_recommended_collections=200)
        assert caps.max_recommended_collections == 200


# ---------------------------------------------------------------------------
# HybridSearchRequest
# ---------------------------------------------------------------------------


class TestHybridSearchRequest:
    def test_defaults(self):
        req = HybridSearchRequest()
        assert req.dense_query is None
        assert req.sparse_query is None
        assert req.text_query is None
        assert req.limit == 10
        assert req.fusion_method == FusionMethod.RRF

    def test_with_text_query(self):
        req = HybridSearchRequest(
            dense_query=[0.1, 0.2],
            text_query="hello world",
            limit=5,
        )
        assert req.text_query == "hello world"
        assert req.limit == 5


# ===========================================================================
# Phase 5 regression: CollectionConfig knobs, legacy defaults
# ===========================================================================

from app.services.vector_db.models import (
    CollectionConfig,
    DistanceMetric,
    HNSWConfig,
    MRLConfig,
    QuantizationType,
)


class TestCollectionConfigDefaults:
    def test_default_quantization_is_scalar(self):
        """CollectionConfig quantization defaults to SCALAR (INT8) for backward-compatibility.

        Existing Qdrant collections were created with INT8 scalar quantization.
        The default preserves that behaviour without requiring callers to opt in.
        """
        cfg = CollectionConfig()
        assert cfg.quantization == QuantizationType.SCALAR

    def test_default_hnsw_is_none(self):
        """HNSW is None by default — providers use their own defaults."""
        cfg = CollectionConfig()
        assert cfg.hnsw is None

    def test_default_mrl_is_none(self):
        """MRL is None by default — no dimension reduction."""
        cfg = CollectionConfig()
        assert cfg.mrl is None

    def test_legacy_fields_unchanged(self):
        """Fields present before Phase 5 retain same defaults."""
        cfg = CollectionConfig()
        assert cfg.embedding_size == 1024
        assert cfg.distance_metric == DistanceMetric.COSINE
        assert cfg.enable_sparse is True
        assert cfg.sparse_idf is False


class TestCollectionConfigKnobs:
    def test_none_quantization_accepted(self):
        cfg = CollectionConfig(quantization=QuantizationType.NONE)
        assert cfg.quantization == QuantizationType.NONE

    def test_product_quantization_accepted(self):
        cfg = CollectionConfig(quantization=QuantizationType.PRODUCT)
        assert cfg.quantization == QuantizationType.PRODUCT

    def test_binary_quantization_accepted(self):
        cfg = CollectionConfig(quantization=QuantizationType.BINARY)
        assert cfg.quantization == QuantizationType.BINARY

    def test_hnsw_config_m_ef(self):
        hnsw = HNSWConfig(m=16, ef_construct=200, ef=128)
        cfg = CollectionConfig(hnsw=hnsw)
        assert cfg.hnsw.m == 16
        assert cfg.hnsw.ef_construct == 200
        assert cfg.hnsw.ef == 128

    def test_mrl_dimensions(self):
        mrl = MRLConfig(dimensions=512)
        cfg = CollectionConfig(mrl=mrl)
        assert cfg.mrl.dimensions == 512


class TestQdrantCreateCollectionKnobs:
    """Verify that CollectionConfig knobs propagate into Qdrant client calls."""

    @pytest.mark.asyncio
    async def test_none_quantization_omits_quantization_kwarg(self):
        """CollectionConfig(quantization=NONE) must not pass quantization_config to Qdrant.

        NONE is the explicit opt-out for quantization; providers should not add any
        quantization when this is set.
        """
        pytest.importorskip("qdrant_client", reason="qdrant_client not installed")
        from app.services.vector_db.qdrant.qdrant import QdrantService
        from unittest.mock import AsyncMock, MagicMock

        svc = QdrantService.__new__(QdrantService)
        svc.client = MagicMock()
        svc.client.create_collection = AsyncMock()

        cfg = CollectionConfig(
            embedding_size=1024,
            quantization=QuantizationType.NONE,
        )
        await svc.create_collection("records", cfg)

        call_kw = svc.client.create_collection.call_args.kwargs
        assert "quantization_config" not in call_kw, (
            "quantization=NONE must not forward quantization_config to Qdrant"
        )

    @pytest.mark.asyncio
    async def test_scalar_quantization_forwarded(self):
        """Default SCALAR quantization must set quantization_config."""
        pytest.importorskip("qdrant_client", reason="qdrant_client not installed")
        from app.services.vector_db.qdrant.qdrant import QdrantService
        from qdrant_client.http.models import ScalarQuantization
        from unittest.mock import AsyncMock, MagicMock

        svc = QdrantService.__new__(QdrantService)
        svc.client = MagicMock()
        svc.client.create_collection = AsyncMock()

        cfg = CollectionConfig(quantization=QuantizationType.SCALAR)
        await svc.create_collection("records", cfg)

        call_kw = svc.client.create_collection.call_args.kwargs
        assert "quantization_config" in call_kw
        assert isinstance(call_kw["quantization_config"], ScalarQuantization)

    @pytest.mark.asyncio
    async def test_hnsw_knobs_forwarded(self):
        """Non-None HNSWConfig fields must appear in hnsw_config passed to Qdrant."""
        pytest.importorskip("qdrant_client", reason="qdrant_client not installed")
        from app.services.vector_db.qdrant.qdrant import QdrantService
        from unittest.mock import AsyncMock, MagicMock

        svc = QdrantService.__new__(QdrantService)
        svc.client = MagicMock()
        svc.client.create_collection = AsyncMock()

        cfg = CollectionConfig(hnsw=HNSWConfig(m=32, ef_construct=512))
        await svc.create_collection("records", cfg)

        call_kw = svc.client.create_collection.call_args.kwargs
        assert "hnsw_config" in call_kw
        hnsw = call_kw["hnsw_config"]
        assert hnsw.m == 32
        assert hnsw.ef_construct == 512

    @pytest.mark.asyncio
    async def test_mrl_dimensions_reduce_vector_size(self):
        """MRLConfig.dimensions must override embedding_size in VectorParams."""
        pytest.importorskip("qdrant_client", reason="qdrant_client not installed")
        from app.services.vector_db.qdrant.qdrant import QdrantService
        from unittest.mock import AsyncMock, MagicMock

        svc = QdrantService.__new__(QdrantService)
        svc.client = MagicMock()
        svc.client.create_collection = AsyncMock()

        cfg = CollectionConfig(embedding_size=1024, mrl=MRLConfig(dimensions=512))
        await svc.create_collection("records", cfg)

        call_kw = svc.client.create_collection.call_args.kwargs
        dense_params = call_kw["vectors_config"]["dense"]
        assert dense_params.size == 512, (
            f"MRL should reduce effective_dim to 512, got {dense_params.size}"
        )

