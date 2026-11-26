"""AH-*: ``BridgeApprovalHold`` keys a pending approval by effect key, so a re-call
after a deferral is answered once and never executed twice."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from harness_core.clock import SystemClock
from harness_core.effects import (
    EffectCall,
    EffectRunner,
    RequestEffectLog,
    ToolExecution,
    args_digest,
)
from harness_core.gateway.endpoint import BoundedHold
from harness_core.interactions import (
    BridgeApprovalHold,
    InMemoryInteractionBridge,
    InMemoryInteractionWakeup,
    JournalInteractionBridge,
    approval_interaction_id,
)
from harness_core.interactions.run_view import ClaimInteractionView
from harness_core.journal import InMemoryJournal
from harness_core.kernel.core.messages import ToolCall
from harness_core.testing.contracts.effect_log import (
    CountingTool,
    context,
    write_effect,
)
from harness_core.testing.contracts.interactions import BINDING
from harness_core.testing.run_spec import minimal_run_spec

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.effects import ApprovalDecision, ToolEffect
    from harness_core.journal.types import InteractionDecision


def ask(call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
    return "ask"


def make_call(invocation: str, args: dict[str, JsonValue] | None = None) -> EffectCall:
    arguments: dict[str, JsonValue] = args if args is not None else {"title": "a"}
    return EffectCall(
        BINDING.run_id,
        invocation,
        ToolCall(id=invocation, name="create_issue", arguments=arguments),
        write_effect(),
        args_digest(arguments),
    )


def runner(log: RequestEffectLog, hold: BridgeApprovalHold) -> EffectRunner:
    return EffectRunner(log, ask, hold)


def test_ah1_the_interaction_is_named_by_effect_key_not_invocation() -> None:
    hold = BridgeApprovalHold(InMemoryInteractionBridge(), BINDING, hold_s=0.01)
    first, recall, other = make_call("inv-1"), make_call("inv-9"), make_call("inv-1", {"title": "b"})
    assert hold.interaction_for(first) == hold.interaction_for(recall)
    assert hold.interaction_for(first) != hold.interaction_for(other)
    assert hold.interaction_for(first) == approval_interaction_id(first.key)
    assert hold.interaction_for(first).startswith("ix.approve.k")


async def test_ah2_approved_within_the_hold_executes_once() -> None:
    bridge = InMemoryInteractionBridge()
    hold = BridgeApprovalHold(bridge, BINDING, hold_s=5)
    call, tool = make_call("inv-1"), CountingTool()

    running = asyncio.create_task(runner(RequestEffectLog(), hold).run(call, context(call), tool))
    for _ in range(100):
        await asyncio.sleep(0)
    assert bridge.requests, "the hold asked before waiting"
    bridge.decide(hold.interaction_for(call), "approved")
    result = await running
    assert result.disposition == "executed"
    assert tool.calls == 1


async def test_ah3_undecided_is_held_and_names_the_interaction() -> None:
    hold = BridgeApprovalHold(InMemoryInteractionBridge(), BINDING, hold_s=0.01)
    call, tool, log = make_call("inv-1"), CountingTool(), RequestEffectLog()
    result = await runner(log, hold).run(call, context(call), tool)
    assert result.disposition == "held"
    assert result.interaction_id == hold.interaction_for(call)
    assert tool.calls == 0
    assert await log.find(call.key) is None


async def test_ah4_a_re_call_after_a_deferral_runs_once_and_never_again() -> None:
    bridge = InMemoryInteractionBridge()
    hold = BridgeApprovalHold(bridge, BINDING, hold_s=0.01)
    log, tool = RequestEffectLog(), CountingTool()
    first = make_call("inv-1")
    assert (await runner(log, hold).run(first, context(first), tool)).disposition == "held"
    bridge.decide(hold.interaction_for(first), "approved")
    recall = make_call("inv-2")
    executed = await runner(log, hold).run(recall, context(recall), tool)
    assert executed.disposition == "executed"
    again = make_call("inv-3")
    recorded = await runner(log, hold).run(again, context(again), tool)
    assert recorded.disposition == "recorded"
    assert recorded.invocation_id == "inv-2"
    assert tool.calls == 1
    assert len(bridge.requests) == 1


@pytest.mark.parametrize("decision", ["denied", "expired", "cancelled"])
async def test_ah5_anything_but_an_approval_refuses_the_call(decision: InteractionDecision) -> None:
    bridge = InMemoryInteractionBridge()
    hold = BridgeApprovalHold(bridge, BINDING, hold_s=0.01)
    call, tool = make_call("inv-1"), CountingTool()
    _ = await runner(RequestEffectLog(), hold).run(call, context(call), tool)
    bridge.decide(hold.interaction_for(call), decision)
    result = await runner(RequestEffectLog(), hold).run(call, context(call), tool)
    assert result.disposition == "denied"
    assert tool.calls == 0


async def test_ah6_an_engine_restart_finds_the_pending_approval_in_the_journal() -> None:
    journal = InMemoryJournal()
    journal.admit(
        BINDING.run_id,
        org_id=BINDING.tenant,
        session_id=BINDING.scope,
        spec=minimal_run_spec(),
        input_text="hi",
    )
    claim = await journal.claim(BINDING.run_id, worker_id="w")
    assert claim is not None
    log, tool = RequestEffectLog(), CountingTool()

    def engine() -> EffectRunner:
        view = ClaimInteractionView(journal, claim)
        bridge = JournalInteractionBridge(view, InMemoryInteractionWakeup(), SystemClock(), poll_s=0.01)
        return runner(log, BridgeApprovalHold(bridge, BINDING, hold_s=0.02))

    first = make_call("inv-1")
    assert (await engine().run(first, context(first), tool)).disposition == "held"
    ix = approval_interaction_id(first.key)
    assert journal.open_interactions(BINDING.run_id) == {ix: "approval"}
    journal.resolve_interaction(BINDING.run_id, ix, "approved")
    recall = make_call("inv-2")
    assert (await engine().run(recall, context(recall), tool)).disposition == "executed"
    again = make_call("inv-3")
    assert (await engine().run(again, context(again), tool)).disposition == "recorded"
    assert tool.calls == 1
    requested = [e for e in journal.events(BINDING.scope) if e.type == "interaction.requested"]
    assert len(requested) == 1


async def test_ah7_a_bounded_hold_keeps_the_interaction_name() -> None:
    inner = BridgeApprovalHold(InMemoryInteractionBridge(), BINDING, hold_s=5)
    bounded = BoundedHold(inner, within_s=0.01)
    call, tool = make_call("inv-1"), CountingTool([ToolExecution(status="ok", content="x")])
    result = await EffectRunner(RequestEffectLog(), ask, bounded).run(call, context(call), tool)
    assert result.disposition == "held"
    assert result.interaction_id == inner.interaction_for(call)
