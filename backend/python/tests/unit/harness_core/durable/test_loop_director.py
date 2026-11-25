"""K-L LD-1..LD-4: a loop director's injections and synthetic turns are journaled with the
turn, are part of every later request, cost no turn, and survive a crash once."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harness_core.failpoints import Arm, Nth, Raise
from harness_core.kernel.core.messages import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from harness_core.kernel.durable.director import (
    SYNTHETIC_PROVIDER,
    SYNTHETIC_TRANSPORT,
    model_turn_index,
    synthetic_turns,
)
from harness_core.kernel.durable.ports import TurnPlan
from harness_core.kernel.durable.transcript import text_of
from harness_core.kernel.fold import fold_run
from harness_core.testing import armed

from .support import RUN, SESSION, Crash, World, assert_paired, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.journal.types import NewEvent
    from harness_core.kernel.durable.ports import TurnResult
    from harness_core.kernel.fold import RunState
    from harness_core.testing.durable import ScriptedModelPort


@dataclass
class _Director:
    """Turn 1: inject a plan message. After a turn whose `lookup` found something:
    take one synthetic turn that calls `lookup` again. Records what it was asked."""

    asked: list[tuple[int, list[tuple[str, str]]]] = field(default_factory=list)

    def before(self, turn: int, state: RunState, previous: Sequence[TurnResult]) -> TurnPlan:
        self.asked.append((turn, [(r.name, r.status) for r in previous]))
        if turn == 1:
            return TurnPlan(inject=("PHASE 1: plan first",), slots={"phase": "plan"})
        if state.slots.get("phase") == "plan" and any(r.name == "lookup" for r in previous):
            call = ToolCall(id="programmatic_1", name="lookup", arguments={"q": "again"})
            return TurnPlan(
                inject=("PHASE 2: dispatching",),
                synthetic=AssistantMessage(content="Dispatching programmatically.", tool_calls=[call]),
                slots={"phase": "verify"},
            )
        if state.slots.get("phase") == "verify":
            return TurnPlan(inject=("PHASE 3: verify",), slots={"phase": "done"})
        return TurnPlan()


def _kernel(world: World, port: ScriptedModelPort, director: _Director, **kw: object):  # noqa: ANN202
    return world.kernel(port, director=director, **kw)


def _state(world: World) -> RunState:
    return fold_run(world.journal.events(SESSION), run_id=RUN)


def _users(port: ScriptedModelPort, call: int) -> list[str]:
    return [text_of(m) for m in port.calls[call].messages if isinstance(m, UserMessage)]


async def test_ld1_injected_messages_ride_with_the_turn_and_stay_in_the_transcript() -> None:
    world = World()
    world.admit(max_turns=4)
    director = _Director()
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("answer"))
    done = await world.run(_kernel(world, port, director))
    assert done.status == "completed"
    # Turn 1 is planned with the plan message after the user's input.
    assert _users(port, 0) == ["please help", "PHASE 1: plan first"]
    injected = [
        e for e in world.events("message.completed") if e.data["role"] == "user" and e.data.get("injected")
    ]
    assert [e.data["injected"] for e in injected] == [True, True, True]
    types = world.types()
    first = types.index("model.request")
    assert types.index("message.completed") < first
    # The synthetic turn's request is never sent; turn 3 sees every message in order.
    assert len(port.calls) == 2
    last = port.calls[-1].messages
    assert [text_of(m) for m in last if isinstance(m, UserMessage)] == [
        "please help",
        "PHASE 1: plan first",
        "PHASE 2: dispatching",
        "PHASE 3: verify",
    ]
    assert_paired(last)
    order = [type(m).__name__ for m in last]
    assert order == [
        "UserMessage",
        "UserMessage",
        "AssistantMessage",
        "ToolMessage",
        "UserMessage",
        "AssistantMessage",
        "ToolMessage",
        "UserMessage",
    ]


async def test_ld2_a_synthetic_turn_is_journaled_not_sampled_and_costs_no_turn() -> None:
    world = World()
    # Two model turns allowed: the synthetic turn between them must not use one.
    world.admit(max_turns=2)
    director = _Director()
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("answer"))
    done = await world.run(_kernel(world, port, director))
    assert done.status == "completed"
    responses = world.events("model.response")
    assert [r.data["provider"] for r in responses] == ["scripted", SYNTHETIC_PROVIDER, "scripted"]
    synthetic = responses[1]
    assert synthetic.data["transport"] == SYNTHETIC_TRANSPORT
    assert synthetic.data["usage"]["inputTokens"] == 0
    # One request per model turn; none for the synthetic one.
    assert len(world.events("model.request")) == 2
    state = _state(world)
    assert synthetic_turns(state) == 1
    assert model_turn_index(state, 3) == 1
    # Its call ran like a model's.
    outcomes = world.events("tool.outcome")
    assert [o.data["invocationId"] for o in outcomes] == ["t1.0", "t2.0"]
    tool_texts = [m.content for m in port.calls[-1].messages if isinstance(m, ToolMessage)]
    assert len(tool_texts) == 2


async def test_ld2b_only_a_synthetic_turns_calls_are_presented() -> None:

    presented: list[str] = []

    class _Presenter:
        def pre_run(self, slots: object) -> list[NewEvent]:
            return []

        def response(self, message: AssistantMessage) -> list[NewEvent]:
            presented.append(message.text)
            return []

        async def finalize(self, ending: object) -> list[NewEvent]:
            return []

    world = World()
    world.admit(max_turns=4)
    _, port = scripted(reply("thinking", ("lookup", {"q": "x"})), reply("answer"))
    done = await world.run(_kernel(world, port, _Director(), presenter=_Presenter()))
    assert done.status == "completed"
    assert presented == ["thinking", "", "answer"]


async def test_ld3_the_director_reads_the_previous_turns_results_and_journals_its_slots() -> None:
    world = World()
    world.admit(max_turns=4)
    director = _Director()
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("answer"))
    _ = await world.run(_kernel(world, port, director))
    assert director.asked == [(1, []), (2, [("lookup", "ok")]), (3, [("lookup", "ok")])]
    saved = [e.data["slots"] for e in world.events("loop.state_saved")]
    assert {"phase": "plan"} in saved and {"phase": "verify"} in saved and {"phase": "done"} in saved
    assert _state(world).slots["phase"] == "done"


async def test_ld4_a_crash_before_the_commit_decides_again_and_commits_once() -> None:
    world = World()
    world.admit(max_turns=4)
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("answer"))
    # The first attempt dies as the synthetic turn's commit is about to go out.
    with armed({"worker.model.before_response_commit": Arm(action=Raise(Crash), trigger=Nth(2))}):
        await world.crash(_kernel(world, port, _Director()))
    assert [r.data["provider"] for r in world.events("model.response")] == ["scripted"]
    done = await world.run(_kernel(world, port, _Director()))
    assert done.status == "completed"
    assert [r.data["provider"] for r in world.events("model.response")] == [
        "scripted",
        SYNTHETIC_PROVIDER,
        "scripted",
    ]
    injected = [e for e in world.events("message.completed") if e.data.get("injected")]
    assert len(injected) == 3
    assert world.journal.status(RUN) == "completed"


async def test_b1_13_a_directors_events_are_journaled_with_its_boundary() -> None:
    from harness_core.journal.types import NewEvent

    @dataclass
    class _Moving:
        def before(self, turn: int, state: RunState, previous: Sequence[TurnResult]) -> TurnPlan:
            if turn != 1:
                return TurnPlan()
            moved = NewEvent("task.created", {"taskId": "s1", "title": "Plan", "status": "in_progress", "deps": []}, critical=False)
            return TurnPlan(slots={"phase": "plan"}, events=(moved,))

    world = World()
    world.admit(max_turns=2)
    _, port = scripted(reply("answer"))
    done = await world.run(world.kernel(port, director=_Moving()))
    assert done.status == "completed"
    [task] = world.events("task.created")
    types = world.types()
    assert types.index("task.created") < types.index("model.request")
    assert task.data["status"] == "in_progress" and task.critical is False


async def test_ld5_a_director_that_ends_the_run_with_its_own_text_gives_the_ending_that_answer() -> None:
    """A synthetic turn's text is never taken as a model's (``texts``), but when the director ends the run with a
    text-only turn of its own, that text is the answer: the presenter is told so, as the journal already says."""
    from harness_core.kernel.durable.ports import RunEnding

    endings: list[RunEnding] = []

    class _Presenter:
        def pre_run(self, slots: object) -> list[NewEvent]:
            return []

        def response(self, message: AssistantMessage) -> list[NewEvent]:
            return []

        async def finalize(self, ending: RunEnding) -> list[NewEvent]:
            endings.append(ending)
            return []

    @dataclass
    class _Answering:
        def before(self, turn: int, state: RunState, previous: Sequence[TurnResult]) -> TurnPlan:
            if turn == 1:
                call = ToolCall(id="own_1", name="lookup", arguments={"q": "x"})
                return TurnPlan(synthetic=AssistantMessage(content="Looking.", tool_calls=[call]))
            return TurnPlan(synthetic=AssistantMessage(content="Found it."))

    world = World()
    world.admit(max_turns=2)
    _, port = scripted()
    done = await world.run(world.kernel(port, director=_Answering(), presenter=_Presenter()))
    assert done.status == "completed"
    assert port.calls == []
    (ending,) = endings
    assert (ending.outcome, ending.texts, ending.answer, ending.confidence) == ("answered", (), "Found it.", None)
