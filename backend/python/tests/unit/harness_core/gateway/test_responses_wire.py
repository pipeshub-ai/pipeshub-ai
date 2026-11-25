"""E3b RW-1..RW-11: the OpenAI Responses API on the wire ↔ kernel model types (pure, table tests)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harness_core.gateway.errors import (
    InvalidResponsesRequest,
    ResponseStateUnsupported,
    UnsupportedParameter,
)
from harness_core.gateway.responses_wire import (
    RESPONSES_WIRE,
    ResponseEncoder,
    ResponsesRequest,
    parse_responses_request,
)
from harness_core.gateway.wire import WireCall
from harness_core.kernel.core.messages import (
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
    ToolCallDeltaEvent,
)
from harness_core.models.provider_state import OPENAI_RESPONSES, with_state
from harness_core.models.transports.openai_responses import normalise_tool_call
from tests.unit.harness_core.gateway.test_chat_wire import FAMILIES

FIXTURE = Path(__file__).parent / "fixtures" / "codex_responses_request.json"
CALL = WireCall("engine-model", "resp_000001", 1_700_000_000)


def codex_request() -> dict[str, Any]:
    """CX-1: Codex's request body, as its own source builds it (provenance in the fixture)."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["body"]


def codex_headers() -> dict[str, str]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["headers"]


def request(**extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"model": "engine-model", "input": "hi", **extra}


def test_rw1_codex_request_parses() -> None:
    body = codex_request()
    parsed = parse_responses_request(body)
    assert parsed.model == body["model"]
    assert parsed.stream is True
    assert parsed.system is not None and parsed.system.startswith(body["instructions"][:40])
    assert parsed.messages and isinstance(parsed.messages[-1], UserMessage | ToolMessage)
    declared = {t["name"] for t in body["tools"] if t["type"] == "function"}
    assert declared <= {t.name for t in parsed.tools}
    # WP62-MGW-1: what Codex asks about its tools and cache is read, not dropped; its client metadata is.
    assert "client_metadata" in parsed.dropped
    assert not {"prompt_cache_key", "parallel_tool_calls"} & set(parsed.dropped)
    assert parsed.parallel_tool_calls is True and parsed.cache_key == body["prompt_cache_key"]
    assert parsed.encrypted_reasoning is ("reasoning.encrypted_content" in body.get("include", []))
    assert parsed.reasoning_items == 1
    assert [t.name for t in parsed.tools] == ["exec_command", "pipeshub_search", "skills__list", "skills__read"]
    roles = [type(m).__name__ for m in parsed.messages]
    assert roles == ["UserMessage", "UserMessage", "AssistantMessage", "ToolMessage", "AssistantMessage", "ToolMessage"]
    calls = [c for m in parsed.messages if isinstance(m, AssistantMessage) for c in m.tool_calls or ()]
    assert [(c.id, c.name) for c in calls] == [("call_search_1", "pipeshub_search"), ("call_skills_1", "skills__list")]
    minimal = parse_responses_request(json.loads(FIXTURE.read_text(encoding="utf-8"))["minimal_body"])
    assert minimal.system == "Say hi" and minimal.messages == (UserMessage(content="hi"),)
    assert minimal.reasoning_effort is None and minimal.tools == ()


def test_rw2_input_shapes() -> None:
    parsed = parse_responses_request(
        request(
            instructions="be brief",
            input=[
                {"role": "developer", "content": "house rules"},
                {"type": "message", "role": "user", "content": [
                    {"type": "input_text", "text": "look"},
                    {"type": "input_image", "image_url": "data:image/png;base64,AAAA", "detail": "auto"},
                ]},
                {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque"},
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "On it."}]},
                {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "write", "arguments": '{"p":"a"}'},
                {"type": "function_call", "call_id": "call_2", "name": "list", "namespace": "skills", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "call_1", "output": "written"},
                {"type": "function_call_output", "call_id": "call_2", "output": [{"type": "input_text", "text": "[]"}]},
                {"role": "developer", "content": [{"type": "input_text", "text": "later note"}]},
                {"role": "assistant", "content": [{"type": "refusal", "refusal": "no"}]},
                {"type": "function_call", "call_id": "call_3", "name": "write", "arguments": '{"p": '},
                {"type": "function_call_output", "call_id": "call_3", "output": "bad"},
                {"role": "user", "content": "thanks"},
            ],
        )
    )
    assert parsed.system == "be brief\n\nhouse rules"
    assert parsed.reasoning_items == 1
    user, assistant, out1, out2, note, refusal, out3, last = parsed.messages
    assert isinstance(user, UserMessage) and isinstance(user.content, list)
    assert [type(p) for p in user.content] == [TextPart, ImagePart]
    # WP62-MGW-2: the reasoning item with encrypted content rides on the turn it preceded.
    assert assistant == with_state(
        AssistantMessage(
            content="On it.",  # pyright: ignore[reportArgumentType]
            tool_calls=[ToolCall(id="call_1", name="write", arguments={"p": "a"}), ToolCall(id="call_2", name="skills__list", arguments={})],
        ),
        OPENAI_RESPONSES,
        [{"type": "reasoning", "id": "rs_1", "encrypted_content": "opaque", "summary": []}],
    )
    assert out1 == ToolMessage(content="written", tool_call_id="call_1")
    assert out2 == ToolMessage(content="[]", tool_call_id="call_2")
    assert note == SystemMessage(content="later note")
    assert isinstance(refusal, AssistantMessage) and refusal.text == "no"
    # Malformed arguments are normalised exactly as the chat wire and the transports do.
    assert refusal.tool_calls == [normalise_tool_call("call_3", "write", '{"p": ')]
    assert out3 == ToolMessage(content="bad", tool_call_id="call_3")
    assert last == UserMessage(content="thanks")
    assert parse_responses_request(request()).messages == (UserMessage(content="hi"),)


@pytest.mark.parametrize(
    ("extra", "param"),
    [
        ({"previous_response_id": "resp_abc"}, "previous_response_id"),
        ({"conversation": "conv_1"}, "conversation"),
        ({"store": True}, "store"),
        ({"background": True}, "background"),
        ({"input": [{"type": "item_reference", "id": "msg_1"}]}, "input.item_reference"),
    ],
)
def test_rw3_stateful_requests_are_refused(extra: dict[str, Any], param: str) -> None:
    with pytest.raises(ResponseStateUnsupported) as refused:
        parse_responses_request(request(**extra))
    assert refused.value.reason_code == "response_state_unsupported" and refused.value.param == param
    assert refused.value.http_status == 400


def test_rw3_store_false_or_absent_is_served() -> None:
    assert parse_responses_request(request(store=False)).messages
    assert parse_responses_request(request(previous_response_id=None)).messages


@pytest.mark.parametrize(
    ("extra", "param"),
    [
        ({"tool_choice": "required"}, "tool_choice"),
        ({"tool_choice": {"type": "function", "name": "write"}}, "tool_choice"),
        ({"truncation": "auto"}, "truncation"),
        ({"text": {"format": {"type": "json_schema", "name": "x", "schema": {}}}}, "text.format"),
        ({"include": ["message.output_text.logprobs"]}, "include=message.output_text.logprobs"),
        ({"top_logprobs": 2}, "top_logprobs"),
        ({"prompt": {"id": "pmpt_1"}}, "prompt"),
        ({"max_tool_calls": 3}, "max_tool_calls"),
        ({"tools": [{"type": "web_search"}]}, "tools.type=web_search"),
        ({"tools": [{"type": "custom", "name": "apply_patch"}]}, "tools.type=custom"),
        ({"tools": [{"type": "local_shell"}]}, "tools.type=local_shell"),
        ({"tools": [{"type": "file_search", "vector_store_ids": []}]}, "tools.type=file_search"),
        ({"tools": [{"type": "mcp", "server_label": "x"}]}, "tools.type=mcp"),
        ({"tools": [{"type": "tool_search"}]}, "tools.type=tool_search"),
        ({"tools": [{"type": "namespace", "name": "ns", "tools": [{"type": "custom", "name": "c"}]}]}, "tools.namespace.tools.type=custom"),
        ({"input": [{"type": "custom_tool_call", "call_id": "c", "name": "apply_patch", "input": "x"}]}, "input.type=custom_tool_call"),
        ({"input": [{"type": "web_search_call", "id": "ws", "status": "completed"}]}, "input.type=web_search_call"),
        ({"input": [{"type": "local_shell_call", "call_id": "c"}]}, "input.type=local_shell_call"),
        ({"input": [{"type": "compaction", "encrypted_content": "x"}]}, "input.type=compaction"),
        ({"input": [{"role": "user", "content": [{"type": "input_file", "file_id": "file_1"}]}]}, "input.content.type=input_file"),
        ({"input": [{"role": "user", "content": [{"type": "input_image", "file_id": "file_1"}]}]}, "input.content.input_image.file_id"),
    ],
)
def test_rw4_unsupported_parameters_are_refused(extra: dict[str, Any], param: str) -> None:
    with pytest.raises(UnsupportedParameter) as refused:
        parse_responses_request(request(**extra))
    assert refused.value.param == param
    assert refused.value.reason_code == "unsupported_parameter"


@pytest.mark.parametrize(
    "body",
    [
        "not an object",
        {"input": "hi"},
        {"model": "engine-model"},
        {"model": "engine-model", "input": []},
        {"model": "engine-model", "input": ["text"]},
        request(input=[{"type": "function_call", "name": "write", "arguments": "{}"}]),
        request(input=[{"type": "function_call", "call_id": "c", "arguments": "{}"}]),
        request(input=[{"role": "tool", "content": "x"}]),
        request(tools=[{"type": "function", "name": "a__b"}, {"type": "namespace", "name": "a", "tools": [{"type": "function", "name": "b"}]}]),
        request(tools=[{"type": "namespace", "name": "n" * 40, "tools": [{"type": "function", "name": "m" * 30}]}]),
        request(tools=[{"type": "function"}]),
    ],
)
def test_rw5_invalid_requests(body: object) -> None:
    with pytest.raises(InvalidResponsesRequest) as refused:
        parse_responses_request(body)
    assert refused.value.reason_code == "invalid_responses_request"


def test_rw6_tracking_and_sampling_fields_are_dropped_and_named() -> None:
    fields = {
        "temperature": 0.2,
        "top_p": 1,
        "user": "u",
        "metadata": {"a": "b"},
        "client_metadata": {"x-codex-installation-id": "i"},
        "service_tier": "auto",
        "prompt_cache_retention": "24h",
        "safety_identifier": "s",
        "stream_options": {"include_obfuscation": False},
        "something_new": 1,
    }
    parsed = parse_responses_request(request(**fields, parallel_tool_calls=False, prompt_cache_key="k"))
    assert parsed.dropped == tuple(sorted(fields))
    # WP62-MGW-1: passed to a transport that sends them; the gateway names them dropped where it cannot.
    assert (parsed.parallel_tool_calls, parsed.cache_key) == (False, "k")


def _turn(*, stop: StopReason = StopReason.TOOL_USE, usage: TokenUsage | None = None) -> ModelResponse:
    return ModelResponse(
        message=AssistantMessage(
            content=[ThinkingPart(thinking="hmm"), TextPart(text="Writing files.")],
            tool_calls=[
                ToolCall(id="call_a", name="write", arguments={"filePath": "a.txt"}),
                ToolCall(id="call_b", name="skills__list", arguments={}),
            ],
        ),
        usage=usage or TokenUsage(input_tokens=120, output_tokens=30, cache_read_tokens=100),
        stop_reason=stop,
        model="gpt-5.4-2026-01-01",
    )


def _with_skills() -> ResponsesRequest:
    return parse_responses_request(
        request(tools=[
            {"type": "function", "name": "write", "parameters": {"type": "object"}},
            {"type": "namespace", "name": "skills", "description": "Skills", "tools": [{"type": "function", "name": "list"}]},
        ])
    )


EXPECTED_OUTPUT = [
    {"type": "reasoning", "summary": [{"type": "summary_text", "text": "hmm"}]},
    {"type": "message", "role": "assistant", "status": "completed",
     "content": [{"type": "output_text", "text": "Writing files.", "annotations": []}]},
    {"type": "function_call", "status": "completed", "call_id": "call_a", "name": "write", "arguments": '{"filePath":"a.txt"}'},
    {"type": "function_call", "status": "completed", "call_id": "call_b", "name": "list", "namespace": "skills", "arguments": "{}"},
]
EXPECTED_USAGE = {
    "input_tokens": 120,
    "input_tokens_details": {"cached_tokens": 100},
    "output_tokens": 30,
    "output_tokens_details": {"reasoning_tokens": 0},
    "total_tokens": 150,
}


def _without_ids(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in item.items() if k != "id"} for item in items]


def test_rw7_response_body() -> None:
    body = RESPONSES_WIRE.body(_with_skills(), _turn(), CALL)
    assert body["id"] == "resp_000001" and body["object"] == "response" and body["model"] == "engine-model"
    assert body["status"] == "completed" and body["store"] is False and body["error"] is None
    assert body["created_at"] == 1_700_000_000
    output: list[dict[str, Any]] = body["output"]  # type: ignore[assignment]
    assert _without_ids(output) == EXPECTED_OUTPUT
    assert len({item["id"] for item in output}) == 4
    assert body["usage"] == EXPECTED_USAGE


def test_rw7_cut_output_is_incomplete() -> None:
    body = RESPONSES_WIRE.body(_with_skills(), _turn(stop=StopReason.MAX_TOKENS), CALL)
    assert body["status"] == "incomplete"
    assert body["incomplete_details"] == {"reason": "max_output_tokens"}


def assemble(events: list[dict[str, Any]]) -> dict[str, Any]:
    """What a Responses client builds from events, checking the stream's invariants on the way:
    sequence numbers in order, one item open at a time, deltas only into the open item, every
    done item equal to what its deltas built, and the terminal response holding exactly those items."""
    assert [e["sequence_number"] for e in events] == list(range(len(events)))
    assert [e["type"] for e in events[:2]] == ["response.created", "response.in_progress"]
    open_item: dict[str, Any] | None = None
    built: dict[str, str] = {}
    done: list[dict[str, Any]] = []
    for event in events[2:-1]:
        kind = event["type"]
        if kind == "response.output_item.added":
            assert open_item is None, "an item opened before the last one closed"
            assert event["output_index"] == len(done)
            open_item = event["item"]
            built[open_item["id"]] = ""
        elif kind in ("response.output_text.delta", "response.function_call_arguments.delta",
                      "response.reasoning_summary_text.delta"):
            assert open_item is not None and event["item_id"] == open_item["id"]
            built[open_item["id"]] += event["delta"]
        elif kind == "response.output_item.done":
            assert open_item is not None and event["item"]["id"] == open_item["id"]
            item = event["item"]
            streamed = built[item["id"]]
            if item["type"] == "function_call":
                assert json.loads(streamed or "{}") == json.loads(item["arguments"])
            elif item["type"] == "message":
                assert streamed == item["content"][0]["text"]
            else:
                # A reasoning item may carry no summary (WP-62: the encrypted item after a streamed summary).
                assert streamed == "".join(part["text"] for part in item["summary"])
            done.append(item)
            open_item = None
        else:
            assert kind in {
                "response.content_part.added", "response.content_part.done", "response.output_text.done",
                "response.function_call_arguments.done", "response.reasoning_summary_part.added",
                "response.reasoning_summary_part.done", "response.reasoning_summary_text.done",
            }, kind
    assert open_item is None
    terminal = events[-1]
    assert terminal["type"] in ("response.completed", "response.incomplete")
    assert terminal["response"]["output"] == done
    return {"output": _without_ids(done), "usage": terminal["response"]["usage"], "status": terminal["response"]["status"]}


def _stream(request_: ResponsesRequest, events: list[StreamEvent], family: str = "openai") -> list[dict[str, Any]]:
    encoder = ResponseEncoder(request_, CALL)
    out = encoder.start()
    for event in events:
        out.extend(encoder.on_event(event))
    return out


def test_rw8_streamed_events_assemble_into_the_body() -> None:
    events: list[StreamEvent] = [
        TextDeltaEvent(delta="Writing "),
        TextDeltaEvent(delta="files."),
        ToolCallDeltaEvent(index=0, id="call_a", name="write", arguments_delta=""),
        ToolCallDeltaEvent(index=0, arguments_delta='{"filePath":'),
        ToolCallDeltaEvent(index=0, arguments_delta='"a.txt"}'),
        ToolCallDeltaEvent(index=1, id="call_b", name="skills__list", arguments_delta="{}"),
        StreamCompleteEvent(response=_turn()),
    ]
    assembled = assemble(_stream(_with_skills(), events, "anthropic"))
    # Reasoning reported only on the final response is sent whole after what streamed before it.
    assert assembled["output"] == [*EXPECTED_OUTPUT[1:], EXPECTED_OUTPUT[0]]
    assert assembled["output"][2]["namespace"] == "skills"
    assert assembled["usage"] == EXPECTED_USAGE and assembled["status"] == "completed"
    framed = RESPONSES_WIRE.frame(_stream(_with_skills(), events)[0])
    assert framed.startswith(b"event: response.created\ndata: {") and framed.endswith(b"\n\n")
    assert RESPONSES_WIRE.terminal == b""


FAMILY_TURN = [
    {"type": "reasoning", "summary": [{"type": "summary_text", "text": "hmm"}]},
    {"type": "message", "role": "assistant", "status": "completed",
     "content": [{"type": "output_text", "text": "Writing files.", "annotations": []}]},
    {"type": "function_call", "status": "completed", "call_id": "call_a", "name": "write", "arguments": '{"filePath":"a.txt"}'},
    {"type": "function_call", "status": "completed", "call_id": "call_b", "name": "read", "arguments": '{"filePath":"b.txt"}'},
]


def _kind(item: dict[str, Any]) -> int:
    return ["reasoning", "message", "function_call"].index(item["type"])


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_rw9_every_family_streams_the_same_turn(family: str) -> None:
    transport_family, events = FAMILIES[family]
    assembled = assemble(_stream(parse_responses_request(request()), list(events), transport_family))
    # Where reasoning lands depends on when the family reports it (streamed first, or on the final response).
    assert sorted(assembled["output"], key=_kind) == FAMILY_TURN
    assert [i["call_id"] for i in assembled["output"] if i["type"] == "function_call"] == ["call_a", "call_b"]
    assert assembled["usage"] == {**EXPECTED_USAGE, "input_tokens": 120}


def test_rw10_whole_answers_and_late_ids() -> None:
    final_only = assemble(_stream(_with_skills(), [StreamCompleteEvent(response=_turn())]))
    assert final_only["output"] == EXPECTED_OUTPUT
    late = assemble(
        _stream(
            _with_skills(),
            [
                ToolCallDeltaEvent(index=0, name="write", arguments_delta='{"filePath":'),
                ToolCallDeltaEvent(index=0, id="call_a", arguments_delta='"a.txt"}'),
                StreamCompleteEvent(response=_turn()),
            ],
        )
    )
    calls = [i for i in late["output"] if i["type"] == "function_call"]
    assert [(c["call_id"], c["name"]) for c in calls] == [("call_a", "write"), ("call_b", "list")]


def test_rw10_the_final_arguments_win_over_different_streamed_ones() -> None:
    events: list[StreamEvent] = [
        ToolCallDeltaEvent(index=0, id="call_a", name="write", arguments_delta='{"filePath": "a.txt"}'),
        StreamCompleteEvent(response=ModelResponse(message=AssistantMessage(
            tool_calls=[ToolCall(id="call_a", name="write", arguments={"filePath": "b.txt"})]))),
    ]
    out = _stream(parse_responses_request(request()), events)
    done = [e for e in out if e["type"] == "response.output_item.done"]
    assert json.loads(done[0]["item"]["arguments"]) == {"filePath": "b.txt"}


def test_rw11_a_provider_failure_after_the_start_fails_the_response() -> None:
    encoder = ResponseEncoder(parse_responses_request(request()), CALL)
    events = [*encoder.start(), *encoder.on_event(TextDeltaEvent(delta="par"))]
    events += encoder.failed("provider_unavailable", "The provider is unavailable")
    assert events[-1]["type"] == "response.failed"
    failed = events[-1]["response"]
    assert failed["status"] == "failed"
    assert failed["error"] == {"code": "provider_unavailable", "message": "The provider is unavailable"}
    assert not any(e["type"] == "response.completed" for e in events)


@pytest.mark.parametrize(
    ("code", "wire_code"),
    [
        ("rate_limited", "rate_limit_exceeded"),
        ("provider_quota", "insufficient_quota"),
        ("budget_exhausted", "insufficient_quota"),
        ("budget_insufficient", "insufficient_quota"),
        ("context_length_exceeded", "context_length_exceeded"),
        ("provider_unavailable", "provider_unavailable"),
    ],
)
def test_rw11_failure_codes_in_openais_vocabulary(code: str, wire_code: str) -> None:
    encoder = ResponseEncoder(parse_responses_request(request()), CALL)
    assert encoder.failed(code, "m")[-1]["response"]["error"]["code"] == wire_code


@pytest.mark.parametrize("code", ["account_not_owned", "budget_exhausted", "route_not_allowed"])
def test_rw12_a_refusal_names_its_code_in_the_message_codex_keeps(code: str) -> None:
    """DEEPROUTE2: Codex keeps only the status and the body's message of a refused call (RL-3 on the real Codex
    lane failed ``http_403``), so the gateway's code travels in the message, as it does for Claude Code (E9 FB-3)."""
    body, headers = RESPONSES_WIRE.error(403, code, "the pinned key belongs to another user")
    error = body["error"]  # pyright: ignore[reportIndexIssue, reportOptionalSubscript, reportArgumentType, reportCallIssue]
    assert error["code"] == code  # type: ignore[index]
    assert error["message"] == f"the pinned key belongs to another user [gateway:{code}]"  # type: ignore[index]
    assert headers == {}
