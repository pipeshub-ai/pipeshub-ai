"""G-49 / GA-26: the VCR gateway. A cassette recorded from a scripted transport replays the same calls with no
provider, refuses a call that differs from the recording, and replays through the real model gateway with the same
answer and the same metered usage."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from harness_core.access.types import ENGINE_MODEL_ID
from harness_core.gateway.model_gateway import ChatCompletion, ChatStream
from harness_core.kernel.core.exceptions import TransportError
from harness_core.kernel.core.messages import (
    AssistantMessage,
    TextPart,
    ToolCall,
    UserMessage,
)
from harness_core.kernel.core.responses import ModelResponse, StopReason, TokenUsage
from harness_core.kernel.core.streaming import StreamCompleteEvent, TextDeltaEvent
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.testing.cassette import (
    Cassette,
    CassetteExhausted,
    CassetteMismatch,
    CassetteTransport,
)
from harness_core.testing.model_gateway import (
    GatewayRig,
    RecordedTransport,
    body_of,
    reply_events,
)
from harness_core.testing.replay import SecretLeftBehind

if TYPE_CHECKING:
    from pathlib import Path

TOOLS = [ToolSchema(name="lookup", description="d", input_schema={"type": "object"})]


def _stream_events() -> list[Any]:
    return [TextDeltaEvent(delta="hel"), TextDeltaEvent(delta="lo"), reply_events("hello")]


async def _drain(transport: CassetteTransport, messages: list[Any]) -> list[Any]:
    return [e async for e in transport.stream(messages, tools=TOOLS, system="today is 2026-10-27")]


async def test_g49_a_recorded_stream_replays_event_for_event(tmp_path: Path) -> None:
    recorder = CassetteTransport.record(RecordedTransport(_stream_events()), name="unit")
    asked = [UserMessage(content="hi")]
    live = await _drain(recorder, asked)
    path = tmp_path / "unit.json"
    recorder.cassette().save(path)

    replay = CassetteTransport.replay(Cassette.load(path))
    # The system prompt carries the date: a replay on another day still matches.
    replayed = [e async for e in replay.stream(asked, tools=TOOLS, system="today is 2027-01-01")]
    assert replayed == live
    replay.assert_played()
    assert replay.provider == "openai" and Cassette.load(path).manifest.source == "scripted"


async def test_g49_a_call_that_differs_from_the_recording_names_the_call() -> None:
    recorder = CassetteTransport.record(RecordedTransport(_stream_events()), name="unit")
    _ = await _drain(recorder, [UserMessage(content="hi")])
    replay = CassetteTransport.replay(recorder.cassette())
    with pytest.raises(CassetteMismatch, match="model call 1 differs"):
        _ = await _drain(replay, [UserMessage(content="hi there")])


async def test_g49_one_call_more_than_recorded_is_refused_and_one_fewer_is_reported() -> None:
    recorder = CassetteTransport.record(RecordedTransport(_stream_events()), name="unit")
    _ = await recorder.complete([UserMessage(content="hi")], tools=TOOLS)
    replay = CassetteTransport.replay(recorder.cassette())
    with pytest.raises(CassetteMismatch, match="made 0 model calls"):
        replay.assert_played()
    _ = await replay.complete([UserMessage(content="hi")], tools=TOOLS)
    with pytest.raises(CassetteExhausted):
        _ = await replay.complete([UserMessage(content="hi")], tools=TOOLS)


async def test_g49_a_provider_failure_mid_stream_replays_as_the_same_error() -> None:
    inner = RecordedTransport(_stream_events(), fail=TransportError("overloaded", status_code=529, retryable=True), fail_after=2)
    recorder = CassetteTransport.record(inner, name="unit")
    with pytest.raises(TransportError):
        _ = await _drain(recorder, [UserMessage(content="hi")])
    replay = CassetteTransport.replay(recorder.cassette())
    seen: list[Any] = []
    with pytest.raises(TransportError) as raised:
        async for event in replay.stream([UserMessage(content="hi")], tools=TOOLS):
            seen.append(event)  # noqa: PERF401 - what streamed before the failure is the point
    assert [e.delta for e in seen] == ["hel", "lo"]
    assert raised.value.status_code == 529


async def test_g49_tool_calls_and_tool_results_are_part_of_the_match() -> None:
    call = ToolCall(id="c1", name="lookup", arguments={"q": "atlas"})
    response = ModelResponse(
        message=AssistantMessage(content=[TextPart(text="")], tool_calls=[call]),
        usage=TokenUsage(input_tokens=3, output_tokens=2),
        stop_reason=StopReason.TOOL_USE,
    )
    recorder = CassetteTransport.record(RecordedTransport([StreamCompleteEvent(response=response)]), name="unit")
    history = [UserMessage(content="find atlas"), AssistantMessage(content=[], tool_calls=[call])]
    _ = await recorder.complete(history, tools=TOOLS)
    replay = CassetteTransport.replay(recorder.cassette())
    other = [UserMessage(content="find atlas"), AssistantMessage(content=[], tool_calls=[call.model_copy(update={"arguments": {"q": "x"}})])]
    with pytest.raises(CassetteMismatch):
        _ = await replay.complete(other, tools=TOOLS)


def test_g49_a_cassette_holding_a_credential_is_never_written(tmp_path: Path) -> None:
    leaked = reply_events("use sk-ant-api03-" + "A" * 40)
    cassette = Cassette.model_validate(
        {
            "manifest": {"name": "leak", "source": "provider", "provider": "anthropic"},
            "interactions": [
                {"request": {"call": "complete", "digest": "d", "message_count": 1}, "events": [leaked.model_dump(mode="json")]}
            ],
        }
    )
    with pytest.raises(SecretLeftBehind):
        cassette.save(tmp_path / "leak.json")
    assert not (tmp_path / "leak.json").exists()


def test_g49_a_fixture_address_is_not_a_secret(tmp_path: Path) -> None:
    cassette = Cassette.model_validate(
        {
            "manifest": {"name": "ok", "source": "scripted", "provider": "scripted"},
            "interactions": [
                {"request": {"call": "complete", "digest": "d", "message_count": 1, "last_user": "mail team@corp.example"}}
            ],
        }
    )
    cassette.save(tmp_path / "ok.json")


async def _gateway_call(transport: CassetteTransport, *, stream: bool) -> tuple[Any, int]:
    rig = GatewayRig()
    binding = rig.pin(transport=transport)
    body = {"model": ENGINE_MODEL_ID, "messages": [{"role": "user", "content": "hi"}], "stream": stream}
    if stream:
        body["stream_options"] = {"include_usage": True}
    outcome = await rig.gateway.chat(rig.token(binding), {}, body_of(body))
    if isinstance(outcome, ChatStream):
        chunks = [c async for c in outcome.chunks]
        answer: Any = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks if c["choices"])
    else:
        assert isinstance(outcome, ChatCompletion)
        answer = outcome.body["choices"][0]["message"]["content"]  # type: ignore[index]
    committed = (await rig.ledger.remaining(_account())).committed
    return answer, committed.tokens


def _account() -> Any:  # noqa: ANN401
    from harness_core.budget.types import RunAccountRef

    return RunAccountRef("org-1", "run-1")


@pytest.mark.parametrize("stream", [False, True])
async def test_g49_through_the_model_gateway_a_replay_answers_and_meters_as_the_recording(stream: bool) -> None:
    """The VCR sits at the gateway's provider transport: token, grant, budget and metering are the gateway's own."""
    recorder = CassetteTransport.record(RecordedTransport(_stream_events()), name="gw")
    recorded = await _gateway_call(recorder, stream=stream)
    replayed = await _gateway_call(CassetteTransport.replay(recorder.cassette()), stream=stream)
    assert replayed == recorded and recorded[0] == "hello" and recorded[1] > 0
