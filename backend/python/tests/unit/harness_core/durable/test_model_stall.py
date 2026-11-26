"""P4.9 ST-6..ST-11: a stalled model stream on the durable kernel is retried as a new model attempt,
bounded by P4.7's same-route policy and the attempt's lease, and never left hanging."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, override

from harness_core.kernel.core.messages import AssistantMessage
from harness_core.kernel.core.responses import ModelResponse, StopReason, TokenUsage
from harness_core.kernel.core.streaming import StreamCompleteEvent, TextDeltaEvent
from harness_core.kernel.durable.adapters import StreamingModelPort
from harness_core.kernel.durable.ports import ModelFailed
from harness_core.models.stream_watch import StreamTimeouts
from harness_core.models.transport_model import TransportModel
from harness_core.models.transports.base import LLMTransport

from .support import Crash, World

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.kernel.core.streaming import StreamEvent
    from harness_core.kernel.durable.ports import ModelCall, SampledTurn

FAST = StreamTimeouts(first_event_s=0.2, idle_s=0.05, response_s=0.2)
STALL = "stall"
PARTIAL = "partial"  # "Hel", then nothing
ANSWER = "answer"  # "Hel", "lo", complete


class StallingTransport(LLMTransport):
    """Plays one behaviour per call; ``hook`` runs when a call stalls (to move a clock)."""

    def __init__(self, *plays: str, hook: Any = None) -> None:  # noqa: ANN401
        self.plays = list(plays)
        self.calls = 0
        self.closed: list[str] = []
        self.hook = hook

    @property
    @override
    def provider(self) -> str:
        return "fake"

    @override
    async def complete(self, messages: Any, tools: Any = None, system: Any = None, model: Any = None,
                       thinking_budget: Any = None, effort: Any = None, system_blocks: Any = None, *, tool_choice: object = None) -> ModelResponse:
        raise NotImplementedError

    @override
    async def complete_structured(self, messages: Any, output_schema: Any, system: Any = None,
                                  model: Any = None) -> Any:
        raise NotImplementedError

    @override
    def stream(self, messages: Any, tools: Any = None, system: Any = None, model: Any = None,
               thinking_budget: Any = None, effort: Any = None, system_blocks: Any = None, *, tool_choice: object = None) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        return self._play(self.plays.pop(0))

    async def _play(self, play: str) -> AsyncIterator[StreamEvent]:
        try:
            if play != STALL:
                yield TextDeltaEvent(delta="Hel")
            if play in (STALL, PARTIAL):
                if self.hook is not None:
                    self.hook()
                await asyncio.Event().wait()
            yield TextDeltaEvent(delta="lo")
            yield StreamCompleteEvent(
                response=ModelResponse(
                    message=AssistantMessage(content="Hello"),
                    usage=TokenUsage(input_tokens=10, output_tokens=2),
                    stop_reason=StopReason.END_TURN,
                    model="fake-1",
                )
            )
        finally:
            self.closed.append(play)


def _port(transport: StallingTransport) -> StreamingModelPort:
    return StreamingModelPort(TransportModel(transport, timeouts=FAST), provider="fake", transport="direct")


def _sleeps(delays: list[float], *, crash_first: bool = False) -> Any:  # noqa: ANN401
    async def sleep(seconds: float) -> None:
        delays.append(seconds)
        if crash_first and len(delays) == 1:
            raise Crash

    return sleep


def _texts(world: World, attempt: int) -> str:
    return "".join(str(f.data["text"]) for f in world.live.frames if f.model_attempt == attempt and f.kind == "text_delta")


async def test_st6_a_stall_after_partial_output_is_retried_as_a_new_attempt() -> None:
    world = World()
    world.admit()
    transport = StallingTransport(PARTIAL, ANSWER)
    delays: list[float] = []
    done = await world.run(world.kernel(_port(transport), sleep=_sleeps(delays)))  # type: ignore[arg-type]
    assert done.status == "completed"
    assert transport.calls == 2
    assert PARTIAL in transport.closed, "the stalled stream was closed, not left reading"
    assert _texts(world, 1) == "Hel", "attempt 1 keeps only what it showed"
    assert _texts(world, 2) == "Hello"
    (request,) = world.events("model.request")
    assert request.data["modelAttempt"] == 2
    assert len(world.events("model.response")) == 1
    assert delays == [1.0]


async def test_st6_a_stream_that_never_starts_is_retried_too() -> None:
    world = World()
    world.admit()
    transport = StallingTransport(STALL, ANSWER)
    delays: list[float] = []
    done = await world.run(world.kernel(_port(transport), sleep=_sleeps(delays)))  # type: ignore[arg-type]
    assert (done.status, transport.calls, delays) == ("completed", 2, [1.0])


async def test_st7_three_stalls_fail_the_run_as_a_timeout() -> None:
    world = World()
    world.admit()
    transport = StallingTransport(STALL, PARTIAL, STALL)
    delays: list[float] = []
    done = await world.run(world.kernel(_port(transport), sleep=_sleeps(delays)))  # type: ignore[arg-type]
    assert (done.status, transport.calls, delays) == ("failed", 3, [1.0, 2.0])
    assert world.events("run.failed")[0].data["errorType"] == "timeout"
    assert world.events("model.response") == []


async def test_st8_no_retry_sleeps_past_the_attempts_lease() -> None:
    world = World()
    world.admit()
    # The stall uses up the lease but for half a second: the 1 s retry would outlive it.
    transport = StallingTransport(STALL, ANSWER, hook=lambda: world.clock.advance(27.5))
    delays: list[float] = []
    done = await world.run(world.kernel(_port(transport), sleep=_sleeps(delays)))  # type: ignore[arg-type]
    assert (done.status, transport.calls, delays) == ("failed", 1, [])
    assert world.events("run.failed")[0].data["errorType"] == "timeout"


class RefusingPort:
    """A model port whose ledger refuses the call: not transient, so never retried."""

    def __init__(self) -> None:
        self.calls = 0

    async def sample(self, call: ModelCall, on_delta: Any) -> SampledTurn:  # noqa: ANN401
        self.calls += 1
        raise ModelFailed("budget_exhausted")

    def restore(self, raw: bytes, *, provider: str, transport: str) -> None:
        return None


async def test_st9_a_budget_refusal_is_never_retried() -> None:
    """Never retried, and never a bare failure (BUDGETEND): with no wrap-up reserve held, the run completes cut
    short on an answer written without a model."""
    world = World()
    world.admit()
    port = RefusingPort()
    delays: list[float] = []
    done = await world.run(world.kernel(port, sleep=_sleeps(delays)))  # type: ignore[arg-type]
    assert (done.status, port.calls, delays) == ("completed", 1, [])
    assert world.events("run.completed")[0].data == {"outcome": "answered", "truncatedBy": "budget"}


async def test_st11_a_crash_during_the_retry_is_re_driven_from_the_journal() -> None:
    world = World()
    world.admit()
    transport = StallingTransport(STALL, ANSWER)
    delays: list[float] = []
    await world.crash(world.kernel(_port(transport), sleep=_sleeps(delays, crash_first=True)))  # type: ignore[arg-type]
    assert world.events("model.response") == [], "nothing of the stalled attempt was journaled"
    done = await world.run(world.kernel(_port(transport), sleep=_sleeps([])))  # type: ignore[arg-type]
    assert done.status == "completed"
    assert len(world.events("model.response")) == 1

