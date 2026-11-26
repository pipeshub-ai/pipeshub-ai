"""E3c MW-1..MW-12: Anthropic's Messages API on the wire ↔ kernel model types (pure, table tests).

Streams are assembled by the ``anthropic`` SDK's own accumulator, so what passes
here is what an Anthropic client (Claude Code uses the same SDK family) builds.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harness_core.gateway.errors import InvalidMessagesRequest, UnsupportedParameter
from harness_core.gateway.messages_wire import (
    ACCEPTED_BETAS,
    MESSAGES_WIRE,
    OVERFLOW_MESSAGE,
    MessageEncoder,
    MessagesRequest,
    message_body,
    messages_error,
    parse_messages_request,
)
from harness_core.gateway.wire import WireCall
from harness_core.kernel.core.messages import (
    MALFORMED_TOOL_CALL_ARGS_KEY,
    MALFORMED_TOOL_CALL_ERROR_KEY,
    AssistantMessage,
    ImagePart,
    ImageSource,
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
from tests.unit.harness_core.gateway.test_chat_wire import FAMILIES

FIXTURE = Path(__file__).parent / "fixtures" / "claude_code_messages_request.json"
CALL = WireCall("engine-model", "msg_000001", 1_700_000_000)
TOOL = {"name": "get_time", "description": "Time in a city", "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}}}


def recorded() -> dict[str, Any]:
    """CC-1: Claude Code 2.1.289's requests, as recorded (provenance in the fixture)."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def claude_code_request(name: str = "tool_round_trip") -> tuple[dict[str, Any], dict[str, str]]:
    found = next(r for r in recorded()["requests"] if r["name"] == name)
    return found["body"], {k.lower(): v for k, v in found["headers"].items()}


def request(**extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"model": "engine-model", "max_tokens": 1024, "messages": [{"role": "user", "content": "hi"}], **extra}


def test_mw1_claude_codes_requests_parse() -> None:
    body, headers = claude_code_request()
    parsed = parse_messages_request(body, headers)
    assert parsed.model == "engine-model" and parsed.stream and parsed.max_tokens == 32000
    assert parsed.reasoning_effort == "high" and not parsed.show_thinking
    assert parsed.betas == tuple(recorded()["beta_sets"]["default"])
    assert parsed.system is not None and not parsed.system.startswith("x-anthropic-billing-header")
    assert parsed.system.startswith("You are a Claude agent")
    kinds = [type(m).__name__ for m in parsed.messages]
    assert kinds == ["UserMessage", "SystemMessage", "AssistantMessage", "ToolMessage", "SystemMessage"]
    assistant = parsed.messages[2]
    assert isinstance(assistant, AssistantMessage) and assistant.tool_calls is not None
    assert (assistant.tool_calls[0].id, assistant.tool_calls[0].name) == ("toolu_1", "Bash")
    assert parsed.messages[3] == ToolMessage(content="hi", tool_call_id="toolu_1")
    assert len(parsed.tools) == 20 and {"Bash", "Read", "Edit", "Write"} <= {t.name for t in parsed.tools}
    assert {"metadata", "system.billing_header", "system.cache_control", "messages.content.cache_control"} <= set(parsed.dropped)
    first, first_headers = claude_code_request("first_turn")
    assert [type(m).__name__ for m in parse_messages_request(first, first_headers).messages] == ["UserMessage", "SystemMessage"]


def test_mw2_content_shapes() -> None:
    history = [
        {"role": "system", "content": "leading rule"},
        {"role": "user", "content": [
            {"type": "text", "text": "look"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBOR"}},
        ]},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "hmm", "signature": "sig-opaque"},
            {"type": "text", "text": "Reading."},
            {"type": "tool_use", "id": "toolu_a", "name": "read", "input": {"path": "a"}},
        ]},
        {"role": "assistant", "content": [
            {"type": "redacted_thinking", "data": "opaque"},
            {"type": "tool_use", "id": "toolu_b", "name": "read", "input": {"path": "b"}},
        ]},
        {"role": "user", "content": [
            {"type": "text", "text": "and then"},
            {"type": "tool_result", "tool_use_id": "toolu_a", "content": "A"},
            {"type": "tool_result", "tool_use_id": "toolu_b", "is_error": True,
             "content": [{"type": "text", "text": "no such file"}]},
        ]},
        {"role": "system", "content": [{"type": "text", "text": "later rule"}]},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "thanks"},
    ]
    parsed = parse_messages_request(request(messages=history))
    assert parsed.system == "leading rule"
    assert parsed.reasoning_items == 2
    assert parsed.messages == (
        UserMessage(content=[
            TextPart(text="look"),
            ImagePart(source=ImageSource(type="base64", media_type="image/png", data="iVBOR")),
        ]),
        AssistantMessage(content="Reading.", tool_calls=[  # pyright: ignore[reportArgumentType]
            ToolCall(id="toolu_a", name="read", arguments={"path": "a"}),
            ToolCall(id="toolu_b", name="read", arguments={"path": "b"}),
        ]),
        ToolMessage(content="A", tool_call_id="toolu_a"),
        ToolMessage(content="no such file", tool_call_id="toolu_b", is_error=True),
        UserMessage(content="and then"),
        SystemMessage(content="later rule"),
        AssistantMessage(content="done"),  # pyright: ignore[reportArgumentType]
        UserMessage(content="thanks"),
    )
    assert parse_messages_request(request(system=[{"type": "text", "text": "a"}, {"type": "text", "text": "b"}])).system == "a\n\nb"


@pytest.mark.parametrize("beta_set", ["default", "no_experimental", "stream_json"])
def test_mw3_claude_codes_beta_sets_are_served(beta_set: str) -> None:
    betas = recorded()["beta_sets"][beta_set]
    headers = {"anthropic-version": "2023-06-01", "anthropic-beta": ",".join(betas)}
    assert parse_messages_request(request(), headers).betas == tuple(betas)
    assert parse_messages_request(request(), {}).betas == ()


@pytest.mark.parametrize(
    ("beta", "refused"),
    [
        ("auto_mode", ["afk-mode-2026-01-31", "dangerous-tool-use-2026-09-03"]),
        ("context-1m-2025-08-07", ["context-1m-2025-08-07"]),
        ("files-api-2025-04-14", ["files-api-2025-04-14"]),
        ("oauth-2025-04-20", ["oauth-2025-04-20"]),
        ("some-future-beta-2027-01-01", ["some-future-beta-2027-01-01"]),
    ],
)
def test_mw3_betas_off_the_allowlist_are_refused_by_name(beta: str, refused: list[str]) -> None:
    sent = recorded()["beta_sets"]["auto_mode"] if beta == "auto_mode" else ["claude-code-20250219", beta]
    with pytest.raises(UnsupportedParameter) as caught:
        parse_messages_request(request(), {"anthropic-beta": ", ".join(sent)})
    assert caught.value.param == "anthropic-beta" and caught.value.reason_code == "unsupported_parameter"
    for name in refused:
        assert f"capability_rejected: beta_header:{name}" in str(caught.value)
    assert caught.value.detail == ",".join(refused)
    assert not set(refused) & ACCEPTED_BETAS


def test_mw3_another_api_version_is_refused_and_odd_beta_text_is_not_logged() -> None:
    with pytest.raises(UnsupportedParameter) as version:
        parse_messages_request(request(), {"anthropic-version": "2023-01-01"})
    assert version.value.param == "anthropic-version"
    with pytest.raises(UnsupportedParameter) as odd:
        parse_messages_request(request(), {"anthropic-beta": "Not A Beta Name!"})
    assert odd.value.detail == "<unrecognised>"


@pytest.mark.parametrize(
    ("extra", "param"),
    [
        ({"stop_sequences": ["END"]}, "stop_sequences"),
        ({"container": "container_1"}, "container"),
        ({"mcp_servers": [{"type": "url", "url": "https://x", "name": "x"}]}, "mcp_servers"),
        ({"safeguards": "auto_mode"}, "safeguards"),
        ({"inference_geo": "us"}, "inference_geo"),
        ({"output_format": {"type": "json_schema", "schema": {}}}, "output_format"),
        ({"output_config": {"format": {"type": "json_schema", "schema": {}}}}, "output_config.format"),
        ({"tool_choice": {"type": "any"}}, "tool_choice"),
        ({"tool_choice": {"type": "tool", "name": "get_time"}}, "tool_choice"),
        ({"tool_choice": {"type": "none"}}, "tool_choice"),
        ({"tools": [{"type": "web_search_20250305", "name": "web_search"}]}, "tools.type=web_search_20250305"),
        ({"tools": [{"type": "bash_20250124", "name": "bash"}]}, "tools.type=bash_20250124"),
        ({"tools": [{"type": "text_editor_20250728", "name": "str_replace_based_edit_tool"}]}, "tools.type=text_editor_20250728"),
        ({"tools": [{"type": "code_execution_20250825", "name": "code_execution"}]}, "tools.type=code_execution_20250825"),
        ({"tools": [{**TOOL, "defer_loading": True}]}, "tools.defer_loading"),
        ({"tools": [{**TOOL, "strict": True}]}, "tools.strict"),
        ({"messages": [{"role": "user", "content": [{"type": "document", "source": {"type": "text", "data": "x"}}]}]},
         "messages.content.type=document"),
        ({"messages": [{"role": "user", "content": [{"type": "search_result", "source": "x", "title": "t", "content": []}]}]},
         "messages.content.type=search_result"),
        ({"messages": [{"role": "user", "content": [{"type": "container_upload", "file_id": "f"}]}]},
         "messages.content.type=container_upload"),
        ({"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": [
            {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {}}]}]},
         "messages.content.type=server_tool_use"),
        ({"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": [
            {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": []}]}]},
         "messages.content.type=web_search_tool_result"),
        ({"messages": [{"role": "user", "content": [{"type": "image", "source": {"type": "file", "file_id": "f"}}]}]},
         "messages.content.image.source.type=file"),
        ({"context_management": {"edits": [{"type": "clear_tool_uses_20250919"}]}},
         "context_management.edits.type=clear_tool_uses_20250919"),
    ],
)
def test_mw4_unsupported_parameters_are_refused(extra: dict[str, Any], param: str) -> None:
    with pytest.raises(UnsupportedParameter) as refused:
        parse_messages_request(request(**extra))
    assert refused.value.param == param and refused.value.reason_code == "unsupported_parameter"
    assert "END" not in str(refused.value) and "https://x" not in str(refused.value)


def test_mw4_claude_codes_auto_mode_safeguards_are_refused() -> None:
    body, headers = claude_code_request("first_turn")
    with pytest.raises(UnsupportedParameter) as refused:
        parse_messages_request({**body, "safeguards": recorded()["auto_mode_safeguards"]}, headers)
    assert refused.value.param == "safeguards"
    assert "safeguards" in recorded()["auto_mode_body_keys"]


@pytest.mark.parametrize(
    ("extra", "capability"),
    [
        ({"messages": [{"role": "user", "content": [{"type": "document", "source": {"type": "text", "data": "x"}}]}]},
         "document_block"),
        ({"output_config": {"format": {"type": "json_schema", "schema": {}}}}, "structured_outputs_unsupported"),
        ({"messages": [{"role": "user", "content": [{"type": "image", "source": {"type": "file", "file_id": "f"}}]}]},
         "image_block"),
    ],
)
def test_mw4_claude_code_reads_which_capability_was_refused(extra: dict[str, Any], capability: str) -> None:
    with pytest.raises(UnsupportedParameter) as refused:
        parse_messages_request(request(**extra))
    assert f"capability_rejected: {capability}" in str(refused.value)


def test_mw4_empty_stop_sequences_and_clearing_thinking_are_served() -> None:
    parsed = parse_messages_request(
        request(stop_sequences=[], context_management={"edits": [{"type": "clear_thinking_20251015", "keep": "all"}]})
    )
    assert parsed.messages == (UserMessage(content="hi"),)


@pytest.mark.parametrize(
    "body",
    [
        "not an object",
        {"messages": [{"role": "user", "content": "hi"}]},
        {"model": "engine-model"},
        {"model": "engine-model", "messages": []},
        request(messages=["text"]),
        request(messages=[{"role": "tool", "content": "x"}]),
        request(messages=[{"role": "assistant", "content": [{"type": "tool_use", "name": "x", "input": {}}]}]),
        request(messages=[{"role": "assistant", "content": [{"type": "tool_use", "id": "t", "input": {}}]}]),
        request(messages=[{"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "x", "input": "s"}]}]),
        request(messages=[{"role": "user", "content": [{"type": "tool_result", "content": "x"}]}]),
        request(tools=[{"input_schema": {"type": "object"}}]),
        request(tools=[{"name": "x", "input_schema": "object"}]),
        request(tools=[TOOL, TOOL]),
        request(max_tokens=0),
        request(max_tokens="many"),
        request(thinking={"type": "sometimes"}),
        request(messages=[{"role": "system", "content": "only a system message"}]),
    ],
)
def test_mw5_invalid_requests(body: object) -> None:
    with pytest.raises(InvalidMessagesRequest) as refused:
        parse_messages_request(body)
    assert refused.value.reason_code == "invalid_messages_request" and refused.value.http_status == 400


def test_mw6_tracking_sampling_and_caching_fields_are_dropped_and_named() -> None:
    parsed = parse_messages_request(
        request(
            temperature=0.2,
            top_p=0.9,
            top_k=5,
            metadata={"user_id": "device-1"},
            service_tier="auto",
            cache_control={"type": "ephemeral"},
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},
            tools=[{**TOOL, "cache_control": {"type": "ephemeral"}, "eager_input_streaming": True}],
            future_field=1,
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}]}],
        )
    )
    assert parsed.dropped == (
        "cache_control",
        "future_field",
        "messages.content.cache_control",
        "metadata",
        "service_tier",
        "temperature",
        "tool_choice.disable_parallel_tool_use",
        "tools.cache_control",
        "tools.eager_input_streaming",
        "top_k",
        "top_p",
    )
    assert parsed.tools[0].name == "get_time" and parsed.messages == (UserMessage(content="hi"),)


def _turn(*, stop: StopReason = StopReason.TOOL_USE, usage: TokenUsage | None = None) -> ModelResponse:
    return ModelResponse(
        message=AssistantMessage(
            content=[ThinkingPart(thinking="hmm"), TextPart(text="Writing files.")],
            tool_calls=[
                ToolCall(id="call_a", name="write", arguments={"filePath": "a.txt"}),
                ToolCall(id="call_b", name="read", arguments={"filePath": "b.txt"}),
            ],
        ),
        usage=usage or TokenUsage(input_tokens=127, output_tokens=30, cache_read_tokens=100, cache_write_tokens=7),
        stop_reason=stop,
        model="provider-model",
    )


def _thinking_request() -> MessagesRequest:
    return parse_messages_request(request(thinking={"type": "adaptive"}))


EXPECTED_CONTENT = [
    {"type": "thinking", "thinking": "hmm", "signature": ""},
    {"type": "text", "text": "Writing files."},
    {"type": "tool_use", "id": "call_a", "name": "write", "input": {"filePath": "a.txt"}},
    {"type": "tool_use", "id": "call_b", "name": "read", "input": {"filePath": "b.txt"}},
]
EXPECTED_USAGE = {"input_tokens": 20, "output_tokens": 30, "cache_creation_input_tokens": 7, "cache_read_input_tokens": 100}


def test_mw7_message_body() -> None:
    body = message_body(_thinking_request(), _turn(), CALL)
    assert body["id"] == "msg_000001" and body["type"] == "message" and body["role"] == "assistant"
    assert body["model"] == "engine-model" and body["stop_sequence"] is None
    assert body["content"] == EXPECTED_CONTENT
    assert body["stop_reason"] == "tool_use" and body["usage"] == EXPECTED_USAGE
    plain = message_body(parse_messages_request(request()), _turn(), CALL)
    assert plain["content"] == EXPECTED_CONTENT[1:]
    omitted = message_body(parse_messages_request(request(thinking={"type": "adaptive", "display": "omitted"})), _turn(), CALL)
    assert omitted["content"] == EXPECTED_CONTENT[1:]


@pytest.mark.parametrize(
    ("stop", "calls", "reason"),
    [
        (StopReason.END_TURN, False, "end_turn"),
        (StopReason.TOOL_USE, True, "tool_use"),
        (StopReason.END_TURN, True, "tool_use"),
        (StopReason.MAX_TOKENS, True, "max_tokens"),
        (StopReason.MAX_TOKENS, False, "max_tokens"),
        (StopReason.STOP_SEQUENCE, False, "end_turn"),
        (StopReason.ERROR, False, "end_turn"),
    ],
)
def test_mw7_stop_reasons(stop: StopReason, calls: bool, reason: str) -> None:
    response = _turn(stop=stop)
    if not calls:
        response = response.model_copy(update={"message": AssistantMessage(content="x")})
    assert message_body(parse_messages_request(request()), response, CALL)["stop_reason"] == reason


def test_mw7_a_malformed_call_is_still_an_object() -> None:
    bad = ToolCall(id="call_m", name="write",
                   arguments={MALFORMED_TOOL_CALL_ARGS_KEY: '{"filePath": ', MALFORMED_TOOL_CALL_ERROR_KEY: "Expecting value"})
    response = ModelResponse(message=AssistantMessage(tool_calls=[bad]), stop_reason=StopReason.TOOL_USE)
    block = message_body(parse_messages_request(request()), response, CALL)["content"][0]
    assert block["type"] == "tool_use" and isinstance(block["input"], dict)
    assert MALFORMED_TOOL_CALL_ARGS_KEY in block["input"]


def assemble(events: list[dict[str, Any]]) -> dict[str, Any]:
    """The ``anthropic`` SDK's accumulator over the events, after the protocol's own invariants."""
    from anthropic.lib.streaming._messages import (
        accumulate_event,  # pyright: ignore[reportPrivateUsage]
    )

    assert events[0]["type"] == "message_start" and events[-1]["type"] == "message_stop"
    assert events[-2]["type"] == "message_delta"
    open_index: int | None = None
    expected = 0
    for event in events[1:-2]:
        kind = event["type"]
        if kind == "content_block_start":
            assert open_index is None and event["index"] == expected, event
            open_index = expected
        elif kind == "content_block_delta":
            assert event["index"] == open_index, event
        elif kind == "content_block_stop":
            assert event["index"] == open_index, event
            open_index, expected = None, expected + 1
        else:
            raise AssertionError(f"unexpected event {kind}")
    assert open_index is None
    snapshot = None
    for event in events:
        snapshot = accumulate_event(event=event, current_snapshot=snapshot)  # type: ignore[arg-type]
    assert snapshot is not None
    out = snapshot.model_dump(exclude_none=True)
    for block in out["content"]:
        block.pop("parsed_output", None)
        block.pop("citations", None)
    return out


def _stream(request_: MessagesRequest, events: list[StreamEvent], family: str = "anthropic") -> list[dict[str, Any]]:
    encoder = MessageEncoder(request_, CALL)
    out = encoder.start()
    for event in events:
        out.extend(encoder.on_event(event))
    return out


def _usage(body: dict[str, Any]) -> dict[str, Any]:
    return {k: body["usage"].get(k) for k in EXPECTED_USAGE}


def test_mw8_streamed_events_assemble_into_the_body() -> None:
    events: list[StreamEvent] = [
        ThinkingDeltaEvent(delta="hm"),
        ThinkingDeltaEvent(delta="m"),
        TextDeltaEvent(delta="Writing "),
        TextDeltaEvent(delta="files."),
        ToolCallDeltaEvent(index=0, id="call_a", name="write", arguments_delta='{"filePath":'),
        ToolCallDeltaEvent(index=0, arguments_delta='"a.txt"}'),
        StreamCompleteEvent(response=_turn()),
    ]
    out = _stream(_thinking_request(), events)
    assembled = assemble(out)
    body = message_body(_thinking_request(), _turn(), CALL)
    assert assembled["content"] == body["content"]
    assert (assembled["id"], assembled["model"], assembled["stop_reason"]) == ("msg_000001", "engine-model", "tool_use")
    assert _usage(assembled) == EXPECTED_USAGE
    assert out[0]["message"]["usage"] == {"input_tokens": 0, "output_tokens": 0}
    framed = MESSAGES_WIRE.frame(out[0])
    assert framed.startswith(b"event: message_start\ndata: {") and framed.endswith(b"\n\n")
    assert MESSAGES_WIRE.terminal == b""


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_mw9_every_family_streams_the_same_turn(family: str) -> None:
    transport_family, events = FAMILIES[family]
    assembled = assemble(_stream(_thinking_request(), list(events), transport_family))
    blocks = sorted(assembled["content"], key=lambda b: ["thinking", "text", "tool_use"].index(b["type"]))
    assert blocks == EXPECTED_CONTENT
    assert [b["id"] for b in assembled["content"] if b["type"] == "tool_use"] == ["call_a", "call_b"]
    assert _usage(assembled) == {"input_tokens": 20, "output_tokens": 30, "cache_creation_input_tokens": 0,
                                 "cache_read_input_tokens": 100}


def test_mw10_whole_answers_and_late_ids() -> None:
    final_only = assemble(_stream(_thinking_request(), [StreamCompleteEvent(response=_turn())]))
    assert final_only["content"] == EXPECTED_CONTENT
    late = assemble(_stream(_thinking_request(), [
        ToolCallDeltaEvent(index=0, name="write", arguments_delta='{"filePath":'),
        ToolCallDeltaEvent(index=0, id="call_a", arguments_delta='"a.txt"}'),
        StreamCompleteEvent(response=_turn()),
    ]))
    assert [(b["id"], b["name"]) for b in late["content"] if b["type"] == "tool_use"] == [("call_a", "write"), ("call_b", "read")]


def test_mw10_the_final_arguments_win_over_different_streamed_ones() -> None:
    events: list[StreamEvent] = [
        ToolCallDeltaEvent(index=0, id="call_a", name="write", arguments_delta='{"filePath":"a.txt"}'),
        StreamCompleteEvent(response=ModelResponse(message=AssistantMessage(
            tool_calls=[ToolCall(id="call_a", name="write", arguments={"filePath": "b.txt"})]))),
    ]
    assembled = assemble(_stream(parse_messages_request(request()), events))
    assert assembled["content"] == [{"type": "tool_use", "id": "call_a", "name": "write", "input": {"filePath": "b.txt"}}]


def test_mw11_a_provider_failure_after_the_start_is_an_error_event() -> None:
    encoder = MessageEncoder(parse_messages_request(request()), CALL)
    events = [*encoder.start(), *encoder.on_event(TextDeltaEvent(delta="par"))]
    events += encoder.failed("provider_unavailable", "The provider is unavailable")
    assert events[-1] == {"type": "error", "error": {"type": "api_error", "message": "The provider is unavailable [gateway:provider_unavailable]"}}
    assert not any(e["type"] in ("message_stop", "message_delta") for e in events)
    assert MESSAGES_WIRE.frame(events[-1]).startswith(b"event: error\n")


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        ("rate_limited", "rate_limit_error"),
        ("provider_quota", "billing_error"),
        ("context_length_exceeded", "invalid_request_error"),
        ("content_filter", "invalid_request_error"),
        ("provider_auth_failed", "api_error"),
        ("provider_unavailable", "api_error"),
    ],
)
def test_mw11_failure_types_in_anthropics_vocabulary(code: str, kind: str) -> None:
    error = MessageEncoder(parse_messages_request(request()), CALL).failed(code, "m")[-1]["error"]
    assert error["type"] == kind
    assert error["message"] == (OVERFLOW_MESSAGE if code == "context_length_exceeded" else "m") + f" [gateway:{code}]"


@pytest.mark.parametrize(
    ("status", "code", "kind", "no_retry"),
    [
        (400, "unsupported_parameter", "invalid_request_error", True),
        (400, "invalid_messages_request", "invalid_request_error", True),
        (400, "context_length_exceeded", "invalid_request_error", True),
        (401, "token_expired", "authentication_error", False),
        (402, "budget_exhausted", "billing_error", True),
        (402, "budget_insufficient", "billing_error", True),
        (402, "provider_quota", "billing_error", True),
        (403, "forbidden_credential", "permission_error", True),
        (403, "caller_credential_header", "permission_error", True),
        (404, "model_not_granted", "not_found_error", True),
        (409, "budget_reservation_settled", "invalid_request_error", True),
        (429, "rate_limited", "rate_limit_error", False),
        (502, "provider_unavailable", "api_error", False),
        (502, "provider_auth_failed", "api_error", True),
        (503, "budget_unavailable", "api_error", False),
        (503, "account_unavailable", "api_error", False),
    ],
)
def test_mw12_errors_and_retry_hints(status: int, code: str, kind: str, no_retry: bool) -> None:
    body, headers = messages_error(status, code, "msg")
    assert body["type"] == "error" and body["error"]["type"] == kind  # type: ignore[index]
    assert headers == ({"x-should-retry": "false"} if no_retry else {})
    assert MESSAGES_WIRE.error(status, code, "msg") == (body, headers)
    if code == "context_length_exceeded":
        assert "prompt is too long" in body["error"]["message"]  # type: ignore[index]
    # E9 FB-3: Claude Code reports only the status and this text, so the text names the gateway's code.
    assert body["error"]["message"].endswith(f" [gateway:{code}]")  # type: ignore[index]
