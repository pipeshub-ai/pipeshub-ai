"""OpenLLM provider: registry entry, chat model construction, reasoning and health-check routing.

All network calls are mocked; no OpenLLM daemon is required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import app.config.ai_models.providers  # noqa: F401
from app.api.routes.health import _llm_health_check_needs_outbound
from app.config.ai_models.registry import ai_model_registry
from app.config.constants.ai_models import (
    OPENLLM_DEFAULT_ENDPOINT,
    OPENLLM_KEY_REQUIRED_MESSAGE,
    OPENLLM_PLACEHOLDER_API_KEY,
)
from app.utils.aimodels import (
    EmbeddingProvider,
    LLMProvider,
    get_generator_model,
)
from app.utils.openai_hosts import endpoint_path_problem

_LOCAL = "http://host.docker.internal:8787/v1"
_CLOUD = "https://www.openllm.sh/v1"


def _config(
    model: str = "lite", api_key: str | None = None, endpoint: str = _LOCAL, **extra
) -> dict:
    cfg: dict = {
        "configuration": {"model": model, "endpoint": endpoint},
        "isDefault": True,
        **extra,
    }
    if api_key is not None:
        cfg["configuration"]["apiKey"] = api_key
    return cfg


class TestOpenLLMRegistry:
    def test_provider_is_registered_for_chat_only(self) -> None:
        provider = ai_model_registry.get_provider("openLLM")
        assert provider["name"] == "OpenLLM"
        assert provider["capabilities"] == ["text_generation"]
        assert provider["iconPath"] == "/icons/ai-models/openllm.svg"

    def test_endpoint_is_prefilled_with_the_docker_host_daemon(self) -> None:
        fields = {
            f["name"]: f
            for f in ai_model_registry.get_provider("openLLM")["fields"][
                "text_generation"
            ]
        }
        assert fields["endpoint"]["defaultValue"] == OPENLLM_DEFAULT_ENDPOINT == _LOCAL
        assert fields["endpoint"]["required"] is True
        assert "https://docs.openllm.sh" in fields["endpoint"]["description"]

    def test_api_key_is_optional_and_secret(self) -> None:
        fields = {
            f["name"]: f
            for f in ai_model_registry.get_provider("openLLM")["fields"][
                "text_generation"
            ]
        }
        assert fields["apiKey"]["required"] is False
        assert fields["apiKey"]["isSecret"] is True

    def test_reasoning_is_off_by_default(self) -> None:
        fields = {
            f["name"]: f
            for f in ai_model_registry.get_provider("openLLM")["fields"][
                "text_generation"
            ]
        }
        assert fields["isReasoning"]["defaultValue"] is False

    def test_placeholder_key_is_not_a_cloud_key(self) -> None:
        assert OPENLLM_PLACEHOLDER_API_KEY
        assert not OPENLLM_PLACEHOLDER_API_KEY.startswith("sk-llm-")


class TestOpenLLMEnums:
    def test_llm_provider_enum_value(self) -> None:
        assert LLMProvider.OPENLLM.value == "openLLM"

    def test_no_embedding_provider(self) -> None:
        assert "openLLM" not in [p.value for p in EmbeddingProvider]


class TestOpenLLMLLM:
    @patch("langchain_openai.ChatOpenAI")
    def test_dispatch_uses_the_configured_endpoint(self, mock_cls) -> None:
        mock_cls.return_value = MagicMock()
        get_generator_model(
            LLMProvider.OPENLLM.value, _config("claude_code/claude-sonnet-5")
        )
        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["base_url"] == _LOCAL
        assert kwargs["model"] == "claude_code/claude-sonnet-5"
        assert kwargs["stream_usage"] is True

    @patch("langchain_openai.ChatOpenAI")
    def test_cloud_key_is_sent_as_the_bearer(self, mock_cls) -> None:
        mock_cls.return_value = MagicMock()
        get_generator_model(
            LLMProvider.OPENLLM.value, _config(api_key="sk-llm-abc", endpoint=_CLOUD)
        )
        assert mock_cls.call_args.kwargs["api_key"] == "sk-llm-abc"
        assert mock_cls.call_args.kwargs["base_url"] == _CLOUD

    @pytest.mark.parametrize("key", [None, "", "   "])
    @patch("langchain_openai.ChatOpenAI")
    def test_empty_key_sends_the_placeholder(self, mock_cls, key) -> None:
        mock_cls.return_value = MagicMock()
        get_generator_model(LLMProvider.OPENLLM.value, _config(api_key=key))
        assert mock_cls.call_args.kwargs["api_key"] == OPENLLM_PLACEHOLDER_API_KEY

    @patch("langchain_openai.ChatOpenAI")
    def test_no_reasoning_effort_unless_the_model_is_flagged_reasoning(
        self, mock_cls
    ) -> None:
        mock_cls.return_value = MagicMock()
        get_generator_model(
            LLMProvider.OPENLLM.value, _config("claude_code/claude-sonnet-5")
        )
        kwargs = mock_cls.call_args.kwargs
        assert "reasoning_effort" not in kwargs
        assert "use_responses_api" not in kwargs

    @patch("langchain_openai.ChatOpenAI")
    def test_flagged_reasoning_model_sends_an_effort(self, mock_cls) -> None:
        mock_cls.return_value = MagicMock()
        get_generator_model(
            LLMProvider.OPENLLM.value,
            _config("claude_code/claude-sonnet-5", isReasoning=True),
            reasoning_effort="low",
        )
        assert mock_cls.call_args.kwargs["reasoning_effort"] == "low"

    @patch("langchain_openai.ChatOpenAI")
    def test_the_models_own_default_effort_replaces_high(self, mock_cls) -> None:
        mock_cls.return_value = MagicMock()
        cfg = _config("claude_code/claude-sonnet-5", isReasoning=True)
        cfg["configuration"]["defaultReasoningEffort"] = "medium"
        get_generator_model(LLMProvider.OPENLLM.value, cfg)
        assert mock_cls.call_args.kwargs["reasoning_effort"] == "medium"

    @patch("langchain_openai.ChatOpenAI")
    def test_temperature_is_omitted_unless_the_admin_set_one(self, mock_cls) -> None:
        mock_cls.return_value = MagicMock()
        get_generator_model(
            LLMProvider.OPENLLM.value, _config("claude_code/claude-haiku-4-5")
        )
        assert mock_cls.call_args.kwargs["temperature"] is None

    @patch("langchain_openai.ChatOpenAI")
    def test_a_configured_temperature_is_sent(self, mock_cls) -> None:
        mock_cls.return_value = MagicMock()
        cfg = _config("lite")
        cfg["configuration"]["temperature"] = 0.7
        get_generator_model(LLMProvider.OPENLLM.value, cfg)
        assert mock_cls.call_args.kwargs["temperature"] == pytest.approx(0.7)

    def test_the_built_client_leaves_temperature_out_of_the_request(self) -> None:
        model = get_generator_model(
            LLMProvider.OPENLLM.value, _config("claude_code/claude-haiku-4-5")
        )
        assert "temperature" not in model._default_params


class TestOpenLLMEndpointHandling:
    def test_pasted_api_path_is_flagged(self) -> None:
        problem = endpoint_path_problem(
            "openLLM", "http://127.0.0.1:8787/v1/chat/completions"
        )
        assert problem is not None
        assert "'http://127.0.0.1:8787/v1'" in problem

    def test_base_url_is_accepted(self) -> None:
        assert endpoint_path_problem("openLLM", _LOCAL) is None


class TestOpenLLMHealthCheckRouting:
    @pytest.mark.parametrize(
        "endpoint",
        [_LOCAL, "http://127.0.0.1:8787/v1", "http://localhost:8787/v1"],
    )
    def test_local_daemon_needs_no_outbound_probe(self, endpoint) -> None:
        assert (
            _llm_health_check_needs_outbound("openLLM", {"endpoint": endpoint}) is False
        )

    def test_cloud_endpoint_needs_the_outbound_probe(self) -> None:
        assert _llm_health_check_needs_outbound("openLLM", {"endpoint": _CLOUD}) is True


class TestOpenLLMKeyMessage:
    @pytest.mark.parametrize(
        ("endpoint", "key", "needs_key_message"),
        [
            (_CLOUD, None, True),
            (_CLOUD, "  ", True),
            (_CLOUD, "sk-llm-bad", False),
            (_LOCAL, None, False),
        ],
    )
    def test_rejected_hosted_endpoint_without_a_key_asks_for_one(
        self, endpoint, key, needs_key_message
    ) -> None:
        from app.api.routes.health import _rejected_settings_message

        cfg = _config("lite", api_key=key, endpoint=endpoint)
        cfg["provider"] = "openLLM"
        message = _rejected_settings_message("auth_error", "chat model", cfg, "lite")
        assert (OPENLLM_KEY_REQUIRED_MESSAGE in message) is needs_key_message
        assert "https://www.openllm.sh" in OPENLLM_KEY_REQUIRED_MESSAGE
