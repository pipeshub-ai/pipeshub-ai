"""Plan approval (G-04, GA-03): every tool but a read is refused until a person approves the run's plan.

The run proposes its plan with the harness's own tool, ``submit_plan``; the gate always asks a person about that call
(the approval card shows the plan), whatever the run's other rules say, and no "allow for this conversation" ever
answers it. Until the latest ``submit_plan`` is approved, the policy refuses every call that is not a read, with
words that tell the model to plan first; reads run as the run's rules say, so the run can look before it plans. An
approved plan unlocks execution under the run's own rules; a declined one ends the run (the loop answers that
nothing was done; an engine's further calls are all refused).

The decision lives in the journal, never in process: the gate reads the run's invocations (``RunState``) whenever it
decides, so a re-claimed attempt finds the plan approved, or declined, exactly as the last one left it. The approved
plan is stored with the run: its steps become the run's ``task.*`` plan, and its text rides ``gates.plan``.

Enforced in the approval policy both executors use (the kernel's for the loop, the Tool Gateway's for an engine),
and served as one more tool beside the run's catalog (``PlanGatedTools``): the model never reaches a tool another way.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any, Final, Literal, cast

from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.flow import grant_answerable
from harness_core.effects.redaction import redaction_of
from harness_core.effects.types import (
    POLICY_DENIAL,
    ExplainsAsk,
    ExplainsDenial,
    ToolEffect,
    ToolExecution,
    consult_policy,
    refresh_policy,
    tool_id_for,
)
from harness_core.engines.events import PlanItem
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.kernel.task_ledger import PLAN_SLOT, TaskLedger

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pydantic import JsonValue

    from harness_core.effects.redaction import Redaction
    from harness_core.effects.types import ApprovalDecision, ApprovalPolicy, ToolContext
    from harness_core.invoker.ports import ToolCatalog
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.fold import Invocation, RunState

SUBMIT_PLAN: Final = "submit_plan"
SUBMIT_PLAN_TOOL_ID: Final = tool_id_for(SUBMIT_PLAN, source="harness", instance="gate")
# The ``tool.denied`` rule, the ``interaction.explained`` rule and the log lines.
PLAN_RULE: Final = "plan_approval"
# Where the approved plan rides the run's slots.
PLAN_GATE_SLOT: Final = "gates.plan"
MAX_PLAN_CHARS: Final = 8000
MAX_STEPS: Final = 20

# A read: it changes nothing, and so a dry run never shadows it; the gate asks a person about it all the same.
PLAN_EFFECT: Final = ToolEffect(
    tool_id=SUBMIT_PLAN_TOOL_ID,
    effect="read",
    idempotent=True,
    sink=SinkDescriptor(egress="none", args_leave_boundary=False),
)
PLAN_SCHEMA: Final = ToolSchema(
    name=SUBMIT_PLAN,
    description=(
        "Propose your plan for this task to the person who must approve it before you act. Nothing that changes "
        "anything can run until they approve it: look up what you need first (reads work), then call this once "
        "with the whole plan. If they approve it, carry it out; if they decline it, stop."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "required": ["plan", "steps"],
        "properties": {
            "plan": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_PLAN_CHARS,
                "description": "What you will do and why, in a few plain sentences.",
            },
            "steps": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_STEPS,
                "items": {"type": "string", "minLength": 1, "maxLength": 300},
                "description": "The steps, in order, each one action you will take.",
            },
        },
    },
)

PLAN_FIRST: Final = (
    "This run needs an approved plan before it can act. Look up whatever you need first (reads work), then call "
    f"{SUBMIT_PLAN} with your plan and its steps. Do not try to change, send or create anything before the plan is "
    "approved: those calls are refused until then."
)
PLAN_PENDING_DENIAL: Final = (
    PLAN_RULE,
    f"plan_not_approved: this run must have its plan approved before it can make this call. Call {SUBMIT_PLAN} with "
    "your plan and its steps, and wait for the approval; reads still work meanwhile.",
)
PLAN_DECLINED_DENIAL: Final = (
    PLAN_RULE,
    "plan_declined: the person declined this run's plan, so nothing more may be done in it. Stop, and tell the user "
    "that nothing was done.",
)
PLAN_APPROVED_TEXT: Final = "The plan is approved. Carry it out now, step by step."
PLAN_DECLINED_ANSWER: Final = (
    "The plan was declined, so nothing was done. Send a new message if you want me to try a different plan."
)

PlanDecision = Literal["none", "pending", "approved", "declined"]


def plan_decision(invocations: Mapping[str, Invocation]) -> PlanDecision:
    """What the run's latest ``submit_plan`` came to, from its journaled invocations alone."""
    latest: Invocation | None = None
    for inv in invocations.values():
        if inv.tool_id != SUBMIT_PLAN_TOOL_ID and inv.name not in (SUBMIT_PLAN, SUBMIT_PLAN_TOOL_ID):
            continue
        if latest is None or (inv.turn, inv.index) > (latest.turn, latest.index):
            latest = inv
    if latest is None:
        return "none"
    if latest.denied_rule is not None or latest.state == "rejected":
        return "declined"
    if latest.state == "completed":
        return "approved" if latest.status == "ok" else "pending"
    return "pending"


@dataclasses.dataclass
class PlanGate:
    """The run's plan approval, read from its journal (``state``) whenever it is asked."""

    state: Callable[[], RunState]

    def decision(self) -> PlanDecision:
        return plan_decision(self.state().invocations)

    @property
    def approved(self) -> bool:
        return self.decision() == "approved"

    @property
    def declined(self) -> bool:
        return self.decision() == "declined"


def is_plan_call(call: ToolCall, effect: ToolEffect) -> bool:
    return effect.tool_id == SUBMIT_PLAN_TOOL_ID


@dataclasses.dataclass
class PlanGatedPolicy:
    """``base`` behind the plan gate: ``submit_plan`` always asks, anything but a read is refused until the plan is
    approved, and after a declined plan everything but a read is refused for good."""

    base: ApprovalPolicy
    gate: PlanGate

    def _own(self, call: ToolCall, effect: ToolEffect) -> ApprovalDecision | None:
        if is_plan_call(call, effect):
            return "deny" if self.gate.declined else "ask"
        if effect.effect == "read" or self.gate.approved:
            return None
        return "deny"

    async def refresh(self) -> None:
        await refresh_policy(self.base)

    async def consult(self, call: ToolCall, effect: ToolEffect) -> None:
        # Only a call the gate leaves to ``base`` is worth asking about outside the run (G-40): one the gate refuses
        # or asks about itself is decided here, and the base's webhook would otherwise deny it unasked.
        if self._own(call, effect) is None:
            await consult_policy(self.base, call, effect)

    def __call__(self, call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        own = self._own(call, effect)
        return own if own is not None else self.base(call, effect)

    def denial(self, call: ToolCall, effect: ToolEffect) -> tuple[str, str]:
        if self._own(call, effect) == "deny":
            return PLAN_DECLINED_DENIAL if self.gate.declined else PLAN_PENDING_DENIAL
        return self.base.denial(call, effect) if isinstance(self.base, ExplainsDenial) else POLICY_DENIAL

    def ask_reason(self, call: ToolCall, effect: ToolEffect) -> Mapping[str, JsonValue] | None:
        if is_plan_call(call, effect):
            return {"rule": PLAN_RULE, "toolId": effect.tool_id, "conversationGrant": False}
        return self.base.ask_reason(call, effect) if isinstance(self.base, ExplainsAsk) else None

    def grant_answers(self, call: ToolCall, effect: ToolEffect) -> bool:
        # A plan is approved by a person each time, never by an earlier "allow for this conversation".
        return not is_plan_call(call, effect) and grant_answerable(self.base, call, effect)

    def redaction(self, call: ToolCall, effect: ToolEffect) -> Redaction | None:
        return redaction_of(self.base, call, effect)


def plan_gated(policy: ApprovalPolicy, gate: PlanGate | None) -> ApprovalPolicy:
    return policy if gate is None else PlanGatedPolicy(policy, gate)


def approved_plan(call: ToolCall) -> ToolExecution:
    """What an approved ``submit_plan`` returns: the go-ahead, the plan as the run's ``task.*`` steps, and the plan
    itself in the ``gates.plan`` slot."""
    plan = str(call.arguments.get("plan") or "").strip()[:MAX_PLAN_CHARS]
    raw = call.arguments.get("steps")
    items = cast("list[object]", raw) if isinstance(raw, list) else []
    steps = [str(s).strip() for s in items if str(s).strip()][:MAX_STEPS]
    ledger = TaskLedger()
    events = ledger.replace([PlanItem(title=step, status="pending") for step in steps])
    stored: dict[str, JsonValue] = {
        "state": "approved",
        "invocationId": call.id,
        "plan": plan,
        "steps": cast("JsonValue", steps),
    }
    return ToolExecution(
        status="ok",
        content=PLAN_APPROVED_TEXT,
        events=tuple(events),
        slots={PLAN_GATE_SLOT: stored, **ledger.slot()} if steps else {PLAN_GATE_SLOT: stored},
    )


class PlanGatedTools:
    """``inner`` with ``submit_plan`` beside its tools. Every other protocol ``inner`` implements stays visible
    (attributes are forwarded), so a port wrapped here is still whatever it was."""

    def __init__(self, inner: ToolCatalog) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401 - forwards the wrapped port's own protocols
        return getattr(self._inner, name)

    def describe_call(self, call: ToolCall) -> ToolEffect | None:
        return PLAN_EFFECT if call.name == SUBMIT_PLAN else self._inner.describe_call(call)

    def describe(self, name: str) -> ToolEffect | None:
        if name == SUBMIT_PLAN:
            return PLAN_EFFECT
        describe = getattr(self._inner, "describe", None)
        return describe(name) if describe is not None else None

    def schemas(self) -> list[ToolSchema] | None:
        inner = [s for s in self._inner.schemas() or [] if s.name != SUBMIT_PLAN]
        return [*inner, PLAN_SCHEMA]

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolExecution:
        if call.name == SUBMIT_PLAN:
            return approved_plan(call)
        return await self._inner.execute(call, ctx)


def plan_tools[T](tools: T, gate: PlanGate | None) -> T:
    return tools if gate is None else cast("T", PlanGatedTools(cast("ToolCatalog", tools)))


__all__ = [
    "MAX_PLAN_CHARS",
    "PLAN_APPROVED_TEXT",
    "PLAN_DECLINED_ANSWER",
    "PLAN_DECLINED_DENIAL",
    "PLAN_EFFECT",
    "PLAN_FIRST",
    "PLAN_GATE_SLOT",
    "PLAN_PENDING_DENIAL",
    "PLAN_RULE",
    "PLAN_SCHEMA",
    "PLAN_SLOT",
    "SUBMIT_PLAN",
    "SUBMIT_PLAN_TOOL_ID",
    "PlanDecision",
    "PlanGate",
    "PlanGatedPolicy",
    "PlanGatedTools",
    "approved_plan",
    "plan_decision",
    "plan_gated",
    "plan_tools",
]
