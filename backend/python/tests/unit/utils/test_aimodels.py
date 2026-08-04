"""Unit tests for pure functions in app.utils.aimodels."""

from unittest.mock import MagicMock, patch

import pytest

from app.config.constants.ai_models import DEFAULT_EMBEDDING_MODEL
from app.utils.aimodels import (
    MAX_OUTPUT_TOKENS,
    MAX_OUTPUT_TOKENS_CLAUDE_4_5,
    MAX_OUTPUT_TOKENS_CLAUDE_MODERN,
    EmbeddingProvider,
    LLMProvider,
    _get_anthropic_max_tokens,
    _is_openai_gpt5_model,
    _reasoning_effort_kwargs,
    get_default_embedding_model,
    get_embedding_model,
    get_generator_model,
    is_multimodal_llm,
)
from app.utils.llm_api_mode_store import REASONING_MANDATORY_FALLBACK_EFFORT, LLMApiMode


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

    def test_claude_haiku_4_5(self):
        assert _get_anthropic_max_tokens("claude-haiku-4-5") == MAX_OUTPUT_TOKENS_CLAUDE_4_5

    def test_claude_haiku_4_5_dated(self):
        assert _get_anthropic_max_tokens("claude-haiku-4-5-20251001") == MAX_OUTPUT_TOKENS_CLAUDE_4_5

    def test_claude_sonnet_5(self):
        assert _get_anthropic_max_tokens("claude-sonnet-5") == MAX_OUTPUT_TOKENS_CLAUDE_MODERN

    def test_claude_opus_4_8(self):
        assert _get_anthropic_max_tokens("claude-opus-4-8") == MAX_OUTPUT_TOKENS_CLAUDE_MODERN

    def test_claude_sonnet_4_6(self):
        assert _get_anthropic_max_tokens("claude-sonnet-4-6") == MAX_OUTPUT_TOKENS_CLAUDE_MODERN

    def test_claude_opus_4_6(self):
        assert _get_anthropic_max_tokens("claude-opus-4-6") == MAX_OUTPUT_TOKENS_CLAUDE_MODERN

    def test_claude_3_5_sonnet(self):
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
        assert MAX_OUTPUT_TOKENS_CLAUDE_MODERN == 16384


# ---------------------------------------------------------------------------
# _reasoning_effort_kwargs
# ---------------------------------------------------------------------------
class TestReasoningEffortKwargs:
    """Tests for _reasoning_effort_kwargs(reasoning_effort, config)."""

    def test_returns_kwarg_when_reasoning_capable(self):
        config = {"isReasoning": True}
        assert _reasoning_effort_kwargs("high", config) == {"reasoning_effort": "high"}

    def test_returns_empty_when_not_reasoning_capable(self):
        """Passing reasoning_effort to a non-reasoning model integration raises
        UnsupportedParamsError in LangChain, so the kwarg must be omitted."""
        config = {"isReasoning": False}
        assert _reasoning_effort_kwargs("high", config) == {}

    def test_defaults_to_high_when_effort_is_none_and_reasoning_capable(self):
        """A reasoning-capable model with no explicit effort defaults to
        'high' rather than silently deferring to the provider's own default."""
        config = {"isReasoning": True}
        assert _reasoning_effort_kwargs(None, config) == {"reasoning_effort": "high"}

    def test_returns_empty_when_effort_is_none_and_not_reasoning(self):
        config = {"isReasoning": False}
        assert _reasoning_effort_kwargs(None, config) == {}

    def test_missing_is_reasoning_key_defaults_to_not_capable(self):
        assert _reasoning_effort_kwargs("max", {}) == {}

    def test_none_string_is_floored_to_low_not_forwarded_as_is(self):
        """'none' is distinct from Python None (let the provider decide), but
        is no longer offered as a UI choice and is silently floored to 'low'
        instead of actually disabling reasoning — see the docstring above."""
        config = {"isReasoning": True}
        assert _reasoning_effort_kwargs("none", config) == {"reasoning_effort": "low"}

    def test_max_mapped_to_xhigh_for_openai_provider(self):
        """Direct OpenAI routes through the Responses API to allow combining
        reasoning effort with bound function tools."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs("max", config, provider="openAI")
        assert result == {
            "reasoning": {"effort": "xhigh"},
            "use_responses_api": True,
        }

    def test_max_mapped_to_xhigh_for_azure_openai_provider(self):
        """Azure OpenAI has the same Chat Completions restriction as direct
        OpenAI, so it also routes through the Responses API."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs("max", config, provider="azureOpenAI")
        assert result == {
            "reasoning": {"effort": "xhigh"},
            "use_responses_api": True,
        }

    def test_max_passed_through_for_anthropic_provider(self):
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs("max", config, provider="anthropic")
        assert result == {"reasoning_effort": "max"}

    def test_none_mapped_to_low_for_anthropic(self):
        """'none' is floored to 'low' up front (Anthropic doesn't support
        'none' anyway; lowest valid is 'low')."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs("none", config, provider="anthropic")
        assert result == {"reasoning_effort": "low"}

    def test_max_mapped_to_high_for_gemini(self):
        """Gemini's highest thinking_level is 'high'."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs("max", config, provider="gemini")
        assert result == {"reasoning_effort": "high"}

    def test_none_floored_to_low_for_gemini(self):
        """'none' is floored to 'low' up front, before Gemini's own map ever
        sees it — Gemini's map only translates a genuine 'none' input to its
        'minimal' tier, which no caller can produce anymore."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs("none", config, provider="gemini")
        assert result == {"reasoning_effort": "low"}

    def test_max_mapped_to_high_for_vertex_ai(self):
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs("max", config, provider="vertexAI")
        assert result == {"reasoning_effort": "high"}

    def test_high_passes_through_unchanged_for_openai(self):
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs("high", config, provider="openAI")
        assert result == {
            "reasoning": {"effort": "high"},
            "use_responses_api": True,
        }

    def test_max_mapped_for_openai_compatible(self):
        """Third-party OpenAI-compatible endpoints don't expose /v1/responses,
        so they keep the legacy reasoning_effort kwarg."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs("max", config, provider="openAICompatible")
        assert result == {"reasoning_effort": "xhigh"}

    def test_openai_compatible_pointed_at_openai_uses_responses_api(self):
        """OpenAI's own API is reachable through the generic OpenAI-compatible
        provider, and hits the same Chat Completions tools restriction, so the
        routing decision must be endpoint-aware and not provider-only."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs(
            "max", config, provider="openAICompatible",
            base_url="https://api.openai.com/v1",
        )
        assert result == {
            "reasoning": {"effort": "xhigh"},
            "use_responses_api": True,
        }

    def test_openai_compatible_self_hosted_keeps_legacy_kwarg(self):
        """An endpoint that merely speaks the OpenAI protocol has no
        /v1/responses, so it must not be switched over."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs(
            "max", config, provider="openAICompatible",
            base_url="https://vllm.internal.corp/v1",
        )
        assert result == {"reasoning_effort": "xhigh"}

    def test_host_match_is_exact_not_substring(self):
        """A lookalike host must not be mistaken for OpenAI's official API."""
        config = {"isReasoning": True}
        for spoof in (
            "https://api.openai.com.evil.example/v1",
            "https://not-api.openai.com/v1",
            "https://api.openai.company/v1",
        ):
            result = _reasoning_effort_kwargs(
                "high", config, provider="openAICompatible", base_url=spoof,
            )
            assert result == {"reasoning_effort": "high"}, spoof

    def test_base_url_host_match_is_case_insensitive(self):
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs(
            "high", config, provider="openAICompatible",
            base_url="https://API.OpenAI.COM/v1",
        )
        assert result["use_responses_api"] is True

    def test_malformed_base_url_does_not_raise(self):
        config = {"isReasoning": True}
        for bad in ("", "not-a-url", "://missing-scheme"):
            result = _reasoning_effort_kwargs(
                "high", config, provider="openAICompatible", base_url=bad,
            )
            assert result == {"reasoning_effort": "high"}, bad

    def test_max_clamped_to_high_for_xai(self):
        """xAI's Grok models don't document an 'xhigh' tier, so 'max' is
        clamped to 'high' instead of reusing OpenAI's map (see _XAI_EFFORT_MAP)."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs("max", config, provider="xai")
        assert result == {"reasoning_effort": "high"}

    def test_azure_ai_openai_subpath_uses_responses_api(self):
        """Azure AI's OpenAI sub-path is called with provider=OPENAI.value
        (see get_generator_model), so it must also route through Responses."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs("high", config, provider="openAI")
        assert result["use_responses_api"] is True

    def test_reasoning_effort_key_absent_for_responses_api_providers(self):
        """The legacy reasoning_effort kwarg must not be sent alongside the
        Responses API kwargs, since ChatOpenAI would reject duplicate/
        conflicting reasoning configuration."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs("medium", config, provider="azureOpenAI")
        assert "reasoning_effort" not in result

    def test_responses_api_reasoning_dict_never_requests_a_summary(self):
        """`reasoning.summary` requires the caller's OpenAI organization to
        be Verified (400s otherwise: "Your organization must be verified to
        generate reasoning summaries...") and some Azure OpenAI / gateway
        (Requesty et al.) Responses API implementations reject the field
        outright even when verified ("Unknown parameter: 'reasoning_summary'."),
        so it must never be requested by default for any Responses-API-routed
        provider."""
        config = {"isReasoning": True}
        for provider, base_url, model_name in (
            ("openAI", None, None),
            ("azureOpenAI", None, None),
            ("openAICompatible", "https://router.requesty.ai/v1", "azure/gpt-5.6-luna@swedencentral"),
        ):
            result = _reasoning_effort_kwargs(
                "high", config, provider=provider, base_url=base_url, model_name=model_name,
            )
            assert result == {
                "reasoning": {"effort": "high"},
                "use_responses_api": True,
            }, (provider, base_url, model_name)

    # -- LM Studio: only low/medium/high are valid, unlike OpenAI itself -----

    def test_lm_studio_high_passes_through_unchanged(self):
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs("high", config, provider="lmStudio")
        assert result == {"reasoning_effort": "high"}

    def test_lm_studio_none_clamped_to_low(self):
        """'none' is floored to 'low' up front (LM Studio's reasoning_effort
        has no 'none' tier anyway; lowest is 'low')."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs("none", config, provider="lmStudio")
        assert result == {"reasoning_effort": "low"}

    def test_lm_studio_max_clamped_to_high(self):
        """LM Studio's reasoning_effort has no 'xhigh'/'max' tier; highest is 'high'."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs("max", config, provider="lmStudio")
        assert result == {"reasoning_effort": "high"}

    # -- Ollama: uses `reasoning` (bool | str), not `reasoning_effort` -------

    def test_ollama_returns_reasoning_key_not_reasoning_effort(self):
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs("high", config, provider="ollama")
        assert result == {"reasoning": "high"}
        assert "reasoning_effort" not in result

    def test_ollama_none_floored_to_low_thinking_stays_on(self):
        """'none' is floored to 'low' up front, so an explicit 'none' choice
        no longer disables Ollama's thinking via `reasoning=False` — it now
        enables it at the lowest tier, same as every other provider."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs("none", config, provider="ollama")
        assert result == {"reasoning": "low"}

    def test_ollama_max_clamped_to_high(self):
        """Ollama's `think` level has no 'max' tier yet; highest is 'high'."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs("max", config, provider="ollama")
        assert result == {"reasoning": "high"}

    def test_ollama_low_and_medium_pass_through_unchanged(self):
        config = {"isReasoning": True}
        assert _reasoning_effort_kwargs("low", config, provider="ollama") == {
            "reasoning": "low"
        }
        assert _reasoning_effort_kwargs("medium", config, provider="ollama") == {
            "reasoning": "medium"
        }

    def test_ollama_returns_empty_when_not_reasoning_capable(self):
        config = {"isReasoning": False}
        assert _reasoning_effort_kwargs("high", config, provider="ollama") == {}

    def test_ollama_defaults_to_high_when_effort_is_none_and_reasoning_capable(self):
        config = {"isReasoning": True}
        assert _reasoning_effort_kwargs(None, config, provider="ollama") == {
            "reasoning": "high"
        }

    # -- gpt-5.x model-name detection (routers/proxies fronting real OpenAI) -

    def test_openai_compatible_gpt5_model_name_uses_responses_api(self):
        """A router/proxy (Requesty, OpenRouter, LiteLLM proxy, ...) that
        transparently forwards to an actual OpenAI gpt-5.x model hits the
        same Chat Completions restriction as the direct provider, even
        though its base_url is the router's own host, not api.openai.com."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs(
            "high", config, provider="openAICompatible",
            base_url="https://router.requesty.ai/v1",
            model_name="gpt-5.6-luna",
        )
        assert result == {
            "reasoning": {"effort": "high"},
            "use_responses_api": True,
        }

    def test_litellm_proxy_gpt5_model_name_uses_responses_api(self):
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs(
            "medium", config, provider="litellmProxy",
            base_url="https://my-litellm-proxy.internal/v1",
            model_name="gpt-5-mini",
        )
        assert result["use_responses_api"] is True

    def test_openrouter_gpt5_model_name_uses_responses_api(self):
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs(
            "high", config, provider="openRouter", model_name="openai/gpt-5.4",
        )
        assert result["use_responses_api"] is True

    def test_gpt5_model_name_match_requires_openai_family_provider(self):
        """A non-OpenAI-protocol provider must never be switched to
        ChatOpenAI's Responses API kwargs, even if its model happens to be
        named like OpenAI's gpt-5.x — those constructors (ChatFireworks,
        ChatXAI, ...) don't accept `use_responses_api`."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs(
            "high", config, provider="fireworks", model_name="gpt-5-lookalike",
        )
        assert result == {"reasoning_effort": "high"}

    def test_non_gpt5_model_name_keeps_legacy_kwarg_on_proxy(self):
        """A proxy serving a non-OpenAI model (even one with 'gpt-5' as a
        substring elsewhere in an unrelated position) keeps the legacy
        kwarg — only the documented gpt-5.x naming pattern matches."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs(
            "high", config, provider="openAICompatible",
            base_url="https://router.requesty.ai/v1",
            model_name="some-other-model-50",
        )
        assert result == {"reasoning_effort": "high"}

    def test_gpt5_model_name_variants_all_match(self):
        config = {"isReasoning": True}
        for name in ("gpt-5", "gpt-5.1", "gpt-5.6-luna", "gpt-5-mini", "openai/gpt-5.4"):
            result = _reasoning_effort_kwargs(
                "high", config, provider="openAICompatible",
                base_url="https://router.requesty.ai/v1", model_name=name,
            )
            assert result["use_responses_api"] is True, name

    # -- reasoning "none" is floored to "low" and then routes like any real
    #    "low" effort choice (including through the Responses API when the
    #    provider/model needs it for reasoning + bound tools) -------------

    def test_reasoning_none_floored_then_routes_through_responses_api_for_direct_openai(self):
        """Once floored to 'low', a direct-OpenAI reasoning-capable model
        needs the Responses API for genuine reasoning + bound tools, same as
        any other non-'none' effort — 'none' no longer gets a free pass onto
        Chat Completions."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs("none", config, provider="openAI")
        assert result == {
            "reasoning": {"effort": "low"},
            "use_responses_api": True,
        }

    def test_reasoning_none_floored_then_routes_through_responses_api_for_gpt5_via_proxy(self):
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs(
            "none", config, provider="openAICompatible",
            base_url="https://router.requesty.ai/v1",
            model_name="gpt-5.6-luna",
        )
        assert result == {
            "reasoning": {"effort": "low"},
            "use_responses_api": True,
        }

    def test_reasoning_none_floored_then_routes_through_responses_api_for_azure_openai(self):
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs("none", config, provider="azureOpenAI")
        assert result == {
            "reasoning": {"effort": "low"},
            "use_responses_api": True,
        }


# ---------------------------------------------------------------------------
# get_default_embedding_model
# ---------------------------------------------------------------------------
class TestGetDefaultEmbeddingModel:
    """Tests for get_default_embedding_model()."""

    @patch("app.utils.embedding_server_client.EmbeddingServerEmbeddings")
    def test_creates_embedding_server_client(self, mock_cls):
        """Default embeddings route through the local embedding server."""
        mock_cls.return_value = MagicMock()
        result = get_default_embedding_model()
        mock_cls.assert_called_once_with(
            model=DEFAULT_EMBEDDING_MODEL,
            trust_remote_code=False,
        )
        assert result is mock_cls.return_value


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

    @patch("app.utils.aimodels.get_embedding_server_embeddings")
    def test_hugging_face(self, mock_fn):
        mock_fn.return_value = MagicMock()
        config = self._base_config()
        result = get_embedding_model(EmbeddingProvider.HUGGING_FACE.value, config)
        mock_fn.assert_called_once_with("test-model", trust_remote_code=False)
        assert result is mock_fn.return_value

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

    @patch("app.utils.aimodels.get_embedding_server_embeddings")
    def test_sentence_transformers(self, mock_fn):
        mock_fn.return_value = MagicMock()
        config = self._base_config()
        result = get_embedding_model(EmbeddingProvider.SENTENCE_TRANSFOMERS.value, config)
        mock_fn.assert_called_once_with("test-model", trust_remote_code=False)
        assert result is mock_fn.return_value

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

    @patch("langchain_openai.ChatOpenAI")
    def test_azure_ai_is_reasoning_flag_alone_does_not_force_temperature(self, mock_cls):
        """Azure AI Foundry also hosts non-OpenAI reasoning models (Llama,
        Mistral, DeepSeek, ...) under `isReasoning=True` — only an actual
        `_is_openai_gpt5_model`-matching name should force temperature=1,
        not the flag alone, since this provider is not guaranteed to be
        talking to OpenAI's own API (see `_default_temperature`)."""
        mock_cls.return_value = MagicMock()
        config = self._base_config("deepseek-v3")
        config["isReasoning"] = True
        config["configuration"]["temperature"] = 0.3
        get_generator_model(LLMProvider.AZURE_AI.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["temperature"] == 0.3

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
        call_kwargs = mock_cls.call_args.kwargs
        # Non-reasoning models keep thinking disabled so <think> tags never
        # leak into the main response content.
        assert call_kwargs["reasoning"] is False
        assert result is mock_cls.return_value

    @patch("langchain_ollama.ChatOllama")
    def test_ollama_reasoning_effort_passed_as_think_level(self, mock_cls):
        """Reasoning-capable Ollama models forward the platform reasoning
        effort as ChatOllama's `reasoning` kwarg (Ollama's native `think`
        field), not the OpenAI-style `reasoning_effort` kwarg."""
        mock_cls.return_value = MagicMock()
        config = self._base_config("gpt-oss:20b")
        config["isReasoning"] = True
        get_generator_model(LLMProvider.OLLAMA.value, config, reasoning_effort="medium")
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["reasoning"] == "medium"
        assert "reasoning_effort" not in call_kwargs

    @patch("langchain_ollama.ChatOllama")
    def test_ollama_reasoning_effort_none_is_floored_to_low(self, mock_cls):
        """An explicit 'none' choice no longer disables Ollama's thinking —
        it's floored to 'low' before reaching the provider map."""
        mock_cls.return_value = MagicMock()
        config = self._base_config("gpt-oss:20b")
        config["isReasoning"] = True
        get_generator_model(LLMProvider.OLLAMA.value, config, reasoning_effort="none")
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["reasoning"] == "low"

    @patch("langchain_ollama.ChatOllama")
    def test_ollama_reasoning_effort_max_clamped_to_high(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config("gpt-oss:20b")
        config["isReasoning"] = True
        get_generator_model(LLMProvider.OLLAMA.value, config, reasoning_effort="max")
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["reasoning"] == "high"

    @patch("langchain_ollama.ChatOllama")
    def test_ollama_reasoning_effort_defaults_to_high_when_unset(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config("gpt-oss:20b")
        config["isReasoning"] = True
        get_generator_model(LLMProvider.OLLAMA.value, config, reasoning_effort=None)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["reasoning"] == "high"

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

    @patch("langchain_openai.ChatOpenAI")
    def test_minimax(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config("MiniMax-M3")
        result = get_generator_model(LLMProvider.MINIMAX.value, config)
        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["model"] == "MiniMax-M3"
        assert call_kwargs["base_url"] == "https://api.minimax.io/v1"
        assert call_kwargs["stream_usage"] is True
        assert result is mock_cls.return_value

    @patch("langchain_openai.ChatOpenAI")
    def test_minimax_temperature_clamping(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config("MiniMax-M3")
        config["configuration"]["temperature"] = 0.0
        result = get_generator_model(LLMProvider.MINIMAX.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        # MiniMax requires temperature > 0, so 0.0 should be clamped to 0.01
        assert call_kwargs["temperature"] == 0.01

    @patch("langchain_openai.ChatOpenAI")
    def test_minimax_temperature_upper_bound(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config("MiniMax-M3")
        config["configuration"]["temperature"] = 2.0
        result = get_generator_model(LLMProvider.MINIMAX.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        # MiniMax requires temperature <= 1.0
        assert call_kwargs["temperature"] == 1.0

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

    # -- reasoning_effort plumbing -----------------------------------------

    @patch("langchain_openai.ChatOpenAI")
    def test_openai_reasoning_effort_passed_when_reasoning_capable(self, mock_cls):
        """Direct OpenAI routes through the Responses API (`reasoning` +
        `use_responses_api=True`) rather than the legacy `reasoning_effort`
        kwarg, since gpt-5.x rejects reasoning_effort + tools on Chat
        Completions."""
        mock_cls.return_value = MagicMock()
        config = self._base_config("gpt-5")
        config["isReasoning"] = True
        get_generator_model(LLMProvider.OPENAI.value, config, reasoning_effort="high")
        call_kwargs = mock_cls.call_args.kwargs
        assert "reasoning_effort" not in call_kwargs
        assert call_kwargs["reasoning"] == {"effort": "high"}
        assert call_kwargs["use_responses_api"] is True

    @patch("langchain_openai.ChatOpenAI")
    def test_openai_reasoning_effort_omitted_when_not_reasoning_capable(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        config["isReasoning"] = False
        get_generator_model(LLMProvider.OPENAI.value, config, reasoning_effort="high")
        call_kwargs = mock_cls.call_args.kwargs
        assert "reasoning_effort" not in call_kwargs
        assert "reasoning" not in call_kwargs
        assert "use_responses_api" not in call_kwargs

    @patch("langchain_openai.ChatOpenAI")
    def test_openai_reasoning_effort_defaults_to_high_when_none(self, mock_cls):
        """Absent reasoning_effort on a reasoning-capable model now defaults to
        'high' (via the Responses API for direct OpenAI) instead of silently
        deferring to the provider's own default."""
        mock_cls.return_value = MagicMock()
        config = self._base_config("gpt-5")
        config["isReasoning"] = True
        get_generator_model(LLMProvider.OPENAI.value, config, reasoning_effort=None)
        call_kwargs = mock_cls.call_args.kwargs
        assert "reasoning_effort" not in call_kwargs
        assert call_kwargs["reasoning"] == {"effort": "high"}
        assert call_kwargs["use_responses_api"] is True

    @patch("langchain_openai.AzureChatOpenAI")
    def test_azure_openai_reasoning_effort_uses_responses_api(self, mock_cls):
        """Azure OpenAI has the same Chat Completions restriction as direct
        OpenAI, so it must also route through the Responses API."""
        mock_cls.return_value = MagicMock()
        config = self._base_config("gpt-5")
        config["isReasoning"] = True
        get_generator_model(LLMProvider.AZURE_OPENAI.value, config, reasoning_effort="max")
        call_kwargs = mock_cls.call_args.kwargs
        assert "reasoning_effort" not in call_kwargs
        assert call_kwargs["reasoning"] == {"effort": "xhigh"}
        assert call_kwargs["use_responses_api"] is True

    @patch("langchain_openai.ChatOpenAI")
    def test_azure_ai_openai_subpath_reasoning_effort_uses_responses_api(self, mock_cls):
        """Azure AI's non-Claude sub-path constructs a ChatOpenAI instance and
        must also route through the Responses API for the same reason."""
        mock_cls.return_value = MagicMock()
        config = self._base_config("gpt-5")
        config["isReasoning"] = True
        get_generator_model(LLMProvider.AZURE_AI.value, config, reasoning_effort="medium")
        call_kwargs = mock_cls.call_args.kwargs
        assert "reasoning_effort" not in call_kwargs
        assert call_kwargs["reasoning"] == {"effort": "medium"}
        assert call_kwargs["use_responses_api"] is True

    @patch("langchain_openai.ChatOpenAI")
    def test_openai_compatible_reasoning_effort_keeps_legacy_kwarg(self, mock_cls):
        """Third-party OpenAI-compatible endpoints don't expose /v1/responses,
        so they must keep using the legacy reasoning_effort kwarg."""
        mock_cls.return_value = MagicMock()
        config = self._base_config("some-reasoning-model")
        config["isReasoning"] = True
        get_generator_model(LLMProvider.OPENAI_COMPATIBLE.value, config, reasoning_effort="max")
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["reasoning_effort"] == "xhigh"
        assert "reasoning" not in call_kwargs
        assert "use_responses_api" not in call_kwargs

    @patch("langchain_openai.ChatOpenAI")
    def test_openai_compatible_pointed_at_openai_uses_responses_api(self, mock_cls):
        """Adding an OpenAI gpt-5.x model under the OpenAI-compatible provider
        with endpoint https://api.openai.com/v1 is a supported setup, and hits
        the same Chat Completions tools restriction as the direct provider."""
        mock_cls.return_value = MagicMock()
        config = self._base_config("gpt-5.6-luna")
        config["configuration"]["endpoint"] = "https://api.openai.com/v1"
        config["isReasoning"] = True
        get_generator_model(LLMProvider.OPENAI_COMPATIBLE.value, config, reasoning_effort="max")
        call_kwargs = mock_cls.call_args.kwargs
        assert "reasoning_effort" not in call_kwargs
        assert call_kwargs["reasoning"] == {"effort": "xhigh"}
        assert call_kwargs["use_responses_api"] is True

    @patch("langchain_openai.ChatOpenAI")
    def test_openai_compatible_router_proxying_gpt5_uses_responses_api(self, mock_cls):
        """A router/gateway (e.g. Requesty, `router.requesty.ai`) configured
        as an OpenAI-compatible entry that forwards to a real OpenAI gpt-5.x
        model must also route through the Responses API, even though its
        base_url is the router's own host rather than api.openai.com."""
        mock_cls.return_value = MagicMock()
        config = self._base_config("gpt-5.6-luna")
        config["configuration"]["endpoint"] = "https://router.requesty.ai/v1"
        config["isReasoning"] = True
        get_generator_model(LLMProvider.OPENAI_COMPATIBLE.value, config, reasoning_effort="high")
        call_kwargs = mock_cls.call_args.kwargs
        assert "reasoning_effort" not in call_kwargs
        assert call_kwargs["reasoning"] == {"effort": "high"}
        assert call_kwargs["use_responses_api"] is True

    @patch("langchain_openai.ChatOpenAI")
    def test_openai_compatible_router_proxying_gpt5_with_reasoning_requested_off(self, mock_cls):
        """Requesting 'none' on the same router/proxy setup is floored to
        'low' up front, so it now routes through the Responses API exactly
        like any other real reasoning effort for this gpt-5.x model."""
        mock_cls.return_value = MagicMock()
        config = self._base_config("gpt-5.6-luna")
        config["configuration"]["endpoint"] = "https://router.requesty.ai/v1"
        config["isReasoning"] = True
        get_generator_model(LLMProvider.OPENAI_COMPATIBLE.value, config, reasoning_effort="none")
        call_kwargs = mock_cls.call_args.kwargs
        assert "reasoning_effort" not in call_kwargs
        assert call_kwargs["reasoning"] == {"effort": "low"}
        assert call_kwargs["use_responses_api"] is True

    @patch("langchain_openai.ChatOpenAI")
    def test_lm_studio_local_endpoint_keeps_legacy_kwarg(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config("qwen3.5:9b")
        config["configuration"]["endpoint"] = "http://localhost:1234/v1"
        config["isReasoning"] = True
        get_generator_model(LLMProvider.LM_STUDIO.value, config, reasoning_effort="high")
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["reasoning_effort"] == "high"
        assert "use_responses_api" not in call_kwargs

    @patch("langchain_openai.ChatOpenAI")
    def test_lm_studio_none_clamped_to_low(self, mock_cls):
        """LM Studio's OpenAI-compatible endpoint only documents low/medium/high
        for reasoning_effort; 'none' has no equivalent, so it clamps to 'low'."""
        mock_cls.return_value = MagicMock()
        config = self._base_config("openai/gpt-oss-20b")
        config["configuration"]["endpoint"] = "http://localhost:1234/v1"
        config["isReasoning"] = True
        get_generator_model(LLMProvider.LM_STUDIO.value, config, reasoning_effort="none")
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["reasoning_effort"] == "low"

    @patch("langchain_openai.ChatOpenAI")
    def test_lm_studio_max_clamped_to_high(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config("openai/gpt-oss-20b")
        config["configuration"]["endpoint"] = "http://localhost:1234/v1"
        config["isReasoning"] = True
        get_generator_model(LLMProvider.LM_STUDIO.value, config, reasoning_effort="max")
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["reasoning_effort"] == "high"

    @patch("langchain_openai.ChatOpenAI")
    def test_lm_studio_is_reasoning_flag_alone_does_not_force_temperature(self, mock_cls):
        """LM Studio only ever serves locally-hosted models (Qwen, DeepSeek,
        ...), never OpenAI's own API — `isReasoning=True` must not force
        temperature=1 for it (see `_default_temperature`)."""
        mock_cls.return_value = MagicMock()
        config = self._base_config("qwen3.5:9b")
        config["configuration"]["endpoint"] = "http://localhost:1234/v1"
        config["configuration"]["temperature"] = 0.4
        config["isReasoning"] = True
        get_generator_model(LLMProvider.LM_STUDIO.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["temperature"] == 0.4

    @patch("langchain_anthropic.ChatAnthropic")
    def test_anthropic_reasoning_effort_passed_when_reasoning_capable(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config("claude-4.5-sonnet")
        config["isReasoning"] = True
        get_generator_model(LLMProvider.ANTHROPIC.value, config, reasoning_effort="medium")
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["reasoning_effort"] == "medium"

    @patch("langchain_anthropic.ChatAnthropic")
    def test_anthropic_reasoning_effort_omitted_when_not_reasoning_capable(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        config["isReasoning"] = False
        get_generator_model(LLMProvider.ANTHROPIC.value, config, reasoning_effort="medium")
        call_kwargs = mock_cls.call_args.kwargs
        assert "reasoning_effort" not in call_kwargs

    @patch("langchain_google_genai.ChatGoogleGenerativeAI")
    def test_gemini_reasoning_effort_passed_when_reasoning_capable(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        config["isReasoning"] = True
        get_generator_model(LLMProvider.GEMINI.value, config, reasoning_effort="low")
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["reasoning_effort"] == "low"

    @patch("langchain_google_genai.ChatGoogleGenerativeAI")
    def test_gemini_reasoning_effort_omitted_when_not_reasoning_capable(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = self._base_config()
        config["isReasoning"] = False
        get_generator_model(LLMProvider.GEMINI.value, config, reasoning_effort="low")
        call_kwargs = mock_cls.call_args.kwargs
        assert "reasoning_effort" not in call_kwargs


# ---------------------------------------------------------------------------
# _is_openai_gpt5_model — gpt-5-chat variants are non-reasoning models and
# must never be routed to the Responses API.
# ---------------------------------------------------------------------------
class TestIsOpenaiGpt5Model:
    def test_matches_plain_gpt5(self):
        assert _is_openai_gpt5_model("gpt-5") is True

    def test_matches_dotted_minor_version(self):
        assert _is_openai_gpt5_model("gpt-5.6-luna") is True

    def test_matches_with_router_prefix(self):
        assert _is_openai_gpt5_model("openai/gpt-5") is True

    def test_excludes_gpt5_chat_latest(self):
        """gpt-5-chat-latest is a non-reasoning chat variant — it must never
        be routed to the Responses API."""
        assert _is_openai_gpt5_model("gpt-5-chat-latest") is False

    def test_excludes_dotted_minor_version_chat_variant(self):
        assert _is_openai_gpt5_model("gpt-5.3-chat-latest") is False

    def test_none_model_name_returns_false(self):
        assert _is_openai_gpt5_model(None) is False

    def test_empty_model_name_returns_false(self):
        assert _is_openai_gpt5_model("") is False

    def test_unrelated_model_returns_false(self):
        assert _is_openai_gpt5_model("gpt-4o") is False


# ---------------------------------------------------------------------------
# _reasoning_effort_kwargs — api_mode override (learned facts, see
# app/utils/llm_api_mode_store.py)
# ---------------------------------------------------------------------------
class TestReasoningEffortKwargsApiMode:
    def test_api_mode_none_falls_back_to_heuristic(self):
        """No learned fact yet — behaves exactly like today."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs(
            "high", config, provider="openAICompatible", model_name="some-random-alias",
            api_mode=None,
        )
        assert result == {"reasoning_effort": "high"}

    def test_api_mode_responses_forces_responses_branch(self):
        """A learned RESPONSES fact routes through the Responses API even
        though neither the provider nor the model name would trigger it on
        their own — this is exactly what a gateway alias the upfront
        heuristic can't classify needs."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs(
            "high", config, provider="openAICompatible", model_name="some-random-alias",
            api_mode=LLMApiMode.RESPONSES.value,
        )
        assert result == {"reasoning": {"effort": "high"}, "use_responses_api": True}

    def test_api_mode_responses_ignored_for_non_openai_family_provider(self):
        """`api_mode` is looked up only by (model_key, model_name), with no
        provider check — if a stale/reused fact says RESPONSES for a
        provider that isn't ChatOpenAI-based (e.g. Anthropic, reached after
        a model entry was reconfigured to a different provider under the
        same key), it must not force `use_responses_api`/`reasoning` onto
        that provider's constructor kwargs, which don't understand them."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs(
            "high", config, provider="anthropic", model_name="claude-x",
            api_mode=LLMApiMode.RESPONSES.value,
        )
        assert result == {"reasoning_effort": "high"}
        assert "use_responses_api" not in result

    def test_api_mode_responses_applies_even_to_a_floored_none_effort(self):
        """'none' is floored to 'low' before api_mode is even consulted, so a
        learned RESPONSES fact applies to it exactly like any other effort —
        there's no longer a "none stays on Chat Completions" exemption."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs(
            "none", config, provider="openAICompatible", model_name="some-random-alias",
            api_mode=LLMApiMode.RESPONSES.value,
        )
        assert result == {"reasoning": {"effort": "low"}, "use_responses_api": True}

    def test_api_mode_no_reasoning_with_tools_drops_reasoning_entirely(self):
        """A learned NO_REASONING_WITH_TOOLS fact means the gateway rejects
        reasoning + tools on both API shapes — the only safe result is to
        skip reasoning altogether."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs(
            "high", config, provider="openAI", model_name="gpt-5.6-luna",
            api_mode=LLMApiMode.NO_REASONING_WITH_TOOLS.value,
        )
        assert result == {}

    def test_api_mode_no_reasoning_with_tools_overrides_gpt5_heuristic(self):
        """Even a model the name heuristic would otherwise route through
        the Responses API defers to the learned fact."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs(
            "high", config, provider="openAI", model_name="gpt-5",
            api_mode=LLMApiMode.NO_REASONING_WITH_TOOLS.value,
        )
        assert result == {}

    def test_api_mode_reasoning_mandatory_bumps_explicit_none(self):
        """A learned REASONING_MANDATORY fact used to be the only thing that
        bumped an explicit "none" effort to the fallback tier; now the
        unconditional floor above does it for every request regardless of
        api_mode, so this exercises that this api_mode's own dedicated
        handling remains a harmless no-op rather than double-processing."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs(
            "none", config, provider="openRouter", model_name="google/gemini-3.6-flash",
            api_mode=LLMApiMode.REASONING_MANDATORY.value,
        )
        assert result == {"reasoning_effort": REASONING_MANDATORY_FALLBACK_EFFORT}

    def test_api_mode_reasoning_mandatory_leaves_non_none_effort_untouched(self):
        """Only an explicit "none" is affected — a real effort choice
        (low/medium/high/...) is passed through unchanged since the
        provider never rejected those."""
        config = {"isReasoning": True}
        result = _reasoning_effort_kwargs(
            "high", config, provider="openRouter", model_name="google/gemini-3.6-flash",
            api_mode=LLMApiMode.REASONING_MANDATORY.value,
        )
        assert result == {"reasoning_effort": "high"}


# ---------------------------------------------------------------------------
# get_generator_model — a non-OpenAI model hosted on Azure AI Foundry must
# stay on the legacy `reasoning_effort` kwarg, not the Responses API, even
# though this provider branch used to force `provider=LLMProvider.OPENAI`.
# ---------------------------------------------------------------------------
class TestGetGeneratorModelAzureAiPassthroughFix:
    def _base_config(self, model="test-model"):
        return {
            "configuration": {
                "model": model,
                "apiKey": "test-key",
                "endpoint": "https://my-deployment.services.ai.azure.com/models",
            },
            "isDefault": True,
        }

    @patch("langchain_openai.ChatOpenAI")
    def test_azure_ai_non_openai_reasoning_model_keeps_legacy_kwarg(self, mock_cls):
        """A non-OpenAI model (e.g. Llama/Mistral/DeepSeek) hosted on Azure
        AI Foundry has no /v1/responses endpoint — flagging it as
        `isReasoning` must NOT route it through the Responses API just
        because the old code forced `provider=LLMProvider.OPENAI.value`
        for this branch."""
        mock_cls.return_value = MagicMock()
        config = self._base_config("Meta-Llama-3.1-70B-Instruct")
        config["isReasoning"] = True
        get_generator_model(LLMProvider.AZURE_AI.value, config, reasoning_effort="high")
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["reasoning_effort"] == "high"
        assert "reasoning" not in call_kwargs
        assert "use_responses_api" not in call_kwargs

    @patch("langchain_openai.ChatOpenAI")
    def test_azure_ai_actual_gpt5_model_still_uses_responses_api(self, mock_cls):
        """An actual gpt-5.x deployment on Azure AI Foundry must still be
        detected via the gpt-5.x name heuristic even with the real
        `azureAI` provider passed through."""
        mock_cls.return_value = MagicMock()
        config = self._base_config("gpt-5")
        config["isReasoning"] = True
        get_generator_model(LLMProvider.AZURE_AI.value, config, reasoning_effort="high")
        call_kwargs = mock_cls.call_args.kwargs
        assert "reasoning_effort" not in call_kwargs
        assert call_kwargs["reasoning"] == {"effort": "high"}
        assert call_kwargs["use_responses_api"] is True

    @patch("langchain_openai.ChatOpenAI")
    def test_azure_ai_non_openai_reasoning_model_keeps_configured_temperature(self, mock_cls):
        """The same non-OpenAI Azure AI Foundry deployment must also keep
        its configured temperature — `isReasoning=True` alone doesn't imply
        OpenAI's temperature=1 restriction for a provider that isn't
        guaranteed to be OpenAI's own API (see `_default_temperature`)."""
        mock_cls.return_value = MagicMock()
        config = self._base_config("Meta-Llama-3.1-70B-Instruct")
        config["configuration"]["temperature"] = 0.5
        config["isReasoning"] = True
        get_generator_model(LLMProvider.AZURE_AI.value, config, reasoning_effort="high")
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["temperature"] == 0.5


# ---------------------------------------------------------------------------
# get_generator_model — isReasoning temperature=1 override, provider-gated:
# only providers guaranteed to be OpenAI's own API (see
# `_RESPONSES_API_PROVIDERS`) trust `isReasoning` alone; gateways/local
# providers need an actual gpt-5.x name match.
# ---------------------------------------------------------------------------
class TestDefaultTemperatureProviderGating:
    def _base_config(self, model="test-model"):
        return {
            "configuration": {
                "model": model,
                "apiKey": "test-key",
                "endpoint": "https://router.requesty.ai/v1",
                "temperature": 0.6,
            },
            "isDefault": True,
        }

    @patch("langchain_openai.AzureChatOpenAI")
    def test_azure_openai_is_reasoning_flag_alone_still_forces_temperature(self, mock_cls):
        """Direct Azure OpenAI is guaranteed to be OpenAI's own API, so
        `isReasoning=True` alone (e.g. for an o-series model whose name
        doesn't match the gpt-5.x pattern) must still force temperature=1."""
        mock_cls.return_value = MagicMock()
        config = self._base_config("o3-mini")
        config["configuration"]["deploymentName"] = "test-deployment"
        config["isReasoning"] = True
        get_generator_model(LLMProvider.AZURE_OPENAI.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["temperature"] == 1

    @patch("langchain_openai.ChatOpenAI")
    def test_litellm_proxy_is_reasoning_flag_alone_does_not_force_temperature(self, mock_cls):
        """A LiteLLM proxy can forward `isReasoning=True` to any backing
        model (Qwen, DeepSeek, ...), not just OpenAI's — the flag alone
        must not force temperature=1 without a gpt-5.x name match."""
        mock_cls.return_value = MagicMock()
        config = self._base_config("qwen-plus")
        config["isReasoning"] = True
        get_generator_model(LLMProvider.LITELLM_PROXY.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["temperature"] == 0.6

    @patch("langchain_openai.ChatOpenAI")
    def test_openrouter_is_reasoning_flag_alone_does_not_force_temperature(self, mock_cls):
        """Same for OpenRouter: it proxies arbitrary reasoning models
        (Gemini, DeepSeek, ...), so `isReasoning=True` alone must not force
        temperature=1 without a gpt-5.x name match."""
        mock_cls.return_value = MagicMock()
        config = self._base_config("google/gemini-3.6-flash")
        config["isReasoning"] = True
        get_generator_model(LLMProvider.OPENROUTER.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["temperature"] == 0.6

    @patch("langchain_openai.ChatOpenAI")
    def test_openai_compatible_gpt5_name_still_forces_temperature_regardless_of_flag(self, mock_cls):
        """The gpt-5.x name heuristic is independent of the provider gate —
        it still applies even without `isReasoning` set explicitly."""
        mock_cls.return_value = MagicMock()
        config = self._base_config("gpt-5.6-luna")
        get_generator_model(LLMProvider.OPENAI_COMPATIBLE.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["temperature"] == 1


# ---------------------------------------------------------------------------
# get_generator_model — a missing/blank/comma-only 'model' field must raise
# a clear ValueError instead of resolving an unguarded, possibly-empty name.
# ---------------------------------------------------------------------------
class TestGetGeneratorModelNameGuards:
    def test_missing_model_field_raises_value_error(self):
        config = {"configuration": {"apiKey": "test-key"}, "isDefault": True}
        with pytest.raises(ValueError, match="missing a 'model'"):
            get_generator_model(LLMProvider.OPENAI.value, config)

    def test_empty_model_field_raises_value_error(self):
        config = {"configuration": {"model": "", "apiKey": "test-key"}, "isDefault": True}
        with pytest.raises(ValueError, match="missing a 'model'"):
            get_generator_model(LLMProvider.OPENAI.value, config)

    def test_blank_comma_only_model_field_raises_value_error(self):
        config = {"configuration": {"model": " , ,", "apiKey": "test-key"}, "isDefault": True}
        with pytest.raises(ValueError, match="empty 'model' field"):
            get_generator_model(LLMProvider.OPENAI.value, config)

    @patch("langchain_openai.ChatOpenAI")
    def test_default_model_name_none_still_resolves_first_entry(self, mock_cls):
        mock_cls.return_value = MagicMock()
        config = {
            "configuration": {"model": "model-a, model-b", "apiKey": "test-key"},
            "isDefault": True,
        }
        get_generator_model(LLMProvider.OPENAI.value, config)
        assert mock_cls.call_args.kwargs["model"] == "model-a"

    def test_non_default_model_name_not_in_list_raises(self):
        config = {
            "configuration": {"model": "model-a, model-b", "apiKey": "test-key"},
            "isDefault": False,
        }
        with pytest.raises(ValueError, match="not found"):
            get_generator_model(LLMProvider.OPENAI.value, config, model_name="model-c")


# ---------------------------------------------------------------------------
# get_generator_model — end-to-end wiring of the learned-mode snapshot
# (app/utils/llm_api_mode_store.py) into the read path
# ---------------------------------------------------------------------------
class TestGetGeneratorModelApiModeWiring:
    def _base_config(self, model="gpt-5.6-luna"):
        return {
            "configuration": {
                "model": model,
                "apiKey": "test-key",
                "endpoint": "https://router.requesty.ai/v1",
            },
            "isDefault": True,
            "isReasoning": True,
            "modelKey": "abc-123",
        }

    @patch("langchain_openai.ChatOpenAI")
    @patch("app.utils.aimodels.get_llm_api_mode_store")
    def test_learned_no_reasoning_mode_is_applied(self, mock_get_store, mock_cls):
        mock_cls.return_value = MagicMock()
        mock_store = MagicMock()
        mock_store.get.return_value = LLMApiMode.NO_REASONING_WITH_TOOLS.value
        mock_get_store.return_value = mock_store

        config = self._base_config()
        get_generator_model(LLMProvider.OPENAI_COMPATIBLE.value, config, reasoning_effort="high")

        mock_store.get.assert_called_once_with("abc-123", "gpt-5.6-luna")
        call_kwargs = mock_cls.call_args.kwargs
        assert "reasoning_effort" not in call_kwargs
        assert "reasoning" not in call_kwargs
        assert "use_responses_api" not in call_kwargs

    @patch("langchain_openai.ChatOpenAI")
    @patch("app.utils.aimodels.get_llm_api_mode_store")
    def test_no_store_loaded_yet_falls_back_to_heuristic(self, mock_get_store, mock_cls):
        """`get_llm_api_mode_store()` returns `None` until a service has
        called it once with a `ConfigurationService` (see that module's
        docstring) — the read path must degrade to today's heuristic
        rather than raise."""
        mock_cls.return_value = MagicMock()
        mock_get_store.return_value = None

        config = self._base_config()
        get_generator_model(LLMProvider.OPENAI_COMPATIBLE.value, config, reasoning_effort="high")

        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["reasoning"] == {"effort": "high"}
        assert call_kwargs["use_responses_api"] is True
