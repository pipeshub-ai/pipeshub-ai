"""`run_usage` frame payload and the auto-compact usage callback."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from app.agent_loop_lib.core.responses import RunUsage, TokenUsage
from app.agent_loop_lib.core.types import UserMessage
from app.agent_loop_lib.hooks.middleware.builtin.auto_compact import make_llm_summarizer
from app.agents.agent_loop.protocol.formatter import AGUI_FORMATTER
from app.agents.agent_loop.protocol.run_usage import (
    MAX_CALLS_PER_FRAME,
    RunUsagePayload,
    UsageLedger,
    emit_run_usage,
    grep_generation_usage_reporter,
)
from tests.unit.agents.adapter.conftest import make_context


def _usage(requests: int, inp: int, out: int, cache_read: int = 0, cache_write: int = 0) -> RunUsage:
    usage = RunUsage()
    for _ in range(requests):
        usage.add(TokenUsage(
            input_tokens=inp, output_tokens=out, cache_read_tokens=cache_read, cache_write_tokens=cache_write,
        ))
    return usage


def _ledger(*entries: tuple[str, TokenUsage]) -> UsageLedger:
    ledger = UsageLedger()
    for source, usage in entries:
        ledger.add(source, usage)
    return ledger


def test_payload_sums_the_main_loop_and_every_other_call() -> None:
    ledger = _ledger(
        ("auto_compact", TokenUsage(input_tokens=500, output_tokens=50)),
        ("sub_agent:researcher", TokenUsage(input_tokens=60, output_tokens=6, cache_read_tokens=30)),
        ("grep_generation", TokenUsage(input_tokens=9, output_tokens=1)),
    )

    payload = RunUsagePayload.from_usage(_usage(3, 100, 10, 40, 5), ledger, model="m")

    assert payload.llmCalls == 6
    assert payload.auxiliaryLlmCalls == 3
    assert payload.inputTokens == 300 + 500 + 60 + 9
    assert payload.outputTokens == 30 + 50 + 6 + 1
    assert payload.cacheReadTokens == 120 + 30
    assert payload.cacheWriteTokens == 15
    assert payload.calls == [[100, 10, 40, 5]] * 3 + [[500, 50, 0, 0], [60, 6, 30, 0], [9, 1, 0, 0]]
    assert payload.callSources == [
        "main", "main", "main", "auto_compact", "sub_agent:researcher", "grep_generation",
    ]


def test_a_frame_within_the_call_cap_carries_no_truncated_key() -> None:
    payload = RunUsagePayload.from_usage(_usage(2, 1, 1), UsageLedger(), model="m")
    assert "truncated" not in payload.to_wire_dict()


def test_the_per_call_breakdown_is_capped_but_the_totals_are_not() -> None:
    loop_calls = MAX_CALLS_PER_FRAME + 4
    ledger = _ledger(("auto_compact", TokenUsage(input_tokens=500, output_tokens=50)))
    payload = RunUsagePayload.from_usage(_usage(loop_calls, 10, 1), ledger, model="m")
    assert len(payload.calls) == len(payload.callSources) == MAX_CALLS_PER_FRAME
    assert payload.truncated is True
    assert payload.llmCalls == loop_calls + 1
    assert payload.inputTokens == loop_calls * 10 + 500


def test_agui_frame_is_custom_run_usage() -> None:
    context = make_context(protocol="agui", run_id="run-1")
    [frame] = AGUI_FORMATTER.run_usage(context, payload=RunUsagePayload(llmCalls=2, inputTokens=5))
    assert frame["data"]["type"] == "CUSTOM"
    assert frame["data"]["name"] == "run_usage"
    assert frame["data"]["value"]["inputTokens"] == 5


@pytest.mark.asyncio
async def test_summarizer_reports_usage() -> None:
    seen: list[TokenUsage] = []

    class _Transport:
        async def complete(self, **_: Any) -> Any:  # noqa: ANN401
            return SimpleNamespace(
                message=SimpleNamespace(text="summary"),
                usage=TokenUsage(input_tokens=7, output_tokens=3),
            )

    registry = SimpleNamespace(resolve=lambda _provider: _Transport())
    summarize = make_llm_summarizer(registry, "langchain", "m", on_usage=seen.append)
    assert await summarize([UserMessage(content="hi")]) == "summary"
    assert seen == [TokenUsage(input_tokens=7, output_tokens=3)]


@pytest.mark.asyncio
async def test_a_failing_usage_callback_keeps_the_summary() -> None:
    class _Transport:
        async def complete(self, **_: Any) -> Any:  # noqa: ANN401
            return SimpleNamespace(
                message=SimpleNamespace(text="the model's summary"),
                usage=TokenUsage(input_tokens=7, output_tokens=3),
            )

    def broken_callback(_usage: TokenUsage) -> None:
        raise RuntimeError("telemetry bug")

    registry = SimpleNamespace(resolve=lambda _provider: _Transport())
    summarize = make_llm_summarizer(registry, "langchain", "m", on_usage=broken_callback)
    assert await summarize([UserMessage(content="hi")]) == "the model's summary"


class TestLoopOutcomeFieldsAreValidatedOnAssignment:
    """`emit_run_usage` sets the loop-outcome fields after construction.
    Pydantic does not validate assignment by default, so `payload.turns =
    result.turns` — a `list[AgentTurn]`, not a count — serialized as `[]`
    and only blew up in the consumer, mid-run, after 200 questions had
    already been paid for."""

    def test_assigning_a_list_to_turns_is_rejected(self) -> None:
        payload = RunUsagePayload()
        with pytest.raises(ValidationError):
            payload.turns = []  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_emit_run_usage_sends_turn_count() -> None:
    written: list[dict[str, Any]] = []

    class _Sink:
        async def write(self, event: dict[str, Any]) -> None:
            written.append(event)

    context = make_context(protocol="agui", run_id="run-1")
    context.include_retrieval_context = True
    context.event_sink = _Sink()
    context.completion_gate_nudges = 2

    agent = SimpleNamespace(usage=_usage(2, 10, 1), spec=SimpleNamespace(max_turns=15))
    result = SimpleNamespace(turns=[object(), object(), object()], error=None)

    await emit_run_usage(context, agent, result, model="m")

    [frame] = written
    value = frame["data"]["value"]
    assert value["turns"] == 3
    assert value["maxTurns"] == 15
    assert value["completionGateNudges"] == 2
    assert value["llmCalls"] == 2


@pytest.mark.asyncio
async def test_emit_run_usage_never_raises_onto_the_answer_path() -> None:
    """`emit_run_usage` runs immediately before `AnswerFinalizer`. A telemetry
    bug must cost the frame, not the user's answer."""
    written: list[dict[str, Any]] = []

    class _Sink:
        async def write(self, event: dict[str, Any]) -> None:
            written.append(event)

    context = make_context(protocol="agui", run_id="run-1")
    context.include_retrieval_context = True
    context.event_sink = _Sink()

    # `usage` is the wrong type entirely — `from_usage` will blow up inside.
    agent = SimpleNamespace(usage="not-a-RunUsage", spec=SimpleNamespace(max_turns=15))
    result = SimpleNamespace(turns=[], error=None)

    await emit_run_usage(context, agent, result, model="m")

    assert written == []


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


@pytest.mark.asyncio
async def test_a_failing_stream_write_never_reaches_the_answer_path() -> None:
    """A client gone mid-run makes the sink raise; that must cost the frame only."""

    class _BrokenSink:
        async def write(self, _event: dict[str, Any]) -> None:
            raise ConnectionResetError("client went away")

    context = make_context(protocol="agui", run_id="run-1")
    context.include_retrieval_context = True
    context.event_sink = _BrokenSink()
    agent = SimpleNamespace(usage=_usage(1, 10, 1), spec=SimpleNamespace(max_turns=15))

    await emit_run_usage(context, agent, SimpleNamespace(turns=[], error=None), model="m")
