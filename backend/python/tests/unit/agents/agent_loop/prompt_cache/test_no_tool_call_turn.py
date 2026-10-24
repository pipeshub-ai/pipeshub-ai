"""The answer-only turn after the turn limit asks the provider for no tool calls.

The tool list still goes out: Anthropic and Bedrock reject tool-call history
without the definitions it refers to, and an unchanged list keeps the cached
tools prefix. Each provider is asked through its own `tool_choice`; one with no
way to say "none" (Bedrock Converse) gets the request it got before.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agent_loop_lib.transport.anthropic import AnthropicTransport
from app.agent_loop_lib.transport.gemini import GeminiTransport
from app.agent_loop_lib.transport.openai import OpenAITransport, RequestDefaults
from app.agents.agent_loop.langchain_transport import LangChainTransport
from tests.unit.agents.adapter.conftest import FakeChatModel

_HISTORY = [
    UserMessage(content="Who owns HarborBank?"),
    AssistantMessage(content="", tool_calls=[ToolCall(id="t1", name="lookup", arguments={"name": "HarborBank"})]),
    ToolMessage(content="HarborBank is owned by Ann.", tool_call_id="t1"),
]
_TOOLS = [ToolSchema(
    name="lookup", description="Looks a name up.",
    input_schema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
)]


def _anthropic(endpoint: Any) -> Any:  # noqa: ANN401
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model="claude-opus-5-5", api_key="test", base_url=endpoint.url, max_retries=0)


def _chat(endpoint: Any) -> Any:  # noqa: ANN401
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model="gpt-6.1-sol", api_key="test", base_url=endpoint.url, max_retries=0)


def _responses(endpoint: Any) -> Any:  # noqa: ANN401
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model="gpt-6.1-sol", api_key="test", base_url=endpoint.url, max_retries=0,
                      use_responses_api=True)


def _azure(endpoint: Any) -> Any:  # noqa: ANN401
    from langchain_openai import AzureChatOpenAI

    return AzureChatOpenAI(azure_endpoint=endpoint.url, azure_deployment="sol", api_version="2025-04-01-preview",
                           api_key="test", max_retries=0)


class TestLangChain:
    @pytest.mark.parametrize(("build", "expected"), [
        (_anthropic, {"type": "none"}),
        (_chat, "none"),
        (_responses, "none"),
        (_azure, "none"),
    ])
    async def test_the_provider_is_asked_for_no_tool_calls(self, endpoint: Any, build: Any, expected: Any) -> None:  # noqa: ANN401
        transport = LangChainTransport(build(endpoint), model_name="m")

        await transport.complete(_HISTORY, tools=_TOOLS, system="You help.")
        await transport.complete(_HISTORY, tools=_TOOLS, system="You help.", tool_choice="none")

        before, final = endpoint.bodies
        assert "tool_choice" not in before
        assert final["tool_choice"] == expected
        assert final["tools"] == before["tools"]

    async def test_a_turn_with_tools_allowed_goes_back_to_the_plain_binding(self, endpoint: Any) -> None:  # noqa: ANN401
        transport = LangChainTransport(_anthropic(endpoint), model_name="m")

        await transport.complete(_HISTORY, tools=_TOOLS, tool_choice="none")
        await transport.complete(_HISTORY, tools=_TOOLS)

        assert "tool_choice" not in endpoint.last

    async def test_streaming_binds_the_choice_too(self) -> None:
        llm = _RecordingAnthropic()

        _ = [event async for event in LangChainTransport(llm, model_name="m").stream(
            _HISTORY, tools=_TOOLS, tool_choice="none",
        )]

        assert llm.bind_kwargs == [{"tool_choice": {"type": "none"}}]

    async def test_bedrock_cannot_say_none_and_gets_the_usual_binding(self) -> None:
        llm = _RecordingBedrock()

        await LangChainTransport(llm, model_name="m").complete(_HISTORY, tools=_TOOLS, tool_choice="none")

        assert llm.bind_kwargs == [{}]


class _Recording(FakeChatModel):
    def __init__(self) -> None:
        super().__init__([AIMessage(content="Ann owns it.")])
        self.bind_kwargs: list[dict[str, Any]] = []

    def bind_tools(self, tools: list[Any], **kwargs: Any) -> FakeChatModel:  # noqa: ANN401
        self.bind_kwargs.append(kwargs)
        return super().bind_tools(tools)

    async def ainvoke(self, messages: list, config: dict | None = None, **_kwargs: Any) -> AIMessage:  # noqa: ANN401
        return await super().ainvoke(messages, config)

    async def astream(self, messages: list, config: dict | None = None, **_kwargs: Any) -> Any:  # noqa: ANN401
        yield AIMessageChunk(content="Ann owns it.")


class _RecordingAnthropic(_Recording):
    pass


_RecordingAnthropic.__name__ = "ChatAnthropic"


class _RecordingBedrock(_Recording):
    pass


_RecordingBedrock.__name__ = "ChatBedrockConverse"


class _Sent(Exception):
    pass


class TestDirectTransports:
    async def test_anthropic(self, endpoint: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN401
        monkeypatch.setenv("ANTHROPIC_BASE_URL", endpoint.url)
        transport = AnthropicTransport(api_key="test", model="claude-opus-5-5", max_retries=0)

        await transport.complete(_HISTORY, tools=_TOOLS)
        await transport.complete(_HISTORY, tools=_TOOLS, tool_choice="none")

        before, final = endpoint.bodies
        assert "tool_choice" not in before
        assert final["tool_choice"] == {"type": "none"}
        assert final["tools"] == before["tools"]

    async def test_anthropic_stream(self) -> None:
        transport = AnthropicTransport(api_key="test", model="claude-opus-5-5")
        transport._client.messages.stream = MagicMock(side_effect=_Sent)

        with pytest.raises(TransportError):
            _ = [event async for event in transport.stream(_HISTORY, tools=_TOOLS, tool_choice="none")]

        assert transport._client.messages.stream.call_args.kwargs["tool_choice"] == {"type": "none"}

    @pytest.mark.parametrize("responses", [False, True])
    async def test_openai(self, endpoint: Any, responses: bool) -> None:  # noqa: ANN401
        transport = OpenAITransport(
            api_key="test", model="gpt-6.1-sol", base_url=endpoint.url, max_retries=0,
            defaults=RequestDefaults(model="gpt-6.1-sol", use_responses_api=responses),
        )

        await transport.complete(_HISTORY, tools=_TOOLS)
        await transport.complete(_HISTORY, tools=_TOOLS, tool_choice="none")

        before, final = endpoint.bodies
        assert "tool_choice" not in before
        assert final["tool_choice"] == "none"
        assert final["tools"] == before["tools"]

    @pytest.mark.parametrize("responses", [False, True])
    async def test_openai_stream(self, responses: bool) -> None:
        transport = OpenAITransport(
            api_key="test", model="gpt-6.1-sol",
            defaults=RequestDefaults(model="gpt-6.1-sol", use_responses_api=responses),
        )
        create = AsyncMock(side_effect=_Sent)
        if responses:
            transport._client.responses.create = create
        else:
            transport._client.chat.completions.create = create

        with pytest.raises(TransportError):
            _ = [event async for event in transport.stream(_HISTORY, tools=_TOOLS, tool_choice="none")]

        assert create.call_args.kwargs["tool_choice"] == "none"

    def test_gemini(self) -> None:
        transport = GeminiTransport(api_key="k", model="gemini-3-flash-preview")

        config = transport._build_config(_TOOLS, None, None, tool_choice="none")

        assert config.tools
        assert config.tool_config.function_calling_config.mode == "NONE"
        assert transport._build_config(_TOOLS, None, None).tool_config is None

    async def test_no_tools_means_no_tool_choice(self, endpoint: Any) -> None:  # noqa: ANN401
        transport = OpenAITransport(api_key="test", model="gpt-6.1-sol", base_url=endpoint.url, max_retries=0)

        await transport.complete([UserMessage(content="hi")], tool_choice="none")

        assert "tool_choice" not in endpoint.last
