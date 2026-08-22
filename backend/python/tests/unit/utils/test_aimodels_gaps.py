"""
Targeted tests for remaining uncovered branches in app.utils.aimodels.

Fills gaps reported by coverage after the existing test_aimodels_*.py suite.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.utils.aimodels import (
    EmbeddingProvider,
    LLMProvider,
    _anthropic_supports_sampling_params,
    coerce_message_content_to_text,
    get_embedding_model,
    get_generator_model,
    get_image_generation_model,
)


# ---------------------------------------------------------------------------
# Embedding dimensions validation (lines 193-194)
# ---------------------------------------------------------------------------


class TestEmbeddingDimensionsInvalid:
    @patch("langchain_openai.embeddings.OpenAIEmbeddings")
    def test_non_numeric_dimensions_ignored(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = {
            "configuration": {
                "model": "text-embedding-3-small",
                "apiKey": "key",
                "dimensions": "not-a-number",
            },
            "isDefault": True,
        }
        get_embedding_model(EmbeddingProvider.OPENAI.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert "dimensions" not in call_kwargs


# ---------------------------------------------------------------------------
# _anthropic_supports_sampling_params (lines 397, 407-417)
# ---------------------------------------------------------------------------


class TestAnthropicSupportsSamplingParams:
    @pytest.mark.parametrize(
        "model_name,expected",
        [
            (None, True),
            ("", True),
            ("gpt-4o", True),
            ("claude-3-5-sonnet", True),
            ("claude-opus-4-6", True),
            ("claude-opus-4-7", False),
            ("claude_opus_4.7", False),
            ("anthropic.claude-opus-4-7-v1", False),
            ("claude-sonnet-5", False),
            ("claude-sonnet-5-0", False),
            ("claude-haiku-5-1", False),
            ("claude-sonnet-4", True),
            ("claude-opus-4", True),
        ],
    )
    def test_sampling_param_support(self, model_name, expected):
        assert _anthropic_supports_sampling_params(model_name) is expected


# ---------------------------------------------------------------------------
# Anthropic / Bedrock / Azure AI omit temperature when sampling disabled
# (branches 445->447, 508->513, 531->533)
# ---------------------------------------------------------------------------


class TestAnthropicNoSamplingTemperature:
    def test_direct_anthropic_opus_47_omits_temperature(self):
        config = {
            "configuration": {
                "model": "claude-opus-4-7",
                "apiKey": "key",
            },
            "isDefault": True,
        }
        with patch("langchain_anthropic.ChatAnthropic") as mock_cls:
            mock_cls.return_value = MagicMock()
            get_generator_model(LLMProvider.ANTHROPIC.value, config)
            assert "temperature" not in mock_cls.call_args.kwargs

    def test_direct_anthropic_sonnet_5_omits_temperature(self):
        config = {
            "configuration": {
                "model": "claude-sonnet-5",
                "apiKey": "key",
            },
            "isDefault": True,
        }
        with patch("langchain_anthropic.ChatAnthropic") as mock_cls:
            mock_cls.return_value = MagicMock()
            get_generator_model(LLMProvider.ANTHROPIC.value, config)
            assert "temperature" not in mock_cls.call_args.kwargs

    def test_bedrock_anthropic_opus_47_omits_temperature(self):
        config = {
            "configuration": {
                "model": "anthropic.claude-opus-4-7",
                "region": "us-east-1",
            },
            "isDefault": True,
        }
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrock") as mock_cls:
                mock_cls.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert "temperature" not in mock_cls.call_args.kwargs

    def test_azure_ai_claude_opus_47_omits_temperature(self):
        config = {
            "configuration": {
                "model": "claude-opus-4-7",
                "apiKey": "key",
                "endpoint": "https://azure.example.com",
            },
            "isDefault": True,
        }
        with patch("langchain_anthropic.ChatAnthropic") as mock_cls:
            mock_cls.return_value = MagicMock()
            get_generator_model(LLMProvider.AZURE_AI.value, config)
            assert "temperature" not in mock_cls.call_args.kwargs

    def test_direct_anthropic_legacy_includes_temperature(self):
        config = {
            "configuration": {
                "model": "claude-3-5-sonnet",
                "apiKey": "key",
            },
            "isDefault": True,
        }
        with patch("langchain_anthropic.ChatAnthropic") as mock_cls:
            mock_cls.return_value = MagicMock()
            get_generator_model(LLMProvider.ANTHROPIC.value, config)
            assert mock_cls.call_args.kwargs["temperature"] == 0.2


# ---------------------------------------------------------------------------
# get_image_generation_model — is_default=True skips list validation (926->929)
# ---------------------------------------------------------------------------


class TestImageGenDefaultSkipsValidation:
    def test_is_default_allows_model_not_in_list(self):
        cfg = {
            "configuration": {"apiKey": "sk", "model": "dall-e-3"},
            "isDefault": True,
        }
        adapter = get_image_generation_model("openAI", cfg, model_name="custom-model")
        assert adapter.model == "custom-model"


# ---------------------------------------------------------------------------
# coerce_message_content_to_text
# ---------------------------------------------------------------------------


class TestCoerceMessageContentToText:
    def test_string_passthrough(self):
        assert coerce_message_content_to_text("plain text") == "plain text"

    def test_none_returns_empty(self):
        assert coerce_message_content_to_text(None) == ""

    def test_gemini_text_blocks_joined(self):
        content = [
            {"type": "text", "text": "Hello "},
            {"type": "text", "text": "world"},
        ]
        assert coerce_message_content_to_text(content) == "Hello world"

    def test_mixed_string_and_dict_blocks(self):
        content = ["prefix ", {"type": "text", "text": "suffix"}]
        assert coerce_message_content_to_text(content) == "prefix suffix"

    def test_non_text_blocks_ignored(self):
        content = [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            {"type": "text", "text": "ok"},
        ]
        assert coerce_message_content_to_text(content) == "ok"

    def test_scalar_list_items_stringified(self):
        assert coerce_message_content_to_text([1, 2, 3]) == "123"

    def test_non_list_non_string_coerced(self):
        assert coerce_message_content_to_text(42) == "42"

