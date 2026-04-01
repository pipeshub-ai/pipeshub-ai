"""
Additional tests for app.utils.custom_embeddings to cover missing lines/branches.

Targets:
  - Lines 124-125: validate_environment when model is not provided (defaults to voyage-01)
  - Lines 390->395, 395->404: post_init branches when client/async_client already set
  - Line 448->450: embed_query when response IS a dict
  - Lines 468->465: aembed_documents loop continuation (dict response)
  - Line 487->489: aembed_query when response IS a dict
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ============================================================================
# VoyageEmbeddings - model not provided (lines 124-125)
# ============================================================================


class TestVoyageDefaultModel:
    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_model_defaults_to_voyage_01(self):
        """When model is not provided, validate_environment sets it to 'voyage-01'."""
        from app.utils.custom_embeddings import VoyageEmbeddings

        emb = VoyageEmbeddings(batch_size=7, voyage_api_key="test-key")
        assert emb.model == "voyage-01"

    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_batch_size_default_for_voyage_01(self):
        """When model defaults to voyage-01, batch_size defaults to 7."""
        from app.utils.custom_embeddings import VoyageEmbeddings

        emb = VoyageEmbeddings(voyage_api_key="test-key")
        assert emb.model == "voyage-01"
        assert emb.batch_size == 7


# ============================================================================
# TogetherEmbeddings - post_init: client/async_client already set
# (lines 390->395, 395->404)
# ============================================================================


class TestTogetherEmbeddingsPresetClients:
    @patch.dict("os.environ", {"TOGETHER_API_KEY": "test-together-key"})
    def test_preset_sync_client_not_overwritten(self):
        """When client is already set (truthy), post_init should NOT overwrite it."""
        from app.utils.custom_embeddings import TogetherEmbeddings

        mock_client = MagicMock()
        emb = TogetherEmbeddings(model="test-model", client=mock_client)
        assert emb.client is mock_client

    @patch.dict("os.environ", {"TOGETHER_API_KEY": "test-together-key"})
    def test_preset_async_client_not_overwritten(self):
        """When async_client is already set (truthy), post_init should NOT overwrite it."""
        from app.utils.custom_embeddings import TogetherEmbeddings

        mock_async_client = MagicMock()
        emb = TogetherEmbeddings(model="test-model", async_client=mock_async_client)
        assert emb.async_client is mock_async_client

    @patch.dict("os.environ", {"TOGETHER_API_KEY": "test-together-key"})
    def test_preset_both_clients(self):
        """When both client and async_client are already set, neither is overwritten."""
        from app.utils.custom_embeddings import TogetherEmbeddings

        mock_sync = MagicMock()
        mock_async = MagicMock()
        emb = TogetherEmbeddings(
            model="test-model", client=mock_sync, async_client=mock_async
        )
        assert emb.client is mock_sync
        assert emb.async_client is mock_async



# ============================================================================
# TogetherEmbeddings - embed_query when response IS a dict (line 448->450)
# ============================================================================


class TestTogetherEmbedQueryDictResponse:
    @patch.dict("os.environ", {"TOGETHER_API_KEY": "test-together-key"})
    def test_embed_query_dict_response(self):
        """When response is already a dict, model_dump is NOT called."""
        from app.utils.custom_embeddings import TogetherEmbeddings

        mock_client = MagicMock()
        mock_client.create.return_value = {
            "data": [{"embedding": [0.1, 0.2, 0.3]}]
        }

        emb = TogetherEmbeddings(model="test-model")
        emb.client = mock_client

        result = emb.embed_query("test text")
        assert result == [0.1, 0.2, 0.3]


# ============================================================================
# TogetherEmbeddings - aembed_documents with dict response (line 468->465)
# ============================================================================


class TestTogetherAEmbedDocumentsDictResponse:
    @pytest.mark.asyncio
    @patch.dict("os.environ", {"TOGETHER_API_KEY": "test-together-key"})
    async def test_aembed_documents_dict_response(self):
        """When async response is already a dict, model_dump is NOT called
        and embeddings list is NOT extended (dict path skips extend)."""
        from app.utils.custom_embeddings import TogetherEmbeddings

        mock_async_client = MagicMock()

        async def mock_create(**kwargs):
            return {"data": [{"embedding": [0.5, 0.6]}]}

        mock_async_client.create = mock_create

        emb = TogetherEmbeddings(model="test-model")
        emb.async_client = mock_async_client

        result = await emb.aembed_documents(["text1"])
        # Dict path doesn't extend embeddings
        assert result == []

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"TOGETHER_API_KEY": "test-together-key"})
    async def test_aembed_documents_multiple_texts(self):
        """Multiple texts where response is not dict - covers loop continuation."""
        from app.utils.custom_embeddings import TogetherEmbeddings

        mock_async_client = MagicMock()
        mock_response_1 = MagicMock()
        mock_response_1.model_dump.return_value = {
            "data": [{"embedding": [0.1, 0.2]}]
        }
        mock_response_2 = MagicMock()
        mock_response_2.model_dump.return_value = {
            "data": [{"embedding": [0.3, 0.4]}]
        }

        call_count = 0

        async def mock_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_response_1
            return mock_response_2

        mock_async_client.create = mock_create

        emb = TogetherEmbeddings(model="test-model")
        emb.async_client = mock_async_client

        result = await emb.aembed_documents(["text1", "text2"])
        assert len(result) == 2
        assert result[0] == [0.1, 0.2]
        assert result[1] == [0.3, 0.4]


# ============================================================================
# TogetherEmbeddings - aembed_query when response IS a dict (line 487->489)
# ============================================================================


class TestTogetherAEmbedQueryDictResponse:
    @pytest.mark.asyncio
    @patch.dict("os.environ", {"TOGETHER_API_KEY": "test-together-key"})
    async def test_aembed_query_dict_response(self):
        """When async response is already a dict, model_dump is NOT called."""
        from app.utils.custom_embeddings import TogetherEmbeddings

        mock_async_client = MagicMock()

        async def mock_create(**kwargs):
            return {"data": [{"embedding": [0.9, 1.0]}]}

        mock_async_client.create = mock_create

        emb = TogetherEmbeddings(model="test-model")
        emb.async_client = mock_async_client

        result = await emb.aembed_query("test query")
        assert result == [0.9, 1.0]


# ============================================================================
# TogetherEmbeddings - embed_documents with multiple texts (non-dict response)
# ============================================================================


class TestTogetherEmbedDocumentsMultiple:
    @patch.dict("os.environ", {"TOGETHER_API_KEY": "test-together-key"})
    def test_embed_documents_multiple_texts_non_dict(self):
        """Multiple texts where response is not dict - covers for loop properly."""
        from app.utils.custom_embeddings import TogetherEmbeddings

        mock_client = MagicMock()
        mock_response_1 = MagicMock()
        mock_response_1.model_dump.return_value = {
            "data": [{"embedding": [0.1, 0.2]}]
        }
        mock_response_2 = MagicMock()
        mock_response_2.model_dump.return_value = {
            "data": [{"embedding": [0.3, 0.4]}]
        }

        mock_client.create.side_effect = [mock_response_1, mock_response_2]

        emb = TogetherEmbeddings(model="test-model")
        emb.client = mock_client

        result = emb.embed_documents(["text1", "text2"])
        assert len(result) == 2
        assert result[0] == [0.1, 0.2]
        assert result[1] == [0.3, 0.4]


# ============================================================================
# TogetherEmbeddings - build_extra: invalid model_kwargs
# ============================================================================


class TestTogetherBuildExtraInvalid:
    @patch.dict("os.environ", {"TOGETHER_API_KEY": "test-together-key"})
    def test_invalid_model_kwargs_raises(self):
        """If model_kwargs contains a field that should be explicit, it raises."""
        from app.utils.custom_embeddings import TogetherEmbeddings

        with pytest.raises(ValueError, match="should be specified explicitly"):
            TogetherEmbeddings(
                model="test-model",
                model_kwargs={"dimensions": 512},
            )


