"""Unit tests for pure functions in app.utils.aimodels."""

from unittest.mock import MagicMock, patch

import pytest

from app.utils.aimodels import (
    MAX_OUTPUT_TOKENS,
    MAX_OUTPUT_TOKENS_CLAUDE_4_5,
    EmbeddingProvider,
    LLMProvider,
    _get_anthropic_max_tokens,
    get_default_embedding_model,
    get_embedding_model,
    get_generator_model,
    is_multimodal_llm,
)


# ---------------------------------------------------------------------------
# is_multimodal_llm
# ---------------------------------------------------------------------------
class TestIsMultimodalLlm:
    """Tests for is_multimodal_llm(config)."""

    def test_top_level_true(self):
        config = {"isMultimodal": True}
        assert is_multimodal_llm(config) is True

    def test_top_level_false(self):
        config = {"isMultimodal": False}
        assert is_multimodal_llm(config) is False

    def test_nested_true(self):
        config = {"configuration": {"isMultimodal": True}}
        assert is_multimodal_llm(config) is True

    def test_nested_false(self):
        config = {"configuration": {"isMultimodal": False}}
        assert is_multimodal_llm(config) is False

    def test_missing_key_returns_false(self):
        config = {"otherKey": "value"}
        assert is_multimodal_llm(config) is False

    def test_nested_missing_returns_false(self):
        config = {"configuration": {"otherKey": "value"}}
        assert is_multimodal_llm(config) is False

    def test_empty_config(self):
        assert is_multimodal_llm({}) is False

    def test_top_level_takes_precedence(self):
        config = {"isMultimodal": True, "configuration": {"isMultimodal": False}}
        assert is_multimodal_llm(config) is True

    def test_missing_configuration_key(self):
        config = {"isMultimodal": False}
        # Should not raise even without 'configuration' key
        assert is_multimodal_llm(config) is False


# ---------------------------------------------------------------------------
# _get_anthropic_max_tokens
# ---------------------------------------------------------------------------
class TestGetAnthropicMaxTokens:
    """Tests for _get_anthropic_max_tokens(model_name)."""

    def test_claude_4_5_sonnet(self):
        assert _get_anthropic_max_tokens("claude-4.5-sonnet") == MAX_OUTPUT_TOKENS_CLAUDE_4_5

    def test_claude_4_5_opus(self):
        assert _get_anthropic_max_tokens("claude-4.5-opus") == MAX_OUTPUT_TOKENS_CLAUDE_4_5

    def test_claude_4_5_in_name(self):
        assert _get_anthropic_max_tokens("anthropic.claude-4.5-haiku-20260301") == MAX_OUTPUT_TOKENS_CLAUDE_4_5

    def test_claude_3_5_sonnet(self):
        # Does NOT contain "4.5"
        assert _get_anthropic_max_tokens("claude-3.5-sonnet") == MAX_OUTPUT_TOKENS

    def test_claude_3_opus(self):
        assert _get_anthropic_max_tokens("claude-3-opus") == MAX_OUTPUT_TOKENS

    def test_non_claude_model(self):
        assert _get_anthropic_max_tokens("gpt-4") == MAX_OUTPUT_TOKENS

    def test_empty_string(self):
        assert _get_anthropic_max_tokens("") == MAX_OUTPUT_TOKENS

    def test_max_tokens_constant_values(self):
        assert MAX_OUTPUT_TOKENS == 4096
        assert MAX_OUTPUT_TOKENS_CLAUDE_4_5 == 64000


# ---------------------------------------------------------------------------
# get_default_embedding_model
# ---------------------------------------------------------------------------
class TestGetDefaultEmbeddingModel:
    """Tests for get_default_embedding_model()."""

    @patch("app.utils.aimodels.HuggingFaceEmbeddings", create=True)
    def test_creates_huggingface_model(self, _mock_hf_class):
        """Verify the function attempts to create a HuggingFaceEmbeddings model."""
        # We need to patch the import inside the function
        mock_hf = MagicMock()
        with patch.dict("sys.modules", {"langchain_huggingface": MagicMock(HuggingFaceEmbeddings=mock_hf)}):
            # Re-import to pick up the mock
            import importlib
            import sys

            # Clear the cached import
            mod_name = "app.utils.aimodels"
            if mod_name in sys.modules:
                mod = sys.modules[mod_name]
            # Call the function which internally imports HuggingFaceEmbeddings
            from app.utils.aimodels import get_default_embedding_model
            result = get_default_embedding_model()
            # The function should return whatever HuggingFaceEmbeddings() returns
            assert result is not None


# ---------------------------------------------------------------------------
# get_embedding_model - provider branches
# ---------------------------------------------------------------------------
class TestGetEmbeddingModel:
    """Tests for get_embedding_model(provider, config) covering all provider branches."""

    def _base_config(self, model="test-model"):
        return {
            "configuration": {
                "model": model,
                "apiKey": "test-key",
                "endpoint": "https://test.endpoint.com",
                "organizationId": "test-org",
                "region": "us-east-1",
            },
            "isDefault": True,
        }

    @patch("langchain_openai.embeddings.OpenAIEmbeddings")
    def test_azure_ai(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_embedding_model(EmbeddingProvider.AZURE_AI.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("langchain_openai.embeddings.AzureOpenAIEmbeddings")
    def test_azure_openai(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_embedding_model(EmbeddingProvider.AZURE_OPENAI.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("langchain_cohere.CohereEmbeddings")
    def test_cohere(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_embedding_model(EmbeddingProvider.COHERE.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("app.utils.aimodels.get_default_embedding_model")
    def test_default(self, mock_fn):
        mock_fn.return_value = MagicMock()
        config = self._base_config()
        result = get_embedding_model(EmbeddingProvider.DEFAULT.value, config)
        mock_fn.assert_called_once()
        assert result is mock_fn.return_value

    @patch("langchain_fireworks.FireworksEmbeddings")
    def test_fireworks(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_embedding_model(EmbeddingProvider.FIREWORKS.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("langchain_google_genai.GoogleGenerativeAIEmbeddings")
    def test_gemini(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config("text-embedding-004")
        result = get_embedding_model(EmbeddingProvider.GEMINI.value, config)
        mock_cls.assert_called_once()
        # Model name should be prefixed with "models/"
        call_kwargs = mock_cls.call_args
        assert call_kwargs.kwargs["model"] == "models/text-embedding-004"
        assert result is mock_cls.return_value

    @patch("langchain_google_genai.GoogleGenerativeAIEmbeddings")
    def test_gemini_already_prefixed(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config("models/text-embedding-004")
        result = get_embedding_model(EmbeddingProvider.GEMINI.value, config)
        call_kwargs = mock_cls.call_args
        # Should NOT double-prefix
        assert call_kwargs.kwargs["model"] == "models/text-embedding-004"

    @patch("langchain_community.embeddings.HuggingFaceEmbeddings")
    def test_hugging_face(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_embedding_model(EmbeddingProvider.HUGGING_FACE.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("langchain_community.embeddings.jina.JinaEmbeddings")
    def test_jina_ai(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_embedding_model(EmbeddingProvider.JINA_AI.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("langchain_mistralai.MistralAIEmbeddings")
    def test_mistral(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_embedding_model(EmbeddingProvider.MISTRAL.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("langchain_ollama.OllamaEmbeddings")
    def test_ollama(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_embedding_model(EmbeddingProvider.OLLAMA.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("langchain_openai.embeddings.OpenAIEmbeddings")
    def test_openai(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_embedding_model(EmbeddingProvider.OPENAI.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("app.utils.aimodels._create_bedrock_client")
    @patch("langchain_aws.BedrockEmbeddings")
    def test_bedrock(self, mock_cls, mock_create_client):
        mock_cls.return_value = MagicMock()
        mock_create_client.return_value = MagicMock()
        config = self._base_config()
        result = get_embedding_model(EmbeddingProvider.AWS_BEDROCK.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("langchain_community.embeddings.SentenceTransformerEmbeddings")
    def test_sentence_transformers(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_embedding_model(EmbeddingProvider.SENTENCE_TRANSFOMERS.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("langchain_openai.embeddings.OpenAIEmbeddings")
    def test_openai_compatible(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_embedding_model(EmbeddingProvider.OPENAI_COMPATIBLE.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("app.utils.custom_embeddings.TogetherEmbeddings")
    def test_together(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_embedding_model(EmbeddingProvider.TOGETHER.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("app.utils.custom_embeddings.VoyageEmbeddings")
    def test_voyage(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_embedding_model(EmbeddingProvider.VOYAGE.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    def test_unknown_provider_raises(self):
        config = self._base_config()
        with pytest.raises(ValueError, match="Unsupported embedding config type"):
            get_embedding_model("nonexistent_provider", config)

    def test_model_name_override(self):
        """Providing a model_name argument should use that instead of config's model."""
        config = self._base_config("config-model, other-model")
        config["isDefault"] = False
        with patch("langchain_openai.embeddings.OpenAIEmbeddings") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = get_embedding_model(
                EmbeddingProvider.OPENAI.value, config, model_name="other-model"
            )
            call_kwargs = mock_cls.call_args
            assert call_kwargs.kwargs["model"] == "other-model"

    def test_model_name_not_in_list_raises(self):
        config = self._base_config("model-a, model-b")
        config["isDefault"] = False
        with pytest.raises(ValueError, match="not found"):
            get_embedding_model(EmbeddingProvider.OPENAI.value, config, model_name="model-c")


# ---------------------------------------------------------------------------
# get_generator_model - provider branches
# ---------------------------------------------------------------------------
class TestGetGeneratorModel:
    """Tests for get_generator_model(provider, config) covering all provider branches."""

    def _base_config(self, model="test-model"):
        return {
            "configuration": {
                "model": model,
                "apiKey": "test-key",
                "endpoint": "https://test.endpoint.com",
                "organizationId": "test-org",
                "region": "us-east-1",
                "deploymentName": "test-deployment",
                "provider": None,
            },
            "isDefault": True,
        }

    @patch("langchain_anthropic.ChatAnthropic")
    def test_anthropic(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_generator_model(LLMProvider.ANTHROPIC.value, config)
        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["model"] == "test-model"
        assert call_kwargs["temperature"] == 0.2
        assert call_kwargs["max_tokens"] == MAX_OUTPUT_TOKENS
        assert result is mock_cls.return_value

    @patch("langchain_anthropic.ChatAnthropic")
    def test_anthropic_claude_4_5(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config("claude-4.5-sonnet")
        result = get_generator_model(LLMProvider.ANTHROPIC.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["max_tokens"] == MAX_OUTPUT_TOKENS_CLAUDE_4_5

    @patch("app.utils.aimodels._create_bedrock_client")
    @patch("langchain_aws.ChatBedrock")
    def test_bedrock(self, mock_cls, mock_create_client):
        mock_cls.return_value = MagicMock()
        mock_create_client.return_value = MagicMock()
        config = self._base_config("anthropic.claude-3-sonnet")
        result = get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("app.utils.aimodels._create_bedrock_client")
    @patch("langchain_aws.ChatBedrock")
    def test_bedrock_auto_detects_mistral(self, mock_cls, mock_create_client):
        mock_cls.return_value = MagicMock()
        mock_create_client.return_value = MagicMock()
        config = self._base_config("mistral-large-2")
        config["configuration"]["provider"] = None
        result = get_generator_model(LLMProvider.AWS_BEDROCK.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["provider"] == LLMProvider.MISTRAL.value

    @patch("langchain_anthropic.ChatAnthropic")
    def test_azure_ai_claude(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config("claude-3.5-sonnet")
        result = get_generator_model(LLMProvider.AZURE_AI.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("langchain_openai.ChatOpenAI")
    def test_azure_ai_non_claude(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config("gpt-4o")
        result = get_generator_model(LLMProvider.AZURE_AI.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("langchain_openai.ChatOpenAI")
    def test_azure_ai_reasoning_model(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config("gpt-5")
        result = get_generator_model(LLMProvider.AZURE_AI.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["temperature"] == 1

    @patch("langchain_openai.AzureChatOpenAI")
    def test_azure_openai(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_generator_model(LLMProvider.AZURE_OPENAI.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("langchain_cohere.ChatCohere")
    def test_cohere(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_generator_model(LLMProvider.COHERE.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("langchain_fireworks.ChatFireworks")
    def test_fireworks(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_generator_model(LLMProvider.FIREWORKS.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("langchain_google_genai.ChatGoogleGenerativeAI")
    def test_gemini(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_generator_model(LLMProvider.GEMINI.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("langchain_groq.ChatGroq")
    def test_groq(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_generator_model(LLMProvider.GROQ.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("langchain_mistralai.ChatMistralAI")
    def test_mistral(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_generator_model(LLMProvider.MISTRAL.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("langchain_ollama.ChatOllama")
    def test_ollama(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_generator_model(LLMProvider.OLLAMA.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("langchain_openai.ChatOpenAI")
    def test_openai(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_generator_model(LLMProvider.OPENAI.value, config)
        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["stream_usage"] is True
        assert result is mock_cls.return_value

    @patch("langchain_openai.ChatOpenAI")
    def test_openai_reasoning_model(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config("gpt-5")
        result = get_generator_model(LLMProvider.OPENAI.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["temperature"] == 1

    @patch("langchain_openai.ChatOpenAI")
    def test_openai_is_reasoning_flag(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config("o1")
        config["isReasoning"] = True
        result = get_generator_model(LLMProvider.OPENAI.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["temperature"] == 1

    @patch("langchain_xai.ChatXAI")
    def test_xai(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_generator_model(LLMProvider.XAI.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("app.utils.custom_chat_model.ChatTogether")
    def test_together(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_generator_model(LLMProvider.TOGETHER.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    @patch("langchain_openai.ChatOpenAI")
    def test_openai_compatible(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        result = get_generator_model(LLMProvider.OPENAI_COMPATIBLE.value, config)
        mock_cls.assert_called_once()
        assert result is mock_cls.return_value

    def test_unknown_provider_raises(self):
        config = self._base_config()
        with pytest.raises(ValueError, match="Unsupported provider type"):
            get_generator_model("nonexistent_provider", config)

    def test_model_name_override(self):
        config = self._base_config("model-a, model-b")
        config["isDefault"] = False
        with patch("langchain_openai.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = get_generator_model(
                LLMProvider.OPENAI.value, config, model_name="model-b"
            )
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == "model-b"

    def test_model_name_not_in_list_raises(self):
        config = self._base_config("model-a, model-b")
        config["isDefault"] = False
        with pytest.raises(ValueError, match="not found"):
            get_generator_model(LLMProvider.OPENAI.value, config, model_name="model-c")
