"""Prompt-cache parameters on the direct OpenAI and Azure transports, and the
policy ``build_direct_transport`` hands every direct transport."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import openai
import pytest

from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.core.streaming import StreamCompleteEvent
from app.agent_loop_lib.transport.openai import OpenAITransport, RequestDefaults
from app.agent_loop_lib.transport.prompt_cache import (
    REJECTED_CACHE_PARAMS,
    CacheScope,
    OpenAICachePlan,
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
        transport = _transport(cache_plan=OpenAICachePlan(KWARGS))
        transport._client.chat.completions.create = AsyncMock(return_value=_chat())
        await transport.complete([UserMessage(content="hi")])
        assert {k: transport._client.chat.completions.create.call_args.kwargs[k] for k in KWARGS} == KWARGS

    async def test_chat_stream(self) -> None:
        transport = _transport(cache_plan=OpenAICachePlan(KWARGS))
        transport._client.chat.completions.create = AsyncMock(return_value=_chunks())
        _ = [event async for event in transport.stream([UserMessage(content="hi")])]
        assert transport._client.chat.completions.create.call_args.kwargs["prompt_cache_key"] == KWARGS["prompt_cache_key"]

    def test_responses_kwargs(self) -> None:
        transport = _transport(cache_plan=OpenAICachePlan(KWARGS), defaults=RequestDefaults(use_responses_api=True, model="gpt-5.4"))
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


BLOCKS = ["Stable instructions. " * 20, "Current time: 10:00."]
BREAKPOINT = {"mode": "explicit"}


class TestBreakpoint:
    """GPT-5.6 and later: the stable block is a system message of its own,
    ending in an explicit breakpoint; the volatile rest follows it."""

    async def test_chat(self) -> None:
        transport = _transport(cache_plan=OpenAICachePlan({"prompt_cache_key": "k"}, breakpoint=True))
        transport._client.chat.completions.create = AsyncMock(return_value=_chat())
        await transport.complete([UserMessage(content="hi")], system="\n\n".join(BLOCKS), system_blocks=BLOCKS)
        messages = transport._client.chat.completions.create.call_args.kwargs["messages"]
        assert messages[:2] == [
            {"role": "system", "content": [{"type": "text", "text": BLOCKS[0], "prompt_cache_breakpoint": BREAKPOINT}]},
            {"role": "system", "content": BLOCKS[1]},
        ]
        assert messages[2]["role"] == "user"

    def test_responses(self) -> None:
        transport = _transport(cache_plan=OpenAICachePlan(breakpoint=True), defaults=RequestDefaults(use_responses_api=True, model="gpt-6.1-sol"))
        kwargs = transport._build_responses_kwargs([UserMessage(content="hi")], None, "\n\n".join(BLOCKS), BLOCKS,
                                                   stream=False)
        assert kwargs["input"][:2] == [
            {"type": "message", "role": "system",
             "content": [{"type": "input_text", "text": BLOCKS[0], "prompt_cache_breakpoint": BREAKPOINT}]},
            {"type": "message", "role": "system", "content": BLOCKS[1]},
        ]
        assert kwargs["input"][2]["role"] == "user"

    async def test_off_the_prompt_is_one_message_as_before(self) -> None:
        transport = _transport(cache_plan=OpenAICachePlan({"prompt_cache_key": "k"}))
        transport._client.chat.completions.create = AsyncMock(return_value=_chat())
        await transport.complete([UserMessage(content="hi")], system="\n\n".join(BLOCKS), system_blocks=BLOCKS)
        messages = transport._client.chat.completions.create.call_args.kwargs["messages"]
        assert messages[0] == {"role": "system", "content": "\n\n".join(BLOCKS)}
        assert messages[1]["role"] == "user"

    async def test_a_refused_breakpoint_keeps_the_key(self) -> None:
        refused = MagicMock()
        transport = _transport(cache_plan=OpenAICachePlan({"prompt_cache_key": "k"}, breakpoint=True), on_cache_rejected=refused)
        transport._client.chat.completions.create = AsyncMock(side_effect=[
            _rejected("prompt_cache_breakpoint"), _chat(),
        ])
        await transport.complete([UserMessage(content="hi")], system_blocks=BLOCKS)
        retry = transport._client.chat.completions.create.call_args.kwargs
        assert retry["prompt_cache_key"] == "k"
        assert retry["messages"][0] == {"role": "system", "content": "\n\n".join(BLOCKS)}
        refused.assert_called_once_with(frozenset({"prompt_cache_breakpoint"}))

    async def test_a_structured_call_carries_no_markers(self) -> None:
        """As on the LangChain arm, a structured call sends no cache kwargs and
        has no refusal retry, so a breakpoint there could only fail it."""
        plan = OpenAICachePlan({"prompt_cache_key": "k"}, breakpoint=True, last_message=True)
        transport = _transport(cache_plan=plan)
        reply = _chat()
        reply.choices[0].message.tool_calls = [
            SimpleNamespace(function=SimpleNamespace(name="structured_output", arguments='{"ok": true}')),
        ]
        transport._client.chat.completions.create = AsyncMock(return_value=reply)
        result = await transport.complete_structured([UserMessage(content="hi")], {"type": "object"}, system=BLOCKS[0])
        assert result.data == {"ok": True}
        sent = transport._client.chat.completions.create.call_args.kwargs
        assert sent["messages"] == [{"role": "system", "content": BLOCKS[0]}, {"role": "user", "content": "hi"}]
        assert "prompt_cache_key" not in sent


class TestLastMessage:
    """Through OpenRouter, Claude caches the history only up to a marked block."""

    async def test_chat_marks_the_last_tool_result(self) -> None:
        transport = _transport(cache_plan=OpenAICachePlan({"prompt_cache_key": "k"}, breakpoint=True, last_message=True))
        transport._client.chat.completions.create = AsyncMock(return_value=_chat())
        history = [
            UserMessage(content="hi"),
            AssistantMessage(tool_calls=[ToolCall(id="t1", name="lookup", arguments={})]),
            ToolMessage(content="found it", tool_call_id="t1"),
        ]
        await transport.complete(history, system_blocks=BLOCKS)
        messages = transport._client.chat.completions.create.call_args.kwargs["messages"]
        assert messages[-1]["content"] == [{"type": "text", "text": "found it", "prompt_cache_breakpoint": BREAKPOINT}]
        assert messages[2] == {"role": "user", "content": "hi"}

    def test_responses_marks_the_last_function_output(self) -> None:
        transport = _transport(cache_plan=OpenAICachePlan(last_message=True),
                               defaults=RequestDefaults(use_responses_api=True, model="gpt-6.1-sol"))
        history = [
            UserMessage(content="hi"),
            AssistantMessage(tool_calls=[ToolCall(id="t1", name="lookup", arguments={})]),
            ToolMessage(content="found it", tool_call_id="t1"),
        ]
        kwargs = transport._build_responses_kwargs(history, None, None, BLOCKS, stream=False)
        assert kwargs["input"][-1]["output"] == [
            {"type": "input_text", "text": "found it", "prompt_cache_breakpoint": BREAKPOINT},
        ]

    async def test_an_assistant_last_is_left_alone(self) -> None:
        transport = _transport(cache_plan=OpenAICachePlan(last_message=True))
        transport._client.chat.completions.create = AsyncMock(return_value=_chat())
        await transport.complete([UserMessage(content="hi"), AssistantMessage(content="draft")])
        messages = transport._client.chat.completions.create.call_args.kwargs["messages"]
        assert messages[-1]["content"] == "draft"


class TestRejection:
    async def test_complete_retries_once_without_the_refused_one(self) -> None:
        rejected = MagicMock()
        transport = _transport(cache_plan=OpenAICachePlan(KWARGS), on_cache_rejected=rejected)
        transport._client.chat.completions.create = AsyncMock(side_effect=[
            _rejected("prompt_cache_retention"), _chat(), _chat(),
        ])
        await transport.complete([UserMessage(content="hi")])
        await transport.complete([UserMessage(content="hi")])
        sent = [call.kwargs for call in transport._client.chat.completions.create.call_args_list]
        assert "prompt_cache_retention" in sent[0]
        assert all("prompt_cache_retention" not in kwargs for kwargs in sent[1:])
        assert all(kwargs["prompt_cache_key"] == KWARGS["prompt_cache_key"] for kwargs in sent)
        rejected.assert_called_once_with(frozenset({"prompt_cache_retention"}))

    async def test_stream_retries_once_without_the_refused_one(self) -> None:
        transport = _transport(cache_plan=OpenAICachePlan(KWARGS))
        transport._client.chat.completions.create = AsyncMock(side_effect=[_rejected("prompt_cache_key"), _chunks()])
        events = [event async for event in transport.stream([UserMessage(content="hi")])]
        assert isinstance(events[-1], StreamCompleteEvent)
        retry = transport._client.chat.completions.create.call_args.kwargs
        assert "prompt_cache_key" not in retry
        assert retry["prompt_cache_retention"] == "24h"

    async def test_complete_resends_until_nothing_is_refused(self) -> None:
        rejected = MagicMock()
        transport = _transport(cache_plan=OpenAICachePlan(KWARGS), on_cache_rejected=rejected)
        transport._client.chat.completions.create = AsyncMock(side_effect=[
            _rejected("prompt_cache_retention"), _rejected("prompt_cache_key"), _chat(),
        ])
        await transport.complete([UserMessage(content="hi")])
        sent = [call.kwargs for call in transport._client.chat.completions.create.call_args_list]
        assert [set(KWARGS) & set(kwargs) for kwargs in sent] == [set(KWARGS), {"prompt_cache_key"}, set()]
        assert [call.args for call in rejected.call_args_list] == [
            (frozenset({"prompt_cache_retention"}),), (frozenset({"prompt_cache_key"}),),
        ]

    async def test_stream_resends_until_nothing_is_refused(self) -> None:
        transport = _transport(cache_plan=OpenAICachePlan(KWARGS))
        transport._client.chat.completions.create = AsyncMock(side_effect=[
            _rejected("prompt_cache_retention"), _rejected("prompt_cache_key"), _chunks(),
        ])
        events = [event async for event in transport.stream([UserMessage(content="hi")])]
        assert isinstance(events[-1], StreamCompleteEvent)
        assert transport._client.chat.completions.create.call_count == 3
        assert transport._cache_kwargs == {}

    async def test_a_parameter_refused_again_is_raised(self) -> None:
        transport = _transport(cache_plan=OpenAICachePlan(KWARGS))
        transport._client.chat.completions.create = AsyncMock(side_effect=[
            _rejected("prompt_cache_retention"), _rejected("prompt_cache_retention"),
        ])
        with pytest.raises(Exception, match="prompt_cache_retention"):
            await transport.complete([UserMessage(content="hi")])
        assert transport._client.chat.completions.create.call_count == 2

    async def test_an_unrelated_400_is_raised(self) -> None:
        transport = _transport(cache_plan=OpenAICachePlan(KWARGS))
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
SCOPE = CacheScope("org-1", "conv-1")
KEY = SCOPE.key(ON.routing_shards)


class TestBuildDirectTransport:
    def test_openai(self) -> None:
        transport = build_direct_transport(_openai_llm(), "gpt-5.4", cache_settings=ON, cache_scope=SCOPE)
        assert transport._cache_kwargs == {"prompt_cache_key": KEY, "prompt_cache_retention": "24h"}
        assert transport._cache_plan.breakpoint is False

    def test_azure_uses_the_configured_model(self) -> None:
        transport = build_direct_transport(_azure_llm(), "gpt-5.4", cache_settings=ON, cache_scope=SCOPE)
        assert transport._cache_kwargs == {"prompt_cache_key": KEY, "prompt_cache_retention": "24h"}

    def test_gpt_6_gets_the_breakpoint(self) -> None:
        azure = build_direct_transport(_azure_llm(), "gpt-6.1-sol", cache_settings=ON, cache_scope=SCOPE)
        assert (azure._cache_kwargs, azure._cache_plan.breakpoint) == ({"prompt_cache_key": KEY}, True)
        openai_ = build_direct_transport(_openai_llm("gpt-6.1-sol"), "gpt-6.1-sol", cache_settings=ON, cache_scope=SCOPE)
        assert (openai_._cache_kwargs, openai_._cache_plan.breakpoint) == ({"prompt_cache_key": SCOPE.key(1)}, True)

    def test_compatible_host_gets_nothing(self) -> None:
        transport = build_direct_transport(_openai_llm(base_url="https://api.together.xyz/v1"), "gpt-6.1-sol",
                                           cache_settings=ON, cache_scope=SCOPE)
        assert (transport._cache_kwargs, transport._cache_plan.breakpoint) == ({}, False)

    def test_openrouter_claude_marks_the_stable_block_and_the_last_message(self) -> None:
        llm = _openai_llm("anthropic/claude-sonnet-5.5", base_url="https://openrouter.ai/api/v1")
        transport = build_direct_transport(llm, "anthropic/claude-sonnet-5.5", cache_settings=ON, cache_scope=SCOPE)
        plan = transport._cache_plan
        assert (plan.kwargs, plan.breakpoint, plan.last_message) == ({"prompt_cache_key": KEY}, True, True)

    def test_anthropic_ttl(self) -> None:
        transport = build_direct_transport(_anthropic_llm(), "claude-opus-5-5",
                                           cache_settings=PromptCacheSettings(anthropic_ttl="1h"), cache_scope=SCOPE)
        assert transport._breakpoint == {"type": "ephemeral", "ttl": "1h"}

    @pytest.mark.parametrize("build", [_openai_llm, _azure_llm, _anthropic_llm])
    def test_disabled(self, build: Any) -> None:  # noqa: ANN401
        transport = build_direct_transport(build(), "gpt-5.4", cache_settings=PromptCacheSettings(enabled=False),
                                           cache_scope=SCOPE)
        assert getattr(transport, "_cache_kwargs", {}) == {}
        assert getattr(transport, "_breakpoint", None) is None

    def test_a_rejection_is_remembered_for_the_next_transport(self) -> None:
        first = build_direct_transport(_openai_llm(), "gpt-5.4", cache_settings=ON, cache_scope=SCOPE)
        first._drop_cache_params(_rejected("prompt_cache_retention"))
        second = build_direct_transport(_openai_llm(), "gpt-5.4", cache_settings=ON, cache_scope=SCOPE)
        assert second._cache_kwargs == {"prompt_cache_key": KEY}

    def test_an_anthropic_rejection_is_remembered_too(self) -> None:
        first = build_direct_transport(_anthropic_llm(), "claude-opus-5-5", cache_settings=ON, cache_scope=SCOPE)
        first._drop_cache_params(_anthropic_rejection())
        second = build_direct_transport(_anthropic_llm(), "claude-opus-5-5", cache_settings=ON, cache_scope=SCOPE)
        assert first._breakpoint is None and second._breakpoint is None

    def test_a_refusal_through_langchain_stops_the_direct_anthropic_markers(self) -> None:
        from app.agents.agent_loop.prompt_cache import forget_cache_params

        forget_cache_params(_anthropic_llm(), "claude-opus-5-5", params={"cache_control"})
        transport = build_direct_transport(_anthropic_llm(), "claude-opus-5-5", cache_settings=ON, cache_scope=SCOPE)
        assert transport._breakpoint is None

    def test_without_settings_the_transport_defaults_apply(self) -> None:
        assert build_direct_transport(_openai_llm(), "gpt-5.4")._cache_kwargs == {}
        assert build_direct_transport(_anthropic_llm(), "claude-opus-5-5")._breakpoint == {"type": "ephemeral"}
