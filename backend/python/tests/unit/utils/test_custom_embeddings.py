"""Tests for custom_embeddings module: VoyageEmbeddings initialization, embed_documents, embed_query."""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.utils.custom_embeddings import (
    VoyageEmbeddings,
    _check_response,
    _create_retry_decorator,
    embed_with_retry,
)


# ============================================================================
# _check_response tests
# ============================================================================


class TestCheckResponse:
    def test_valid_response(self):
        response = {"data": [{"embedding": [0.1, 0.2]}]}
        result = _check_response(response)
        assert result == response

    def test_missing_data_key_raises(self):
        response = {"error": "something went wrong"}
        with pytest.raises(RuntimeError, match="Voyage API Error"):
            _check_response(response)

    def test_empty_response_raises(self):
        with pytest.raises(RuntimeError, match="Voyage API Error"):
            _check_response({})


# ============================================================================
# VoyageEmbeddings initialization tests
# ============================================================================


class TestVoyageEmbeddingsInit:
    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_basic_init(self):
        embeddings = VoyageEmbeddings(model="voyage-2", batch_size=72)
        assert embeddings.model == "voyage-2"
        assert embeddings.batch_size == 72
        assert embeddings.max_retries == 6
        assert embeddings.truncation is True

    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_default_batch_size_voyage_2(self):
        embeddings = VoyageEmbeddings(model="voyage-2")
        assert embeddings.batch_size == 72

    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_default_batch_size_voyage_02(self):
        embeddings = VoyageEmbeddings(model="voyage-02")
        assert embeddings.batch_size == 72

    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_default_batch_size_other_model(self):
        embeddings = VoyageEmbeddings(model="voyage-3")
        assert embeddings.batch_size == 7

    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_api_key_from_env(self):
        embeddings = VoyageEmbeddings(model="voyage-2", batch_size=72)
        assert embeddings.voyage_api_key.get_secret_value() == "test-api-key"

    def test_api_key_from_param(self):
        embeddings = VoyageEmbeddings(
            model="voyage-2",
            batch_size=72,
            voyage_api_key="direct-key",
        )
        assert embeddings.voyage_api_key.get_secret_value() == "direct-key"

    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_custom_max_retries(self):
        embeddings = VoyageEmbeddings(model="voyage-2", batch_size=72, max_retries=3)
        assert embeddings.max_retries == 3

    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_custom_timeout(self):
        embeddings = VoyageEmbeddings(
            model="voyage-2", batch_size=72, request_timeout=30.0
        )
        assert embeddings.request_timeout == 30.0

    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_default_api_base(self):
        embeddings = VoyageEmbeddings(model="voyage-2", batch_size=72)
        assert embeddings.voyage_api_base == "https://api.voyageai.com/v1/embeddings"

    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_truncation_disabled(self):
        embeddings = VoyageEmbeddings(
            model="voyage-2", batch_size=72, truncation=False
        )
        assert embeddings.truncation is False


# ============================================================================
# _invocation_params tests
# ============================================================================


class TestInvocationParams:
    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_standard_model_params(self):
        embeddings = VoyageEmbeddings(model="voyage-2", batch_size=72)
        params = embeddings._invocation_params(["hello"], input_type="document")
        assert params["url"] == "https://api.voyageai.com/v1/embeddings"
        assert "Bearer test-api-key" in params["headers"]["Authorization"]
        assert params["json"]["model"] == "voyage-2"
        assert params["json"]["input"] == ["hello"]
        assert params["json"]["input_type"] == "document"
        assert params["json"]["truncation"] is True

    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_multimodal_model_params_text(self):
        embeddings = VoyageEmbeddings(model="voyage-multimodal-3", batch_size=7)
        params = embeddings._invocation_params(["hello"], input_type="document")
        assert params["url"] == "https://api.voyageai.com/v1/multimodalembeddings"
        assert params["json"]["model"] == "voyage-multimodal-3"
        inputs = params["json"]["inputs"]
        assert len(inputs) == 1
        assert inputs[0]["content"][0]["type"] == "text"
        assert inputs[0]["content"][0]["text"] == "hello"

    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_multimodal_model_params_image(self):
        embeddings = VoyageEmbeddings(model="voyage-multimodal-3", batch_size=7)
        image_data = "data:image/png;base64,abc123"
        params = embeddings._invocation_params([image_data], input_type="document")
        inputs = params["json"]["inputs"]
        assert len(inputs) == 1
        assert inputs[0]["content"][0]["type"] == "image_base64"
        assert inputs[0]["content"][0]["image_base64"] == image_data


# ============================================================================
# embed_documents tests
# ============================================================================


class TestEmbedDocuments:
    @patch("app.utils.custom_embeddings.embed_with_retry")
    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_single_batch(self, mock_embed_retry):
        mock_embed_retry.return_value = {
            "data": [
                {"embedding": [0.1, 0.2, 0.3]},
                {"embedding": [0.4, 0.5, 0.6]},
            ]
        }
        embeddings = VoyageEmbeddings(model="voyage-2", batch_size=72)
        result = embeddings.embed_documents(["text1", "text2"])
        assert len(result) == 2
        assert result[0] == [0.1, 0.2, 0.3]
        assert result[1] == [0.4, 0.5, 0.6]
        mock_embed_retry.assert_called_once()

    @patch("app.utils.custom_embeddings.embed_with_retry")
    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_multiple_batches(self, mock_embed_retry):
        mock_embed_retry.side_effect = [
            {"data": [{"embedding": [0.1]}]},
            {"data": [{"embedding": [0.2]}]},
        ]
        embeddings = VoyageEmbeddings(model="voyage-3", batch_size=1)
        result = embeddings.embed_documents(["text1", "text2"])
        assert len(result) == 2
        assert result[0] == [0.1]
        assert result[1] == [0.2]
        assert mock_embed_retry.call_count == 2

    @patch("app.utils.custom_embeddings.embed_with_retry")
    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_empty_documents(self, mock_embed_retry):
        embeddings = VoyageEmbeddings(model="voyage-2", batch_size=72)
        result = embeddings.embed_documents([])
        assert result == []
        mock_embed_retry.assert_not_called()

    @patch("app.utils.custom_embeddings.embed_with_retry")
    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_input_type_is_document(self, mock_embed_retry):
        mock_embed_retry.return_value = {"data": [{"embedding": [0.1]}]}
        embeddings = VoyageEmbeddings(model="voyage-2", batch_size=72)
        embeddings.embed_documents(["text1"])
        # Verify the invocation params have input_type="document"
        call_kwargs = mock_embed_retry.call_args
        json_data = call_kwargs[1].get("json") or call_kwargs.kwargs.get("json")
        assert json_data["input_type"] == "document"


# ============================================================================
# embed_query tests
# ============================================================================


class TestEmbedQuery:
    @patch("app.utils.custom_embeddings.embed_with_retry")
    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_single_query(self, mock_embed_retry):
        mock_embed_retry.return_value = {
            "data": [{"embedding": [0.1, 0.2, 0.3]}]
        }
        embeddings = VoyageEmbeddings(model="voyage-2", batch_size=72)
        result = embeddings.embed_query("test query")
        assert result == [0.1, 0.2, 0.3]
        mock_embed_retry.assert_called_once()

    @patch("app.utils.custom_embeddings.embed_with_retry")
    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_input_type_is_query(self, mock_embed_retry):
        mock_embed_retry.return_value = {"data": [{"embedding": [0.1]}]}
        embeddings = VoyageEmbeddings(model="voyage-2", batch_size=72)
        embeddings.embed_query("test query")
        call_kwargs = mock_embed_retry.call_args
        json_data = call_kwargs[1].get("json") or call_kwargs.kwargs.get("json")
        assert json_data["input_type"] == "query"

    @patch("app.utils.custom_embeddings.embed_with_retry")
    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_query_wraps_text_in_list(self, mock_embed_retry):
        mock_embed_retry.return_value = {"data": [{"embedding": [0.1]}]}
        embeddings = VoyageEmbeddings(model="voyage-2", batch_size=72)
        embeddings.embed_query("single text")
        call_kwargs = mock_embed_retry.call_args
        json_data = call_kwargs[1].get("json") or call_kwargs.kwargs.get("json")
        assert json_data["input"] == ["single text"]


# ============================================================================
# embed_general_texts tests
# ============================================================================


class TestEmbedGeneralTexts:
    @patch("app.utils.custom_embeddings.embed_with_retry")
    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_no_input_type(self, mock_embed_retry):
        mock_embed_retry.return_value = {"data": [{"embedding": [0.1]}]}
        embeddings = VoyageEmbeddings(model="voyage-2", batch_size=72)
        result = embeddings.embed_general_texts(["text1"])
        assert len(result) == 1
        call_kwargs = mock_embed_retry.call_args
        json_data = call_kwargs[1].get("json") or call_kwargs.kwargs.get("json")
        assert json_data["input_type"] is None

    @patch("app.utils.custom_embeddings.embed_with_retry")
    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_with_input_type(self, mock_embed_retry):
        mock_embed_retry.return_value = {"data": [{"embedding": [0.1]}]}
        embeddings = VoyageEmbeddings(model="voyage-2", batch_size=72)
        embeddings.embed_general_texts(["text1"], input_type="document")
        call_kwargs = mock_embed_retry.call_args
        json_data = call_kwargs[1].get("json") or call_kwargs.kwargs.get("json")
        assert json_data["input_type"] == "document"


# ============================================================================
# _get_embeddings input_type validation tests
# ============================================================================


class TestGetEmbeddingsValidation:
    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_invalid_input_type_raises(self):
        embeddings = VoyageEmbeddings(model="voyage-2", batch_size=72)
        with pytest.raises(ValueError, match="input_type .* is invalid"):
            embeddings._get_embeddings(["text"], input_type="invalid")


# ============================================================================
# _create_retry_decorator tests
# ============================================================================


class TestCreateRetryDecorator:
    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_creates_retry_decorator(self):
        embeddings = VoyageEmbeddings(model="voyage-2", batch_size=72)
        decorator = _create_retry_decorator(embeddings)
        assert callable(decorator)


# ============================================================================
# embed_with_retry tests
# ============================================================================


class TestEmbedWithRetry:
    @patch("app.utils.custom_embeddings.requests.post")
    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_successful_call(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embedding": [0.1, 0.2]}]}
        mock_post.return_value = mock_response

        embeddings = VoyageEmbeddings(model="voyage-2", batch_size=72)
        result = embed_with_retry(
            embeddings,
            url="https://api.voyageai.com/v1/embeddings",
            headers={"Authorization": "Bearer test"},
            json={"model": "voyage-2", "input": ["text"]},
            timeout=None,
        )
        assert "data" in result
        assert len(result["data"]) == 1

    @patch("app.utils.custom_embeddings.requests.post")
    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_api_error_raises_runtime_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {"error": "rate limit exceeded"}
        mock_post.return_value = mock_response

        embeddings = VoyageEmbeddings(model="voyage-2", batch_size=72, max_retries=1)
        with pytest.raises(RuntimeError, match="Voyage API Error"):
            embed_with_retry(
                embeddings,
                url="https://api.voyageai.com/v1/embeddings",
                headers={"Authorization": "Bearer test"},
                json={"model": "voyage-2", "input": ["text"]},
                timeout=None,
            )


# ============================================================================
# VoyageEmbeddings - show_progress_bar path
# ============================================================================


class TestVoyageShowProgressBar:
    @patch("app.utils.custom_embeddings.embed_with_retry")
    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_show_progress_bar_with_tqdm(self, mock_embed_retry):
        mock_embed_retry.return_value = {"data": [{"embedding": [0.1]}]}
        embeddings = VoyageEmbeddings(
            model="voyage-2", batch_size=72, show_progress_bar=True
        )
        result = embeddings._get_embeddings(["text1"], input_type="document")
        assert len(result) == 1

    @patch.dict("os.environ", {"VOYAGE_API_KEY": "test-api-key"})
    def test_show_progress_bar_without_tqdm(self):
        """When tqdm is not installed and show_progress_bar=True, should raise ImportError."""
        embeddings = VoyageEmbeddings(
            model="voyage-2", batch_size=72, show_progress_bar=True
        )
        with patch.dict("sys.modules", {"tqdm": None, "tqdm.auto": None}):
            with pytest.raises(ImportError, match="Must have tqdm installed"):
                embeddings._get_embeddings(["text1"], input_type="document")


# ============================================================================
# TogetherEmbeddings tests
# ============================================================================


class TestTogetherEmbeddingsInit:
    @patch.dict("os.environ", {"TOGETHER_API_KEY": "test-together-key"})
    def test_basic_init(self):
        from app.utils.custom_embeddings import TogetherEmbeddings

        emb = TogetherEmbeddings(model="BAAI/bge-base-en-v1.5")
        assert emb.model == "BAAI/bge-base-en-v1.5"
        assert emb.together_api_key.get_secret_value() == "test-together-key"
        assert emb.client is not None
        assert emb.async_client is not None

    @patch.dict("os.environ", {"TOGETHER_API_KEY": "test-together-key"})
    def test_custom_base_url(self):
        from app.utils.custom_embeddings import TogetherEmbeddings

        emb = TogetherEmbeddings(
            model="test-model",
            base_url="https://custom.api.com/v1/",
        )
        assert emb.together_api_base == "https://custom.api.com/v1/"

    @patch.dict("os.environ", {"TOGETHER_API_KEY": "test-together-key"})
    def test_with_dimensions(self):
        from app.utils.custom_embeddings import TogetherEmbeddings

        emb = TogetherEmbeddings(model="test-model", dimensions=512)
        params = emb._invocation_params
        assert params["dimensions"] == 512

    @patch.dict("os.environ", {"TOGETHER_API_KEY": "test-together-key"})
    def test_invocation_params_without_dimensions(self):
        from app.utils.custom_embeddings import TogetherEmbeddings

        emb = TogetherEmbeddings(model="test-model")
        params = emb._invocation_params
        assert "dimensions" not in params
        assert params["model"] == "test-model"


class TestTogetherEmbeddingsEmbed:
    @patch.dict("os.environ", {"TOGETHER_API_KEY": "test-together-key"})
    def test_embed_documents(self):
        from app.utils.custom_embeddings import TogetherEmbeddings

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {
            "data": [{"embedding": [0.1, 0.2, 0.3]}]
        }
        # Make isinstance check return False so model_dump is called
        mock_client.create.return_value = mock_response

        emb = TogetherEmbeddings(model="test-model")
        emb.client = mock_client

        result = emb.embed_documents(["hello world"])
        assert len(result) == 1
        assert result[0] == [0.1, 0.2, 0.3]

    @patch.dict("os.environ", {"TOGETHER_API_KEY": "test-together-key"})
    def test_embed_query(self):
        from app.utils.custom_embeddings import TogetherEmbeddings

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {
            "data": [{"embedding": [0.4, 0.5, 0.6]}]
        }
        mock_client.create.return_value = mock_response

        emb = TogetherEmbeddings(model="test-model")
        emb.client = mock_client

        result = emb.embed_query("test query")
        assert result == [0.4, 0.5, 0.6]

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"TOGETHER_API_KEY": "test-together-key"})
    async def test_aembed_documents(self):
        from app.utils.custom_embeddings import TogetherEmbeddings

        mock_async_client = MagicMock()
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {
            "data": [{"embedding": [0.7, 0.8]}]
        }

        async def mock_create(**kwargs):
            return mock_response

        mock_async_client.create = mock_create

        emb = TogetherEmbeddings(model="test-model")
        emb.async_client = mock_async_client

        result = await emb.aembed_documents(["async text"])
        assert len(result) == 1
        assert result[0] == [0.7, 0.8]

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"TOGETHER_API_KEY": "test-together-key"})
    async def test_aembed_query(self):
        from app.utils.custom_embeddings import TogetherEmbeddings

        mock_async_client = MagicMock()
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {
            "data": [{"embedding": [0.9, 1.0]}]
        }

        async def mock_create(**kwargs):
            return mock_response

        mock_async_client.create = mock_create

        emb = TogetherEmbeddings(model="test-model")
        emb.async_client = mock_async_client

        result = await emb.aembed_query("async query")
        assert result == [0.9, 1.0]

    @patch.dict("os.environ", {"TOGETHER_API_KEY": "test-together-key"})
    def test_embed_documents_with_dict_response(self):
        """When response is already a dict, it should be used directly."""
        from app.utils.custom_embeddings import TogetherEmbeddings

        mock_client = MagicMock()
        # Return a dict directly (isinstance(response, dict) == True)
        mock_client.create.return_value = {
            "data": [{"embedding": [0.1, 0.2]}]
        }

        emb = TogetherEmbeddings(model="test-model")
        emb.client = mock_client

        # When response is dict, no model_dump called, embeddings NOT extended
        result = emb.embed_documents(["text"])
        # Dict path doesn't extend embeddings
        assert result == []


class TestTogetherEmbeddingsBuildExtra:
    @patch.dict("os.environ", {"TOGETHER_API_KEY": "test-together-key"})
    def test_extra_kwargs_transferred_to_model_kwargs(self):
        """Extra kwargs that are not standard fields get moved to model_kwargs."""
        import warnings
        from app.utils.custom_embeddings import TogetherEmbeddings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            emb = TogetherEmbeddings(
                model="test-model",
                custom_param="custom_value",
            )
        assert emb.model_kwargs.get("custom_param") == "custom_value"

    @patch.dict("os.environ", {"TOGETHER_API_KEY": "test-together-key"})
    def test_duplicate_key_raises(self):
        """If a key is in both values and model_kwargs, should raise ValueError."""
        from app.utils.custom_embeddings import TogetherEmbeddings

        with pytest.raises(ValueError, match="Found .* supplied twice"):
            TogetherEmbeddings(
                model="test-model",
                model_kwargs={"model": "duplicate"},
            )
