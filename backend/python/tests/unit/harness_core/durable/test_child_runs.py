"""P6.1 K-1..K-11: a parent delegates to durable child runs, waits on `join`, and resumes
with their results as tool outcomes; crash points on both sides."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from harness_core.failpoints import Raise
from harness_core.journal.children import ChildGrantRequest
from harness_core.journal.types import CommitReceipt, CommitRequest, Suspend
from harness_core.kernel.core.messages import ToolMessage
from harness_core.kernel.durable import DurableKernel, ToolExecution
from harness_core.kernel.durable.delegation import (
    DelegatingTool,
    NamedDelegation,
    goal_tool,
)
from harness_core.testing import armed
from harness_core.testing.run_spec import minimal_run_spec

from .support import RUN, Crash, World, assert_paired, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.journal import Claim, JournalEvent
    from harness_core.journal.types import AttemptToken, LeaseGrant
    from harness_core.kernel.durable import ToolContext
    from harness_core.testing.durable import ScriptedModelPort

CRASH = Raise(Crash)


def _delegation(grants: ChildGrantRequest | None = None) -> NamedDelegation:
    return NamedDelegation(
        {
            "delegate": DelegatingTool(
                goal_tool("delegate", "Hand one task to a sub-agent"),
                grants or ChildGrantRequest(),
            )
        }
    )


def _world(max_children: int = 2) -> World:
    world = World()
    world.admit(spec=minimal_run_spec(max_children=max_children))
    return world


def _parent(
    world: World, port: ScriptedModelPort, grants: ChildGrantRequest | None = None
) -> DurableKernel:
    return world.kernel(port, children=world.journal, delegation=_delegation(grants))


def _stream(world: World, stream_id: str, type_: str | None = None) -> list[JournalEvent]:
    return [e for e in world.journal.events(stream_id) if type_ is None or e.type == type_]


def _tool_texts(port: ScriptedModelPort) -> list[str]:
    return [m.text for m in port.calls[-1].messages if isinstance(m, ToolMessage)]


async def _run_child(world: World, child: str, text: str = "child answer") -> None:
    _, port = scripted(reply(text))
    done = await world.run(world.kernel(port), child)
    assert done.status == "completed"


async def test_k1_parent_delegates_waits_and_resumes_with_the_result() -> None:
    world = _world()
    _, port = scripted(reply("", ("delegate", {"goal": "find the churn", "task_id": "churn"})), reply("final"))
    first = await world.run(_parent(world, port))
    assert (first.status, first.reason) == ("suspended", "join")
    (child,) = world.journal.children_of(RUN)
    assert world.journal.status(child) == "queued"
    assert [e.type for e in _stream(world, child)] == ["run.admitted", "run.spec_pinned"]
    (spawned,) = world.events("child.spawned")
    assert spawned.data["invocationId"] == "t1.0" and spawned.data["taskId"] == "churn"
    assert world.journal.status(RUN) == "suspended"

    await _run_child(world, child)
    assert world.journal.status(RUN) == "queued"
    (completed,) = world.events("child.completed")
    assert completed.data["outcome"] == "answered"
    assert world.types()[-2:] == ["child.completed", "run.resumed"]

    done = await world.run(_parent(world, port))
    assert done.status == "completed"
    (text,) = _tool_texts(port)
    assert json.loads(text) == {"output": "child answer", "success": True}
    assert_paired(port.calls[-1].messages)
    (outcome,) = world.events("tool.outcome")
    assert outcome.data["status"] == "ok" and outcome.data["labels"] == {"integrity": "trusted"}
    assert [e.type for e in _stream(world, child)][-1] == "run.completed"


async def test_k2_two_children_wake_the_parent_once() -> None:
    world = _world()
    _, port = scripted(
        reply("", ("delegate", {"goal": "a"}), ("delegate", {"goal": "b"})), reply("both")
    )
    assert (await world.run(_parent(world, port))).reason == "join"
    one, two = world.journal.children_of(RUN)
    await _run_child(world, one, "first")
    assert world.journal.status(RUN) == "suspended"
    await _run_child(world, two, "second")
    assert world.journal.status(RUN) == "queued"
    assert len(world.events("run.resumed")) == 1
    done = await world.run(_parent(world, port))
    assert done.status == "completed"
    assert sorted(json.loads(t)["output"] for t in _tool_texts(port)) == ["first", "second"]


@pytest.mark.parametrize(
    ("max_children", "grants", "reason", "told"),
    [
        # MA1 FB-1: the calling model reads what was refused, not the code alone.
        (2, ChildGrantRequest(tools=("slack@x/post",)), "child_grant_too_large", "child_grant_too_large (tools)"),
        (0, None, "children_not_granted", "children_not_granted"),
    ],
)
async def test_k3_a_refused_spawn_is_the_call_error_and_the_run_goes_on(
    max_children: int,
    grants: ChildGrantRequest | None,
    reason: str,
    told: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    world = _world(max_children)
    _, port = scripted(reply("", ("delegate", {"goal": "x"})), reply("did it myself"))
    done = await world.run(_parent(world, port, grants))
    assert done.status == "completed"
    assert world.journal.children_of(RUN) == []
    (denied,) = world.events("tool.denied")
    assert denied.data["rule"] == reason
    (text,) = _tool_texts(port)
    assert f"child run refused: {told}" in text and f"{told} (" not in text
    refusals = [r for r in caplog.records if getattr(r, "harness_event", None) == "child.refused"]
    assert len(refusals) == 1 and refusals[0].levelname == "WARNING"


async def test_k4_crash_after_admission_never_admits_twice() -> None:
    world = _world()
    _, port = scripted(reply("", ("delegate", {"goal": "x"})), reply("final"))
    with armed({"worker.spawn.after_admission": CRASH}):
        await world.crash(_parent(world, port))
    assert len(world.journal.children_of(RUN)) == 1
    assert (await world.run(_parent(world, port))).reason == "join"
    (child,) = world.journal.children_of(RUN)
    assert len(world.events("child.spawned")) == 1
    await _run_child(world, child)
    assert (await world.run(_parent(world, port))).status == "completed"


async def test_k5_a_crashed_child_is_reclaimed_and_wakes_its_parent_once() -> None:
    world = _world()
    _, port = scripted(reply("", ("delegate", {"goal": "x"})), reply("final"))
    _ = await world.run(_parent(world, port))
    (child,) = world.journal.children_of(RUN)
    _, child_port = scripted(reply("child crashed here"), reply("child answer"))
    with armed({"worker.model.before_response_commit": CRASH}):
        await world.crash(world.kernel(child_port), child)
    assert world.journal.status(RUN) == "suspended"
    assert (await world.run(world.kernel(child_port), child)).status == "completed"
    assert len(world.events("run.resumed")) == 1
    assert (await world.run(_parent(world, port))).status == "completed"
    assert json.loads(_tool_texts(port)[0])["output"] == "child answer"


async def test_k6_parent_crashed_while_waiting_resumes_on_a_new_attempt() -> None:
    world = _world()
    _, port = scripted(reply("", ("delegate", {"goal": "x"})), reply("final"))
    with armed({"worker.batch.before_suspend": CRASH}):
        await world.crash(_parent(world, port))
    (child,) = world.journal.children_of(RUN)
    await _run_child(world, child)
    done = await world.run(_parent(world, port))
    assert done.status == "completed"
    assert world.events("run.suspended") == []
    assert json.loads(_tool_texts(port)[0])["output"] == "child answer"


async def test_k7_both_crash_one_child_one_outcome() -> None:
    world = _world()
    _, port = scripted(reply("", ("delegate", {"goal": "x"})), reply("final"))
    with armed({"worker.spawn.after_admission": CRASH}):
        await world.crash(_parent(world, port))
    (child,) = world.journal.children_of(RUN)
    _, child_port = scripted(reply("lost"), reply("child answer"))
    with armed({"worker.model.before_response_commit": CRASH}):
        await world.crash(world.kernel(child_port), child)
    assert (await world.run(_parent(world, port))).reason == "join"
    assert (await world.run(world.kernel(child_port), child)).status == "completed"
    assert (await world.run(_parent(world, port))).status == "completed"
    assert world.journal.children_of(RUN) == [child]
    assert len(world.events("tool.outcome")) == 1


class _ChildWinsTheRace:
    """The parent's journal, where the child ends between the parent's fold and its
    `join` suspension: exactly the window Node's `JOIN_READY` closes."""

    def __init__(self, world: World) -> None:
        self._world = world
        self._journal = world.journal
        self.raced = False

    async def claim(
        self, run_id: str | None = None, *, worker_id: str, org_id: str | None = None
    ) -> Claim | None:
        return await self._journal.claim(run_id, worker_id=worker_id, org_id=org_id)

    async def read(
        self, stream_id: str, *, after_seq: int = 0, limit: int | None = None
    ) -> list[JournalEvent]:
        return await self._journal.read(stream_id, after_seq=after_seq, limit=limit)

    async def last_seq(self, stream_id: str) -> int:
        return await self._journal.last_seq(stream_id)

    async def renew(self, token: AttemptToken) -> LeaseGrant:
        return await self._journal.renew(token)

    async def release(self, token: AttemptToken, *, reason: str) -> None:
        await self._journal.release(token, reason=reason)

    async def commit(self, request: CommitRequest) -> CommitReceipt:
        joining = isinstance(request.transition, Suspend) and request.transition.reason == "join"
        if joining and not self.raced:
            self.raced = True
            (child,) = self._journal.children_of(RUN)
            await _run_child(self._world, child)
        return await self._journal.commit(request)


async def test_k8_child_ends_before_the_parent_suspends_join_ready() -> None:
    world = _world()
    _, port = scripted(reply("", ("delegate", {"goal": "x"})), reply("final"))
    racing = _ChildWinsTheRace(world)
    kernel = DurableKernel(
        journal=racing,
        content=world.content,
        model=port,
        tools=world.tools,
        clock=world.clock,
        children=world.journal,
        delegation=_delegation(),
    )
    done = await world.run(kernel)
    assert racing.raced
    assert done.status == "completed"
    assert world.events("run.suspended") == []
    assert world.events("run.resumed") == []
    assert json.loads(_tool_texts(port)[0])["output"] == "child answer"


async def test_k9_a_failed_child_is_an_error_result() -> None:
    world = _world()
    _, port = scripted(reply("", ("delegate", {"goal": "x"})), reply("final"))
    _ = await world.run(_parent(world, port))
    (child,) = world.journal.children_of(RUN)
    _, child_port = scripted()
    assert (await world.run(world.kernel(child_port), child)).status == "failed"
    assert (await world.run(_parent(world, port))).status == "completed"
    (outcome,) = world.events("tool.outcome")
    assert outcome.data["status"] == "error"
    result = json.loads(_tool_texts(port)[0])
    assert result["success"] is False and result["output"] == ""


async def test_k10_cancelling_the_waiting_parent_cancels_its_child() -> None:
    world = _world()
    _, port = scripted(reply("", ("delegate", {"goal": "x"})))
    _ = await world.run(_parent(world, port))
    (child,) = world.journal.children_of(RUN)
    world.journal.request_cancel(RUN)
    assert world.journal.status(RUN) == "cancelled"
    assert world.journal.status(child) == "cancelled"
    assert world.events("child.completed") == []


async def test_k11_a_child_asking_for_input_reports_needs_input() -> None:
    world = _world()
    _, port = scripted(reply("", ("delegate", {"goal": "x"})), reply("final"))
    _ = await world.run(_parent(world, port))
    (child,) = world.journal.children_of(RUN)
    _, child_port = scripted(reply("", ("ask", {"question": "which region?"})))

    async def ask(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        return ToolExecution(status="ok", content={"asked": True}, needs_input=True)

    world.tools.add("ask", ask, tags=[("category", "read")])
    assert (await world.run(world.kernel(child_port), child)).status == "completed"
    (completed,) = world.events("child.completed")
    assert completed.data["outcome"] == "needs_input"
    assert (await world.run(_parent(world, port))).status == "completed"
    result = json.loads(_tool_texts(port)[0])
    assert result["success"] is True
    assert result["needs_input"].startswith("This sub-agent could not fully complete its task")


async def test_k3_a_delegation_adds_its_own_events_to_a_refused_spawn() -> None:
    """CTL-6: what a refusal means to the person (a notice) is the delegation's to say; the kernel journals it with
    the refused call."""
    from harness_core.journal.types import NewEvent

    seen: list[str] = []

    class _Noting(NamedDelegation):
        def refused(self, call: object, refusal: object) -> tuple[NewEvent, ...]:
            seen.append(getattr(refusal, "reason").value)
            return (NewEvent("x.host.notice", {"code": "noted"}, critical=False),)

    world = World(extension_writers={"x.host.notice": frozenset({"worker"})})
    world.admit(spec=minimal_run_spec(max_children=0))
    _, port = scripted(reply("", ("delegate", {"goal": "x"})), reply("did it myself"))
    delegation = _Noting({"delegate": DelegatingTool(goal_tool("delegate", "Hand one task"), ChildGrantRequest())})
    done = await world.run(world.kernel(port, children=world.journal, delegation=delegation))
    assert done.status == "completed"
    assert seen == ["children_not_granted"]
    assert [e.data for e in world.events("x.host.notice")] == [{"code": "noted"}]
