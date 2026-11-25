"""Run gates (WP-31, G-03/G-04) beside edit-then-approve (WP-29, AP-06), as round 8 merged them.

A plan is approved or declined as the run wrote it: ``submit_plan`` is never offered for editing, so the plan a
person approves is the plan the run carries out. A dry run's write is shadowed whatever its arguments: nobody is
asked, so no edit can reach it, and the call the runner would make is simulated, never handed to its tool.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.edits import (
    EDITABLE,
    ApprovedEdit,
    edited_call,
    offering_edit,
)
from harness_core.effects.log import RequestEffectLog
from harness_core.effects.policy import LayeredApprovalPolicy, parse_rules
from harness_core.effects.runner import EffectRunner
from harness_core.effects.types import (
    EffectCall,
    ToolContext,
    ToolEffect,
    ToolExecution,
    args_digest,
    tool_id_for,
)
from harness_core.gates.plan import PLAN_EFFECT, SUBMIT_PLAN, PlanGate, PlanGatedPolicy
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.fold import RunState

if TYPE_CHECKING:
    from harness_core.effects.runner import HoldDecision

ORG_ASKS = LayeredApprovalPolicy((parse_rules({"writes": "ask"}),), ("org",))
WRITE = ToolEffect(
    tool_id_for("post_message"), "write", False, SinkDescriptor(egress="external", args_leave_boundary=True)
)
CTX = ToolContext("r", "o", "inv_1", "a", "k")


def _call(name: str, arguments: dict[str, object], effect: ToolEffect) -> EffectCall:
    call = ToolCall(id="c1", name=name, arguments=arguments)  # type: ignore[arg-type]
    return EffectCall("r", "inv_1", call, effect, args_digest(call.arguments))


async def _never(call: ToolCall, ctx: ToolContext) -> ToolExecution:
    raise AssertionError("a dry run made the call")


@dataclasses.dataclass
class _EditingHold:
    """A person who would approve every call with changed arguments, if anyone asked them."""

    asked: list[str] = dataclasses.field(default_factory=list[str])

    async def hold(self, call: EffectCall) -> HoldDecision | None:
        self.asked.append(call.invocation_id)
        return "approved"

    def approved_edit(self, call: EffectCall) -> ApprovedEdit | None:
        return ApprovedEdit({"text": "edited by a person"}, "sha256:edit")


def test_r8_a_plan_is_never_offered_for_editing() -> None:
    gate = PlanGatedPolicy(ORG_ASKS, PlanGate(lambda: RunState(run_id="r")))
    plan = ToolCall(id="c1", name=SUBMIT_PLAN, arguments={"plan": "File the bug.", "steps": ["Create the issue"]})
    reason = gate.ask_reason(plan, PLAN_EFFECT)
    assert reason is not None and reason["rule"] == "plan_approval"
    assert gate(plan, PLAN_EFFECT) == "ask"
    assert EDITABLE not in offering_edit(plan, PLAN_EFFECT, reason)


def test_r8_a_write_asked_outside_the_plan_gate_is_still_editable() -> None:
    gate = PlanGatedPolicy(ORG_ASKS, PlanGate(lambda: RunState(run_id="r")))
    post = ToolCall(id="c2", name="post_message", arguments={"text": "hi"})
    # Until the plan is approved the gate refuses the write outright: nothing to approve, nothing to edit.
    assert gate(post, WRITE) == "deny"
    assert offering_edit(post, WRITE, {"rule": "writes"})[EDITABLE] is True


async def test_r8_a_dry_run_asks_nobody_so_no_edit_reaches_its_write() -> None:
    hold = _EditingHold()
    runner = EffectRunner(RequestEffectLog(), ORG_ASKS, hold=hold, dry_run=True)
    result = await runner.run(_call("post_message", {"text": "hi"}, WRITE), CTX, _never)
    assert hold.asked == []
    assert result.disposition == "executed" and result.execution is not None
    assert result.execution.shadow is not None and result.execution.shadow.would_ask is True
    assert '"text": "hi"' in str(result.execution.content)


async def test_r8_an_edited_write_reaching_a_dry_run_is_shadowed_with_the_edit() -> None:
    runner = EffectRunner(RequestEffectLog(), dry_run=True)
    edited = edited_call(_call("post_message", {"text": "hi"}, WRITE), {"text": "edited by a person"})
    performed = await runner.perform(edited, CTX, _never, write_intent=True)
    assert performed is not None and performed.shadow is not None
    assert "edited by a person" in str(performed.content)
