"""Unit tests for the multimodal-embeddings cleanup on ``VectorStore``.

These tests exercise behaviour that was introduced to stop leaking raw
image URIs into ``page_content``, to route Voyage's ``aembed_documents``
to the multimodal endpoint, to honour the user-set ``Output Dimensions``
on AWS Bedrock Titan, and to record ``is_multimodal`` on the collection
signature so a future ``isMultimodal`` toggle on the same model can be
caught by the health-check flow.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_vectorstore():
    """Instantiate ``VectorStore`` with every external dependency mocked."""
    with patch(
        "app.modules.transformers.vectorstore.FastEmbedSparse"
    ) as mock_sparse, patch(
        "app.modules.transformers.vectorstore._get_shared_nlp"
    ) as mock_nlp:
        mock_sparse.return_value = MagicMock()
        mock_nlp.return_value = MagicMock()

        from app.modules.transformers.vectorstore import VectorStore

        return VectorStore(
            logger=MagicMock(),
            config_service=AsyncMock(),
            graph_provider=AsyncMock(),
            collection_name="test_collection",
            vector_db_service=AsyncMock(),
        )


# ===================================================================
# _build_image_point — the shared payload builder
# ===================================================================

class TestBuildImagePoint:
    """The builder must never leak the image URI into ``page_content`` and
    must always stamp an ``image`` block-type marker so retrieval can
    recognise the hit."""

    def test_page_content_is_empty(self):
        from app.modules.transformers.vectorstore import VectorStore

        data_url = "data:image/png;base64," + "A" * 2048
        chunk = {
            "metadata": {"orgId": "org-1", "virtualRecordId": "v1", "blockIndex": 3},
            "image_uri": data_url,
        }

        point = VectorStore._build_image_point([0.1, 0.2], chunk)

        assert point.payload["page_content"] == ""

    def test_metadata_is_identity_only_without_image_blob(self):
        """The Qdrant payload is identity-only — ``virtualRecordId`` +
        ``blockId`` + ``orgId`` + a ``blockType`` marker. The image URI
        itself (often a multi-MB base64 data URL) must NOT land on the
        payload: the bytes live in blob storage and are resolved at
        read time via ``block.data.uri`` in the record graph.
        Duplicating them into Qdrant bloats the collection for no
        read-path benefit. ``page_content`` also stays empty so the
        blob never leaks into the LLM prompt."""
        from app.models.blocks import BlockType
        from app.modules.transformers.vectorstore import VectorStore

        chunk = {
            "metadata": {"orgId": "org-1", "virtualRecordId": "v1", "blockId": "b3"},
            "image_uri": "data:image/png;base64,abc",
        }

        point = VectorStore._build_image_point([0.1, 0.2], chunk)

        metadata = point.payload["metadata"]
        assert metadata["blockType"] == BlockType.IMAGE.value
        assert "imageUri" not in metadata, (
            "image bytes must not be persisted on the Qdrant payload — "
            "the bytes live in blob storage"
        )
        # page_content stays empty for the same reason.
        assert point.payload["page_content"] == ""
        # Identity metadata is preserved verbatim.
        assert metadata["orgId"] == "org-1"
        assert metadata["virtualRecordId"] == "v1"
        assert metadata["blockId"] == "b3"

    def test_dense_vector_only(self):
        from app.modules.transformers.vectorstore import VectorStore

        point = VectorStore._build_image_point([0.5, 0.6, 0.7], {"image_uri": "u"})

        assert set(point.vector.keys()) == {"dense"}
        assert point.vector["dense"] == [0.5, 0.6, 0.7]


# ===================================================================
# Every native path produces the new image-point shape
# ===================================================================

class TestNativePathsEmitCleanPoints:
    """Each native image-embedding path must write ``page_content=""`` and
    must NOT copy the image URI onto the payload — the bytes live in blob
    storage and are resolved downstream. These tests cover the
    Cohere / Voyage / Bedrock / Jina / Gemini / generic paths."""

    @pytest.mark.asyncio
    async def test_cohere_payload_shape(self):
        # The Cohere image path lazy-imports ``cohere`` and instantiates
        # ``cohere.ClientV2``. ``unittest.mock.patch("cohere.ClientV2")``
        # resolves the target eagerly, so without the package installed
        # the patch itself raises ``ModuleNotFoundError``. Skip rather
        # than fail in minimal dev environments; CI runs with the full
        # dependency set installed.
        pytest.importorskip("cohere")

        vs = _make_vectorstore()
        vs.api_key = "k"
        vs.model_name = "embed-v3"

        mock_response = MagicMock()
        mock_response.embeddings.float = [[0.1, 0.2]]

        mock_co = MagicMock()
        mock_co.embed.return_value = mock_response

        chunks = [{"metadata": {"orgId": "o"}, "image_uri": "data:image/png;base64,abc"}]
        with patch("cohere.ClientV2", return_value=mock_co):
            points = await vs._process_image_embeddings_cohere(chunks, ["data:image/png;base64,abc"])

        assert len(points) == 1
        assert points[0].payload["page_content"] == ""
        # The image bytes must stay OUT of the Qdrant payload — they
        # live in blob storage and are resolved at read time via
        # ``block.data.uri``. Persisting them here would bloat the
        # collection for no read-path benefit.
        assert "imageUri" not in points[0].payload["metadata"]
        assert points[0].payload["metadata"]["blockType"] == "image"

    @pytest.mark.asyncio
    async def test_bedrock_payload_shape_and_output_dim(self):
        vs = _make_vectorstore()
        vs.aws_access_key_id = "AKID"
        vs.aws_secret_access_key = "s"
        vs.region_name = "us-east-1"
        vs.model_name = "amazon.titan-embed-image-v1"
        vs.output_dimensions = 384  # user chose 384

        captured: dict = {}

        mock_body = MagicMock()
        mock_body.read.return_value = json.dumps({"embedding": [0.1, 0.2]}).encode()

        def _invoke(**kwargs):
            captured["body"] = json.loads(kwargs["body"])
            return {"body": mock_body}

        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = _invoke

        chunks = [{"metadata": {"orgId": "o"}, "image_uri": "data:image/png;base64,aW1nCg=="}]

        with patch("boto3.client", return_value=mock_client):
            points = await vs._process_image_embeddings_bedrock(
                chunks, ["data:image/png;base64,aW1nCg=="]
            )

        assert len(points) == 1
        # outputEmbeddingLength is forwarded from self.output_dimensions, not hardcoded 1024.
        assert captured["body"]["embeddingConfig"]["outputEmbeddingLength"] == 384
        assert points[0].payload["page_content"] == ""
        assert "imageUri" not in points[0].payload["metadata"]

    @pytest.mark.asyncio
    async def test_bedrock_output_dim_defaults_to_1024(self):
        vs = _make_vectorstore()
        vs.aws_access_key_id = "AKID"
        vs.aws_secret_access_key = "s"
        vs.region_name = "us-east-1"
        vs.model_name = "amazon.titan-embed-image-v1"
        vs.output_dimensions = None  # not configured

        captured: dict = {}

        mock_body = MagicMock()
        mock_body.read.return_value = json.dumps({"embedding": [0.1]}).encode()

        def _invoke(**kwargs):
            captured["body"] = json.loads(kwargs["body"])
            return {"body": mock_body}

        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = _invoke

        chunks = [{"metadata": {}, "image_uri": "data:image/png;base64,aW1n"}]

        with patch("boto3.client", return_value=mock_client):
            await vs._process_image_embeddings_bedrock(
                chunks, ["data:image/png;base64,aW1n"]
            )

        assert captured["body"]["embeddingConfig"]["outputEmbeddingLength"] == 1024

    @pytest.mark.asyncio
    async def test_voyage_routes_to_multimodal_endpoint_via_data_urls(self):
        """Regression test: Voyage's ``VoyageEmbeddings._invocation_params``
        distinguishes the multimodal endpoint from the text one by
        checking ``startswith("data:image")``. If we hand it bare base64
        (which block storage sometimes does) the SDK would silently route
        to the text endpoint. After the fix we normalize everything to a
        ``data:`` URL first."""
        vs = _make_vectorstore()
        vs.dense_embeddings = MagicMock()
        vs.dense_embeddings.batch_size = 2
        captured: dict = {"inputs": []}

        async def _aembed(inputs):
            captured["inputs"].append(list(inputs))
            return [[0.1, 0.2]] * len(inputs)

        vs.dense_embeddings.aembed_documents = AsyncMock(side_effect=_aembed)

        chunks = [{"metadata": {"orgId": "o"}, "image_uri": "aW1hZ2U="}]
        points = await vs._process_image_embeddings_voyage(chunks, ["aW1hZ2U="])

        assert len(points) == 1
        assert points[0].payload["page_content"] == ""
        # The call into VoyageEmbeddings must see a data URL so it routes
        # to /v1/multimodalembeddings.
        flat = [i for batch in captured["inputs"] for i in batch]
        assert flat, "Voyage aembed_documents was not called"
        assert all(i.startswith("data:image/") for i in flat), flat

    @pytest.mark.asyncio
    async def test_voyage_skips_unnormalizable_inputs(self):
        vs = _make_vectorstore()
        vs.dense_embeddings = MagicMock()
        vs.dense_embeddings.batch_size = 4
        vs.dense_embeddings.aembed_documents = AsyncMock(return_value=[])

        # Completely bogus payload (spaces + special chars) cannot be
        # normalized — the Voyage path must drop it instead of sending
        # garbage to the endpoint.
        chunks = [{"metadata": {}, "image_uri": "not a valid !@#"}]
        points = await vs._process_image_embeddings_voyage(chunks, ["not a valid !@#"])

        assert points == []
        vs.dense_embeddings.aembed_documents.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_generic_payload_shape(self):
        vs = _make_vectorstore()
        vs.dense_embeddings = MagicMock()
        vs.dense_embeddings.aembed_documents = AsyncMock(return_value=[[0.1, 0.2]])
        vs.embedding_provider = "openAICompatible"

        chunks = [{"metadata": {"orgId": "o"}, "image_uri": "data:image/png;base64,aW1n"}]
        points = await vs._process_image_embeddings_generic(
            chunks, ["data:image/png;base64,aW1n"]
        )

        assert len(points) == 1
        assert points[0].payload["page_content"] == ""
        assert "imageUri" not in points[0].payload["metadata"]
        assert points[0].payload["metadata"]["blockType"] == "image"

    @pytest.mark.asyncio
    async def test_gemini_v2_omits_task_type(self):
        """Regression: ``gemini-embedding-2*`` (the multimodal family —
        ``-2-preview``, ``-2``, …) does NOT use ``taskType``. Per
        Google's docs the recommended asymmetric-retrieval signal for
        v2 is a prompt-prefix on text inputs (handled by the LangChain
        wrapper around the text path); image inputs need no signal at
        all because they share the unified embedding space.

        Sending ``taskType=RETRIEVAL_DOCUMENT`` on the v2 image path was
        observed to push image vectors into a sub-space unreachable from
        text queries — the "PNG retrieval 0-hit" bug. This test pins the
        image body to omit ``taskType`` so we don't regress.
        """
        vs = _make_vectorstore()
        vs.api_key = "fake-key"
        vs.model_name = "gemini-embedding-2-preview"
        vs.output_dimensions = 3072

        captured: dict = {}

        class _Resp:
            status_code = 200

            def json(self):
                return {"embedding": {"values": [0.1, 0.2, 0.3]}}

            def raise_for_status(self):
                return None

        class _Client:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *args):
                return None

            async def post(self_inner, url, headers=None, json=None):
                captured["url"] = url
                captured["headers"] = headers
                captured["body"] = json
                return _Resp()

        chunks = [
            {
                "metadata": {"orgId": "o", "virtualRecordId": "vr1", "blockIndex": 0},
                "image_uri": "data:image/png;base64,aW1n",
            }
        ]

        async def _fake_normalize(uri):
            return ("aW1n", "image/png")

        with patch.object(vs, "_normalize_image_with_mime", side_effect=_fake_normalize), \
             patch("httpx.AsyncClient", return_value=_Client()):
            points = await vs._process_image_embeddings_gemini(
                chunks, ["data:image/png;base64,aW1n"]
            )

        assert len(points) == 1
        # The whole point of this test: v2 must NOT send taskType, or the
        # image vectors land in a subspace unreachable from text queries.
        assert "taskType" not in captured["body"]
        # And keep the other invariants intact.
        assert captured["body"]["output_dimensionality"] == 3072
        assert points[0].payload["page_content"] == ""
        assert points[0].payload["metadata"]["blockType"] == "image"
        assert "imageUri" not in points[0].payload["metadata"]

    @pytest.mark.asyncio
    async def test_gemini_v1_sends_retrieval_document_task_type(self):
        """Regression: ``gemini-embedding-001`` (the text-only generation)
        DOES honour ``taskType`` and pairs ``RETRIEVAL_DOCUMENT`` ↔
        ``RETRIEVAL_QUERY`` vectors. Even though v1 returns HTTP 400 for
        ``inline_data`` parts in production, we keep ``taskType`` on the
        body so the early-400 detection path sees a well-formed request
        and the caller can fall back to VLM captioning cleanly.
        """
        vs = _make_vectorstore()
        vs.api_key = "fake-key"
        vs.model_name = "gemini-embedding-001"
        vs.output_dimensions = 768

        captured: dict = {}

        class _Resp:
            status_code = 200

            def json(self):
                return {"embedding": {"values": [0.1, 0.2, 0.3]}}

            def raise_for_status(self):
                return None

        class _Client:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *args):
                return None

            async def post(self_inner, url, headers=None, json=None):
                captured["url"] = url
                captured["headers"] = headers
                captured["body"] = json
                return _Resp()

        chunks = [
            {
                "metadata": {"orgId": "o", "virtualRecordId": "vr1", "blockIndex": 0},
                "image_uri": "data:image/png;base64,aW1n",
            }
        ]

        async def _fake_normalize(uri):
            return ("aW1n", "image/png")

        with patch.object(vs, "_normalize_image_with_mime", side_effect=_fake_normalize), \
             patch("httpx.AsyncClient", return_value=_Client()):
            await vs._process_image_embeddings_gemini(
                chunks, ["data:image/png;base64,aW1n"]
            )

        assert captured["body"]["taskType"] == "RETRIEVAL_DOCUMENT"


# ===================================================================
# Collection signature: is_multimodal
# ===================================================================

class TestSignatureIsMultimodal:
    def test_build_signature_includes_flag_when_known(self):
        from app.modules.transformers.vectorstore import _build_embedding_signature

        sig = _build_embedding_signature(
            "cohere", "embed-v4.0", 1024, is_multimodal=True
        )
        assert sig["is_multimodal"] is True

    def test_build_signature_omits_flag_when_unknown(self):
        from app.modules.transformers.vectorstore import _build_embedding_signature

        sig = _build_embedding_signature("cohere", "embed-v3", 1024)
        assert "is_multimodal" not in sig

    def test_signatures_match_when_flags_agree(self):
        from app.modules.transformers.vectorstore import (
            _build_embedding_signature,
            _signatures_match,
        )

        stored = _build_embedding_signature(
            "gemini", "gemini-embedding-2-preview", 3072, is_multimodal=True
        )

        assert _signatures_match(
            stored=stored,
            new_provider="gemini",
            new_model="gemini-embedding-2-preview",
            new_is_multimodal=True,
        )

    def test_signatures_mismatch_on_multimodal_flip(self):
        """Same provider/model/dim, but the ``isMultimodal`` flag toggled
        from True to False must be caught as a mismatch — the text-only
        and multimodal modes of the same model produce vectors that
        aren't interchangeable."""
        from app.modules.transformers.vectorstore import (
            _build_embedding_signature,
            _signatures_match,
        )

        stored = _build_embedding_signature(
            "cohere", "embed-v4.0", 1024, is_multimodal=True
        )

        assert not _signatures_match(
            stored=stored,
            new_provider="cohere",
            new_model="embed-v4.0",
            new_is_multimodal=False,
        )

    def test_missing_flag_treated_as_unknown(self):
        """Legacy signatures written before the flag existed must not
        look like a mismatch when the new side does know the flag."""
        from app.modules.transformers.vectorstore import (
            _build_embedding_signature,
            _signatures_match,
        )

        stored = _build_embedding_signature("cohere", "embed-v3", 1024)  # no flag

        assert _signatures_match(
            stored=stored,
            new_provider="cohere",
            new_model="embed-v3",
            new_is_multimodal=True,
        )
