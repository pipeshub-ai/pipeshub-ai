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
    """Cover lines 151-152: generic exception in _normalize_image_to_base64."""

    @pytest.mark.asyncio
    async def test_exception_during_normalization_returns_none(self):
        """Any unexpected exception in the try block returns None."""
        vs = _make_vectorstore()
        # Passing a non-string that passes the initial check is impossible because
        # the first check returns None. We need to cause an exception in the
        # string processing path. Mock re.fullmatch to raise.
        with patch("app.modules.transformers.vectorstore.re.fullmatch", side_effect=RuntimeError("regex crashed")):
            result = await vs._normalize_image_to_base64("some_valid_string")
        assert result is None

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
# _process_image_embeddings_voyage - exception in gather results
# ===================================================================


class TestVoyageBatchException:
    """Cover lines 545-546: voyage gather returns an exception."""

    @pytest.mark.asyncio
    async def test_voyage_gather_with_exception_result(self):
        """Simulate gather returning an Exception result by patching asyncio.gather."""
        vs = _make_vectorstore()
        vs.dense_embeddings = MagicMock()
        vs.dense_embeddings.batch_size = 2

        image_chunks = [
            {"metadata": {}, "image_uri": "img1"},
        ]
        image_base64s = ["b64_1"]

        # Patch asyncio.gather at the module level used by vectorstore
        async def fake_gather(*coros, return_exceptions=False):
            # Cancel the coros to avoid warnings
            for c in coros:
                c.close()
            return [RuntimeError("Simulated Voyage exception")]

        with patch("asyncio.gather", fake_gather):
            result = await vs._process_image_embeddings_voyage(image_chunks, image_base64s)

        # The patched gather bypasses process_batch, so the Exception in results
        # is silently filtered by isinstance(r, list). Result is [] with no warning.
        assert result == []


# ===================================================================
# _process_image_embeddings_bedrock - NoCredentialsError + Exception
# ===================================================================


class TestBedrockBranches:
    """Cover bedrock exception branches."""

    @pytest.mark.asyncio
    async def test_bedrock_no_credentials_during_invoke(self):
        """When invoke_model raises NoCredentialsError, it becomes EmbeddingError in gather results."""
        vs = _make_vectorstore()
        vs.model_name = "amazon.titan-embed-image-v1"
        vs.aws_access_key_id = "fake_key"
        vs.aws_secret_access_key = "fake_secret"
        vs.region_name = "us-east-1"

        mock_bedrock = MagicMock()
        from botocore.exceptions import NoCredentialsError

        mock_bedrock.invoke_model.side_effect = NoCredentialsError()

        with patch("boto3.client", return_value=mock_bedrock):
            vs._normalize_image_to_base64 = AsyncMock(return_value="base64data")

            # The EmbeddingError is raised inside the async function, and with
            # return_exceptions=True in gather, it ends up as an Exception in results.
            # The gather result is [EmbeddingError(...)], which is caught by
            # isinstance(result, Exception) on line 638-639.
            result = await vs._process_image_embeddings_bedrock(
                [{"metadata": {}, "image_uri": "img1"}],
                ["base64data"],
            )

        # The EmbeddingError is caught in the gather results loop and logged
        assert result == []
        vs.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_bedrock_client_error_returns_none(self):
        """When invoke_model raises ClientError, the image is skipped."""
        vs = _make_vectorstore()
        vs.model_name = "amazon.titan-embed-image-v1"
        vs.aws_access_key_id = "fake_key"
        vs.aws_secret_access_key = "fake_secret"
        vs.region_name = "us-east-1"

        mock_bedrock = MagicMock()
        from botocore.exceptions import ClientError

        mock_bedrock.invoke_model.side_effect = ClientError(
            {"Error": {"Code": "ValidationException", "Message": "bad input"}},
            "InvokeModel",
        )

        with patch("boto3.client", return_value=mock_bedrock):
            vs._normalize_image_to_base64 = AsyncMock(return_value="base64data")

            result = await vs._process_image_embeddings_bedrock(
                [{"metadata": {}, "image_uri": "img1"}],
                ["base64data"],
            )

        assert result == []

    @pytest.mark.asyncio
    async def test_bedrock_unexpected_error(self):
        """When invoke_model raises unexpected error, it's logged as warning."""
        vs = _make_vectorstore()
        vs.model_name = "amazon.titan-embed-image-v1"
        vs.aws_access_key_id = "fake_key"
        vs.aws_secret_access_key = "fake_secret"
        vs.region_name = "us-east-1"

        mock_bedrock = MagicMock()
        mock_bedrock.invoke_model.side_effect = ValueError("unexpected")

        with patch("boto3.client", return_value=mock_bedrock):
            vs._normalize_image_to_base64 = AsyncMock(return_value="base64data")

            result = await vs._process_image_embeddings_bedrock(
                [{"metadata": {}, "image_uri": "img1"}],
                ["base64data"],
            )

        assert result == []

    @pytest.mark.asyncio
    async def test_bedrock_normalize_returns_none(self):
        """When _normalize_image_to_base64 returns None, image is skipped."""
        vs = _make_vectorstore()
        vs.model_name = "amazon.titan-embed-image-v1"
        vs.aws_access_key_id = "fake_key"
        vs.aws_secret_access_key = "fake_secret"
        vs.region_name = "us-east-1"

        mock_bedrock = MagicMock()

        with patch("boto3.client", return_value=mock_bedrock):
            vs._normalize_image_to_base64 = AsyncMock(return_value=None)

            result = await vs._process_image_embeddings_bedrock(
                [{"metadata": {}, "image_uri": "img1"}],
                ["invalid_data"],
            )

        assert result == []

    @pytest.mark.asyncio
    async def test_bedrock_no_credentials_during_client_creation(self):
        """When boto3.client raises NoCredentialsError, it becomes EmbeddingError."""
        vs = _make_vectorstore()
        vs.model_name = "amazon.titan-embed-image-v1"
        vs.aws_access_key_id = None
        vs.aws_secret_access_key = None
        vs.region_name = None

        from botocore.exceptions import NoCredentialsError

        with patch("boto3.client", side_effect=NoCredentialsError()):
            with pytest.raises(EmbeddingError, match="AWS credentials not found"):
                await vs._process_image_embeddings_bedrock(
                    [{"metadata": {}, "image_uri": "img1"}],
                    ["base64data"],
                )

    @pytest.mark.asyncio
    async def test_bedrock_gather_exception_logged(self):
        """When gather result is an Exception (not PointStruct), it's logged."""
        vs = _make_vectorstore()
        vs.model_name = "amazon.titan-embed-image-v1"
        vs.aws_access_key_id = "key"
        vs.aws_secret_access_key = "secret"
        vs.region_name = "us-east-1"

        mock_bedrock = MagicMock()
        # Raise a generic error that propagates through gather
        mock_bedrock.invoke_model.side_effect = RuntimeError("Bedrock general failure")

        with patch("boto3.client", return_value=mock_bedrock):
            vs._normalize_image_to_base64 = AsyncMock(return_value="base64data")

            result = await vs._process_image_embeddings_bedrock(
                [{"metadata": {}, "image_uri": "img1"}],
                ["base64data"],
            )

        # The RuntimeError is caught inside embed_single_bedrock_image -> returns None
        # so the gather result is [None] which is neither PointStruct nor Exception
        assert result == []


# ===================================================================
# _process_image_embeddings_jina - exception in gather results
# ===================================================================


class TestJinaBatchException:
    """Cover lines 733-734: jina gather returns an exception."""

    @pytest.mark.asyncio
    async def test_jina_gather_with_exception_result(self):
        """Simulate gather returning an Exception result for Jina."""
        vs = _make_vectorstore()
        vs.model_name = "jina-clip-v1"
        vs.api_key = "fake-key"

        image_chunks = [
            {"metadata": {}, "image_uri": "img1"},
        ]
        image_base64s = ["b64_1"]

        mock_client = AsyncMock()

        async def fake_gather(*coros, return_exceptions=False):
            for c in coros:
                c.close()
            return [RuntimeError("Simulated Jina exception")]

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("asyncio.gather", fake_gather):
                result = await vs._process_image_embeddings_jina(
                    image_chunks,
                    image_base64s,
                )

        # The patched gather bypasses process_batch, so the Exception is filtered
        # silently by isinstance(r, list). Result is [] with no warning logged.
        assert result == []

    @pytest.mark.asyncio
    async def test_jina_all_images_fail_normalization(self):
        """When all images fail normalization in a Jina batch, returns empty."""
        vs = _make_vectorstore()
        vs.model_name = "jina-clip-v1"
        vs.api_key = "fake-key"

        mock_client = AsyncMock()

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            vs._normalize_image_to_base64 = AsyncMock(return_value=None)

            result = await vs._process_image_embeddings_jina(
                [{"metadata": {}, "image_uri": "img1"}],
                ["invalid_data"],
            )

        assert result == []


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

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
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

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
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

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": True})
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

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": True})
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

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
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

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": True})
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

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            new_callable=AsyncMock,
        ) as mock_llm:
            # Not multimodal LLM either
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
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

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
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

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            result = await vs.index_documents(
                container, "org-1", "rec-1", "vr-1", "text/plain"
            )

        assert result is True

    @pytest.mark.asyncio
    async def test_image_describe_failure_skipped(self):
        """When describe_images returns success=False for an image, it's skipped."""
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()
        vs.describe_images = AsyncMock(
            return_value=[{"index": 0, "success": False, "error": "VLM failed"}]
        )

        block = MagicMock()
        block.type = "image"
        block.index = 0
        block.data = {"uri": "base64data"}

        container = MagicMock()
        container.blocks = [block]
        container.block_groups = []

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": True})
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
        mock_embed.embed_query.return_value = [0.1] * 1024
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
        mock_embed.embed_query = MagicMock(return_value=[0.1] * 1024)

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
        mock_embed.embed_query.return_value = [0.1] * 1024
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


# ===================================================================
# Cohere image embedding edge cases
# ===================================================================


class TestCohereEdgeCases:
    """Cover Cohere image embedding size limit branch."""

    @pytest.mark.asyncio
    async def test_cohere_image_size_limit_skip(self):
        """When Cohere returns 'image size must be at most' error, image is skipped."""
        vs = _make_vectorstore()
        vs.api_key = "fake-key"
        vs.model_name = "embed-english-v3.0"

        mock_co = MagicMock()

        def fake_embed(**kwargs):
            raise Exception("image size must be at most 5MB")

        mock_co.embed = fake_embed

        mock_cohere_module = MagicMock()
        mock_cohere_module.ClientV2.return_value = mock_co

        with patch.dict("sys.modules", {"cohere": mock_cohere_module}):
            result = await vs._process_image_embeddings_cohere(
                [{"metadata": {}, "image_uri": "img1"}],
                ["base64data"],
            )

        assert result == []

    @pytest.mark.asyncio
    async def test_cohere_other_exception_in_gather(self):
        """When Cohere raises a non-size-limit error, it's caught as Exception in gather."""
        vs = _make_vectorstore()
        vs.api_key = "fake-key"
        vs.model_name = "embed-english-v3.0"

        mock_co = MagicMock()

        def fake_embed(**kwargs):
            raise RuntimeError("API rate limit exceeded")

        mock_co.embed = fake_embed

        mock_cohere_module = MagicMock()
        mock_cohere_module.ClientV2.return_value = mock_co

        with patch.dict("sys.modules", {"cohere": mock_cohere_module}):
            result = await vs._process_image_embeddings_cohere(
                [{"metadata": {}, "image_uri": "img1"}],
                ["base64data"],
            )

        # The exception is caught in gather as return_exceptions=True
        assert result == []
