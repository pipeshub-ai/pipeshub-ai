"""
End-to-end integration test for the per-model `defaultReasoningEffort` fix.

Reproduces the bug from the PR description: the platform-wide hardcoded
`DEFAULT_REASONING_EFFORT` ("high") is sent to every reasoning-capable model
that has no explicit per-request/agent effort, which a provider whose API
only accepts a subset of tiers rejects outright — failing both the health
check and, at chat time, the request itself.

This exercises the real (non-mocked) `get_generator_model` / health-check
code path — only the LangChain provider client's constructor is faked, at
the exact boundary where a real provider would reject an unsupported
`reasoning_effort` value. No Docker services or network access are required.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.api.routes.health import perform_llm_health_check
from app.utils.aimodels import LLMProvider, get_generator_model

pytestmark = pytest.mark.integration


class _RejectsHighReasoningChatModel:
    """Stands in for a real OpenAI-compatible provider (e.g. a hosted Qwen
    model) whose API 400s on `reasoning_effort="high"`/`"xhigh"` but accepts
    lower tiers — the exact shape of provider this feature was built for.

    Everything past construction behaves like a normal working chat model,
    so a health check that gets past the rejected tier succeeds end-to-end.
    """

    REJECTED_EFFORTS = frozenset({"high", "xhigh"})

    def __init__(self, **kwargs: object) -> None:
        effort = kwargs.get("reasoning_effort")
        if effort in self.REJECTED_EFFORTS:
            raise ValueError(
                f"400 Bad Request: reasoning_effort '{effort}' is not supported by "
                "this model. Accepted values: low, medium."
            )
        self.kwargs = kwargs

    def bind_tools(self, *_args: object, **_kwargs: object) -> "_RejectsHighReasoningChatModel":
        return self

    async def ainvoke(self, _payload: object) -> SimpleNamespace:
        return SimpleNamespace(content="ok")

    async def astream(self, _payload: object):
        for chunk in ("hel", "lo"):
            yield SimpleNamespace(content=chunk)


def _config(**overrides: object) -> dict:
    config = {
        "provider": LLMProvider.OPENAI_COMPATIBLE.value,
        "configuration": {
            "model": "hosted-qwen-restricted",
            "endpoint": "https://restricted-provider.example.com/v1",
            "apiKey": "sk-test",
        },
        "isReasoning": True,
    }
    config.update(overrides)
    return config


class TestModelDefaultReasoningEffortEndToEnd:
    def test_get_generator_model_uses_platform_default_and_the_provider_rejects_it(self) -> None:
        """Reproduces the bug: with no model-level default configured, the
        hardcoded platform default ("high") is sent and this provider 400s —
        this is the failure this feature fixes."""
        config = _config()  # no defaultReasoningEffort

        with patch("langchain_openai.ChatOpenAI", _RejectsHighReasoningChatModel):
            with pytest.raises(ValueError, match="reasoning_effort 'high' is not supported"):
                get_generator_model(
                    LLMProvider.OPENAI_COMPATIBLE.value, config, model_name="hosted-qwen-restricted",
                )

    def test_get_generator_model_uses_model_default_and_the_provider_accepts_it(self) -> None:
        """The fix: a model-level defaultReasoningEffort of "low" is picked up
        with no explicit per-request effort, and the provider accepts it."""
        config = _config(defaultReasoningEffort="low")

        with patch("langchain_openai.ChatOpenAI", _RejectsHighReasoningChatModel):
            model = get_generator_model(
                LLMProvider.OPENAI_COMPATIBLE.value, config, model_name="hosted-qwen-restricted",
            )

        assert model.kwargs["reasoning_effort"] == "low"

    async def test_health_check_fails_without_a_model_default(self) -> None:
        """The exact bug report: saving this model without a configured
        default fails its own health check."""
        config = _config()  # no defaultReasoningEffort

        with patch("langchain_openai.ChatOpenAI", _RejectsHighReasoningChatModel):
            response = await perform_llm_health_check(config, MagicMock())

        assert response.status_code == 500
        assert b"reasoning_effort" in response.body

    async def test_health_check_passes_with_a_matching_model_default(self) -> None:
        """Configuring defaultReasoningEffort="low" on the model fixes the
        health check without needing any per-request override."""
        config = _config(defaultReasoningEffort="low")

        with patch("langchain_openai.ChatOpenAI", _RejectsHighReasoningChatModel):
            response = await perform_llm_health_check(config, MagicMock())

        assert response.status_code == 200
        body = response.body.decode()
        assert '"status":"healthy"' in body.replace(" ", "")

    async def test_health_check_explicit_effort_still_overrides_model_default(self) -> None:
        """Per-request/agent effort remains the top of the resolution chain
        even once a model-level default is configured."""
        config = _config(defaultReasoningEffort="high")

        with patch("langchain_openai.ChatOpenAI", _RejectsHighReasoningChatModel):
            with pytest.raises(ValueError, match="reasoning_effort 'high' is not supported"):
                get_generator_model(
                    LLMProvider.OPENAI_COMPATIBLE.value, config,
                    model_name="hosted-qwen-restricted", reasoning_effort="high",
                )
