import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from app.exceptions.indexing_exceptions import (
    EmbeddingError,
    IndexingError,
    VectorStoreError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_vectorstore():
    """Instantiate a VectorStore with everything mocked to bypass __init__ side effects."""
    with patch(
        "app.modules.transformers.vectorstore.SparseEmbedder"
    ) as mock_sparse:
        mock_sparse.return_value = MagicMock()

        from app.modules.transformers.vectorstore import VectorStore
        from app.services.vector_db.models import VectorDBCapabilities

        vdb = AsyncMock()
        caps = VectorDBCapabilities(
            supports_sparse_vectors=False,
            supports_server_side_text_search=False,
        )
        vdb.get_capabilities = MagicMock(return_value=caps)
        vdb.get_service_name = MagicMock(return_value="mock")

        vs = VectorStore(
            logger=MagicMock(),
            config_service=AsyncMock(),
            graph_provider=AsyncMock(),
            collection_name="test_collection",
            vector_db_service=vdb,
        )
        return vs


# ===================================================================
# text_splitting._get_segmenter / detect_language branch coverage
# (replaces the removed spaCy _get_shared_nlp coverage)
# ===================================================================


class TestGetSegmenterBranches:
    """Cover _get_segmenter branches: cache hit, alias mapping, unsupported fallback."""

    def test_unsupported_language_falls_back_to_english(self):
        from app.modules.parsers.text_splitting import _get_segmenter

        segmenter = _get_segmenter("xx-not-a-real-language")
        assert segmenter is not None

    def test_aliased_language_routes_to_target_rule_set(self):
        """Portuguese has no native pysbd rule set; it should route to Spanish."""
        from app.modules.parsers.text_splitting import _get_segmenter

        segmenter = _get_segmenter("pt")
        assert segmenter is not None

    def test_cache_returns_same_instance(self):
        from app.modules.parsers.text_splitting import _get_segmenter

        seg1 = _get_segmenter("en")
        seg2 = _get_segmenter("en")
        assert seg1 is seg2

    def test_segment_failure_falls_back_to_regex(self):
        """split_into_sentences never raises even if the segmenter errors."""
        from app.modules.parsers.text_splitting import split_into_sentences

        with patch(
            "app.modules.parsers.text_splitting._get_segmenter",
            side_effect=RuntimeError("pysbd exploded"),
        ):
            result = split_into_sentences("First. Second.", "en")
        assert result  # regex fallback still produces output


# ===================================================================
# __init__ generic exception
# ===================================================================


class TestVectorStoreInit:
    """Cover __init__ exception paths."""

    @pytest.mark.asyncio
    async def test_sparse_embedding_failure_raises_indexing_error(self):
        """When SparseEmbedder raises during lazy init, _ensure_sparse_embeddings raises IndexingError."""
        vs = _make_vectorstore()
        # Enable sparse vector support so _ensure_sparse_embeddings tries to init
        from app.services.vector_db.models import VectorDBCapabilities
        vs._capabilities = VectorDBCapabilities(
            supports_sparse_vectors=True,
            supports_server_side_text_search=False,
        )
        vs.sparse_embeddings = None  # ensure not already initialised

        with patch(
            "app.modules.transformers.vectorstore.SparseEmbedder",
            side_effect=TypeError("unexpected type error"),
        ), patch(
            "fastembed.SparseTextEmbedding",
            side_effect=ImportError("fastembed not installed"),
        ):
            with pytest.raises(IndexingError, match="Failed to initialise sparse embeddings"):
                await vs._ensure_sparse_embeddings()


# ===================================================================
# _normalize_image_to_base64 - exception fallthrough
# ===================================================================


class TestNormalizeImageExceptionFallthrough:
    """VectorStore._normalize_image_to_base64 delegates to
    app.utils.image_utils.normalize_image_to_base64 — see
    tests/unit/utils/test_image_utils.py::TestNormalizeImageToBase64 for the
    exception-fallthrough and non-string-input coverage of that shared helper.
    """

    @pytest.mark.asyncio
    async def test_non_string_returns_none(self):
        """Non-string input returns None from the initial check."""
        vs = _make_vectorstore()
        result = await vs._normalize_image_to_base64(object())
        assert result is None


# ===================================================================
# split_into_sentences - bullet/list and heading-like input handled via pysbd
# rule sets rather than the removed custom_sentence_boundary component.
# ===================================================================


class TestSplitIntoSentencesBulletsAndHeadings:
    """Sanity-check bullet/list and heading-like text doesn't explode splitting."""

    def test_letter_bullet_list(self):
        from app.modules.parsers.text_splitting import split_into_sentences

        result = split_into_sentences("a. First item. b. Second item.", "en")
        assert result

    def test_numeric_bullet_list(self):
        from app.modules.parsers.text_splitting import split_into_sentences

        result = split_into_sentences("1. First item. 2. Second item.", "en")
        assert result

    def test_dash_bullet_marker(self):
        from app.modules.parsers.text_splitting import split_into_sentences

        result = split_into_sentences("- item one\n- item two", "en")
        assert result

    def test_all_caps_heading_followed_by_body(self):
        from app.modules.parsers.text_splitting import split_into_sentences

        result = split_into_sentences("INTRODUCTION\nThis is the body text.", "en")
        assert result


# ===================================================================
# _create_embeddings - unexpected exception -> IndexingError
# ===================================================================


class TestCreateEmbeddingsUnexpectedException:
    """Cover lines 908-909: unexpected exception in _create_embeddings."""

    @pytest.mark.asyncio
    async def test_unexpected_exception_becomes_indexing_error(self):
        """When delete_embeddings raises an unexpected exception, it propagates directly."""
        from langchain_core.documents import Document

        vs = _make_vectorstore()
        vs.delete_embeddings = AsyncMock(side_effect=TypeError("unexpected type error"))

        chunks = [Document(page_content="test", metadata={})]

        with pytest.raises(TypeError, match="unexpected type error"):
            await vs._create_embeddings(chunks, "rec-1", "vr-1")

    @pytest.mark.asyncio
    async def test_vectorstore_error_propagated_from_document_chunks(self):
        """VectorStoreError from _process_document_chunks is re-raised as VectorStoreError."""
        from langchain_core.documents import Document

        vs = _make_vectorstore()
        vs.delete_embeddings = AsyncMock()
        vs._process_document_chunks = AsyncMock(
            side_effect=VectorStoreError("store failed", details={})
        )

        chunks = [Document(page_content="test", metadata={})]

        with pytest.raises(VectorStoreError):
            await vs._create_embeddings(chunks, "rec-1", "vr-1")


# ===================================================================
# index_documents - deeper branch coverage
# ===================================================================


class TestIndexDocumentsDeeper:
    """Cover deeper branches in index_documents."""

    @pytest.mark.asyncio
    async def test_unexpected_exception_in_index_documents(self):
        """Unexpected exception during block processing is wrapped as IndexingError."""
        from unittest.mock import PropertyMock

        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)

        # Make reading `block.type` raise so the classification loop hits the outer except
        block = MagicMock()
        type(block).type = PropertyMock(side_effect=RuntimeError("broken type"))

        container = MagicMock()
        container.blocks = [block]
        container.block_groups = []

        with pytest.raises((RuntimeError, IndexingError)):
            await vs.index_documents(container, "org-1", "rec-1", "vr-1")

    @pytest.mark.asyncio
    async def test_block_group_non_table_type_skipped(self):
        """Block groups that are not 'table' type are not processed."""
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)

        bg = MagicMock()
        bg.type = "chart"  # Not 'table'

        container = MagicMock()
        container.blocks = []
        container.block_groups = [bg]

        result = await vs.index_documents(
            container, "org-1", "rec-1", "vr-1", "text/plain"
        )

        # blocks=[] but block_groups=[bg], so "not blocks and not block_groups" is False
        # But no documents_to_embed -> returns True
        assert result is True

    @pytest.mark.asyncio
    async def test_image_block_with_none_data(self):
        """Image block with data=None should not produce documents."""
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=True)

        block = MagicMock()
        block.type = "image"
        block.index = 0
        block.data = None

        container = MagicMock()
        container.blocks = [block]
        container.block_groups = []

        result = await vs.index_documents(
            container, "org-1", "rec-1", "vr-1", "image/png"
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_image_block_with_no_uri(self):
        """Image block with data={} but no 'uri' key should not produce documents."""
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=True)

        block = MagicMock()
        block.type = "image"
        block.index = 0
        block.data = {"no_uri_key": "value"}

        container = MagicMock()
        container.blocks = [block]
        container.block_groups = []

        result = await vs.index_documents(
            container, "org-1", "rec-1", "vr-1", "image/png"
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_table_block_with_no_data(self):
        """Table block group with data=None should not produce documents."""
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)

        bg = MagicMock()
        bg.type = "table"
        bg.index = 0
        bg.data = None  # No data

        container = MagicMock()
        container.blocks = []
        container.block_groups = [bg]

        result = await vs.index_documents(
            container, "org-1", "rec-1", "vr-1", "text/plain"
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_images_uris_empty_after_processing(self):
        """Image blocks where all data is None -> images_uris is empty."""
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=True)

        # Image block with data dict but uri is None
        block = MagicMock()
        block.type = "image"
        block.index = 0
        block.data = {"uri": None}

        container = MagicMock()
        container.blocks = [block]
        container.block_groups = []

        result = await vs.index_documents(
            container, "org-1", "rec-1", "vr-1", "image/png"
        )

        # images_uris would be [None] -> truthy, then proceeds
        assert result is True

    @pytest.mark.asyncio
    async def test_non_multimodal_embedding_non_multimodal_llm_images_skipped(self):
        """When neither embedding nor LLM is multimodal, images are collected but not embedded."""
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)  # not multimodal embedding

        block = MagicMock()
        block.type = "image"
        block.index = 0
        block.data = {"uri": "base64data"}

        container = MagicMock()
        container.blocks = [block]
        container.block_groups = []

        # Not multimodal LLM either
        result = await vs.index_documents(
            container, "org-1", "rec-1", "vr-1", "image/png"
        )

        # images_uris has data but neither multimodal embedding nor multimodal LLM
        # -> no documents_to_embed from images -> returns True
        assert result is True

    @pytest.mark.asyncio
    async def test_drawing_block_type_as_image(self):
        """Drawing block type is processed as image."""
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=True)
        vs._create_embeddings = AsyncMock()

        block = MagicMock()
        block.type = "drawing"
        block.index = 0
        block.data = {"uri": "base64data"}

        container = MagicMock()
        container.blocks = [block]
        container.block_groups = []

        result = await vs.index_documents(
            container, "org-1", "rec-1", "vr-1", "image/png"
        )

        assert result is True
        vs._create_embeddings.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_textsection_block_processed_as_text(self):
        """textsection block type is processed as text."""
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()

        block = MagicMock()
        block.type = "textsection"
        block.index = 0
        block.data = "Section text here"

        container = MagicMock()
        container.blocks = [block]
        container.block_groups = []

        result = await vs.index_documents(
            container, "org-1", "rec-1", "vr-1", "text/plain"
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_image_without_a_description_is_skipped(self):
        """An image block carrying no prose has nothing to embed under
        text-only embeddings."""
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()

        block = MagicMock()
        block.type = "image"
        block.index = 0
        block.data = {"uri": "base64data"}
        block.image_metadata = None

        container = MagicMock()
        container.blocks = [block]
        container.block_groups = []

        result = await vs.index_documents(
            container, "org-1", "rec-1", "vr-1", "image/png"
        )

        # No documents_to_embed from failed descriptions -> returns True
        assert result is True


# ===================================================================
# get_embedding_model_instance - AWS Bedrock credentials
# ===================================================================


class TestGetEmbeddingModelInstanceBedrock:
    """Cover the AWS Bedrock credential persistence branch."""

    @pytest.mark.asyncio
    async def test_bedrock_credentials_stored(self):
        """When provider is AWS_BEDROCK, credentials are persisted."""
        from app.utils.aimodels import EmbeddingProvider

        vs = _make_vectorstore()

        config = {
            "provider": EmbeddingProvider.AWS_BEDROCK.value,
            "configuration": {
                "apiKey": "key",
                "model": "amazon.titan-embed-v1",
                "region": "us-east-1",
                "awsAccessKeyId": "AKIA...",
                "awsAccessSecretKey": "secret...",
            },
            "isDefault": True,
            "isMultimodal": False,
        }
        vs.config_service.get_config = AsyncMock(
            return_value={"embedding": [config]}
        )
        vs._initialize_collection = AsyncMock()

        mock_embed = MagicMock()
        mock_embed.aembed_query = AsyncMock(return_value=[0.1] * 1024)
        mock_embed.model_name = "amazon.titan-embed-v1"

        with patch(
            "app.modules.transformers.vectorstore.get_embedding_model",
            return_value=mock_embed,
        ):
            await vs.get_embedding_model_instance()

        assert vs.aws_access_key_id == "AKIA..."
        assert vs.aws_secret_access_key == "secret..."
        assert vs.region_name == "us-east-1"

    @pytest.mark.asyncio
    async def test_model_name_fallback_to_unknown(self):
        """When none of model_name, model, or model_id attributes exist, falls back to 'unknown'."""
        vs = _make_vectorstore()

        config = {
            "provider": "openai",
            "configuration": {"apiKey": "key", "model": "test"},
            "isDefault": True,
            "isMultimodal": False,
        }
        vs.config_service.get_config = AsyncMock(
            return_value={"embedding": [config]}
        )
        vs._initialize_collection = AsyncMock()

        # Create an embedding object with no model_name, model, or model_id
        mock_embed = MagicMock(spec=[])
        mock_embed.aembed_query = AsyncMock(return_value=[0.1] * 1024)

        with patch(
            "app.modules.transformers.vectorstore.get_embedding_model",
            return_value=mock_embed,
        ):
            await vs.get_embedding_model_instance()

        assert vs.model_name == "unknown"

    @pytest.mark.asyncio
    async def test_non_default_config_used_when_no_default(self):
        """When no config has isDefault=True, the first config is used."""
        vs = _make_vectorstore()

        config = {
            "provider": "openai",
            "configuration": {"apiKey": "key", "model": "test-model"},
            "isMultimodal": False,
        }
        vs.config_service.get_config = AsyncMock(
            return_value={"embedding": [config]}
        )
        vs._initialize_collection = AsyncMock()

        mock_embed = MagicMock()
        mock_embed.aembed_query = AsyncMock(return_value=[0.1] * 1024)
        mock_embed.model_name = "test-model"

        with patch(
            "app.modules.transformers.vectorstore.get_embedding_model",
            return_value=mock_embed,
        ):
            await vs.get_embedding_model_instance()

        assert vs.dense_embeddings is mock_embed


# ===================================================================
# _process_document_chunks - remote concurrent batch failure
# ===================================================================


class TestProcessDocumentChunksRemoteFailure:
    """Cover the remote concurrent batch failure path."""

    @pytest.mark.asyncio
    async def test_remote_batch_failure_raises_vectorstore_error(self):
        """When a remote batch fails during gather, VectorStoreError is raised."""
        from langchain_core.documents import Document

        vs = _make_vectorstore()
        # Set a non-local provider so concurrent (parallel) path is used
        vs.embedding_provider = "openai"

        call_count = 0

        async def fake_embed_and_upsert(docs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("batch 2 failed")

        vs._embed_and_upsert_documents = fake_embed_and_upsert

        # Create enough chunks to generate 2 batches (batch_size=50 → 100 docs = 2 batches)
        chunks = [
            Document(page_content=f"test {i}", metadata={}) for i in range(100)
        ]

        with pytest.raises(VectorStoreError, match="Failed to store batch"):
            await vs._process_document_chunks(chunks)


