"""E3 CW-1..CW-7: OpenAI chat completions on the wire ↔ kernel model types (pure, table tests)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from harness_core.gateway.chat_wire import (
    ChunkEncoder,
    completion_body,
    parse_chat_request,
)
from harness_core.gateway.errors import InvalidChatRequest, UnsupportedParameter
from harness_core.kernel.core.messages import (
    MALFORMED_TOOL_CALL_ARGS_KEY,
    MALFORMED_TOOL_CALL_ERROR_KEY,
    AssistantMessage,
    ImagePart,
    SystemMessage,
    TextPart,
    ThinkingPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from harness_core.kernel.core.responses import ModelResponse, StopReason, TokenUsage
from harness_core.kernel.core.streaming import (
    StreamCompleteEvent,
    StreamEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallDeltaEvent,
)

# What opencode 1.18.34 sent (F4 §3 [run]), with a tool round-trip in the history.
OPENCODE_BODY: dict[str, Any] = {
    "model": "engine-model",
    "max_tokens": 8192,
    "messages": [
        {"role": "system", "content": "You are a coding engine."},
        {"role": "user", "content": "Create probe.txt"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "write", "arguments": '{"filePath":"probe.txt","content":"x"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "written"},
    ],
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "write",
                "description": "Write a file",
                "parameters": {"type": "object", "properties": {"filePath": {"type": "string"}}},
            },
        }
    ],
    "tool_choice": "auto",
    "stream": True,
    "stream_options": {"include_usage": True},
}


def test_cw1_opencode_request_parses() -> None:
    parsed = parse_chat_request(OPENCODE_BODY)
    assert parsed.model == "engine-model"
    assert parsed.system == "You are a coding engine."
    assert parsed.stream and parsed.include_usage
    assert parsed.max_tokens == 8192
    assert [t.name for t in parsed.tools] == ["write"]
    assert parsed.tools[0].input_schema["type"] == "object"
    user, assistant, tool = parsed.messages
    assert isinstance(user, UserMessage) and user.content == "Create probe.txt"
    assert isinstance(assistant, AssistantMessage)
    assert assistant.tool_calls == [ToolCall(id="call_1", name="write", arguments={"filePath": "probe.txt", "content": "x"})]
    assert isinstance(tool, ToolMessage) and tool.tool_call_id == "call_1" and tool.content == "written"
    assert parsed.dropped == ()


def test_cw2_roles_and_parts() -> None:
    parsed = parse_chat_request(
        {
            "model": "m",
            "messages": [
                {"role": "developer", "content": [{"type": "text", "text": "rules"}]},
                {"role": "system", "content": "more rules"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "look"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
                    ],
                },
                {"role": "system", "content": "late note"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": "c", "type": "function", "function": {"name": "t", "arguments": "{bad"}}],
                },
                {"role": "tool", "tool_call_id": "c", "content": [{"type": "text", "text": "out"}]},
            ],
        }
    )
    assert parsed.system == "rules\n\nmore rules"
    user, late, assistant, tool = parsed.messages
    assert isinstance(user, UserMessage) and isinstance(user.content, list)
    text, data_image = user.content
    assert isinstance(text, TextPart) and text.text == "look"
    assert isinstance(data_image, ImagePart)
    assert (data_image.source.type, data_image.source.media_type, data_image.source.data) == ("base64", "image/png", "QUJD")
    assert isinstance(late, SystemMessage) and late.content == "late note"
    assert isinstance(assistant, AssistantMessage) and assistant.tool_calls is not None
    assert assistant.tool_calls[0].name == "t"
    assert isinstance(tool, ToolMessage) and tool.content == "out"


@pytest.mark.parametrize(
    ("body", "param"),
    [
        ({"n": 2}, "n"),
        ({"tool_choice": "required"}, "tool_choice"),
        ({"tool_choice": {"type": "function", "function": {"name": "x"}}}, "tool_choice"),
        ({"stop": ["\n"]}, "stop"),
        ({"logprobs": True}, "logprobs"),
        ({"response_format": {"type": "json_object"}}, "response_format"),
        ({"functions": [{"name": "f"}]}, "functions"),
        ({"function_call": "auto"}, "function_call"),
        ({"audio": {"voice": "x"}}, "audio"),
        ({"modalities": ["text", "audio"]}, "modalities"),
        ({"prediction": {"type": "content", "content": "x"}}, "prediction"),
    ],
)
def test_cw3_refused_parameters(body: dict[str, Any], param: str) -> None:
    with pytest.raises(UnsupportedParameter) as refused:
        parse_chat_request({"model": "m", "messages": [{"role": "user", "content": "hi"}], **body})
    assert refused.value.param == param
    assert refused.value.reason_code == "unsupported_parameter"


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        "text",
        {"messages": [{"role": "user", "content": "hi"}]},
        {"model": "m"},
        {"model": "m", "messages": []},
        {"model": "m", "messages": [{"role": "robot", "content": "hi"}]},
        {"model": "m", "messages": [{"role": "tool", "content": "x"}]},
        {"model": "m", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 0},
    ],
)
def test_cw3_invalid_requests(body: object) -> None:
    with pytest.raises(InvalidChatRequest) as refused:
        parse_chat_request(body)
    assert refused.value.reason_code == "invalid_chat_request"
    assert "hi" not in str(refused.value)


def test_cw4_tracking_and_sampling_fields_are_dropped_and_named() -> None:
    parsed = parse_chat_request(
        {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "user": "u-1",
            "metadata": {"session": "s"},
            "store": True,
            "service_tier": "flex",
            "prompt_cache_key": "k",
            "safety_identifier": "s",
            "temperature": 0.2,
            "top_p": 0.9,
            "seed": 7,
            "presence_penalty": 0.1,
            "frequency_penalty": 0.1,
            "parallel_tool_calls": True,
            "providerOptions": {"x": 1},
        }
    )
    # WP56-WIRE-1: the tool and cache options are read and passed on (the cache key as the org's routing key).
    assert parsed.parallel_tool_calls is True and parsed.cache_key == "k"
    assert parsed.dropped == (
        "frequency_penalty",
        "metadata",
        "presence_penalty",
        "providerOptions",
        "safety_identifier",
        "seed",
        "service_tier",
        "store",
        "temperature",
        "top_p",
        "user",
    )


def _turn(*, finish: StopReason = StopReason.TOOL_USE) -> ModelResponse:
    return ModelResponse(
        message=AssistantMessage(
            content=[ThinkingPart(thinking="hmm"), TextPart(text="Writing files.")],
            tool_calls=[
                ToolCall(id="call_a", name="write", arguments={"filePath": "a.txt"}),
                ToolCall(id="call_b", name="read", arguments={"filePath": "b.txt"}),
            ],
        ),
        usage=TokenUsage(input_tokens=120, output_tokens=30, cache_read_tokens=100),
        stop_reason=finish,
        model="gpt-4o-2024-08-06",
    )


@pytest.mark.parametrize(
    ("stop", "tool_calls", "finish"),
    [
        (StopReason.END_TURN, False, "stop"),
        (StopReason.STOP_SEQUENCE, False, "stop"),
        (StopReason.TOOL_USE, True, "tool_calls"),
        (StopReason.END_TURN, True, "tool_calls"),
        (StopReason.MAX_TOKENS, False, "length"),
        (StopReason.MAX_TOKENS, True, "length"),
        (StopReason.CANCELLED, False, "stop"),
    ],
)
def test_cw5_finish_reasons(stop: StopReason, tool_calls: bool, finish: str) -> None:
    calls = [ToolCall(id="c", name="t", arguments={})] if tool_calls else None
    body = completion_body(
        ModelResponse(message=AssistantMessage(content="x", tool_calls=calls), stop_reason=stop),
        model_id="engine-model",
        completion_id="chatcmpl-1",
        created=1,
    )
    assert body["choices"][0]["finish_reason"] == finish  # type: ignore[index]


def test_cw5_completion_body() -> None:
    body = completion_body(
        _turn(),
        model_id="engine-model",
        completion_id="chatcmpl-1",
        created=1_700_000_000,
    )
    assert body == {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1_700_000_000,
        "model": "engine-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Writing files.",
                    "reasoning_content": "hmm",
                    "tool_calls": [
                        {"id": "call_a", "type": "function", "function": {"name": "write", "arguments": '{"filePath":"a.txt"}'}},
                        {"id": "call_b", "type": "function", "function": {"name": "read", "arguments": '{"filePath":"b.txt"}'}},
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "prompt_tokens_details": {"cached_tokens": 100},
        },
    }


def test_cw5_malformed_tool_call_keeps_the_models_text() -> None:
    call = ToolCall(
        id="c",
        name="t",
        arguments={MALFORMED_TOOL_CALL_ARGS_KEY: '{"a": ', MALFORMED_TOOL_CALL_ERROR_KEY: "Expecting value"},
    )
    body = completion_body(
        ModelResponse(message=AssistantMessage(tool_calls=[call])), model_id="m", completion_id="i", created=0
    )
    assert body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] == '{"a": '  # type: ignore[index]


@pytest.mark.parametrize(
    ("usage", "prompt", "cached"),
    [
        (TokenUsage(input_tokens=120, output_tokens=5, cache_read_tokens=100), 120, 100),
        (TokenUsage(input_tokens=70, output_tokens=5, cache_write_tokens=50), 70, 0),
        (TokenUsage(input_tokens=170, output_tokens=5, cache_read_tokens=100, cache_write_tokens=50), 170, 100),
    ],
)
def test_cw5_the_prompt_is_reported_whole_with_its_cached_part(usage: TokenUsage, prompt: int, cached: int) -> None:
    body = completion_body(
        ModelResponse(message=AssistantMessage(content="x"), usage=usage), model_id="m", completion_id="i", created=0
    )
    assert body["usage"]["prompt_tokens"] == prompt  # type: ignore[index]
    assert body["usage"]["total_tokens"] == prompt + 5  # type: ignore[index]
    assert body["usage"]["prompt_tokens_details"]["cached_tokens"] == cached  # type: ignore[index]


def assemble(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """What an OpenAI client (the openai SDK, the AI SDK) builds from chunks: per-index tool
    calls keyed on their first fragment (which must carry id and name), arguments concatenated."""
    content, reasoning, finish, usage = "", "", None, None
    calls: dict[int, dict[str, str]] = {}
    for chunk in chunks:
        assert chunk["object"] == "chat.completion.chunk"
        assert chunk["model"] == "engine-model"
        if chunk.get("usage"):
            usage = chunk["usage"]
        for choice in chunk["choices"]:
            delta = choice["delta"]
            content += delta.get("content") or ""
            reasoning += delta.get("reasoning_content") or ""
            for call in delta.get("tool_calls") or []:
                if call["index"] not in calls:
                    assert call.get("id") and call["function"].get("name"), f"first fragment lacks id/name: {call}"
                    calls[call["index"]] = {"id": call["id"], "name": call["function"]["name"], "arguments": ""}
                calls[call["index"]]["arguments"] += call["function"].get("arguments") or ""
            finish = choice["finish_reason"] or finish
    return {
        "content": content,
        "reasoning": reasoning,
        "tool_calls": [
            {"id": c["id"], "name": c["name"], "arguments": json.loads(c["arguments"])} for _, c in sorted(calls.items())
        ],
        "finish": finish,
        "usage": usage,
    }


def _final(family_usage: TokenUsage) -> StreamCompleteEvent:
    turn = _turn()
    return StreamCompleteEvent(response=turn.model_copy(update={"usage": family_usage}))


# The same turn as each transport family reports it (harness_core/models/transports/*).
FAMILIES: dict[str, tuple[str, list[StreamEvent]]] = {
    # OpenAI / Azure chat completions: an opener with id and name and empty arguments, then fragments.
    "openai_chat": (
        "openai",
        [
            ThinkingDeltaEvent(delta="hm"),
            ThinkingDeltaEvent(delta="m"),
            TextDeltaEvent(delta="Writing "),
            TextDeltaEvent(delta="files."),
            ToolCallDeltaEvent(index=0, id="call_a", name="write", arguments_delta=""),
            ToolCallDeltaEvent(index=0, arguments_delta='{"filePath":'),
            ToolCallDeltaEvent(index=0, arguments_delta='"a.txt"}'),
            ToolCallDeltaEvent(index=1, id="call_b", name="read", arguments_delta='{"filePath":"b.txt"}'),
            _final(TokenUsage(input_tokens=120, output_tokens=30, cache_read_tokens=100)),
        ],
    ),
    # Azure via the Responses API: calls at sparse output indexes (a reasoning item is index 0).
    "azure_responses": (
        "azure_direct",
        [
            ThinkingDeltaEvent(delta="hmm"),
            TextDeltaEvent(delta="Writing files."),
            ToolCallDeltaEvent(index=2, id="call_a", name="write", arguments_delta=""),
            ToolCallDeltaEvent(index=2, arguments_delta='{"filePath":"a.txt"}'),
            ToolCallDeltaEvent(index=3, id="call_b", name="read", arguments_delta=""),
            ToolCallDeltaEvent(index=3, arguments_delta='{"filePath":"b.txt"}'),
            _final(TokenUsage(input_tokens=120, output_tokens=30, cache_read_tokens=100)),
        ],
    ),
    # A provider whose name arrives before its id (some OpenAI-compatible servers).
    "late_ids": (
        "openai",
        [
            ThinkingDeltaEvent(delta="hmm"),
            TextDeltaEvent(delta="Writing files."),
            ToolCallDeltaEvent(index=0, name="write", arguments_delta='{"filePath":'),
            ToolCallDeltaEvent(index=0, id="call_a", arguments_delta='"a.txt"}'),
            ToolCallDeltaEvent(index=1, name="read", arguments_delta='{"filePath":"b.txt"}'),
            _final(TokenUsage(input_tokens=120, output_tokens=30, cache_read_tokens=100)),
        ],
    ),
    # Anthropic: text streams; tool calls only on the final message.
    "anthropic": (
        "anthropic",
        [
            TextDeltaEvent(delta="Writing files."),
            _final(TokenUsage(input_tokens=120, output_tokens=30, cache_read_tokens=100)),
        ],
    ),
    # Gemini: each call arrives whole in one fragment.
    "gemini": (
        "gemini",
        [
            ThinkingDeltaEvent(delta="hmm"),
            TextDeltaEvent(delta="Writing files."),
            ToolCallDeltaEvent(index=0, id="call_a", name="write", arguments_delta='{"filePath":"a.txt"}'),
            ToolCallDeltaEvent(index=1, id="call_b", name="read", arguments_delta='{"filePath":"b.txt"}'),
            _final(TokenUsage(input_tokens=120, output_tokens=30, cache_read_tokens=100)),
        ],
    ),
    # Ollama: text only while streaming, calls on the final message.
    "ollama": (
        "ollama",
        [
            TextDeltaEvent(delta="Writing "),
            TextDeltaEvent(delta="files."),
            _final(TokenUsage(input_tokens=120, output_tokens=30, cache_read_tokens=100)),
        ],
    ),
}


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_cw6_cw7_every_family_streams_the_same_turn(family: str) -> None:
    _, events = FAMILIES[family]
    encoder = ChunkEncoder("engine-model", "chatcmpl-1", 1, include_usage=True)
    chunks = encoder.start()
    for event in events:
        chunks.extend(encoder.on_event(event))
    assembled = assemble(chunks)
    assert assembled == {
        "content": "Writing files.",
        "reasoning": "hmm",
        "tool_calls": [
            {"id": "call_a", "name": "write", "arguments": {"filePath": "a.txt"}},
            {"id": "call_b", "name": "read", "arguments": {"filePath": "b.txt"}},
        ],
        "finish": "tool_calls",
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "prompt_tokens_details": {"cached_tokens": 100},
        },
    }
    assert chunks[-1]["choices"] == []
    assert chunks[-2]["choices"][0]["finish_reason"] == "tool_calls"


def test_cw6_usage_chunk_only_when_asked() -> None:
    encoder = ChunkEncoder("engine-model", "chatcmpl-1", 1, include_usage=False)
    chunks = [*encoder.start(), *encoder.on_event(TextDeltaEvent(delta="hi")), *encoder.on_event(_final(TokenUsage()))]
    assert all(chunk["choices"] for chunk in chunks)
    assert "usage" not in chunks[-1]


def test_fb2_text_only_on_the_final_response_is_sent_whole() -> None:
    encoder = ChunkEncoder("engine-model", "chatcmpl-1", 1, include_usage=False)
    final = StreamCompleteEvent(response=ModelResponse(message=AssistantMessage(content="whole answer")))
    assembled = assemble([*encoder.start(), *encoder.on_event(final)])
    assert (assembled["content"], assembled["finish"]) == ("whole answer", "stop")
    streamed = ChunkEncoder("engine-model", "chatcmpl-2", 1, include_usage=False)
    chunks = [*streamed.start(), *streamed.on_event(TextDeltaEvent(delta="whole answer")), *streamed.on_event(final)]
    assert assemble(chunks)["content"] == "whole answer"
