"""GA-03 (G-04, C20.05): with the plan-approval policy, execution tools are refused until a person approves the plan.

The run reads, proposes its plan with ``submit_plan`` (always asked, never answered by a conversation grant), and
acts only once the plan is approved; the plan is stored with the run (its ``task.*`` steps and the ``gates.plan``
slot). A declined plan ends the run cleanly, with nothing done. The decision is read from the journal, so a
re-claimed attempt sees it exactly as the last one left it; an engine's Tool Gateway enforces the same gate.
"""

from __future__ import annotations

import pytest

from harness_core.contracts.generated.common import ContentRef
from harness_core.contracts.generated.run_spec import RunGates, RunSpec
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.policy import LayeredApprovalPolicy, parse_rules
from harness_core.effects.types import ToolEffect, allow_all, tool_id_for
from harness_core.engines.durable.fold import fold_engine_run
from harness_core.gates.plan import (
    PLAN_DECLINED_ANSWER,
    PLAN_FIRST,
    PLAN_GATE_SLOT,
    PLAN_RULE,
    PLAN_SCHEMA,
    SUBMIT_PLAN,
    SUBMIT_PLAN_TOOL_ID,
    PlanGate,
    PlanGatedPolicy,
    plan_decision,
)
from harness_core.journal.types import JournalEvent
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.fold import Invocation, RunState
from harness_core.testing.engines.durable import EngineWorld, engine_run_spec
from harness_core.testing.run_spec import minimal_run_spec
from tests.unit.harness_core.durable.support import RUN, World, reply, scripted

PLAN_ARGS = {"plan": "File the login bug and tell ops.", "steps": ["File the bug", "Email ops"]}
WRITE = ToolEffect(tool_id_for("create_issue"), "write", False, SinkDescriptor(egress="external", args_leave_boundary=True))
READ = ToolEffect(tool_id_for("lookup"), "read", True, SinkDescriptor(egress="none", args_leave_boundary=False))
PLAN = ToolEffect(SUBMIT_PLAN_TOOL_ID, "read", True, SinkDescriptor(egress="none", args_leave_boundary=False))


def gated(spec: RunSpec | None = None) -> RunSpec:
    return (spec or minimal_run_spec()).model_copy(update={"gates": RunGates(planApproval="required")})


def _world(run_kind: str = "interactive") -> World:
    world = World()
    world.admit(spec=gated(minimal_run_spec().model_copy(update={"run_kind": run_kind})))
    return world


def _script() -> object:
    return scripted(
        reply("", ("create_issue", {"title": "too early"})),
        reply("", ("lookup", {"q": "login bug"}), ("submit_plan", PLAN_ARGS)),
        reply("", ("create_issue", {"title": "Login fails"})),
        reply("Filed the bug."),
    )[1]


# GA-03-1: denied until approved, then the run acts under its own rules; a background (scheduled) run alike: it
# waits, suspended, for whoever approves.
@pytest.mark.parametrize("run_kind", ["interactive", "scheduled"])
async def test_ga03_1_execution_waits_for_the_plan_and_runs_once_it_is_approved(run_kind: str) -> None:
    world = _world(run_kind)
    port = _script()
    suspended = await world.run(world.kernel(port))
    assert suspended.status == "suspended" and suspended.reason is not None
    # The early write was refused by the gate, and nothing reached the service.
    (denied,) = world.events("tool.denied")
    assert denied.data["rule"] == PLAN_RULE
    assert world.saas.effects == []
    # The read ran while the plan waited; only the plan is asked.
    assert [e.data["toolId"] for e in world.events("tool.intent")] == [tool_id_for("lookup")]
    assert len(world.events("interaction.requested")) == 1
    world.journal.resolve_interaction(RUN, suspended.reason, "approved")
    done = await world.run(world.kernel(port))
    assert done.status == "completed"
    assert [e.op for e in world.saas.effects] == ["create_issue"]


async def test_ga03_1_the_approved_plan_is_stored_and_journaled_with_the_run() -> None:
    world = _world()
    port = _script()
    suspended = await world.run(world.kernel(port))
    assert suspended.reason is not None
    world.journal.resolve_interaction(RUN, suspended.reason, "approved")
    await world.run(world.kernel(port))
    saved = [e.data["slots"] for e in world.events("loop.state_saved") if PLAN_GATE_SLOT in e.data["slots"]]
    assert saved and saved[-1][PLAN_GATE_SLOT]["state"] == "approved"  # type: ignore[index]
    assert saved[-1][PLAN_GATE_SLOT]["steps"] == PLAN_ARGS["steps"]  # type: ignore[index]
    assert [e.data["title"] for e in world.events("task.created")] == PLAN_ARGS["steps"]


async def test_ga03_1_the_first_turn_is_told_to_plan_and_sees_the_plan_tool() -> None:
    world = _world()
    port = _script()
    await world.run(world.kernel(port))
    first = port.calls[0]  # type: ignore[attr-defined]
    assert any(PLAN_FIRST[:50] in m.model_dump_json() for m in first.messages)
    assert SUBMIT_PLAN in [t.name for t in first.tools or []]


# GA-03-2: a declined plan ends the run cleanly, with nothing done.
async def test_ga03_2_a_declined_plan_ends_the_run_with_nothing_done() -> None:
    world = _world()
    port = _script()
    suspended = await world.run(world.kernel(port))
    assert suspended.reason is not None
    calls_before = len(port.calls)  # type: ignore[attr-defined]
    world.journal.resolve_interaction(RUN, suspended.reason, "denied")
    done = await world.run(world.kernel(port))
    assert done.status == "completed"
    assert world.saas.effects == []
    # The gate answers itself: the model is not called again.
    assert len(port.calls) == calls_before  # type: ignore[attr-defined]
    finals = [e for e in world.events("message.completed") if e.data.get("isFinal")]
    assert len(finals) == 1
    assert PLAN_DECLINED_ANSWER.encode() in await world.content.get(ContentRef.model_validate(finals[0].data["textRef"]))


# GA-03-3: the gate's own rules.
def _state(*invocations: Invocation) -> RunState:
    return RunState(run_id="r", invocations={i.invocation_id: i for i in invocations})


def _inv(n: int, name: str, **fields: object) -> Invocation:
    return Invocation(invocation_id=f"i{n}", turn=n, index=0, call_id=f"c{n}", name=name, args_digest="d", **fields)  # type: ignore[arg-type]


def test_ga03_3_the_decision_is_the_latest_plans_from_the_journal() -> None:
    assert plan_decision({}) == "none"
    assert plan_decision(_state(_inv(1, SUBMIT_PLAN)).invocations) == "pending"
    approved = _inv(1, SUBMIT_PLAN, state="completed", status="ok", tool_id=SUBMIT_PLAN_TOOL_ID)
    assert plan_decision(_state(approved).invocations) == "approved"
    declined = _inv(2, SUBMIT_PLAN, state="completed", status="error", denied_rule="interaction_denied")
    assert plan_decision(_state(approved, declined).invocations) == "declined"


def test_ga03_3_the_policy_asks_about_the_plan_and_refuses_execution_until_it_is_approved() -> None:
    state = _state()
    policy = PlanGatedPolicy(allow_all, PlanGate(lambda: state))
    plan_call = ToolCall(id="p", name=SUBMIT_PLAN, arguments=PLAN_ARGS)
    write_call = ToolCall(id="w", name="create_issue", arguments={})
    assert policy(plan_call, PLAN) == "ask"
    assert policy(write_call, WRITE) == "deny"
    assert policy(ToolCall(id="r", name="lookup", arguments={}), READ) == "allow"
    rule, text = policy.denial(write_call, WRITE)
    assert rule == PLAN_RULE and SUBMIT_PLAN in text
    reason = policy.ask_reason(plan_call, PLAN)
    assert reason is not None and reason["rule"] == PLAN_RULE and reason["conversationGrant"] is False
    # No "allow for this conversation" ever approves a plan.
    assert policy.grant_answers(plan_call, PLAN) is False


def test_ga03_3_after_approval_the_runs_own_rules_decide() -> None:
    approved = _state(_inv(1, SUBMIT_PLAN, state="completed", status="ok", tool_id=SUBMIT_PLAN_TOOL_ID))
    org = LayeredApprovalPolicy((parse_rules({"writes": "ask"}),), ("org",))
    policy = PlanGatedPolicy(org, PlanGate(lambda: approved))
    assert policy(ToolCall(id="w", name="create_issue", arguments={}), WRITE) == "ask"


def test_ga03_3_the_plan_tool_takes_a_plan_and_its_steps() -> None:
    assert PLAN_SCHEMA.input_schema["required"] == ["plan", "steps"]


# GA-03-4: an engine's Tool Gateway enforces the same gate.
async def test_ga03_4_an_engines_write_is_refused_until_its_plan_is_approved() -> None:
    world = EngineWorld()
    script = f'tool create {{"title": "x"}}\ntool {SUBMIT_PLAN} {{"plan": "p", "steps": ["a"]}}\nsay waiting'
    world.admit("run-plan", script, spec=gated(engine_run_spec()))
    disposition = await world.attempt("run-plan")
    assert disposition.status == "suspended"
    assert sum(world.effects.values()) == 0
    denied = [e for e in world.events("run-plan") if e.type == "tool.denied"]
    assert [d.data["rule"] for d in denied] == [PLAN_RULE]
    assert "interaction.requested" in world.types("run-plan")


def test_ga03_4_an_engine_folds_a_declined_plan_from_the_refusal_alone() -> None:
    events = [
        JournalEvent.from_envelope(e)
        for e in (
            _event(1, "tool.denied", {"invocationId": "inv_p", "rule": "interaction_denied", "toolId": SUBMIT_PLAN_TOOL_ID}),
            _event(2, "tool.outcome", {"invocationId": "inv_p", "status": "error", "labels": {"integrity": "internal"}}),
        )
    ]
    state = fold_engine_run(events, run_id="r")
    assert plan_decision(state.invocations) == "declined"


def _event(seq: int, kind: str, data: dict[str, object]) -> dict[str, object]:
    return {
        "orgId": "o", "sessionId": "s", "streamId": "s", "seq": seq, "runId": "r", "type": kind,
        "schemaVersion": 1, "critical": True, "ts": "2026-10-31T00:00:00.000Z", "writer": "worker",
        "visibility": "owner", "data": data, "attemptId": "a", "fence": 1,
    }
