"""``LangChainTransport`` with a cache strategy: what every call path sends,
the fallback when a provider rejects a cache parameter, usage, and a request
prefix that stays the same from one turn to the next."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, SystemMessage

from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ImagePart,
    ImageSource,
    TextPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.core.streaming import StreamCompleteEvent
from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agent_loop_lib.transport.prompt_cache import REJECTED_CACHE_PARAMS
from app.agents.agent_loop.langchain_transport import LangChainTransport
from app.agents.agent_loop.prompt_cache import (
    AnthropicCacheStrategy,
    OpenAICacheStrategy,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

STABLE = "You are PipesHub. " * 40
VOLATILE = "Current time: 2026-10-11T00:00:00Z"
TOOLS = [
    ToolSchema(name="search", description="Search.", input_schema={
        "type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"],
    }),
    ToolSchema(name="fetch", description="Fetch.", input_schema={"type": "object", "properties": {}}),
]
KWARGS = {"prompt_cache_key": "pipeshub:abc:1", "prompt_cache_retention": "24h"}
FIVE_MIN = {"type": "ephemeral"}


@pytest.fixture(autouse=True)
def _clean_rejections() -> Iterator[None]:
    REJECTED_CACHE_PARAMS.clear()
    yield
    REJECTED_CACHE_PARAMS.clear()


def _bad_request(message: str) -> openai.BadRequestError:
    response = httpx.Response(400, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    return openai.BadRequestError(f"Error code: 400 - {message}", response=response, body=None)


class _RecordingModel:
    """Records what each call carried; raises the queued errors first."""

    def __init__(self, errors: list[Exception] | None = None, chunks_before_error: int = 0) -> None:
        self.calls: list[dict[str, Any]] = []
        self.bound: list[Any] = []
        self._errors = list(errors or [])
        self._chunks_before_error = chunks_before_error

    def bind_tools(self, tools: list[Any]) -> _RecordingModel:
        self.bound.append(tools)
        return self

    async def ainvoke(self, messages: list, config: Any = None, **kwargs: Any) -> AIMessage:  # noqa: ANN401
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if self._errors:
            raise self._errors.pop(0)
        return AIMessage(content="ok")

    async def astream(self, messages: list, config: Any = None, **kwargs: Any) -> AsyncIterator[AIMessageChunk]:  # noqa: ANN401
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if self._errors:
            for _ in range(self._chunks_before_error):
                yield AIMessageChunk(content="partial ")
            raise self._errors.pop(0)
        yield AIMessageChunk(content="ok")


def _messages() -> list[Any]:
    return [UserMessage(content="Who owns HarborBank?")]


def _transport(model: Any, strategy: Any = None, **kwargs: Any) -> LangChainTransport:  # noqa: ANN401
    return LangChainTransport(model, model_name="gpt-5.4", cache_strategy=strategy or OpenAICacheStrategy(KWARGS),
                              **kwargs)


async def _stream(transport: LangChainTransport, **kwargs: Any) -> list[Any]:  # noqa: ANN401
    return [event async for event in transport.stream(_messages(), tools=TOOLS, **kwargs)]


class TestEveryCallCarriesTheStrategy:
    async def test_complete(self) -> None:
        model = _RecordingModel()
        await _transport(model).complete(_messages(), tools=TOOLS, system_blocks=[STABLE, VOLATILE])
        assert model.calls[0]["kwargs"] == KWARGS

    async def test_stream(self) -> None:
        model = _RecordingModel()
        await _stream(_transport(model), system_blocks=[STABLE, VOLATILE])
        assert model.calls[0]["kwargs"] == KWARGS

    async def test_system_blocks_reach_the_strategy(self) -> None:
        model = _RecordingModel()
        strategy = AnthropicCacheStrategy(FIVE_MIN, top_level=True)
        await _transport(model, strategy).complete(
            _messages(), tools=TOOLS, system=f"{STABLE}\n\n{VOLATILE}", system_blocks=[STABLE, VOLATILE],
        )
        system = model.calls[0]["messages"][0]
        assert isinstance(system, SystemMessage)
        assert system.content == [
            {"type": "text", "text": STABLE, "cache_control": FIVE_MIN}, {"type": "text", "text": f"\n\n{VOLATILE}"},
        ]
        assert model.bound[0][-1]["cache_control"] == FIVE_MIN
        assert model.calls[0]["kwargs"] == {"cache_control": FIVE_MIN}

    async def test_a_plain_system_is_one_stable_block(self) -> None:
        model = _RecordingModel()
        await _transport(model, AnthropicCacheStrategy(FIVE_MIN, top_level=True)).complete(
            _messages(), system="Be brief.",
        )
        assert model.calls[0]["messages"][0].content == [
            {"type": "text", "text": "Be brief.", "cache_control": FIVE_MIN},
        ]

    async def test_without_a_strategy_nothing_changes(self) -> None:
        model = _RecordingModel()
        await LangChainTransport(model, model_name="m").complete(
            _messages(), tools=TOOLS, system_blocks=[STABLE, VOLATILE],
        )
        assert model.calls[0]["kwargs"] == {}
        assert model.calls[0]["messages"][0].content == f"{STABLE}\n\n{VOLATILE}"
        assert "cache_control" not in json.dumps(model.bound[0])

    @pytest.mark.parametrize("streaming", [False, True])
    async def test_the_image_relocation_retry(self, streaming: bool) -> None:
        conflict = RuntimeError("Image URLs are only allowed for messages with role 'user'")
        model = _RecordingModel(errors=[conflict])
        transport = _transport(model)
        messages = [
            UserMessage(content="what is this?"),
            AssistantMessage(tool_calls=[ToolCall(id="t1", name="fetch", arguments={})]),
            ToolMessage(content=[
                TextPart(text="[r1]"), ImagePart(source=ImageSource(type="url", data="https://x/1.png")),
            ], tool_call_id="t1"),
        ]
        if streaming:
            _ = [event async for event in transport.stream(messages, tools=TOOLS)]
        else:
            await transport.complete(messages, tools=TOOLS)
        assert len(model.calls) == 2
        assert all(call["kwargs"] == KWARGS for call in model.calls)


class TestRejectedCacheParameter:
    async def test_complete_resends_once_without_them(self) -> None:
        model = _RecordingModel(errors=[_bad_request("Unrecognized request argument supplied: prompt_cache_retention")])
        transport = _transport(model)
        response = await transport.complete(_messages(), tools=TOOLS, system_blocks=[STABLE, VOLATILE])
        assert response.message.content[0].text == "ok"
        assert [call["kwargs"] for call in model.calls] == [KWARGS, {}]
        await transport.complete(_messages(), tools=TOOLS)
        assert model.calls[-1]["kwargs"] == {}
        assert REJECTED_CACHE_PARAMS.rejected("gpt-5.4", None)

    async def test_stream_resends_once_without_them(self) -> None:
        model = _RecordingModel(errors=[_bad_request("Unknown parameter: 'prompt_cache_key'.")])
        events = await _stream(_transport(model))
        assert isinstance(events[-1], StreamCompleteEvent)
        assert [call["kwargs"] for call in model.calls] == [KWARGS, {}]

    async def test_anthropic_markers_are_dropped_too(self) -> None:
        import anthropic

        response = httpx.Response(400, request=httpx.Request("POST", "https://proxy.example/v1/messages"))
        error = anthropic.BadRequestError("Error code: 400 - cache_control: Extra inputs are not permitted",
                                          response=response, body=None)
        model = _RecordingModel(errors=[error])
        await _transport(model, AnthropicCacheStrategy(FIVE_MIN, top_level=False)).complete(
            _messages(), tools=TOOLS, system_blocks=[STABLE, VOLATILE],
        )
        retry = model.calls[-1]
        assert retry["kwargs"] == {}
        assert "cache_control" not in json.dumps([m.content for m in retry["messages"]])
        assert "cache_control" not in json.dumps(model.bound[-1])
        assert retry["messages"][0].content == f"{STABLE}\n\n{VOLATILE}"

    async def test_another_400_is_not_retried(self) -> None:
        model = _RecordingModel(errors=[_bad_request("Invalid 'messages[3]'")])
        with pytest.raises(TransportError):
            await _transport(model).complete(_messages(), tools=TOOLS)
        assert len(model.calls) == 1
        assert not REJECTED_CACHE_PARAMS.rejected("gpt-5.4", None)

    async def test_not_after_chunks_have_streamed(self) -> None:
        model = _RecordingModel(errors=[_bad_request("prompt_cache_key rejected")], chunks_before_error=1)
        with pytest.raises(TransportError):
            await _stream(_transport(model))
        assert len(model.calls) == 1

    async def test_a_second_rejection_is_raised(self) -> None:
        message = "Unrecognized request argument supplied: prompt_cache_retention"
        model = _RecordingModel(errors=[_bad_request(message), _bad_request(message)])
        with pytest.raises(TransportError):
            await _transport(model).complete(_messages(), tools=TOOLS)
        assert len(model.calls) == 2


class TestUsage:
    async def test_bedrock_input_includes_cached_tokens(self) -> None:
        from langchain_aws import ChatBedrockConverse

        client = MagicMock()
        client.converse.return_value = {
            "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 100, "outputTokens": 5, "totalTokens": 1105,
                      "cacheReadInputTokens": 900, "cacheWriteInputTokens": 100},
            "metrics": {"latencyMs": 1},
            "ResponseMetadata": {"HTTPStatusCode": 200},
        }
        llm = ChatBedrockConverse(model="us.anthropic.claude-sonnet-4-5-20250929-v1:0", region_name="us-east-1",
                                  client=client)
        usage = (await LangChainTransport(llm, model_name="claude").complete(_messages())).usage
        # Pins langchain-aws reporting input without cached tokens; if it starts
        # including them, this transport would count them twice.
        assert (usage.input_tokens, usage.cache_read_tokens, usage.cache_write_tokens) == (1100, 900, 100)


class TestPrefixStaysTheSame:
    """Turn N+1 begins with turn N's request: the same tools, the same system
    prompt, and turn N's messages unchanged once the markers are removed."""

    async def test_anthropic_turn_to_turn(self, endpoint) -> None:
        from langchain_anthropic import ChatAnthropic

        llm = ChatAnthropic(model="claude-opus-5-5", api_key="test", base_url=endpoint.url, max_retries=0)
        transport = LangChainTransport(llm, model_name="claude-opus-5-5",
                                       cache_strategy=AnthropicCacheStrategy(FIVE_MIN, top_level=True))
        history: list[Any] = [UserMessage(content="Who owns HarborBank?")]
        for turn in range(3):
            await transport.complete(list(history), tools=TOOLS, system_blocks=[STABLE, VOLATILE])
            history += [
                AssistantMessage(tool_calls=[ToolCall(id=f"t{turn}", name="search", arguments={"q": f"q{turn}"})]),
                ToolMessage(content=f"result {turn}", tool_call_id=f"t{turn}"),
            ]
        first, second, third = (_unmarked(body) for body in endpoint.bodies)
        for earlier, later in ((first, second), (second, third)):
            assert later["tools"] == earlier["tools"]
            assert later["system"] == earlier["system"]
            assert later["messages"][: len(earlier["messages"])] == earlier["messages"]
        assert all(len(_markers(body)) == 3 for body in endpoint.bodies)


def _unmarked(node: Any) -> Any:  # noqa: ANN401
    if isinstance(node, dict):
        out = {k: _unmarked(v) for k, v in node.items() if k != "cache_control"}
        if isinstance(out.get("content"), str):
            out["content"] = [{"type": "text", "text": out["content"]}]
        return out
    if isinstance(node, list):
        return [_unmarked(item) for item in node]
    return node


def _markers(node: Any) -> list[Any]:  # noqa: ANN401
    if isinstance(node, dict):
        own = [node["cache_control"]] if "cache_control" in node else []
        return own + [m for v in node.values() for m in _markers(v)]
    if isinstance(node, list):
        return [m for item in node for m in _markers(item)]
    return []
