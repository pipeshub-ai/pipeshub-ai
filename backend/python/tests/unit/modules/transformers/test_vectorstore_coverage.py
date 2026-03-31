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
        "app.modules.transformers.vectorstore.FastEmbedSparse"
    ) as mock_sparse, patch(
        "app.modules.transformers.vectorstore._get_shared_nlp"
    ) as mock_nlp:
        mock_sparse.return_value = MagicMock()
        mock_nlp.return_value = MagicMock()

        from app.modules.transformers.vectorstore import VectorStore

        vs = VectorStore(
            logger=MagicMock(),
            config_service=AsyncMock(),
            graph_provider=AsyncMock(),
            collection_name="test_collection",
            vector_db_service=AsyncMock(),
        )
        return vs


# ===================================================================
# _get_shared_nlp branch coverage
# ===================================================================


class TestGetSharedNlp:
    """Cover _get_shared_nlp branches for sentencizer and custom_sentence_boundary."""

    def test_sentencizer_already_present(self):
        """When sentencizer is already in pipe_names, it is not re-added."""
        with patch(
            "app.modules.transformers.vectorstore.FastEmbedSparse"
        ) as mock_sparse, patch(
            "app.modules.transformers.vectorstore.spacy"
        ) as mock_spacy:
            mock_sparse.return_value = MagicMock()

            from app.modules.transformers.vectorstore import _get_shared_nlp

            # Clear the cached nlp
            if hasattr(_get_shared_nlp, "_cached_nlp"):
                delattr(_get_shared_nlp, "_cached_nlp")

            mock_nlp = MagicMock()
            mock_nlp.pipe_names = ["sentencizer", "parser"]
            mock_spacy.load.return_value = mock_nlp

            result = _get_shared_nlp()

            # Should not call add_pipe for sentencizer since it's already present
            # But should try to add custom_sentence_boundary
            assert result is mock_nlp

            # Clean up
            if hasattr(_get_shared_nlp, "_cached_nlp"):
                delattr(_get_shared_nlp, "_cached_nlp")

    def test_custom_sentence_boundary_add_raises(self):
        """When adding custom_sentence_boundary fails, it's silently caught."""
        with patch(
            "app.modules.transformers.vectorstore.FastEmbedSparse"
        ) as mock_sparse, patch(
            "app.modules.transformers.vectorstore.spacy"
        ) as mock_spacy:
            mock_sparse.return_value = MagicMock()

            from app.modules.transformers.vectorstore import _get_shared_nlp

            # Clear the cached nlp
            if hasattr(_get_shared_nlp, "_cached_nlp"):
                delattr(_get_shared_nlp, "_cached_nlp")

            mock_nlp = MagicMock()
            mock_nlp.pipe_names = []

            def fake_add_pipe(name, **kwargs):
                if name == "custom_sentence_boundary":
                    raise Exception("component not registered")

            mock_nlp.add_pipe = fake_add_pipe
            mock_spacy.load.return_value = mock_nlp

            result = _get_shared_nlp()

            assert result is mock_nlp

            # Clean up
            if hasattr(_get_shared_nlp, "_cached_nlp"):
                delattr(_get_shared_nlp, "_cached_nlp")

    def test_cached_nlp_returned(self):
        """When _cached_nlp is set, it is returned directly."""
        from app.modules.transformers.vectorstore import _get_shared_nlp

        mock_cached = MagicMock()
        setattr(_get_shared_nlp, "_cached_nlp", mock_cached)

        result = _get_shared_nlp()
        assert result is mock_cached

        # Clean up
        delattr(_get_shared_nlp, "_cached_nlp")


# ===================================================================
# __init__ generic exception
# ===================================================================


class TestVectorStoreInit:
    """Cover __init__ exception paths."""

    def test_sparse_embedding_failure_raises_indexing_error(self):
        """When FastEmbedSparse raises, it becomes IndexingError via inner try."""
        with patch(
            "app.modules.transformers.vectorstore._get_shared_nlp"
        ) as mock_nlp, patch(
            "app.modules.transformers.vectorstore.FastEmbedSparse",
            side_effect=TypeError("unexpected type error"),
        ):
            mock_nlp.return_value = MagicMock()

            from app.modules.transformers.vectorstore import VectorStore

            with pytest.raises(IndexingError, match="Failed to initialize sparse embeddings"):
                VectorStore(
                    logger=MagicMock(),
                    config_service=AsyncMock(),
                    graph_provider=AsyncMock(),
                    collection_name="test_collection",
                    vector_db_service=AsyncMock(),
                )


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
# custom_sentence_boundary - heading and letter bullet branches
# ===================================================================


class TestCustomSentenceBoundaryDeeper:
    """Cover heading detection branches and letter bullet branches."""

    def _make_mock_doc(self, tokens_data):
        """Create a minimal mock doc with token-like objects."""
        tokens = []
        for i, data in enumerate(tokens_data):
            tok = MagicMock()
            tok.i = i
            tok.text = data["text"]
            tok.like_num = data.get("like_num", False)
            tok.is_sent_start = data.get("is_sent_start", None)
            tokens.append(tok)

        class DocSlice:
            def __init__(self, toks):
                self._tokens = toks

            def __getitem__(self, key):
                if isinstance(key, slice):
                    return DocSlice(self._tokens[key])
                return self._tokens[key]

            def __iter__(self):
                return iter(self._tokens)

            def __len__(self):
                return len(self._tokens)

        return DocSlice(tokens)

    def test_letter_bullet_followed_by_period(self):
        """Single letter + period should NOT cause sentence split."""
        from app.modules.transformers.vectorstore import VectorStore

        tokens_data = [
            {"text": "a", "like_num": False},
            {"text": ".", "is_sent_start": True},
            {"text": "end"},
        ]
        doc = self._make_mock_doc(tokens_data)

        VectorStore.custom_sentence_boundary(doc)

        assert doc[1].is_sent_start is False

    def test_heading_all_caps_not_at_end(self):
        """All-caps token before end should not cause sentence split when next_token.i < len(doc) - 1."""
        from app.modules.transformers.vectorstore import VectorStore

        tokens_data = [
            {"text": "INTRODUCTION", "like_num": False},
            {"text": "text", "is_sent_start": True},
            {"text": "more"},
            {"text": "end"},
        ]
        doc = self._make_mock_doc(tokens_data)

        VectorStore.custom_sentence_boundary(doc)

        assert doc[1].is_sent_start is False

    def test_heading_all_caps_at_end(self):
        """All-caps token at end of doc (next_token.i >= len(doc) - 1) should NOT set is_sent_start to False."""
        from app.modules.transformers.vectorstore import VectorStore

        tokens_data = [
            {"text": "HEADING", "like_num": False},
            {"text": "NEXT", "is_sent_start": True},
        ]
        doc = self._make_mock_doc(tokens_data)

        # next_token.i = 1, len(doc) - 1 = 1, so condition 1 < 1 is False
        VectorStore.custom_sentence_boundary(doc)

        # is_sent_start should NOT be changed (stays True)
        assert doc[1].is_sent_start is True

    def test_numeric_bullet_with_short_digits(self):
        """Numeric bullet (short number + period) should not cause sentence split."""
        from app.modules.transformers.vectorstore import VectorStore

        tokens_data = [
            {"text": "1", "like_num": True},
            {"text": ".", "is_sent_start": True},
            {"text": "end"},
        ]
        doc = self._make_mock_doc(tokens_data)

        VectorStore.custom_sentence_boundary(doc)

        assert doc[1].is_sent_start is False

    def test_dash_bullet_marker(self):
        """Dash bullet marker should not cause sentence split."""
        from app.modules.transformers.vectorstore import VectorStore

        tokens_data = [
            {"text": "-"},
            {"text": "item", "is_sent_start": True},
            {"text": "end"},
        ]
        doc = self._make_mock_doc(tokens_data)

        VectorStore.custom_sentence_boundary(doc)

        assert doc[1].is_sent_start is False

    def test_all_caps_with_digit_is_not_heading(self):
        """All-caps text containing digits (serial number) should not be treated as heading."""
        from app.modules.transformers.vectorstore import VectorStore

        tokens_data = [
            {"text": "ABC123", "like_num": False},
            {"text": "next", "is_sent_start": True},
            {"text": "end"},
        ]
        doc = self._make_mock_doc(tokens_data)

        VectorStore.custom_sentence_boundary(doc)

        # ABC123 has digits, so it won't match the all-caps heading rule
        # is_sent_start should remain True (not changed)
        assert doc[1].is_sent_start is True


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

        assert result == []
        vs.logger.warning.assert_called()


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

        assert result == []
        vs.logger.warning.assert_called()

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
        """When delete_embeddings raises a non-custom exception, it becomes IndexingError."""
        from langchain_core.documents import Document

        vs = _make_vectorstore()
        vs.delete_embeddings = AsyncMock(side_effect=TypeError("unexpected type error"))

        chunks = [Document(page_content="test", metadata={})]

        with pytest.raises(IndexingError, match="Unexpected error during embedding creation"):
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
        """Unexpected exception during block processing -> IndexingError."""
        from app.models.blocks import Block, BlocksContainer

        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)

        # Create a block whose type property raises when .lower() is called
        block = MagicMock()
        block.type = MagicMock()
        block.type.lower.side_effect = AttributeError("no lower")

        container = MagicMock()
        container.blocks = [block]
        container.block_groups = []

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            with pytest.raises(IndexingError, match="Unexpected error during indexing"):
                await vs.index_documents(container, "org-1", "rec-1", "vr-1", "text/plain")

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
        vs.nlp = MagicMock(return_value=MagicMock(sents=[MagicMock(text="Section text")]))

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
        ), patch("app.modules.transformers.vectorstore.QdrantVectorStore"):
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
        ), patch("app.modules.transformers.vectorstore.QdrantVectorStore"):
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
        ), patch("app.modules.transformers.vectorstore.QdrantVectorStore"):
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
        vs.embedding_provider = "openai"
        vs.vector_store = AsyncMock()

        call_count = 0

        async def fake_aadd_documents(docs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("batch 2 failed")

        vs.vector_store.aadd_documents = fake_aadd_documents

        # Create enough chunks to generate 2 batches
        chunks = [
            Document(page_content=f"test {i}", metadata={}) for i in range(100)
        ]

        with pytest.raises(VectorStoreError, match="Failed to store document batch"):
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
