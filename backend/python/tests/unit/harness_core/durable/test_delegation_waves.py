"""K-L DT-1..DT-4: a delegation registry decides each pending call against the run's
delegations: spawn, refuse, or wait for calls of its batch, which then run in waves."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from harness_core.failpoints import Raise
from harness_core.journal.children import ChildRequest
from harness_core.kernel.core.messages import ToolMessage
from harness_core.kernel.durable.delegation import (
    Decision,
    Delegated,
    DelegatedOutcome,
    DelegationView,
    Refuse,
    Wait,
    delegate_effect,
    goal_tool,
    is_success,
)
from harness_core.testing import armed
from harness_core.testing.run_spec import minimal_run_spec

from .support import RUN, Crash, World, assert_paired, reply, scripted

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.effects.types import ToolEffect
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.core.tool_schema import ToolSchema
    from harness_core.testing.durable import ScriptedModelPort


class _Waves:
    """`delegate{goal, task_id, after?}`: waits for the batch's calls named in `after`,
    then runs with their outputs prefixed; `goal: "refuse"` is refused."""

    def __init__(self) -> None:
        self.views: list[DelegationView] = []

    def delegates(self, call: ToolCall) -> bool:
        return call.name == "delegate"

    def decide(self, call: Delegated, view: DelegationView) -> Decision:
        self.views.append(view)
        args = call.call.arguments
        if args.get("goal") == "refuse":
            return Refuse("refused: not a task for a sub-agent")
        after = [str(a) for a in args.get("after") or []]  # pyright: ignore[reportGeneralTypeIssues]
        by_task = {str(d.call.arguments.get("task_id")): d for d in (*view.earlier, *view.batch)}
        prerequisites = [by_task[a] for a in after]
        open_ = [p.invocation_id for p in prerequisites if p.state != "joined"]
        if open_:
            return Wait(tuple(open_))
        prefix = "".join(
            f"[{a}: {(p.document or {}).get('output')}] "  # pyright: ignore[reportAttributeAccessIssue]
            for a, p in zip(after, prerequisites, strict=True)
        )
        return ChildRequest(
            invocation_id=call.invocation_id,
            goal=prefix + str(args.get("goal")),
            task_id=str(args.get("task_id")),
        )

    async def outcome(self, call: ToolCall, document: JsonValue) -> DelegatedOutcome:
        output = document.get("output") if isinstance(document, dict) else None
        return DelegatedOutcome(f"shown: {output}", error=not is_success(document))

    def effect(self, name: str) -> ToolEffect:
        return delegate_effect(name)

    def schemas(self) -> list[ToolSchema]:
        return [goal_tool("delegate", "Hand one task to a sub-agent")]


def _world() -> World:
    world = World()
    world.admit(spec=minimal_run_spec(max_children=4))
    return world


def _parent(world: World, port: ScriptedModelPort, waves: _Waves):  # noqa: ANN202
    return world.kernel(port, children=world.journal, delegation=waves)


async def _child(world: World, child: str, text: str) -> None:
    _, port = scripted(reply(text))
    assert (await world.run(world.kernel(port), child)).status == "completed"


def _goal(world: World, child: str) -> str:
    return world.journal._runs[child].input_text  # pyright: ignore[reportPrivateUsage]


def _batch() -> tuple[str, dict[str, JsonValue]]:
    return ("delegate", {"goal": "summarize", "task_id": "b", "after": ["a"]})


async def test_dt1_a_dependent_runs_in_the_next_wave_with_its_prerequisites_result() -> None:
    world = _world()
    waves = _Waves()
    _, port = scripted(
        reply("", ("delegate", {"goal": "fetch", "task_id": "a"}), _batch()),
        reply("done"),
    )
    first = await world.run(_parent(world, port, waves))
    assert (first.status, first.reason) == ("suspended", "join")
    (a,) = world.journal.children_of(RUN)
    await _child(world, a, "tickets T-1, T-2")
    assert world.journal.status(RUN) == "queued"
    # Woken: the second wave is admitted with the first's output, and the parent waits again.
    second = await world.run(_parent(world, port, waves))
    assert (second.status, second.reason) == ("suspended", "join")
    a2, b = world.journal.children_of(RUN)
    assert a2 == a
    assert _goal(world, b) == "[a: tickets T-1, T-2] summarize"
    await _child(world, b, "two tickets")
    done = await world.run(_parent(world, port, waves))
    assert done.status == "completed"
    texts = [m.text for m in port.calls[-1].messages if isinstance(m, ToolMessage)]
    assert texts == ["shown: tickets T-1, T-2", "shown: two tickets"]
    assert_paired(port.calls[-1].messages)
    assert len(world.events("child.spawned")) == 2
    assert len(world.events("run.suspended")) == 2
    assert len(world.events("run.resumed")) == 2


async def test_dt2_a_refusal_is_the_calls_error_and_admits_nothing() -> None:
    world = _world()
    _, port = scripted(reply("", ("delegate", {"goal": "refuse", "task_id": "x"})), reply("ok"))
    done = await world.run(_parent(world, port, _Waves()))
    assert done.status == "completed"
    assert world.journal.children_of(RUN) == []
    (outcome,) = world.events("tool.outcome")
    assert outcome.data["status"] == "error"
    (text,) = [m.text for m in port.calls[-1].messages if isinstance(m, ToolMessage)]
    assert text == "refused: not a task for a sub-agent"
    assert world.events("tool.denied") == []


async def test_dt3_the_registry_shapes_what_the_model_reads_of_a_joined_child() -> None:
    world = _world()
    _, port = scripted(reply("", ("delegate", {"goal": "g", "task_id": "a"})), reply("ok"))
    _ = await world.run(_parent(world, port, _Waves()))
    (child,) = world.journal.children_of(RUN)
    await _child(world, child, "found it")
    assert (await world.run(_parent(world, port, _Waves()))).status == "completed"
    (text,) = [m.text for m in port.calls[-1].messages if isinstance(m, ToolMessage)]
    assert text == "shown: found it"
    (outcome,) = world.events("tool.outcome")
    assert outcome.data["status"] == "ok" and outcome.data["labels"] == {"integrity": "trusted"}


async def test_dt3b_every_earlier_delegation_is_in_the_view_with_its_document() -> None:
    world = _world()
    waves = _Waves()
    _, port = scripted(
        reply("", ("delegate", {"goal": "g", "task_id": "a"})),
        reply("", ("delegate", {"goal": "h", "task_id": "c", "after": []})),
        reply("ok"),
    )
    _ = await world.run(_parent(world, port, waves))
    (a,) = world.journal.children_of(RUN)
    await _child(world, a, "first answer")
    _ = await world.run(_parent(world, port, waves))
    view = waves.views[-1]
    (earlier,) = view.earlier
    assert earlier.state == "joined" and earlier.turn == 1
    assert json.loads(json.dumps(earlier.document)) == {"output": "first answer", "success": True}
    assert earlier.outcome == "shown: first answer"
    assert [d.state for d in view.batch] == ["pending"]


async def test_dt5_a_childs_product_completes_its_result_document() -> None:
    """K-L: a ChildResultShaper (deep mode's sub-agent) shapes what the parent joins."""

    class _Noted:
        async def shape(self, document: dict[str, JsonValue], outcome: str) -> dict[str, JsonValue]:
            return {**document, "output": f"{document['output']} (noted, {outcome})", "x.extra": True}

    world = _world()
    waves = _Waves()
    seen: list[JsonValue] = []
    original = waves.outcome

    async def recording(call: ToolCall, document: JsonValue) -> DelegatedOutcome:
        seen.append(document)
        return await original(call, document)

    waves.outcome = recording  # type: ignore[method-assign]
    _, port = scripted(reply("", ("delegate", {"goal": "g", "task_id": "a"})), reply("ok"))
    _ = await world.run(_parent(world, port, waves))
    (child,) = world.journal.children_of(RUN)
    _, child_port = scripted(reply("found"))
    assert (await world.run(world.kernel(child_port, child_result=_Noted()), child)).status == "completed"
    assert (await world.run(_parent(world, port, waves))).status == "completed"
    assert seen == [{"output": "found (noted, answered)", "success": True, "x.extra": True}]


async def test_dt4_a_crash_after_the_second_wave_is_admitted_admits_nothing_twice() -> None:
    world = _world()
    waves = _Waves()
    _, port = scripted(
        reply("", ("delegate", {"goal": "fetch", "task_id": "a"}), _batch()),
        reply("done"),
    )
    _ = await world.run(_parent(world, port, waves))
    (a,) = world.journal.children_of(RUN)
    await _child(world, a, "rows")
    with armed({"worker.spawn.after_admission": Raise(Crash)}):
        await world.crash(_parent(world, port, waves))
    assert len(world.journal.children_of(RUN)) == 2
    second = await world.run(_parent(world, port, waves))
    assert (second.status, second.reason) == ("suspended", "join")
    assert len(world.events("child.spawned")) == 2
    b = world.journal.children_of(RUN)[1]
    await _child(world, b, "summary")
    assert (await world.run(_parent(world, port, waves))).status == "completed"
