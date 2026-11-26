"""Live deltas (coalesced, fenced, best effort) and presentation events (S6)."""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, cast

import pytest

from harness_core.journal import InMemoryLiveBus, NewEvent
from harness_core.kernel.core.messages import AssistantMessage
from harness_core.kernel.core.responses import ModelResponse, StopReason, TokenUsage
from harness_core.kernel.core.streaming import (
    StreamCompleteEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallDeltaEvent,
)
from harness_core.kernel.durable import ToolExecution
from harness_core.kernel.durable.adapters import (
    TOOL_ARGUMENTS_STARTED,
    StreamingModelPort,
    TransportModelPort,
)
from harness_core.kernel.durable.ports import ModelCall, ModelFailed

from .support import RUN, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    import pytest
    from pydantic import JsonValue

    from harness_core.journal import LiveFrame
    from harness_core.kernel.durable import ToolContext
    from harness_core.kernel.durable.ports import RunEnding
    from harness_core.kernel.fold import RunState


class _StreamingModel:
    """A ``Model`` whose ``stream`` yields fixed deltas and ``complete`` the same text."""

    model_name = "fake"

    def __init__(self, chunks: list[str], thinking: list[str] | None = None) -> None:
        self.chunks = chunks
        self.thinking = thinking or []
        self.completed = 0

    def _response(self) -> ModelResponse:
        return ModelResponse(
            message=AssistantMessage(content="".join(self.chunks)),
            usage=TokenUsage(input_tokens=3, output_tokens=5),
            stop_reason=StopReason.END_TURN,
            model="fake-1",
        )

    async def complete(self, *args: object, **kwargs: object) -> ModelResponse:
        self.completed += 1
        return self._response()

    async def _events(self) -> AsyncIterator[object]:
        for delta in self.thinking:
            yield ThinkingDeltaEvent(delta=delta)
        for delta in self.chunks:
            yield TextDeltaEvent(delta=delta)
        yield StreamCompleteEvent(response=self._response())

    def stream(self, *args: object, **kwargs: object) -> AsyncIterator[object]:
        return self._events()


async def test_l16_the_streaming_port_forwards_deltas_and_returns_the_assembled_message() -> (
    None
):
    model = _StreamingModel(["You get ", "25 days"], thinking=["hmm"])
    port = StreamingModelPort(model, provider="openai", transport="direct")  # type: ignore[arg-type]
    deltas: list[tuple[str, str]] = []

    async def on_delta(kind: str, text: str) -> None:
        deltas.append((kind, text))

    sampled = await port.sample(ModelCall(messages=[]), on_delta)  # type: ignore[arg-type]
    assert deltas == [
        ("reasoning_delta", "hmm"),
        ("text_delta", "You get "),
        ("text_delta", "25 days"),
    ]
    assert sampled.message.text == "You get 25 days"
    assert (sampled.provider, sampled.transport, sampled.model) == (
        "openai",
        "direct",
        "fake-1",
    )
    assert model.completed == 0
    blocking = await TransportModelPort(
        model, provider="openai", transport="direct"
    ).sample(  # type: ignore[arg-type]
        ModelCall(messages=[]), on_delta
    )
    assert blocking.message.text == sampled.message.text


class _ToolDeltaModel(_StreamingModel):
    """Streams a narration, then two calls' argument deltas: a search and the answer."""

    async def _events(self) -> AsyncIterator[object]:
        yield TextDeltaEvent(delta="Here: ")
        yield ToolCallDeltaEvent(index=0, id="c0", name="search", arguments_delta='{"q": "x"}')
        yield ToolCallDeltaEvent(index=1, id="c1", name="final_answer", arguments_delta='{"answer_markdown": "25 ')
        yield ToolCallDeltaEvent(index=1, arguments_delta='days\\n", "confidence"')
        yield ToolCallDeltaEvent(index=0, arguments_delta="")
        yield ToolCallDeltaEvent(index=1, arguments_delta=': "High"}')
        yield StreamCompleteEvent(response=self._response())


@pytest.mark.parametrize("resolver", [True, False])
async def test_fa1_the_answer_argument_streams_as_text_through_the_binding(resolver: bool) -> None:
    """PA-14: the argument the ``streamed_answer_argument`` binding names streams
    as text deltas, decoded, the way legacy streams ``final_answer``; any other
    call's arguments never stream, and without the binding nothing does. The
    first argument delta of a call that is not the answer is announced once
    (C-2: legacy re-asserts ``calling_llm`` there)."""
    from harness_core import bindings

    port = StreamingModelPort(_ToolDeltaModel(["Here: "]), provider="openai", transport="direct")  # type: ignore[arg-type]
    deltas: list[tuple[str, str]] = []

    async def on_delta(kind: str, text: str) -> None:
        deltas.append((kind, text))

    resolve = (lambda name: "answer_markdown" if name == "final_answer" else None) if resolver else None
    with bindings.scoped(bindings.KernelBindings(streamed_answer_argument=resolve)):
        _ = await port.sample(ModelCall(messages=[]), on_delta)  # type: ignore[arg-type]
    streamed = [("text_delta", "Here: "), ("tool_call", TOOL_ARGUMENTS_STARTED)]
    if resolver:
        streamed += [("text_delta", "25 "), ("text_delta", "days\n")]
    assert deltas == streamed


async def test_rs1_the_sampled_message_keeps_the_reasoning_it_streamed() -> None:
    """The response presentation journals the reasoning from the message, so a
    transport that leaves it out of the assembled message still journals it."""
    from harness_core.kernel.core.messages import ThinkingPart

    model = _StreamingModel(["25 days"], thinking=["The handbook ", "says so."])
    port = StreamingModelPort(model, provider="openai", transport="direct")  # type: ignore[arg-type]

    async def on_delta(kind: str, text: str) -> None:
        return None

    sampled = await port.sample(ModelCall(messages=[]), on_delta)  # type: ignore[arg-type]
    content = sampled.message.content
    assert not isinstance(content, str)
    thinking = [p.thinking for p in content if isinstance(p, ThinkingPart)]
    assert thinking == ["The handbook says so."]
    assert sampled.message.text == "25 days"


async def test_l16_deltas_are_coalesced_per_window_numbered_and_fenced() -> None:
    world = World()
    bus = InMemoryLiveBus()
    world.live = bus  # type: ignore[assignment]
    world.admit()
    _, port = scripted(reply("hello there"))
    done = await world.run(world.kernel(port, live_window_s=60.0))
    assert done.status == "completed"
    frames = await bus.read(RUN)
    # The first delta goes out alone (RT-5); the rest share one window.
    assert [(f.fence, f.model_attempt, f.n, f.kind) for f in frames] == [
        (1, 1, 1, "text_delta"),
        (1, 1, 2, "text_delta"),
    ]
    assert "".join(str(f.data["text"]) for f in frames) == "hello there"


async def test_l16_a_zero_window_publishes_every_delta_in_order() -> None:
    world = World()
    bus = InMemoryLiveBus()
    world.live = bus  # type: ignore[assignment]
    world.admit()
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("hello there"))
    _ = await world.run(world.kernel(port, live_window_s=0.0))
    frames = await bus.read(RUN)
    assert [f.n for f in frames] == list(range(1, len(frames) + 1))
    assert len(frames) >= 2
    assert {f.model_attempt for f in frames} == {2}
    assert "".join(str(f.data["text"]) for f in frames) == "hello there"


class _FailingBus:
    def __init__(self) -> None:
        self.calls = 0

    async def publish(self, frame: LiveFrame) -> None:
        self.calls += 1
        raise ConnectionError("redis down")


async def test_l5_a_failing_live_bus_never_stops_the_run_and_is_logged_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    world = World()
    world.admit()
    bus = _FailingBus()
    world.live = bus  # type: ignore[assignment]
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("hello there"))
    done = await world.run(world.kernel(port, live_window_s=0.0))
    assert done.status == "completed"
    assert bus.calls >= 2
    lines = [
        r
        for r in caplog.records
        if getattr(r, "harness_event", None) == "live.degraded"
    ]
    assert len(lines) == 1
    assert lines[0].levelno == logging.WARNING
    assert lines[0].harness["runId"] == RUN  # type: ignore[attr-defined]
    assert lines[0].harness["errorType"] == "ConnectionError"  # type: ignore[attr-defined]


class _Presenter:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.endings: list[RunEnding] = []

    def pre_run(self, slots: Mapping[str, JsonValue]) -> list[NewEvent]:
        if self.fail:
            raise RuntimeError("presenter bug")
        return [
            NewEvent("x.test.goal", {"goal": slots[key]}, critical=False)
            for key in sorted(slots)
            if key.startswith("pre_run.")
        ]

    def response(self, message: AssistantMessage) -> list[NewEvent]:
        if self.fail:
            raise RuntimeError("presenter bug")
        return [
            NewEvent("x.test.call", {"callId": c.id, "name": c.name}, critical=False)
            for c in message.tool_calls or []
        ]

    async def finalize(self, ending: RunEnding) -> list[NewEvent]:
        if self.fail:
            raise RuntimeError("presenter bug")
        self.endings.append(ending)
        return [NewEvent("x.test.answer", {"outcome": ending.outcome}, critical=False)]


TEST_EXTENSIONS = {
    name: frozenset({"worker"})
    for name in ("x.test.call", "x.test.result", "x.test.answer", "x.test.goal")
}


def _world() -> World:
    return World(extension_writers=TEST_EXTENSIONS)  # type: ignore[arg-type]


def _types(world: World) -> list[str]:
    return world.types()


async def test_presentation_rides_with_the_facts_it_describes() -> None:
    world = _world()

    async def shown(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        return ToolExecution(
            status="ok",
            content={"n": 1},
            events=(
                NewEvent(
                    "x.test.result", {"invocationId": ctx.invocation_id}, critical=False
                ),
            ),
        )

    world.tools.add("shown", shown, tags=[("category", "read")])
    world.admit()
    presenter = _Presenter()
    _, port = scripted(reply("let me look", ("shown", {})), reply("done"))
    done = await world.run(world.kernel(port, presenter=presenter))
    assert done.status == "completed"
    types = _types(world)
    response = types.index("model.response")
    assert types[response + 1] == "x.test.call"
    outcome = types.index("tool.outcome")
    assert types[outcome + 1] == "x.test.result"
    assert types[-2:] == ["x.test.answer", "run.completed"]
    [ending] = presenter.endings
    assert ending.outcome == "answered"
    assert ending.texts == ("let me look", "done")


async def test_a_question_and_a_cancel_are_finalized_with_their_outcome() -> None:
    world = _world()

    async def ask(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        return ToolExecution(status="ok", content={"asked": True}, needs_input=True)

    world.tools.add("ask_user", ask, tags=[("category", "read")])
    world.admit()
    presenter = _Presenter()
    _, port = scripted(reply("", ("ask_user", {"q": "which?"})))
    _ = await world.run(world.kernel(port, presenter=presenter))
    assert [e.outcome for e in presenter.endings] == ["needs_input"]

    cancelled = _world()
    cancelled.on_read = lambda w: w.journal.request_cancel(RUN)
    cancelled.admit()
    stopped = _Presenter()
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("never"))
    result = await cancelled.run(cancelled.kernel(port, presenter=stopped))
    assert result.status == "cancelled"
    assert [e.outcome for e in stopped.endings] == ["stopped"]
    assert cancelled.types()[-2:] == ["x.test.answer", "run.cancelled"]


async def test_a_model_failure_is_finalized_as_an_error_answer() -> None:
    world = _world()
    world.admit()
    presenter = _Presenter()
    _, port = scripted(reply("unused"))

    async def broken(call: ModelCall, on_delta: object) -> object:
        raise ModelFailed("server_error", "boom")

    port.sample = broken  # type: ignore[method-assign]
    failed = await world.run(world.kernel(port, presenter=presenter))
    assert failed.status == "failed"
    [ending] = presenter.endings
    assert (ending.outcome, ending.error_type) == ("error", "server_error")
    assert world.types()[-2:] == ["x.test.answer", "run.failed"]


async def test_a_failing_presenter_costs_the_detail_never_the_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    world = World()
    world.admit()
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("done"))
    done = await world.run(world.kernel(port, presenter=_Presenter(fail=True)))
    assert done.status == "completed"
    assert not any(t.startswith("x.test") for t in _types(world))
    stages = sorted(
        r.harness["stage"]  # type: ignore[attr-defined]
        for r in caplog.records
        if getattr(r, "harness_event", None) == "presentation.failed"
    )
    assert stages == ["finalize", "response", "response"]


class _Goal:
    key = "goal"

    async def run(self, input_text: str) -> JsonValue:
        return {"description": input_text.upper()}


async def test_pr1_the_pre_run_presentation_is_committed_with_the_pre_run() -> None:
    world = _world()
    world.admit()
    _, port = scripted(reply("done"))
    done = await world.run(world.kernel(port, presenter=_Presenter(), pre_run=[_Goal()]))
    assert done.status == "completed"
    types = world.types()
    saved = [i for i, t in enumerate(types) if t == "loop.state_saved"]
    assert types[saved[-1] + 1] == "x.test.goal"
    goal = next(e for e in world.events() if e.type == "x.test.goal")
    assert goal.data == {"goal": {"description": "PLEASE HELP"}}
    prerun = [cid for cid, _ in world.journal.commits(RUN) if ":prerun:" in cid]
    assert len(prerun) == 1


async def test_pr1_a_failing_pre_run_presenter_keeps_the_pre_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    world = _world()
    world.admit()
    _, port = scripted(reply("done"))
    done = await world.run(
        world.kernel(port, presenter=_Presenter(fail=True), pre_run=[_Goal()])
    )
    assert done.status == "completed"
    assert "x.test.goal" not in world.types()
    assert port.calls[-1].slots["pre_run.goal"] == {"description": "PLEASE HELP"}
    assert "pre_run" in {
        r.harness["stage"]  # type: ignore[attr-defined]
        for r in caplog.records
        if getattr(r, "harness_event", None) == "presentation.failed"
    }


async def test_pr1_the_turn_shaper_decides_the_request_the_journal_describes() -> None:
    """The shaper reads the journaled pre-run slot; the journaled request
    (`contextDigest`) is the shaped one, not the raw transcript."""
    from harness_core.contracts import canonical_digest
    from harness_core.kernel.core.messages import UserMessage
    from harness_core.kernel.core.tool_schema import ToolSchema

    shaped_turns: list[int] = []

    class Shaper:
        async def shape(self, call: ModelCall, turn: int, state: RunState) -> ModelCall:
            shaped_turns.append(turn)
            goal = cast("dict[str, JsonValue]", state.slots["pre_run.goal"])
            return dataclasses.replace(
                call,
                system=f"Goal: {goal['description']}",
                tools=[ToolSchema(name="lookup", description="Look up.", input_schema={})],
                messages=[*call.messages, UserMessage(content="[footer]")],
            )

        async def enter(self, turn: int, state: RunState) -> None:
            return None

    world = _world()
    world.admit()
    _, port = scripted(reply("", ("lookup", {"q": "a"})), reply("done"))
    done = await world.run(world.kernel(port, pre_run=[_Goal()], shaper=Shaper()))
    assert done.status == "completed"
    assert shaped_turns == [1, 2]
    first = port.calls[0]
    assert first.system == "Goal: PLEASE HELP"
    assert [t.name for t in first.tools or []] == ["lookup"]
    request = next(e for e in world.events() if e.type == "model.request")
    assert request.data["contextDigest"] == canonical_digest(
        [m.model_dump(mode="json") for m in first.messages]
    )
