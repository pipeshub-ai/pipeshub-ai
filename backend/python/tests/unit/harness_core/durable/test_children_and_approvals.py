"""CA-1..2: one batch both delegates to a child run (P6.1) and asks for an approval (P2.6c).

The batch holds its approval first; the call runs once whatever order the decision and the
child's end arrive in, and the parent answers only after both.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from harness_core.journal.children import ChildGrantRequest
from harness_core.kernel.core.messages import ToolMessage
from harness_core.kernel.durable.delegation import (
    DelegatingTool,
    NamedDelegation,
    goal_tool,
)
from harness_core.testing.interactions import ManualWakeup
from harness_core.testing.run_spec import minimal_run_spec

from .support import RUN, World, assert_paired, reply, scripted

if TYPE_CHECKING:
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.durable import DurableKernel, ToolEffect
    from harness_core.testing.durable import ScriptedModelPort

ASK = "ix.approve.t1.1"


def _ask_for_writes(call: ToolCall, effect: ToolEffect) -> str:
    return "ask" if effect.needs_intent_commit else "allow"


def _kernel(world: World, port: ScriptedModelPort, wakeup: ManualWakeup) -> DurableKernel:
    delegation = NamedDelegation(
        {"delegate": DelegatingTool(goal_tool("delegate", "Hand one task to a sub-agent"), ChildGrantRequest())}
    )
    return world.kernel(
        port,
        children=world.journal,
        delegation=delegation,
        approval=_ask_for_writes,
        interactions=wakeup,
        approval_hold_s=10.0,
        interaction_poll_s=2.0,
    )


def _world() -> World:
    world = World()
    world.admit(spec=minimal_run_spec(max_children=2))
    return world


def _script() -> tuple[object, ScriptedModelPort]:
    return scripted(
        reply("", ("delegate", {"goal": "find the churn", "task_id": "churn"}), ("post_message", {"text": "hi"})),
        reply("final"),
    )


async def _run_child(world: World, child: str) -> None:
    _, port = scripted(reply("child answer"))
    assert (await world.run(world.kernel(port), child)).status == "completed"


def _posts(world: World) -> int:
    return sum(1 for e in world.saas.effects if e.op == "post_message")


def _finish(port: ScriptedModelPort) -> None:
    texts = [m.text for m in port.calls[-1].messages if isinstance(m, ToolMessage)]
    assert json.loads(texts[0]) == {"output": "child answer", "success": True}
    assert_paired(port.calls[-1].messages)


async def test_ca1_approved_in_the_hold_runs_once_then_the_parent_joins() -> None:
    world = _world()
    _, port = _script()
    wakeup = ManualWakeup(world.clock)

    def on_wait(waits: int) -> None:
        if waits == 2:
            world.journal.resolve_interaction(RUN, ASK, "approved")
            wakeup.wake(RUN)

    wakeup.on_wait = on_wait
    first = await world.run(_kernel(world, port, wakeup))
    assert (first.status, first.reason) == ("suspended", "join")
    assert _posts(world) == 1
    (child,) = world.journal.children_of(RUN)

    await _run_child(world, child)
    done = await world.run(_kernel(world, port, ManualWakeup(world.clock)))
    assert done.status == "completed"
    assert _posts(world) == 1
    _finish(port)


async def test_ca2_child_ends_while_the_approval_is_suspended_then_the_decision_resumes() -> None:
    world = _world()
    _, port = _script()
    first = await world.run(_kernel(world, port, ManualWakeup(world.clock)))
    assert (first.status, first.reason) == ("suspended", ASK)
    (child,) = world.journal.children_of(RUN)

    await _run_child(world, child)
    # The parent waits on its approval, not only on children: the child's end must not wake it.
    assert world.journal.status(RUN) == "suspended"
    assert _posts(world) == 0

    world.journal.resolve_interaction(RUN, ASK, "approved")
    assert world.journal.status(RUN) == "queued"
    done = await world.run(_kernel(world, port, ManualWakeup(world.clock)))
    assert done.status == "completed"
    assert _posts(world) == 1
    _finish(port)
