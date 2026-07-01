"""Integration tests for the LM Studio provider in app.utils.aimodels.

All network calls are mocked — no running LM Studio instance is required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.utils.aimodels import (
    EmbeddingProvider,
    LLMProvider,
    get_embedding_model,
    get_generator_model,
)

_ENDPOINT = "http://localhost:1234/v1"


def _config(model: str, api_key: str | None = None, endpoint: str = _ENDPOINT) -> dict:
    cfg: dict = {
        "configuration": {
            "model": model,
            "endpoint": endpoint,
        },
        "isDefault": True,
    }
    if api_key is not None:
        cfg["configuration"]["apiKey"] = api_key
    return cfg


# ---------------------------------------------------------------------------
# Enum values
# ---------------------------------------------------------------------------

class TestLMStudioEnums:
    def test_llm_provider_enum_value(self):
        assert LLMProvider.LM_STUDIO.value == "lmStudio"

    def test_embedding_provider_enum_value(self):
        assert EmbeddingProvider.LM_STUDIO.value == "lmStudio"

    def test_llm_provider_in_list(self):
        assert "lmStudio" in [p.value for p in LLMProvider]

    def test_embedding_provider_in_list(self):
        assert "lmStudio" in [p.value for p in EmbeddingProvider]


# ---------------------------------------------------------------------------
# LLM dispatch
# ---------------------------------------------------------------------------

class TestLMStudioLLM:
    @patch("langchain_openai.ChatOpenAI")
    def test_dispatch_creates_chatopenai_with_user_endpoint(self, mock_cls):
        mock_cls.return_value = MagicMock()
        get_generator_model(LLMProvider.LM_STUDIO.value, _config("llama3.2"))
        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["base_url"] == _ENDPOINT
        assert kwargs["model"] == "llama3.2"

    @patch("langchain_openai.ChatOpenAI")
    def test_api_key_used_when_provided(self, mock_cls):
        mock_cls.return_value = MagicMock()
        get_generator_model(LLMProvider.LM_STUDIO.value, _config("llama3.2", api_key="my-key"))
        assert mock_cls.call_args.kwargs["api_key"] == "my-key"

    @patch("langchain_openai.ChatOpenAI")
    def test_api_key_falls_back_to_placeholder_when_absent(self, mock_cls):
        mock_cls.return_value = MagicMock()
        get_generator_model(LLMProvider.LM_STUDIO.value, _config("llama3.2"))
        assert mock_cls.call_args.kwargs["api_key"] == "lm-studio"

    @patch("langchain_openai.ChatOpenAI")
    def test_api_key_falls_back_to_placeholder_when_empty_string(self, mock_cls):
        mock_cls.return_value = MagicMock()
        get_generator_model(LLMProvider.LM_STUDIO.value, _config("llama3.2", api_key=""))
        assert mock_cls.call_args.kwargs["api_key"] == "lm-studio"

    @patch("langchain_openai.ChatOpenAI")
    def test_default_temperature(self, mock_cls):
        mock_cls.return_value = MagicMock()
        get_generator_model(LLMProvider.LM_STUDIO.value, _config("llama3.2"))
        assert mock_cls.call_args.kwargs["temperature"] == pytest.approx(0.2)

    @patch("langchain_openai.ChatOpenAI")
    def test_stream_usage_enabled(self, mock_cls):
        mock_cls.return_value = MagicMock()
        get_generator_model(LLMProvider.LM_STUDIO.value, _config("llama3.2"))
        assert mock_cls.call_args.kwargs["stream_usage"] is True

    @patch("langchain_openai.ChatOpenAI")
    def test_custom_endpoint_forwarded(self, mock_cls):
        mock_cls.return_value = MagicMock()
        custom = "http://192.168.1.100:1234/v1"
        get_generator_model(LLMProvider.LM_STUDIO.value, _config("llama3.2", endpoint=custom))
        assert mock_cls.call_args.kwargs["base_url"] == custom


# ---------------------------------------------------------------------------
# Embedding dispatch
# ---------------------------------------------------------------------------

class TestLMStudioEmbedding:
    @patch("langchain_openai.embeddings.OpenAIEmbeddings")
    def test_dispatch_creates_openai_embeddings_with_user_endpoint(self, mock_cls):
        mock_cls.return_value = MagicMock()
        get_embedding_model(EmbeddingProvider.LM_STUDIO.value, _config("nomic-embed-text-v1.5"))
        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["base_url"] == _ENDPOINT
        assert kwargs["model"] == "nomic-embed-text-v1.5"

    @patch("langchain_openai.embeddings.OpenAIEmbeddings")
    def test_api_key_falls_back_to_placeholder(self, mock_cls):
        mock_cls.return_value = MagicMock()
        get_embedding_model(EmbeddingProvider.LM_STUDIO.value, _config("nomic-embed-text-v1.5"))
        assert mock_cls.call_args.kwargs["api_key"] == "lm-studio"

    @patch("langchain_openai.embeddings.OpenAIEmbeddings")
    def test_api_key_used_when_provided(self, mock_cls):
        mock_cls.return_value = MagicMock()
        get_embedding_model(
            EmbeddingProvider.LM_STUDIO.value,
            _config("nomic-embed-text-v1.5", api_key="custom-key"),
        )
        assert mock_cls.call_args.kwargs["api_key"] == "custom-key"

    @patch("langchain_openai.embeddings.OpenAIEmbeddings")
    def test_dimensions_forwarded(self, mock_cls):
        mock_cls.return_value = MagicMock()
        cfg = {
            "configuration": {
                "model": "nomic-embed-text-v1.5",
                "endpoint": _ENDPOINT,
                "dimensions": 256,
            },
            "isDefault": True,
        }
        get_embedding_model(EmbeddingProvider.LM_STUDIO.value, cfg)
        assert mock_cls.call_args.kwargs["dimensions"] == 256
