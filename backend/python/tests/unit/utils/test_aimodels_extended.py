"""
Extended tests for app.utils.aimodels covering missing lines:
- _create_bedrock_client with explicit keys vs default chain
- get_embedding_model with model_name specified but not in list
- get_embedding_model with HuggingFace provider
- get_generator_model with Bedrock auto-detect (mistral, meta, amazon, cohere, ai21, qwen)
- _get_anthropic_max_tokens for 4.5 model
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from app.utils.aimodels import (
    EmbeddingProvider,
    LLMProvider,
    MAX_OUTPUT_TOKENS,
    MAX_OUTPUT_TOKENS_CLAUDE_4_5,
    ModelType,
    _create_bedrock_client,
    _get_anthropic_max_tokens,
    get_embedding_model,
    get_generator_model,
    is_multimodal_llm,
)


# ============================================================================
# _create_bedrock_client
# ============================================================================


class TestCreateBedrockClient:
    def test_with_explicit_keys(self):
        config = {
            "awsAccessKeyId": "AKIAIOSFODNN7EXAMPLE",
            "awsAccessSecretKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "region": "us-east-1",
        }
        with patch("boto3.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            _create_bedrock_client(config)
            mock_session_cls.assert_called_once_with(
                aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
                aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                region_name="us-east-1",
            )
            mock_session.client.assert_called_once_with("bedrock-runtime")

    def test_without_explicit_keys(self):
        config = {"region": "us-west-2"}
        with patch("boto3.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            _create_bedrock_client(config)
            mock_session_cls.assert_called_once_with(region_name="us-west-2")


# ============================================================================
# _get_anthropic_max_tokens
# ============================================================================


class TestGetAnthropicMaxTokens:
    def test_claude_4_5(self):
        assert _get_anthropic_max_tokens("claude-4.5-sonnet") == MAX_OUTPUT_TOKENS_CLAUDE_4_5

    def test_regular_model(self):
        assert _get_anthropic_max_tokens("claude-3-opus") == MAX_OUTPUT_TOKENS


# ============================================================================
# get_embedding_model - model_name not in list
# ============================================================================


class TestGetEmbeddingModelExtended:
    def test_model_name_not_in_list(self):
        config = {
            "configuration": {"model": "model-a, model-b", "apiKey": "key"},
            "isDefault": False,
        }
        with pytest.raises(ValueError, match="not found in"):
            get_embedding_model(EmbeddingProvider.OPENAI.value, config, model_name="model-c")

    def test_hugging_face_with_api_key(self):
        config = {
            "configuration": {
                "model": "sentence-transformers/all-MiniLM-L6-v2",
                "apiKey": "hf_token",
                "model_kwargs": {"device": "cpu"},
                "encode_kwargs": {},
            },
            "isDefault": True,
        }
        with patch("langchain_community.embeddings.HuggingFaceEmbeddings") as mock_hf:
            mock_hf.return_value = MagicMock()
            result = get_embedding_model(EmbeddingProvider.HUGGING_FACE.value, config)
            mock_hf.assert_called_once()
            call_kwargs = mock_hf.call_args
            assert "api_key" in call_kwargs.kwargs.get("model_kwargs", {})

    def test_hugging_face_without_api_key(self):
        config = {
            "configuration": {
                "model": "sentence-transformers/all-MiniLM-L6-v2",
                "model_kwargs": {"device": "cpu"},
            },
            "isDefault": True,
        }
        with patch("langchain_community.embeddings.HuggingFaceEmbeddings") as mock_hf:
            mock_hf.return_value = MagicMock()
            result = get_embedding_model(EmbeddingProvider.HUGGING_FACE.value, config)
            mock_hf.assert_called_once()

    def test_hugging_face_normalize_defaults(self):
        config = {
            "configuration": {
                "model": "sentence-transformers/all-MiniLM-L6-v2",
            },
            "isDefault": True,
        }
        with patch("langchain_community.embeddings.HuggingFaceEmbeddings") as mock_hf:
            mock_hf.return_value = MagicMock()
            result = get_embedding_model(EmbeddingProvider.HUGGING_FACE.value, config)
            call_kwargs = mock_hf.call_args
            assert call_kwargs.kwargs.get("encode_kwargs", {}).get("normalize_embeddings") is True


# ============================================================================
# get_generator_model - Bedrock auto-detect
# ============================================================================


class TestGetGeneratorModelBedrock:
    def _make_config(self, model_name, provider=None, custom_provider=None):
        config = {
            "configuration": {
                "model": model_name,
                "region": "us-east-1",
            },
            "isDefault": True,
        }
        if provider is not None:
            config["configuration"]["provider"] = provider
        if custom_provider is not None:
            config["configuration"]["customProvider"] = custom_provider
        return config

    def test_auto_detect_mistral(self):
        config = self._make_config("mistral.mistral-7b")
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrock") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert mock_chat.call_args.kwargs["provider"] == "mistral"

    def test_auto_detect_meta(self):
        config = self._make_config("meta.llama3-70b-instruct")
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrock") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert mock_chat.call_args.kwargs["provider"] == "meta"

    def test_auto_detect_amazon(self):
        config = self._make_config("amazon.titan-text-express")
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrock") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert mock_chat.call_args.kwargs["provider"] == "amazon"

    def test_auto_detect_cohere(self):
        config = self._make_config("cohere.command-r-plus")
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrock") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert mock_chat.call_args.kwargs["provider"] == "cohere"

    def test_auto_detect_ai21(self):
        config = self._make_config("ai21.jamba-instruct")
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrock") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert mock_chat.call_args.kwargs["provider"] == "ai21"

    def test_auto_detect_qwen(self):
        config = self._make_config("qwen-72b")
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrock") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert mock_chat.call_args.kwargs["provider"] == "qwen"

    def test_auto_detect_default_anthropic(self):
        config = self._make_config("some-unknown-model")
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrock") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert mock_chat.call_args.kwargs["provider"] == "anthropic"

    def test_custom_provider(self):
        config = self._make_config("custom-model", provider="other", custom_provider="my_custom")
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrock") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert mock_chat.call_args.kwargs["provider"] == "my_custom"

    def test_other_provider_no_custom(self):
        """When provider='other' but no customProvider, fall back to auto-detect."""
        config = self._make_config("claude-3-sonnet", provider="other")
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrock") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert mock_chat.call_args.kwargs["provider"] == "anthropic"

    def test_anthropic_model_in_bedrock(self):
        """Anthropic models in Bedrock should have max_tokens in model_kwargs."""
        config = self._make_config("anthropic.claude-3-sonnet")
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrock") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                model_kwargs = mock_chat.call_args.kwargs.get("model_kwargs", {})
                assert "max_tokens" in model_kwargs
