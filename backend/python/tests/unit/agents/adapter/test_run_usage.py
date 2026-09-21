"""`run_usage` frame payload, the auto-compact usage callback, and the
benchmark env knobs on the agent factory."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.agent_loop_lib.core.responses import RunUsage, TokenUsage
from app.agent_loop_lib.core.types import UserMessage
from app.agent_loop_lib.hooks.middleware.builtin.auto_compact import make_llm_summarizer
from app.agents.agent_loop.protocol.formatter import AGUI_FORMATTER
from app.agents.agent_loop.protocol.run_usage import RunUsagePayload, emit_run_usage
from tests.unit.agents.adapter.conftest import make_context


def _usage(requests: int, inp: int, out: int, cache_read: int = 0) -> RunUsage:
    usage = RunUsage()
    for _ in range(requests):
        usage.add(TokenUsage(input_tokens=inp, output_tokens=out, cache_read_tokens=cache_read))
    return usage


def test_payload_sums_loop_and_auxiliary_usage() -> None:
    payload = RunUsagePayload.from_usage(_usage(3, 100, 10, 40), _usage(1, 500, 50), model="m")
    assert payload.llmCalls == 4
    assert payload.inputTokens == 800
    assert payload.outputTokens == 80
    assert payload.cacheReadTokens == 120
    assert payload.auxiliaryLlmCalls == 1
    assert payload.calls == [[100, 10, 40]] * 3 + [[500, 50, 0]]


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

    def test_turns_is_a_count_not_the_turn_list(self) -> None:
        from app.agents.agent_loop.protocol.run_usage import emit_run_usage
        assert "len(turns)" in inspect.getsource(emit_run_usage)


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
