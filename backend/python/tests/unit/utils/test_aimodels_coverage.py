"""
Additional tests for app.utils.aimodels to cover missing lines/branches.

Targets:
  - Lines 82-83: get_default_embedding_model exception propagation
  - Lines 139-140: get_embedding_model with is_default=False, model_name=None
  - Line 141->146: get_embedding_model with is_default=True, model_name provided
  - Line 150->154: Azure AI check_embedding_ctx_length (always False due to "cohere" or bug)
  - Lines 213->216: HuggingFace encode_kwargs already has normalize_embeddings
  - Lines 323-324: get_generator_model with is_default=False, model_name=None
  - Line 325->330: get_generator_model with is_default=True, model_name provided
  - Bedrock Anthropic max_tokens for claude-4.5 in bedrock
  - Azure OpenAI reasoning model branch
  - Azure AI claude reasoning model branch
  - OpenAI compatible reasoning model branch
"""

from unittest.mock import MagicMock, patch

import pytest

from app.utils.aimodels import (
    EmbeddingProvider,
    LLMProvider,
    MAX_OUTPUT_TOKENS,
    MAX_OUTPUT_TOKENS_CLAUDE_4_5,
    get_default_embedding_model,
    get_embedding_model,
    get_generator_model,
)


# ============================================================================
# get_default_embedding_model - exception propagation (lines 82-83)
# ============================================================================


class TestGetDefaultEmbeddingModelException:
    def test_exception_is_reraised(self):
        """When HuggingFaceEmbeddings raises, the exception propagates."""
        mock_hf_module = MagicMock()
        mock_hf_module.HuggingFaceEmbeddings.side_effect = RuntimeError("model load failed")

        with patch.dict("sys.modules", {"langchain_huggingface": mock_hf_module}):
            with pytest.raises(RuntimeError, match="model load failed"):
                get_default_embedding_model()


# ============================================================================
# get_embedding_model - is_default=False, model_name=None (lines 139-140)
# ============================================================================


class TestGetEmbeddingModelNotDefaultNoName:
    def test_not_default_no_model_name_uses_first(self):
        """When is_default=False and model_name=None, picks first model from list."""
        config = {
            "configuration": {
                "model": "model-alpha, model-beta",
                "apiKey": "key",
                "organizationId": "org",
            },
            "isDefault": False,
        }
        with patch("langchain_openai.embeddings.OpenAIEmbeddings") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = get_embedding_model(EmbeddingProvider.OPENAI.value, config)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == "model-alpha"


# ============================================================================
# get_embedding_model - is_default=True, model_name provided (line 141->146)
# This branch: is_default=True and model_name is not None
# The code does NOT enter any of the if/elif branches, so model_name is
# used as-is without validation.
# ============================================================================


class TestGetEmbeddingModelDefaultWithName:
    def test_is_default_true_with_model_name_uses_provided(self):
        """When is_default=True and model_name is provided, it's used directly."""
        config = {
            "configuration": {
                "model": "model-alpha, model-beta",
                "apiKey": "key",
                "organizationId": "org",
            },
            "isDefault": True,
        }
        with patch("langchain_openai.embeddings.OpenAIEmbeddings") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = get_embedding_model(
                EmbeddingProvider.OPENAI.value, config, model_name="model-beta"
            )
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == "model-beta"


# ============================================================================
# get_embedding_model - Azure AI check_embedding_ctx_length
# Note: Due to a bug in the source code on line 150:
#   if "cohere" or "embed-v" in model_name.lower():
# This always evaluates to True because "cohere" is truthy.
# The else branch (line 153) is unreachable.
# We still test various model names to document this behavior.
# ============================================================================


class TestAzureAIEmbeddingCtxLength:
    @patch("langchain_openai.embeddings.OpenAIEmbeddings")
    def test_azure_ai_cohere_model(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = {
            "configuration": {
                "model": "cohere-embed-english",
                "apiKey": "key",
                "endpoint": "https://test.endpoint.com",
            },
            "isDefault": True,
        }
        result = get_embedding_model(EmbeddingProvider.AZURE_AI.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["check_embedding_ctx_length"] is False

    @patch("langchain_openai.embeddings.OpenAIEmbeddings")
    def test_azure_ai_embed_v_model(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = {
            "configuration": {
                "model": "embed-v3",
                "apiKey": "key",
                "endpoint": "https://test.endpoint.com",
            },
            "isDefault": True,
        }
        result = get_embedding_model(EmbeddingProvider.AZURE_AI.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        # Due to the bug, this is always False
        assert call_kwargs["check_embedding_ctx_length"] is False

    @patch("langchain_openai.embeddings.OpenAIEmbeddings")
    def test_azure_ai_non_cohere_model(self, mock_cls):
        """Non-cohere models should have check_embedding_ctx_length=True."""
        mock_cls.return_value = MagicMock()
        config = {
            "configuration": {
                "model": "text-embedding-ada-002",
                "apiKey": "key",
                "endpoint": "https://test.endpoint.com",
            },
            "isDefault": True,
        }
        result = get_embedding_model(EmbeddingProvider.AZURE_AI.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["check_embedding_ctx_length"] is True


# ============================================================================
# get_embedding_model - HuggingFace with existing normalize_embeddings
# (line 213->216)
# ============================================================================


class TestHuggingFaceExistingNormalize:
    @patch("langchain_community.embeddings.HuggingFaceEmbeddings")
    def test_encode_kwargs_with_existing_normalize(self, mock_cls):
        """When encode_kwargs already has normalize_embeddings, it is preserved."""
        mock_cls.return_value = MagicMock()
        config = {
            "configuration": {
                "model": "sentence-transformers/all-MiniLM-L6-v2",
                "encode_kwargs": {"normalize_embeddings": False},
            },
            "isDefault": True,
        }
        result = get_embedding_model(EmbeddingProvider.HUGGING_FACE.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["encode_kwargs"]["normalize_embeddings"] is False


# ============================================================================
# get_embedding_model - OpenAI compatible with providers to skip check
# ============================================================================


class TestOpenAICompatibleEmbeddingCtxLength:
    @patch("langchain_openai.embeddings.OpenAIEmbeddings")
    def test_google_endpoint_skips_check(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = {
            "configuration": {
                "model": "text-embedding-004",
                "apiKey": "key",
                "endpoint": "https://generativelanguage.google.com/v1",
            },
            "isDefault": True,
        }
        result = get_embedding_model(EmbeddingProvider.OPENAI_COMPATIBLE.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["check_embedding_ctx_length"] is False

    @patch("langchain_openai.embeddings.OpenAIEmbeddings")
    def test_cohere_endpoint_skips_check(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = {
            "configuration": {
                "model": "embed-english-v3.0",
                "apiKey": "key",
                "endpoint": "https://api.cohere.ai/v1",
            },
            "isDefault": True,
        }
        result = get_embedding_model(EmbeddingProvider.OPENAI_COMPATIBLE.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["check_embedding_ctx_length"] is False

    @patch("langchain_openai.embeddings.OpenAIEmbeddings")
    def test_voyage_endpoint_skips_check(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = {
            "configuration": {
                "model": "voyage-2",
                "apiKey": "key",
                "endpoint": "https://api.voyage.ai/v1",
            },
            "isDefault": True,
        }
        result = get_embedding_model(EmbeddingProvider.OPENAI_COMPATIBLE.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["check_embedding_ctx_length"] is False

    @patch("langchain_openai.embeddings.OpenAIEmbeddings")
    def test_normal_endpoint_checks_length(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = {
            "configuration": {
                "model": "text-embedding-ada-002",
                "apiKey": "key",
                "endpoint": "https://api.openai.com/v1",
            },
            "isDefault": True,
        }
        result = get_embedding_model(EmbeddingProvider.OPENAI_COMPATIBLE.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["check_embedding_ctx_length"] is True


# ============================================================================
# get_generator_model - is_default=False, model_name=None (lines 323-324)
# ============================================================================


class TestGetGeneratorModelNotDefaultNoName:
    def test_not_default_no_model_name_uses_first(self):
        """When is_default=False and model_name=None, picks first model from list."""
        config = {
            "configuration": {
                "model": "gpt-4o, gpt-4o-mini",
                "apiKey": "key",
                "organizationId": "org",
            },
            "isDefault": False,
        }
        with patch("langchain_openai.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = get_generator_model(LLMProvider.OPENAI.value, config)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == "gpt-4o"


# ============================================================================
# get_generator_model - is_default=True, model_name provided (line 325->330)
# ============================================================================


class TestGetGeneratorModelDefaultWithName:
    def test_is_default_true_with_model_name_uses_provided(self):
        """When is_default=True and model_name is provided, it's used directly."""
        config = {
            "configuration": {
                "model": "gpt-4o, gpt-4o-mini",
                "apiKey": "key",
                "organizationId": "org",
            },
            "isDefault": True,
        }
        with patch("langchain_openai.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = get_generator_model(
                LLMProvider.OPENAI.value, config, model_name="gpt-4o-mini"
            )
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == "gpt-4o-mini"


# ============================================================================
# get_generator_model - Bedrock with Anthropic Claude 4.5
# ============================================================================


class TestBedrockAnthropicClaude45:
    def test_bedrock_claude_4_5_max_tokens(self):
        """Anthropic models with 4.5 in Bedrock should have higher max_tokens."""
        config = {
            "configuration": {
                "model": "anthropic.claude-4.5-sonnet",
                "region": "us-east-1",
            },
            "isDefault": True,
        }
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrock") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                model_kwargs = mock_chat.call_args.kwargs.get("model_kwargs", {})
                assert model_kwargs["max_tokens"] == MAX_OUTPUT_TOKENS_CLAUDE_4_5

    def test_bedrock_non_anthropic_empty_model_kwargs(self):
        """Non-Anthropic models in Bedrock should have empty model_kwargs."""
        config = {
            "configuration": {
                "model": "meta.llama3-70b",
                "region": "us-east-1",
            },
            "isDefault": True,
        }
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrock") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                model_kwargs = mock_chat.call_args.kwargs.get("model_kwargs", {})
                assert model_kwargs == {}


# ============================================================================
# get_generator_model - Azure OpenAI reasoning model
# ============================================================================


class TestAzureOpenAIReasoningModel:
    @patch("langchain_openai.AzureChatOpenAI")
    def test_azure_openai_gpt5_reasoning(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = {
            "configuration": {
                "model": "gpt-5",
                "apiKey": "key",
                "endpoint": "https://test.openai.azure.com",
                "deploymentName": "gpt5-deployment",
            },
            "isDefault": True,
        }
        result = get_generator_model(LLMProvider.AZURE_OPENAI.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["temperature"] == 1

    @patch("langchain_openai.AzureChatOpenAI")
    def test_azure_openai_is_reasoning_flag(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = {
            "configuration": {
                "model": "o1",
                "apiKey": "key",
                "endpoint": "https://test.openai.azure.com",
                "deploymentName": "o1-deployment",
            },
            "isDefault": True,
            "isReasoning": True,
        }
        result = get_generator_model(LLMProvider.AZURE_OPENAI.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["temperature"] == 1


# ============================================================================
# get_generator_model - Azure AI claude 4.5 with reasoning
# ============================================================================


class TestAzureAIClaude45:
    @patch("langchain_anthropic.ChatAnthropic")
    def test_azure_ai_claude_4_5_max_tokens(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = {
            "configuration": {
                "model": "claude-4.5-sonnet",
                "apiKey": "key",
                "endpoint": "https://test.endpoint.com",
            },
            "isDefault": True,
        }
        result = get_generator_model(LLMProvider.AZURE_AI.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["max_tokens"] == MAX_OUTPUT_TOKENS_CLAUDE_4_5

    @patch("langchain_anthropic.ChatAnthropic")
    def test_azure_ai_claude_reasoning_flag(self, mock_cls):
        """Azure AI with claude model and isReasoning flag."""
        mock_cls.return_value = MagicMock()
        config = {
            "configuration": {
                "model": "claude-3.5-sonnet",
                "apiKey": "key",
                "endpoint": "https://test.endpoint.com",
            },
            "isDefault": True,
            "isReasoning": True,
        }
        result = get_generator_model(LLMProvider.AZURE_AI.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["temperature"] == 1

    @patch("langchain_anthropic.ChatAnthropic")
    def test_azure_ai_claude_custom_max_tokens(self, mock_cls):
        """Azure AI claude with custom maxTokens in configuration."""
        mock_cls.return_value = MagicMock()
        config = {
            "configuration": {
                "model": "claude-3.5-sonnet",
                "apiKey": "key",
                "endpoint": "https://test.endpoint.com",
                "maxTokens": 8192,
            },
            "isDefault": True,
        }
        result = get_generator_model(LLMProvider.AZURE_AI.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["max_tokens"] == 8192

    @patch("langchain_openai.ChatOpenAI")
    def test_azure_ai_non_claude_is_reasoning_flag(self, mock_cls):
        """Azure AI non-claude model with isReasoning flag."""
        mock_cls.return_value = MagicMock()
        config = {
            "configuration": {
                "model": "gpt-4o",
                "apiKey": "key",
                "endpoint": "https://test.endpoint.com",
            },
            "isDefault": True,
            "isReasoning": True,
        }
        result = get_generator_model(LLMProvider.AZURE_AI.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["temperature"] == 1


# ============================================================================
# get_generator_model - OpenAI compatible reasoning
# ============================================================================


class TestOpenAICompatibleReasoning:
    @patch("langchain_openai.ChatOpenAI")
    def test_openai_compatible_gpt5(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = {
            "configuration": {
                "model": "gpt-5",
                "apiKey": "key",
                "endpoint": "https://custom.api.com",
            },
            "isDefault": True,
        }
        result = get_generator_model(LLMProvider.OPENAI_COMPATIBLE.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["temperature"] == 1

    @patch("langchain_openai.ChatOpenAI")
    def test_openai_compatible_is_reasoning_flag(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = {
            "configuration": {
                "model": "o3",
                "apiKey": "key",
                "endpoint": "https://custom.api.com",
            },
            "isDefault": True,
            "isReasoning": True,
        }
        result = get_generator_model(LLMProvider.OPENAI_COMPATIBLE.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["temperature"] == 1


# ============================================================================
# get_generator_model - Ollama fallback endpoint from env var
# ============================================================================


class TestOllamaEndpoint:
    @patch("langchain_ollama.ChatOllama")
    @patch.dict("os.environ", {"OLLAMA_API_URL": "http://custom:11434"})
    def test_ollama_uses_env_var_fallback(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = {
            "configuration": {
                "model": "llama3",
            },
            "isDefault": True,
        }
        result = get_generator_model(LLMProvider.OLLAMA.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["base_url"] == "http://custom:11434"

    @patch("langchain_ollama.ChatOllama")
    def test_ollama_uses_config_endpoint(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = {
            "configuration": {
                "model": "llama3",
                "endpoint": "http://myollama:11434",
            },
            "isDefault": True,
        }
        result = get_generator_model(LLMProvider.OLLAMA.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["base_url"] == "http://myollama:11434"


# ============================================================================
# get_embedding_model - SentenceTransformers with encode_kwargs
# ============================================================================


class TestSentenceTransformersEncodeKwargs:
    @patch("langchain_community.embeddings.SentenceTransformerEmbeddings")
    def test_sentence_transformers_with_encode_kwargs(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = {
            "configuration": {
                "model": "all-MiniLM-L6-v2",
                "encode_kwargs": {"normalize_embeddings": True},
                "cache_folder": "/tmp/models",
            },
            "isDefault": True,
        }
        result = get_embedding_model(EmbeddingProvider.SENTENCE_TRANSFOMERS.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["encode_kwargs"] == {"normalize_embeddings": True}
        assert call_kwargs["cache_folder"] == "/tmp/models"


# ============================================================================
# _create_bedrock_client - region from env var
# ============================================================================


class TestBedrockClientRegion:
    @patch.dict("os.environ", {"AWS_DEFAULT_REGION": "eu-west-1"})
    def test_region_from_env_var(self):
        """When config has no region, falls back to AWS_DEFAULT_REGION env var."""
        config = {}
        with patch("boto3.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            from app.utils.aimodels import _create_bedrock_client
            _create_bedrock_client(config)
            mock_session_cls.assert_called_once_with(region_name="eu-west-1")

    def test_empty_keys_treated_as_no_keys(self):
        """Empty string keys (after strip) should use default credential chain."""
        config = {
            "awsAccessKeyId": "  ",
            "awsAccessSecretKey": "  ",
            "region": "us-east-1",
        }
        with patch("boto3.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            from app.utils.aimodels import _create_bedrock_client
            _create_bedrock_client(config)
            mock_session_cls.assert_called_once_with(region_name="us-east-1")

    def test_custom_service_name(self):
        """Test passing a custom service_name."""
        config = {"region": "us-east-1"}
        with patch("boto3.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            from app.utils.aimodels import _create_bedrock_client
            _create_bedrock_client(config, service_name="bedrock")
            mock_session.client.assert_called_once_with("bedrock")


# ============================================================================
# Bedrock Jamba auto-detect (ai21 via 'jamba' in name)
# ============================================================================


class TestBedrockJambaDetection:
    def test_jamba_in_model_name(self):
        config = {
            "configuration": {
                "model": "jamba-large-instruct",
                "region": "us-east-1",
            },
            "isDefault": True,
        }
        with patch("app.utils.aimodels._create_bedrock_client", return_value=MagicMock()):
            with patch("langchain_aws.ChatBedrock") as mock_chat:
                mock_chat.return_value = MagicMock()
                get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
                assert mock_chat.call_args.kwargs["provider"] == "ai21"
