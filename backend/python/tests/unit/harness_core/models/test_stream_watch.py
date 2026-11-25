"""P4.9 ST-1..ST-5: a model call is bounded by its progress, not its length.

Every test runs on the virtual-time loop: the bounds are deadlines on the loop's clock, and on a real loop a busy
host's scheduling delay landed inside a 0.1 s window (ST-2 failed once under a full ``-n 4`` run). Here the clock
reads exactly the gaps the stream slept, so each case is decided by the gaps, never by the host."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from harness_core.kernel.core.exceptions import TransportError
from harness_core.kernel.core.messages import AssistantMessage
from harness_core.kernel.core.responses import ModelResponse
from harness_core.kernel.core.streaming import StreamCompleteEvent, TextDeltaEvent
from harness_core.models.policy import classify_failure
from harness_core.models.stream_watch import (
    ModelStalled,
    StreamTimeouts,
    bounded,
    watched,
)
from harness_core.testing.virtual_time import on_virtual_time

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.kernel.core.streaming import StreamEvent

T = StreamTimeouts(first_event_s=0.3, idle_s=0.1, response_s=0.2)


class FakeStream:
    """Yields ``deltas`` after the given gaps (seconds), then completes; ``stall_after`` stops there forever."""

    def __init__(self, gaps: list[float], *, stall_after: int | None = None) -> None:
        self.gaps = gaps
        self.stall_after = stall_after
        self.closed = False

    async def events(self) -> AsyncIterator[StreamEvent]:
        try:
            for i, gap in enumerate(self.gaps):
                if self.stall_after is not None and i == self.stall_after:
                    await asyncio.Event().wait()
                await asyncio.sleep(gap)
                yield TextDeltaEvent(delta=str(i))
            if self.stall_after is not None and self.stall_after >= len(self.gaps):
                await asyncio.Event().wait()
            yield StreamCompleteEvent(response=ModelResponse(message=AssistantMessage(content="done")))
        finally:
            self.closed = True


async def _drain(fake: FakeStream) -> list[StreamEvent]:
    return [event async for event in watched(fake.events(), T)]


@on_virtual_time
async def test_st1_a_stream_that_never_starts_stalls_on_its_first_event() -> None:
    fake = FakeStream([], stall_after=0)
    with pytest.raises(ModelStalled) as caught:
        _ = await _drain(fake)
    assert (caught.value.phase, caught.value.events, caught.value.retryable) == ("first_event", 0, True)
    assert isinstance(caught.value, TransportError) and caught.value.status_code is None
    assert classify_failure(caught.value) == "timeout"
    assert fake.closed, "the provider stream is closed"


@on_virtual_time
async def test_st2_a_trickle_is_never_cut_however_long_it_runs() -> None:
    gaps = [0.06] * 12  # each gap 0.6 of idle; the whole stream runs 0.72 s, over first_event_s and 7 x idle_s
    events = await _drain(FakeStream(gaps))
    assert [e.delta for e in events if isinstance(e, TextDeltaEvent)] == [str(i) for i in range(12)]
    assert isinstance(events[-1], StreamCompleteEvent)


@on_virtual_time
async def test_st3_a_stall_after_partial_output_is_not_retryable_in_place() -> None:
    fake = FakeStream([0.0, 0.0, 0.0], stall_after=2)
    seen: list[StreamEvent] = []
    with pytest.raises(ModelStalled) as caught:
        async for event in watched(fake.events(), T):
            seen.append(event)  # noqa: PERF401 - a comprehension loses the events before the stall
    assert len(seen) == 2
    assert (caught.value.phase, caught.value.events, caught.value.retryable) == ("idle", 2, False)
    assert classify_failure(caught.value) == "timeout", "the kernel still retries it as a new attempt"
    assert fake.closed


@on_virtual_time
async def test_st4_a_long_silence_before_the_first_event_is_not_a_stall() -> None:
    # Hidden reasoning: 0.25 s of nothing (2.5 x idle_s, under first_event_s), then fast deltas.
    events = await _drain(FakeStream([0.25, 0.01, 0.01]))
    assert isinstance(events[-1], StreamCompleteEvent) and len(events) == 4


@on_virtual_time
async def test_st5_a_non_streamed_call_is_bounded_on_the_whole_response() -> None:
    with pytest.raises(ModelStalled) as caught:
        _ = await bounded(asyncio.Event().wait(), T)
    assert (caught.value.phase, caught.value.retryable) == ("response", True)
    assert await bounded(asyncio.sleep(0.01, result="ok"), T) == "ok"


@on_virtual_time
async def test_a_providers_own_timeout_passes_through_unchanged() -> None:
    async def provider_timeout() -> None:
        raise TimeoutError("read timed out")

    with pytest.raises(TimeoutError) as caught:
        _ = await bounded(provider_timeout(), T)
    assert not isinstance(caught.value, ModelStalled)


@on_virtual_time
async def test_a_consumer_that_stops_early_closes_the_provider_stream() -> None:
    fake = FakeStream([0.0, 0.0, 0.0])
    stream = watched(fake.events(), T)
    _ = await anext(stream)
    await stream.aclose()
    assert fake.closed


@on_virtual_time
async def test_st2_the_gap_alone_decides_a_trickle_just_inside_runs_just_over_stalls() -> None:
    inside = await _drain(FakeStream([0.0, 0.099, 0.099]))
    assert isinstance(inside[-1], StreamCompleteEvent)
    with pytest.raises(ModelStalled) as caught:
        _ = await _drain(FakeStream([0.0, 0.099, 0.101]))
    assert (caught.value.phase, caught.value.events) == ("idle", 2)


@on_virtual_time
async def test_st4_the_first_event_bound_is_its_own_just_inside_runs_just_over_stalls() -> None:
    assert isinstance((await _drain(FakeStream([0.299])))[-1], StreamCompleteEvent)
    with pytest.raises(ModelStalled) as caught:
        _ = await _drain(FakeStream([0.301]))
    assert (caught.value.phase, caught.value.events) == ("first_event", 0)


@on_virtual_time
async def test_st5_a_response_just_over_its_bound_stalls() -> None:
    assert await bounded(asyncio.sleep(0.199, result="ok"), T) == "ok"
    with pytest.raises(ModelStalled):
        _ = await bounded(asyncio.sleep(0.201), T)
