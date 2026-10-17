"""Prompt-cache parameters on the direct OpenAI and Azure transports, and the
policy ``build_direct_transport`` hands every direct transport."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import openai
import pytest

from app.agent_loop_lib.core.messages import UserMessage
from app.agent_loop_lib.core.streaming import StreamCompleteEvent
from app.agent_loop_lib.transport.openai import OpenAITransport, RequestDefaults
from app.agent_loop_lib.transport.prompt_cache import (
    REJECTED_CACHE_PARAMS,
    PromptCacheSettings,
)
from app.agents.agent_loop.direct_transport import build_direct_transport

KWARGS = {"prompt_cache_key": "pipeshub:abc:1", "prompt_cache_retention": "24h"}


@pytest.fixture(autouse=True)
def _clean_rejections() -> Any:  # noqa: ANN401
    REJECTED_CACHE_PARAMS.clear()
    yield
    REJECTED_CACHE_PARAMS.clear()


def _chat(cached: int = 0, written: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=5, prompt_tokens_details=SimpleNamespace(
            cached_tokens=cached, cache_write_tokens=written,
        )),
    )


def _chunks() -> Any:  # noqa: ANN401
    async def gen() -> Any:  # noqa: ANN401
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="ok", tool_calls=None),
                                                       finish_reason="stop")], usage=None)
    return gen()


def _rejected(param: str) -> openai.BadRequestError:
    response = httpx.Response(400, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    return openai.BadRequestError(f"Error code: 400 - Unrecognized request argument supplied: {param}",
                                  response=response, body=None)


def _anthropic_rejection() -> Exception:
    import anthropic

    response = httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    return anthropic.BadRequestError("Error code: 400 - cache_control: Extra inputs are not permitted",
                                     response=response, body=None)


def _transport(**kwargs: Any) -> OpenAITransport:  # noqa: ANN401
    return OpenAITransport(api_key="sk-test", model="gpt-5.4", **kwargs)


class TestRequests:
    async def test_chat_complete(self) -> None:
        transport = _transport(cache_kwargs=KWARGS)
        transport._client.chat.completions.create = AsyncMock(return_value=_chat())
        await transport.complete([UserMessage(content="hi")])
        assert {k: transport._client.chat.completions.create.call_args.kwargs[k] for k in KWARGS} == KWARGS

    async def test_chat_stream(self) -> None:
        transport = _transport(cache_kwargs=KWARGS)
        transport._client.chat.completions.create = AsyncMock(return_value=_chunks())
        _ = [event async for event in transport.stream([UserMessage(content="hi")])]
        assert transport._client.chat.completions.create.call_args.kwargs["prompt_cache_key"] == KWARGS["prompt_cache_key"]

    def test_responses_kwargs(self) -> None:
        transport = _transport(cache_kwargs=KWARGS, defaults=RequestDefaults(use_responses_api=True, model="gpt-5.4"))
        kwargs = transport._build_responses_kwargs([UserMessage(content="hi")], None, "sys", None, stream=False)
        assert {k: kwargs[k] for k in KWARGS} == KWARGS

    async def test_none_by_default(self) -> None:
        transport = _transport()
        transport._client.chat.completions.create = AsyncMock(return_value=_chat())
        await transport.complete([UserMessage(content="hi")])
        assert "prompt_cache_key" not in transport._client.chat.completions.create.call_args.kwargs

    def test_cache_writes_are_read(self) -> None:
        usage = _transport()._usage_from(_chat(cached=60, written=20))
        assert (usage.input_tokens, usage.cache_read_tokens, usage.cache_write_tokens) == (100, 60, 20)


class TestRejection:
    async def test_complete_retries_once_without_them(self) -> None:
        rejected = MagicMock()
        transport = _transport(cache_kwargs=KWARGS, on_cache_rejected=rejected)
        transport._client.chat.completions.create = AsyncMock(side_effect=[
            _rejected("prompt_cache_retention"), _chat(), _chat(),
        ])
        await transport.complete([UserMessage(content="hi")])
        await transport.complete([UserMessage(content="hi")])
        sent = [call.kwargs for call in transport._client.chat.completions.create.call_args_list]
        assert "prompt_cache_retention" in sent[0]
        assert all("prompt_cache_key" not in kwargs for kwargs in sent[1:])
        rejected.assert_called_once()

    async def test_stream_retries_once_without_them(self) -> None:
        transport = _transport(cache_kwargs=KWARGS)
        transport._client.chat.completions.create = AsyncMock(side_effect=[_rejected("prompt_cache_key"), _chunks()])
        events = [event async for event in transport.stream([UserMessage(content="hi")])]
        assert isinstance(events[-1], StreamCompleteEvent)
        assert "prompt_cache_key" not in transport._client.chat.completions.create.call_args.kwargs

    async def test_an_unrelated_400_is_raised(self) -> None:
        transport = _transport(cache_kwargs=KWARGS)
        transport._client.chat.completions.create = AsyncMock(side_effect=[_rejected("frobnicate")])
        with pytest.raises(Exception, match="frobnicate"):
            await transport.complete([UserMessage(content="hi")])
        assert transport._cache_kwargs == KWARGS


def _openai_llm(model: str = "gpt-5.4", base_url: str | None = None) -> Any:  # noqa: ANN401
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=model, api_key="sk-test", **({"base_url": base_url} if base_url else {}))


def _azure_llm() -> Any:  # noqa: ANN401
    from langchain_openai import AzureChatOpenAI

    return AzureChatOpenAI(azure_endpoint="https://acme.openai.azure.com", azure_deployment="prod-54",
                           api_version="2025-04-01-preview", api_key="sk-test")


def _anthropic_llm() -> Any:  # noqa: ANN401
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model="claude-opus-5-5", api_key="sk-test")


ON = PromptCacheSettings()


class TestBuildDirectTransport:
    def test_openai(self) -> None:
        transport = build_direct_transport(_openai_llm(), "gpt-5.4", cache_settings=ON, cache_routing_key="k")
        assert transport._cache_kwargs == {"prompt_cache_key": "k", "prompt_cache_retention": "24h"}

    def test_azure_uses_the_configured_model(self) -> None:
        transport = build_direct_transport(_azure_llm(), "gpt-5.4", cache_settings=ON, cache_routing_key="k")
        assert transport._cache_kwargs == {"prompt_cache_key": "k", "prompt_cache_retention": "24h"}

    def test_compatible_host_gets_nothing(self) -> None:
        transport = build_direct_transport(_openai_llm(base_url="https://openrouter.ai/api/v1"), "gpt-5.4",
                                           cache_settings=ON, cache_routing_key="k")
        assert transport._cache_kwargs == {}

    def test_anthropic_ttl(self) -> None:
        transport = build_direct_transport(_anthropic_llm(), "claude-opus-5-5",
                                           cache_settings=PromptCacheSettings(anthropic_ttl="1h"), cache_routing_key="k")
        assert transport._breakpoint == {"type": "ephemeral", "ttl": "1h"}

    @pytest.mark.parametrize("build", [_openai_llm, _azure_llm, _anthropic_llm])
    def test_disabled(self, build: Any) -> None:  # noqa: ANN401
        transport = build_direct_transport(build(), "gpt-5.4", cache_settings=PromptCacheSettings(enabled=False),
                                           cache_routing_key="k")
        assert getattr(transport, "_cache_kwargs", {}) == {}
        assert getattr(transport, "_breakpoint", None) is None

    def test_a_rejection_is_remembered_for_the_next_transport(self) -> None:
        first = build_direct_transport(_openai_llm(), "gpt-5.4", cache_settings=ON, cache_routing_key="k")
        first._drop_cache_params(_rejected("prompt_cache_retention"))
        second = build_direct_transport(_openai_llm(), "gpt-5.4", cache_settings=ON, cache_routing_key="k")
        assert second._cache_kwargs == {}

    def test_an_anthropic_rejection_is_remembered_too(self) -> None:
        first = build_direct_transport(_anthropic_llm(), "claude-opus-5-5", cache_settings=ON, cache_routing_key="k")
        first._drop_cache_params(_anthropic_rejection())
        second = build_direct_transport(_anthropic_llm(), "claude-opus-5-5", cache_settings=ON, cache_routing_key="k")
        assert first._breakpoint is None and second._breakpoint is None

    def test_without_settings_the_transport_defaults_apply(self) -> None:
        assert build_direct_transport(_openai_llm(), "gpt-5.4")._cache_kwargs == {}
        assert build_direct_transport(_anthropic_llm(), "claude-opus-5-5")._breakpoint == {"type": "ephemeral"}
