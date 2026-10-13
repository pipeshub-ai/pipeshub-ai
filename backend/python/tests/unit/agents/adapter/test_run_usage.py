"""`run_usage` frame payload, the request `UsageLedger`, and `emit_run_usage`."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from app.agent_loop_lib.core.responses import TokenUsage
from app.agents.agent_loop.protocol.formatter import AGUI_FORMATTER
from app.agents.agent_loop.protocol.run_usage import (
    MAX_CALLS_PER_FRAME,
    RunUsagePayload,
    UsageLedger,
    emit_run_usage,
    grep_generation_usage_reporter,
)
from tests.unit.agents.adapter.conftest import make_context


def _ledger(*entries: tuple[str, TokenUsage]) -> UsageLedger:
    ledger = UsageLedger()
    for source, usage in entries:
        ledger.add(source, usage)
    return ledger


def _main_calls(count: int, inp: int, out: int) -> list[tuple[str, TokenUsage]]:
    return [("main", TokenUsage(input_tokens=inp, output_tokens=out))] * count


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def write(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def _opted_in_context() -> Any:  # noqa: ANN401
    context = make_context(protocol="agui", run_id="run-1")
    context.include_retrieval_context = True
    context.event_sink = _Sink()
    return context


def test_payload_sums_every_call_and_labels_each() -> None:
    ledger = _ledger(
        ("single_shot:intent.parse_intent_and_route", TokenUsage(input_tokens=900, output_tokens=40)),
        ("main", TokenUsage(input_tokens=100, output_tokens=10, cache_read_tokens=40, cache_write_tokens=5)),
        ("sub_agent:researcher", TokenUsage(input_tokens=60, output_tokens=6, cache_read_tokens=30)),
        ("grep_generation", TokenUsage(input_tokens=9, output_tokens=1)),
        ("main", TokenUsage(input_tokens=120, output_tokens=12)),
    )

    payload = RunUsagePayload.from_ledger(ledger, model="m")

    assert payload.llmCalls == 5
    assert payload.auxiliaryLlmCalls == 3
    assert payload.inputTokens == 900 + 100 + 60 + 9 + 120
    assert payload.outputTokens == 40 + 10 + 6 + 1 + 12
    assert payload.cacheReadTokens == 70
    assert payload.cacheWriteTokens == 5
    assert payload.calls[1] == [100, 10, 40, 5]
    assert payload.callSources == [
        "single_shot:intent.parse_intent_and_route", "main", "sub_agent:researcher", "grep_generation", "main",
    ]


def test_a_frame_within_the_call_cap_carries_no_truncated_key() -> None:
    payload = RunUsagePayload.from_ledger(_ledger(*_main_calls(2, 1, 1)), model="m")
    assert "truncated" not in payload.to_wire_dict()


def test_the_per_call_breakdown_is_capped_but_the_totals_are_not() -> None:
    calls = MAX_CALLS_PER_FRAME + 5
    payload = RunUsagePayload.from_ledger(_ledger(*_main_calls(calls, 10, 1)), model="m")
    assert len(payload.calls) == len(payload.callSources) == MAX_CALLS_PER_FRAME
    assert payload.truncated is True
    assert payload.llmCalls == calls
    assert payload.inputTokens == calls * 10


def test_agui_frame_is_custom_run_usage() -> None:
    context = make_context(protocol="agui", run_id="run-1")
    [frame] = AGUI_FORMATTER.run_usage(context, payload=RunUsagePayload(llmCalls=2, inputTokens=5))
    assert frame["data"]["type"] == "CUSTOM"
    assert frame["data"]["name"] == "run_usage"
    assert frame["data"]["value"]["inputTokens"] == 5


class TestLoopOutcomeFieldsAreValidatedOnAssignment:
    """`emit_run_usage` sets the loop-outcome fields after construction.
    Without `validate_assignment` a wrong type (the turn LIST, not its count)
    went out on the wire and failed only in the consumer, after the run had
    already been paid for."""

    def test_assigning_a_list_to_turns_is_rejected(self) -> None:
        payload = RunUsagePayload()
        with pytest.raises(ValidationError):
            payload.turns = []  # type: ignore[assignment]


class TestEmitRunUsage:
    async def test_sends_the_ledger_with_the_main_loop_outcome(self) -> None:
        context = _opted_in_context()
        context.completion_gate_nudges = 2
        context.usage_ledger.add("main", TokenUsage(input_tokens=10, output_tokens=1))
        context.usage_ledger.add("main", TokenUsage(input_tokens=10, output_tokens=1))
        agent = SimpleNamespace(spec=SimpleNamespace(max_turns=15))
        result = SimpleNamespace(turns=[object(), object(), object()], error=None)

        await emit_run_usage(context, agent, result, model="m")

        [frame] = context.event_sink.events
        value = frame["data"]["value"]
        assert value["turns"] == 3
        assert value["maxTurns"] == 15
        assert value["completionGateNudges"] == 2
        assert value["llmCalls"] == 2

    async def test_a_turn_that_ended_before_the_loop_still_reports_its_calls(self) -> None:
        """A clarifying question or a failure before the loop has no result."""
        context = _opted_in_context()
        context.usage_ledger.add("single_shot:intent.parse_intent_and_route", TokenUsage(input_tokens=900))

        await emit_run_usage(context, None, model="m")

        [frame] = context.event_sink.events
        value = frame["data"]["value"]
        assert value["callSources"] == ["single_shot:intent.parse_intent_and_route"]
        assert "turns" not in value
        assert "maxTurns" not in value

    async def test_only_the_first_call_sends_a_frame(self) -> None:
        """Sent from every exit of a turn; a failure after the success-path
        frame (the finalizer raising) must not send a second one."""
        context = _opted_in_context()

        await emit_run_usage(context, None, model="m")
        await emit_run_usage(context, None, model="m")

        assert len(context.event_sink.events) == 1

    async def test_nothing_is_sent_unless_the_request_opted_in(self) -> None:
        context = make_context(protocol="agui", run_id="run-1")
        context.event_sink = _Sink()

        await emit_run_usage(context, None, model="m")

        assert context.event_sink.events == []

    async def test_a_fault_never_reaches_the_answer_path(self) -> None:
        """`emit_run_usage` runs immediately before `AnswerFinalizer`."""
        context = _opted_in_context()
        context.usage_ledger.add("main", "not a TokenUsage")  # type: ignore[arg-type]

        await emit_run_usage(context, None, model="m")

        assert context.event_sink.events == []

    async def test_a_failing_stream_write_never_reaches_the_answer_path(self) -> None:
        """A client gone mid-run makes the sink raise; that must cost the frame only."""

        class _BrokenSink:
            async def write(self, _event: dict[str, Any]) -> None:
                raise ConnectionResetError("client went away")

        context = _opted_in_context()
        context.event_sink = _BrokenSink()

        await emit_run_usage(context, None, model="m")


class TestGrepGenerationUsage:
    def test_reports_into_the_request_ledger(self) -> None:
        ledger = UsageLedger()
        report = grep_generation_usage_reporter({"usage_ledger": ledger})
        assert report is not None

        report(AIMessage(content="", usage_metadata={
            "input_tokens": 40, "output_tokens": 12, "total_tokens": 52,
        }))

        assert ledger.entries == [("grep_generation", TokenUsage(input_tokens=40, output_tokens=12))]

    def test_nothing_is_wired_unless_the_request_opted_in(self) -> None:
        assert grep_generation_usage_reporter({"usage_ledger": None}) is None
        assert grep_generation_usage_reporter({}) is None
        assert grep_generation_usage_reporter(None) is None
