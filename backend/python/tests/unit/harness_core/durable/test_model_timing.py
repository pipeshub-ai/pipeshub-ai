"""P4.10 MT-1..MT-4: each model call's progress (first-event latency, longest gap, duration, events) is measured
on an injected clock and logged on ``model.response``, so a slow step explains itself; nothing journaled changes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from harness_core.kernel.core.messages import AssistantMessage
from harness_core.kernel.core.responses import ModelResponse, StopReason, TokenUsage
from harness_core.kernel.core.streaming import StreamCompleteEvent, TextDeltaEvent
from harness_core.kernel.durable.adapters import StreamingModelPort, TransportModelPort
from harness_core.kernel.durable.ports import ModelCall
from harness_core.models.stream_watch import CallClock, CallTiming, StreamTimeouts
from harness_core.models.transport_model import TransportModel

from .support import World
from .test_model_stall import ANSWER, STALL, StallingTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest

    from harness_core.kernel.core.streaming import StreamEvent

FAST = StreamTimeouts(first_event_s=0.2, idle_s=0.05, response_s=0.2)


class _Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def _response(text: str = "Hello") -> ModelResponse:
    return ModelResponse(
        message=AssistantMessage(content=text),
        usage=TokenUsage(input_tokens=10, output_tokens=2, cache_read_tokens=8, reasoning_tokens=1),
        stop_reason=StopReason.END_TURN,
        model="fake-1",
    )


class _PacedModel:
    """Streams after the given silences (seconds before each event, the complete event last)."""

    model_name = "fake-1"
    accepts_cache_marks = False

    def __init__(self, clock: _Clock, silences: list[float]) -> None:
        self._clock = clock
        self._silences = silences

    def stream(self, *_: Any, **__: Any) -> AsyncIterator[StreamEvent]:  # noqa: ANN401
        return self._play()

    async def _play(self) -> AsyncIterator[StreamEvent]:
        *deltas, last = self._silences
        for i, silence in enumerate(deltas):
            self._clock.t += silence
            yield TextDeltaEvent(delta=f"d{i}")
        self._clock.t += last
        yield StreamCompleteEvent(response=_response())

    async def complete(self, *_: Any, **__: Any) -> ModelResponse:  # noqa: ANN401
        self._clock.t += 7.5
        return _response()


async def _ignore(_kind: str, _text: str) -> None:
    return None


def _call() -> ModelCall:
    return ModelCall(messages=[], tools=None, system="s", slots={}, input_index=None)


def test_mt1_the_clock_times_a_call_from_its_start() -> None:
    clock = _Clock()
    watch = CallClock(clock)
    for silence in (3.0, 0.5, 4.0, 0.25):
        clock.t += silence
        watch.event()
    clock.t += 1.0
    assert watch.timing() == CallTiming(first_event_s=3.0, max_gap_s=4.0, duration_s=8.75, events=4)


def test_mt1_a_call_with_no_event_waited_its_whole_duration_for_the_first() -> None:
    clock = _Clock()
    watch = CallClock(clock)
    clock.t += 2.0
    assert watch.timing() == CallTiming(first_event_s=2.0, max_gap_s=0.0, duration_s=2.0, events=0)


async def test_mt1_a_streamed_sample_carries_its_timing() -> None:
    clock = _Clock()
    port = StreamingModelPort(
        _PacedModel(clock, [12.0, 0.5, 30.0, 0.25]),  # type: ignore[arg-type]
        provider="fake",
        transport="direct",
        clock=clock,
    )
    sampled = await port.sample(_call(), _ignore)
    assert sampled.timing == CallTiming(first_event_s=12.0, max_gap_s=30.0, duration_s=42.75, events=4)


async def test_mt2_a_non_streamed_sample_is_one_event_at_its_end() -> None:
    clock = _Clock()
    port = TransportModelPort(
        _PacedModel(clock, []),  # type: ignore[arg-type]
        provider="fake",
        transport="direct",
        clock=clock,
    )
    sampled = await port.sample(_call(), _ignore)
    assert sampled.timing == CallTiming(first_event_s=7.5, max_gap_s=0.0, duration_s=7.5, events=1)


def _response_lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [
        r.harness  # type: ignore[attr-defined]
        for r in caplog.records
        if getattr(r, "harness_event", None) == "model.response"
    ]


async def test_mt3_mt5_the_response_line_carries_the_timing_and_the_event_does_not(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    clock = _Clock()
    port = StreamingModelPort(
        _PacedModel(clock, [2.0, 0.5, 9.0]),  # type: ignore[arg-type]
        provider="fake",
        transport="direct",
        clock=clock,
    )
    world = World()
    world.admit()
    done = await world.run(world.kernel(port))
    assert done.status == "completed"
    (line,) = _response_lines(caplog)
    assert {k: line[k] for k in ("firstEventMs", "maxGapMs", "durationMs", "events")} == {
        "firstEventMs": 2000,
        "maxGapMs": 9000,
        "durationMs": 11500,
        "events": 3,
    }
    assert (line["inputTokens"], line["cacheReadTokens"]) == (10, 8), "P4.11 LG-1: the call's cache reads"
    assert line["reasoningTokens"] == 1, "P4.17L RT-5: the call's reasoning share"
    (event,) = world.events("model.response")
    assert not {"timing", "firstEventMs"} & set(event.data)


async def test_mt4_after_a_stall_the_line_times_the_attempt_that_answered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    clock = _Clock()

    def stalled() -> None:
        clock.t += 500.0

    transport = StallingTransport(STALL, ANSWER, hook=stalled)
    port = StreamingModelPort(TransportModel(transport, timeouts=FAST), provider="fake", transport="direct", clock=clock)

    async def no_sleep(_seconds: float) -> None:
        return None

    world = World()
    world.admit()
    done = await world.run(world.kernel(port, sleep=no_sleep))  # type: ignore[arg-type]
    assert done.status == "completed"
    (line,) = _response_lines(caplog)
    assert line["modelAttempt"] == 2
    assert line["durationMs"] < 500_000
    assert line["events"] == 3
