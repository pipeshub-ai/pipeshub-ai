"""Provider dispatch for the `direct` arm.

The switch is binary (langchain|direct); which provider serves a request comes
from the model the request selected. These guard that mapping, and that an
unsupported or misconfigured provider degrades to LangChain instead of failing
the turn.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.agents.agent_loop.direct_transport import build_direct_transport
from app.agents.agent_loop.factory import (
    DIRECT_TRANSPORT,
    LANGCHAIN_TRANSPORT,
    _transport_provider,
)


class _Stub:
    """Named after the LangChain class it stands in for -- the dispatcher keys
    on the class name, which is the discriminator actually available at the
    call site."""

    def __init__(self, **attrs) -> None:
        for k, v in attrs.items():
            setattr(self, k, v)


def _named(name: str, **attrs):
    return type(name, (_Stub,), {})(**attrs)


def _azure():
    return _named(
        "AzureChatOpenAI",
        azure_endpoint="https://e.openai.azure.com",
        deployment_name="dep",
        openai_api_key=SecretStr("sk-x"),
        openai_api_version="2024-10-01-preview",
        temperature=1.0,
        reasoning={"effort": "high"},
        use_responses_api=True,
        request_timeout=360.0,
    )


def _openai():
    return _named(
        "ChatOpenAI",
        openai_api_key=SecretStr("sk-x"),
        model_name="gpt-5.6-luna",
        temperature=1.0,
        reasoning={"effort": "high"},
        use_responses_api=True,
        request_timeout=360.0,
    )


def _anthropic():
    return _named(
        "ChatAnthropic",
        anthropic_api_key=SecretStr("sk-ant"),
        model="claude-sonnet-5",
        max_tokens=16384,
        temperature=None,
        thinking={"type": "adaptive", "display": "summarized"},
        default_request_timeout=360.0,
    )


def _gemini():
    return _named(
        "ChatGoogleGenerativeAI",
        google_api_key="g-key",
        model="gemini-3-flash-preview",
        temperature=0.2,
        thinking_level="high",
        thinking_budget=None,
        timeout=360.0,
    )


class TestDispatch:
    @pytest.mark.parametrize(
        ("factory", "expected_provider"),
        [
            (_azure, "azure_direct"),
            (_openai, "openai"),
            (_anthropic, "anthropic"),
            (_gemini, "gemini"),
        ],
    )
    def test_each_configured_provider_gets_its_own_transport(
        self, factory, expected_provider
    ) -> None:
        transport = build_direct_transport(factory(), model_name="m", model_key="k")
        assert transport is not None
        assert transport.provider == expected_provider

    def test_unknown_provider_falls_back_rather_than_raising(self) -> None:
        """A model for a provider with no direct transport must degrade to the
        LangChain path, not fail the turn."""
        assert build_direct_transport(_named("ChatBedrock"), model_name="m") is None

    def test_misconfigured_model_falls_back_rather_than_raising(self) -> None:
        """from_langchain_model raises on missing credentials; the dispatcher
        turns that into a fallback so a bad config is slow, never broken."""
        broken = _named("ChatAnthropic", anthropic_api_key=None, model="claude-sonnet-5")
        assert build_direct_transport(broken, model_name="m") is None


class TestSwitch:
    def test_defaults_to_langchain(self, monkeypatch) -> None:
        monkeypatch.delenv("PIPESHUB_AGENT_TRANSPORT", raising=False)
        assert _transport_provider() == LANGCHAIN_TRANSPORT

    def test_direct_selects_the_direct_arm(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_AGENT_TRANSPORT", "direct")
        assert _transport_provider() == DIRECT_TRANSPORT

    def test_azure_direct_is_still_accepted(self, monkeypatch) -> None:
        """Deployments, compose files and load-test scripts set the old value;
        the rename must not strand them."""
        monkeypatch.setenv("PIPESHUB_AGENT_TRANSPORT", "azure_direct")
        assert _transport_provider() == DIRECT_TRANSPORT

    def test_blank_falls_back_to_langchain(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_AGENT_TRANSPORT", "   ")
        assert _transport_provider() == LANGCHAIN_TRANSPORT


class TestCapturedConfigReachesTheTransport:
    """The drift this whole contract exists to prevent: a transport that copies
    credentials but not behaviour silently sends a different request."""

    def test_openai_captures_reasoning_and_endpoint_choice(self) -> None:
        t = build_direct_transport(_openai(), model_name="gpt-5.6-luna")
        assert t._defaults.reasoning == {"effort": "high"}
        assert t._wants_responses() is True

    def test_anthropic_captures_thinking(self) -> None:
        t = build_direct_transport(_anthropic(), model_name="claude-sonnet-5")
        assert t._thinking == {"type": "adaptive", "display": "summarized"}
        assert t._max_tokens == 16384

    def test_gemini_captures_thinking_level(self) -> None:
        t = build_direct_transport(_gemini(), model_name="gemini-3-flash-preview")
        assert t._thinking_level == "high"
        assert t._temperature == 0.2

    def test_unknown_value_falls_back_instead_of_failing_every_turn(
        self, monkeypatch
    ) -> None:
        """The registry holds only langchain and direct, so an unrecognised value
        used to reach TransportRegistry.resolve and raise RegistryError -- a typo
        in a deployment env var took the service down rather than costing it an
        optimisation."""
        monkeypatch.setenv("PIPESHUB_AGENT_TRANSPORT", "typo-transport")
        assert _transport_provider() == LANGCHAIN_TRANSPORT

    def test_case_is_normalised(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_AGENT_TRANSPORT", "DIRECT")
        assert _transport_provider() == DIRECT_TRANSPORT
