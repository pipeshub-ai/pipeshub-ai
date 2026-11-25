"""P1b T2 (D19, REPORT 6.2.2): a message sent while a run is active steers it.

Node queues it (``input.queued``); the worker consumes it at the next boundary
before a model call (``input.consumed``) and the model sees it at the tail. A
turn that would end the run with input queued becomes a follow-up turn; Node
refuses a completion while input is queued, which closes the race with a
message queued during the completion commit.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

import pytest

from harness_core.contracts import dump_contract
from harness_core.failpoints import Arm, Nth, Raise, registry
from harness_core.journal import InMemoryContentStore, InputPending, WriterNotAllowed
from harness_core.journal.inline import inline_ref
from harness_core.journal.types import Complete, JournalEvent, NewEvent
from harness_core.kernel.core.messages import ToolMessage, UserMessage
from harness_core.kernel.durable import ModelFailed, ToolExecution
from harness_core.kernel.durable.transcript import TranscriptRenderer
from harness_core.kernel.fold import FOLD_VERSION, fold_run, fold_session

from .support import RUN, SESSION, Crash, World, assert_paired, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from pydantic import JsonValue

    from harness_core.kernel.core.messages import Message, ToolCall
    from harness_core.kernel.durable import (
        DeltaKind,
        ModelCall,
        SampledTurn,
        ToolContext,
        ToolEffect,
    )
    from harness_core.testing.durable import ScriptedModelPort

STEER = "also check the 2025 policy"


def _world() -> World:
    world = World()
    world.admit()
    return world


def _texts(messages: list[Message]) -> list[str]:
    return [m.content for m in messages if isinstance(m, UserMessage) and isinstance(m.content, str)]


def _queue_on_turn(port: ScriptedModelPort, world: World, turn: int, text: str = STEER) -> None:
    """Queues ``text`` while the model streams its ``turn``-th response."""
    original = port.sample

    async def sample(call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]) -> SampledTurn:
        if len(port.calls) + 1 == turn:
            _ = world.journal.queue_input(RUN, text)
        return await original(call, on_delta)

    port.sample = sample  # type: ignore[method-assign]


def _lines(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "harness_event", None) == event]


# ---- ST-2: the next tool boundary ----


async def test_st2_input_queued_during_a_tool_is_consumed_before_the_next_model_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    world = _world()
    world.on_read = lambda w: w.journal.queue_input(RUN, STEER)
    _, port = scripted(reply("", ("lookup", {"q": "policy"})), reply("done"))
    done = await world.run(world.kernel(port))
    assert done.status == "completed"
    types = world.types()
    assert types.index("input.consumed") == types.index("tool.outcome") + 1
    assert types.index("input.consumed") < types.index("turn.completed")
    (consumed,) = world.events("input.consumed")
    assert consumed.data == {"messageSeq": 1} and consumed.writer == "worker"
    second = port.calls[1].messages
    assert_paired(second)
    # The tail of the next request: after the tool result, as the user wrote it.
    assert isinstance(second[-2], ToolMessage)
    assert second[-1] == UserMessage(content=STEER)
    assert world.journal.pending_inputs(RUN) == []
    (line,) = _lines(caplog, "input.consumed")
    fields = line.harness  # type: ignore[attr-defined]
    assert {k: fields[k] for k in ("runId", "orgId", "messageSeq", "boundary")} == {
        "runId": RUN,
        "orgId": "org1",
        "messageSeq": 1,
        "boundary": "tool_results",
    }
    assert STEER not in line.getMessage() and STEER not in repr(fields)


async def test_st2_several_inputs_are_consumed_together_in_queue_order() -> None:
    world = _world()

    def queue_two(w: World) -> None:
        _ = w.journal.queue_input(RUN, "first")
        _ = w.journal.queue_input(RUN, "second")

    world.on_read = queue_two
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("done"))
    assert (await world.run(world.kernel(port))).status == "completed"
    assert [e.data["messageSeq"] for e in world.events("input.consumed")] == [1, 2]
    assert _texts(port.calls[1].messages)[-2:] == ["first", "second"]


# ---- ST-3: queued while the model streams ----


async def test_st3_input_queued_while_a_tool_turn_streams_waits_for_the_tool_boundary() -> None:
    world = _world()
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("done"))
    _queue_on_turn(port, world, 1)
    assert (await world.run(world.kernel(port))).status == "completed"
    assert STEER not in _texts(port.calls[0].messages)
    assert _texts(port.calls[1].messages)[-1] == STEER
    types = world.types()
    assert types.index("tool.outcome") < types.index("input.consumed")


async def test_st3_an_answer_streamed_while_input_was_queued_becomes_a_follow_up_turn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    world = _world()
    _, port = scripted(reply("the 2024 policy says 20 days"), reply("and 2025 says 25"))
    _queue_on_turn(port, world, 1)
    done = await world.run(world.kernel(port))
    assert done.status == "completed"
    assert len(port.calls) == 2
    second = port.calls[1].messages
    assert _texts(second) == ["please help", STEER]
    assert second[-1] == UserMessage(content=STEER)
    types = world.types()
    first_answer = types.index("model.response")
    assert types[first_answer + 1 : first_answer + 3] == ["turn.completed", "input.consumed"]
    assert types.count("run.completed") == 1
    assert [e.data for e in world.events("message.completed") if e.data["role"] == "assistant"][-1][
        "isFinal"
    ] is True
    (line,) = _lines(caplog, "input.consumed")
    assert line.harness["boundary"] == "turn_end"  # type: ignore[attr-defined]
    # The worker had read the input: it follows up without a refused completion.
    assert _lines(caplog, "run.follow_up") == []


async def test_st3_input_queued_during_the_completion_commit_is_refused_then_followed_up(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The race the refusal closes: the worker read no input, Node queued one
    before the completion landed."""
    caplog.set_level(logging.INFO)
    world = _world()
    original = world.journal.commit
    raced: list[bool] = []

    async def commit(request: Any) -> Any:  # noqa: ANN401
        if isinstance(request.transition, Complete) and not raced:
            raced.append(True)
            _ = world.journal.queue_input(RUN, STEER)
        return await original(request)

    world.journal.commit = commit  # type: ignore[method-assign]
    _, port = scripted(reply("first answer"), reply("answer with the steer"))
    done = await world.run(world.kernel(port))
    assert done.status == "completed" and raced == [True]
    assert _texts(port.calls[1].messages)[-1] == STEER
    assert world.types().count("run.completed") == 1
    assert len(world.events("input.consumed")) == 1
    (line,) = _lines(caplog, "run.follow_up")
    assert line.harness["reason"] == "input_pending"  # type: ignore[attr-defined]


async def test_st3_node_refuses_a_completion_while_input_is_queued() -> None:
    world = _world()
    claim = await world.claim()
    _ = world.journal.queue_input(RUN, STEER)
    from harness_core.journal.types import CommitRequest

    with pytest.raises(InputPending):
        _ = await world.journal.commit(
            CommitRequest(f"{claim.attempt_id}:c:1", claim.token, (), Complete())
        )
    # Leaving it is an explicit choice (no turn left); consuming it in the batch is fine.
    receipt = await world.journal.commit(
        CommitRequest(
            f"{claim.attempt_id}:c:2",
            claim.token,
            (NewEvent("input.consumed", {"messageSeq": 1}),),
            Complete(),
        )
    )
    assert receipt.events[-1].type == "run.completed"


# ---- ST-4: crash windows ----


@pytest.mark.parametrize(
    "failpoint",
    [
        "worker.input.before_consume_commit",
        "worker.input.after_consume_commit",
        "worker.model.before_response_commit",
    ],
)
async def test_st4_a_crash_between_queue_and_consume_consumes_exactly_once(failpoint: str) -> None:
    world = _world()
    world.on_read = lambda w: w.journal.queue_input(RUN, STEER) if not w.events("input.queued") else None
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("done"), reply("done"))
    # The first model call is turn 1; only the failpoint after the steer is armed.
    registry.arm(failpoint, Arm(Raise(Crash), Nth(2 if failpoint.startswith("worker.model") else 1)))
    await world.crash(world.kernel(port))
    registry.reset()
    done = await world.run(world.kernel(port))
    assert done.status == "completed"
    assert len(world.events("input.consumed")) == 1
    last = port.calls[-1].messages
    assert_paired(last)
    assert _texts(last).count(STEER) == 1
    assert _texts(last)[-1] == STEER


# ---- ST-5: suspended ----


def _ask_for_writes(call: ToolCall, effect: ToolEffect) -> str:
    return "ask" if effect.needs_intent_commit else "allow"


async def test_st5_input_queued_while_suspended_is_consumed_after_the_resume() -> None:
    world = _world()
    _, port = scripted(reply("", ("create_issue", {"title": "x"})), reply("created, and noted"))
    kernel = world.kernel(port, approval=_ask_for_writes)
    assert (await world.run(kernel)).status == "suspended"
    assert world.journal.queue_input(RUN, STEER) == 1
    world.journal.resolve_interaction(RUN, "ix.approve.t1.0", "approved")
    done = await world.run(world.kernel(port, approval=_ask_for_writes))
    assert done.status == "completed"
    types = world.types()
    assert types.index("input.queued") < types.index("run.resumed")
    assert types.index("tool.outcome") < types.index("input.consumed")
    last = port.calls[-1].messages
    assert_paired(last)
    assert last[-1] == UserMessage(content=STEER)


# ---- ST-7: cancel at the same boundaries ----


async def test_st7_cancel_is_checked_at_the_boundary_and_leaves_queued_input_unconsumed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    world = _world()

    def queue_then_cancel(w: World) -> None:
        _ = w.journal.queue_input(RUN, STEER)
        w.journal.request_cancel(RUN)

    world.on_read = queue_then_cancel
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("never"))
    cancelled = await world.run(world.kernel(port))
    assert cancelled.status == "cancelled"
    assert world.events("input.consumed") == []
    assert len(port.calls) == 1
    # Node takes no input into a run being cancelled: the next message is a new run.
    with pytest.raises(Exception, match="not taking input"):
        _ = world.journal.queue_input(RUN, "after cancel")
    (line,) = _lines(caplog, "run.terminate")
    assert (line.harness["status"], line.harness["leftInputs"]) == ("cancelled", 1)  # type: ignore[attr-defined]


async def test_c21_a_failing_run_logs_the_input_it_leaves(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    world = _world()
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("never"))
    original = port.sample

    async def fails_second(call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]) -> SampledTurn:
        if port.calls:
            _ = world.journal.queue_input(RUN, STEER)
            # Not transient, so the kernel does not retry it (P4.9).
            raise ModelFailed("invalid_request", "boom")
        return await original(call, on_delta)

    port.sample = fails_second  # type: ignore[method-assign]
    failed = await world.run(world.kernel(port))
    assert failed.status == "failed"
    assert world.journal.pending_inputs(RUN) == [1]
    (line,) = _lines(caplog, "run.terminate")
    assert (line.harness["status"], line.harness["leftInputs"]) == ("failed", 1)  # type: ignore[attr-defined]
    assert STEER not in line.getMessage() and STEER not in repr(line.harness)  # type: ignore[attr-defined]


# ---- completion paths ----


async def test_a_question_with_input_queued_becomes_a_follow_up_turn() -> None:
    world = World()

    async def ask(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        _ = world.journal.queue_input(RUN, "use the 2025 one")
        return ToolExecution(status="ok", content={"asked": True}, needs_input=True)

    world.tools.add("ask_user", ask, tags=[("category", "read")])
    world.admit()
    _, port = scripted(
        reply("", ("ask_user", {"q": "which year?"})),
        reply("", ("lookup", {"q": "2025 policy"})),
        reply("2025: 25 days"),
    )
    done = await world.run(world.kernel(port))
    # The question was answered by the queued input: the run goes on, tools and all.
    assert (done.status, done.reason) == ("completed", None)
    (completed,) = world.events("run.completed")
    assert completed.data == {"outcome": "answered"}
    assert len(port.calls) == 3
    assert _texts(port.calls[1].messages)[-1] == "use the 2025 one"


async def test_a_terminal_tool_with_input_queued_keeps_its_outcome_and_follows_up() -> None:
    world = World()

    async def final(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        _ = world.journal.queue_input(RUN, STEER)
        return ToolExecution(status="ok", content="ok", answer=str(args.get("answer")))

    world.tools.add("final_answer", final, tags=[("category", "read")])
    world.admit()
    _, port = scripted(reply("", ("final_answer", {"answer": "20 days"})), reply("25 days"))
    done = await world.run(world.kernel(port))
    assert done.status == "completed"
    assert [name for name, _ in world.tools.executed] == ["final_answer"]
    assert len(world.events("tool.outcome")) == 1
    assert _texts(port.calls[1].messages)[-1] == STEER


async def test_out_of_turns_the_run_completes_and_leaves_queued_input(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    world = World()
    world.admit(max_turns=1)
    world.on_read = lambda w: w.journal.queue_input(RUN, STEER)
    # The answer-only turn past the budget (J-15) answers nothing: the degraded tail, input left queued.
    _, port = scripted(reply("partial findings: the leave policy gives twenty days a year", ("lookup", {"q": "x"})), reply(""))
    done = await world.run(world.kernel(port))
    assert done.status == "completed"
    assert world.events("input.consumed") == []
    assert world.journal.pending_inputs(RUN) == [1]
    (line,) = [r for r in _lines(caplog, "run.complete") if r.harness.get("reason") == "cut_short"]  # type: ignore[attr-defined]
    assert line.harness["leftInputs"] == 1  # type: ignore[attr-defined]
    assert line.harness["truncatedBy"] == "turns"  # type: ignore[attr-defined]


# ---- ST-6: idle ----


async def test_st6_a_run_with_nothing_queued_journals_no_input_event() -> None:
    world = _world()
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("done"))
    assert (await world.run(world.kernel(port))).status == "completed"
    assert [t for t in world.types() if t.startswith("input.")] == []


# ---- the fold (FOLD_VERSION 5) ----


def _event(seq: int, type_: str, data: dict[str, JsonValue], writer: str = "worker", run: str = RUN) -> JournalEvent:
    return JournalEvent(
        org_id="org1",
        session_id=SESSION,
        stream_id=SESSION,
        seq=seq,
        run_id=run,
        attempt_id="a" if writer == "worker" else None,
        fence=1 if writer == "worker" else None,
        type=type_,
        critical=True,
        ts="2026-10-01T12:00:00.000Z",
        writer=writer,  # type: ignore[arg-type]
        data=data,
    )


REF = {"ref": "inline:aGk", "hash": "sha256:8f434346648f6b96df89dda901c5176b10a6d83961dd3c1ac88b59b2dc327aa4", "size": 2}


def test_the_fold_tracks_queued_inputs_until_consumed_and_ignores_replays() -> None:
    assert FOLD_VERSION >= 6
    events = [
        _event(1, "input.queued", {"messageSeq": 1, "textRef": REF}, "node"),
        _event(2, "input.queued", {"messageSeq": 2, "textRef": REF}, "node"),
        _event(3, "input.queued", {"messageSeq": 1, "textRef": REF}, "node"),
        _event(4, "input.consumed", {"messageSeq": 1}),
        _event(5, "input.consumed", {"messageSeq": 1}),
        _event(6, "input.queued", {"messageSeq": 1, "textRef": REF}, "node"),
    ]
    run = fold_run(events, run_id=RUN)
    assert (run.queued_inputs, run.consumed_inputs) == ([2], [1])
    assert run.ignored_events == 3
    session = fold_session(events, stream_id=SESSION)
    steering = [e for e in session.entries if e.input_seq is not None]
    assert [(e.seq, e.role, e.input_seq) for e in steering] == [(4, "user", 1)]


async def test_a_consumed_input_is_never_taken_for_the_runs_own_input() -> None:
    ref = cast("JsonValue", dump_contract(inline_ref(b"hi")))
    events = [
        _event(1, "input.queued", {"messageSeq": 1, "textRef": ref}, "node"),
        _event(2, "input.consumed", {"messageSeq": 1}),
        _event(3, "message.completed", {"role": "user", "textRef": ref, "isFinal": False}),
    ]
    session = fold_session(events, stream_id=SESSION)
    renderer = TranscriptRenderer(InMemoryContentStore(), cast("Any", None))
    _, index = await renderer.render_for(session, RUN)
    assert index == 1


# ---- ST-9: writer allowlist ----


async def test_st9_only_node_queues_and_only_the_worker_consumes() -> None:
    world = _world()
    claim = await world.claim()
    from harness_core.journal.types import CommitRequest

    with pytest.raises(WriterNotAllowed):
        _ = await world.journal.commit(
            CommitRequest(
                f"{claim.attempt_id}:x:1",
                claim.token,
                (NewEvent("input.queued", {"messageSeq": 1}),),
            )
        )
    with pytest.raises(WriterNotAllowed):
        world.journal._validate(  # the allowlist Node enforces
            _event(9, "input.consumed", {"messageSeq": 1}, "node")
        )
