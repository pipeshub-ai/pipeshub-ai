"""K-L DP-1..DP-8: legacy ``OrchestratorLoop`` as a pure phase function of the journal.

DP-8 drives the real ``OrchestratorLoop.run`` with a fake agent and the same
turn results, and checks that both inject the same messages at the same
boundaries and dispatch at the same point."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.agents.agent_loop.loops import orchestrator as legacy
from app.harness.assembly.deep import (
    DeepPhase,
    deep_step,
    programmatic_calls,
)
from harness_core.kernel.durable.ports import TurnResult

PLAN = "PLAN"
MAX = 15


def _r(name: str, content: object = "ok", status: str = "ok") -> TurnResult:
    shown = content if isinstance(content, str) else json.dumps(content)
    return TurnResult(name=name, call_id=f"c-{name}", status=status, content=shown)


def _critique(passed: bool) -> TurnResult:
    return _r("critique_plan", {"passed": passed, "confidence": "high", "issues": []})


def _step(phase: DeepPhase | None, previous: list[TurnResult], *, turn_index: int = 1, has_plan: bool = True):  # noqa: ANN202
    return deep_step(phase, previous, turn_index=turn_index, max_turns=MAX, has_plan=has_plan, planning_message=PLAN)


def test_dp1_the_first_turn_is_planned() -> None:
    step = _step(None, [], turn_index=0)
    assert step.inject == (PLAN,)
    assert step.phase == DeepPhase()
    assert not step.dispatch


def test_dp2_a_passed_critique_with_a_plan_dispatches_then_verifies() -> None:
    step = _step(DeepPhase(), [_r("create_plan"), _critique(True)])
    assert step.dispatch and step.inject == ()
    assert step.phase == DeepPhase(phase="programmatic", rounds=1, approved=True)
    after = _step(step.phase, [_r("spawn_agent", "found")])
    assert after.inject == (legacy.PHASE3_VERIFY,) and after.phase.phase == "verify"


def test_dp2b_programmatic_calls_are_legacys_and_deterministic() -> None:
    from app.agent_loop_lib.modules.pipeline.planner.base import PlanStep

    steps = [
        PlanStep(id="fetch", description="Fetch tickets", domain="jira", tool_names=["jira"]),
        PlanStep(
            id="report",
            description="Write it up",
            domain="writer",
            depends_on=["fetch"],
            boundaries=["no fetching"],
            output_format="a table",
        ),
    ]
    calls = programmatic_calls(steps, 3)
    assert calls == programmatic_calls(steps, 3)
    assert calls != programmatic_calls(steps, 4)
    assert [c.id.rsplit("_", 1)[0] for c in calls] == ["programmatic_spawn_fetch", "programmatic_spawn_report"]
    assert calls[1].arguments == {
        "role": "writer",
        "goal": legacy._step_goal_text(steps[1]),  # pyright: ignore[reportPrivateUsage]
        "task_id": "report",
        "depends_on": ["fetch"],
        "tools": [],
        "reasoning": "Programmatic dispatch from structured plan step 'report'",
    }


def test_dp3_failed_critiques_replan_then_finalize() -> None:
    phase = DeepPhase()
    for rounds in (1, 2):
        step = _step(phase, [_critique(False)])
        assert step.inject == (legacy.PHASE1_REPLAN,)
        assert step.phase.rounds == rounds
        phase = step.phase
    third = _step(phase, [_critique(False)])
    assert third.inject == (legacy.PHASE1_REPLAN, legacy.PHASE1_FINALIZE)
    assert third.phase.phase == "finalize"
    final = _step(third.phase, [_r("create_plan")])
    assert final.dispatch and final.phase.approved


def test_dp4_a_plan_without_critique_is_nudged_and_not_counted() -> None:
    step = _step(DeepPhase(), [_r("create_plan")])
    assert step.inject == (legacy.PHASE1_VALIDATE,)
    assert step.phase.rounds == 0
    assert _step(DeepPhase(), [_r("search")]).inject == ()


def test_dp5_llm_dispatch_retries_once_then_verifies() -> None:
    step = _step(DeepPhase(), [_critique(True)], has_plan=False)
    assert step.inject == (legacy.PHASE2_DISPATCH,) and not step.dispatch
    assert step.phase.phase == "dispatch" and step.phase.approved
    quiet = _step(step.phase, [_r("create_plan")], has_plan=False)
    assert quiet.inject == () and quiet.phase == step.phase
    failed = _step(step.phase, [_r("spawn_agent", "bad task id", "error")], has_plan=False)
    assert failed.inject == (legacy.PHASE2_RETRY,) and failed.phase.retries == 1
    again = _step(failed.phase, [_r("spawn_agent", "bad", "error")], has_plan=False)
    assert again.inject == (legacy.PHASE3_VERIFY,) and again.phase.phase == "verify"
    ran = _step(step.phase, [_r("spawn_agent", "bad", "error"), _r("spawn_agent", "fine")], has_plan=False)
    assert ran.inject == (legacy.PHASE3_VERIFY,)


def test_dp6_failed_verification_revises_then_finishes() -> None:
    phase = DeepPhase(phase="verify", approved=True)
    first = _step(phase, [_r("verify_result", {"passed": False})])
    assert first.inject == (legacy.PHASE3_REVISE,)
    second = _step(first.phase, [_r("verify_result", {"passed": False})])
    assert second.inject == (legacy.PHASE3_FINISH,)
    assert _step(second.phase, [_r("verify_result", {"passed": True})]).inject == ()
    assert _step(phase, [_r("verify_result", "critic unavailable")]).inject == (legacy.PHASE3_REVISE,)


def test_dp7_the_turn_budget_bounds_planning_as_legacy_does() -> None:
    near = _step(DeepPhase(), [_critique(False)], turn_index=MAX - 1)
    assert near.inject == (legacy.PHASE1_REPLAN,)
    out = _step(DeepPhase(rounds=1), [_critique(False)], turn_index=MAX)
    # No turn left for FINALIZE: legacy approves the plan as it stands.
    assert out.inject == (legacy.PHASE1_REPLAN,) and out.dispatch
    assert out.phase.approved


def test_phase_slot_round_trips() -> None:
    phase = DeepPhase(phase="verify", rounds=2, retries=1, verify_rounds=1, approved=True)
    assert DeepPhase.from_slot(json.loads(json.dumps(phase.slot()))) == phase
    assert DeepPhase.from_slot(None) is None


# ---- DP-8: differential against the legacy loop ----

Turn = list[TurnResult] | None  # None: the model answered (the run stops)


@dataclass
class _Scope:
    slots: dict[str, Any] = field(default_factory=dict)
    turns: list[Any] = field(default_factory=list)

    def get(self, slot: Any) -> Any:  # noqa: ANN401
        return self.slots.get(slot.key, slot.default_factory())

    def set(self, slot: Any, value: Any) -> None:  # noqa: ANN401
        self.slots[slot.key] = value


@dataclass
class _Agent:
    script: list[Turn]
    has_plan: bool
    max_turns: int = MAX
    start_turn_index: int = 0
    events: list[str] = field(default_factory=list)
    scope: _Scope = field(default_factory=_Scope)

    @property
    def runtime(self) -> Any:  # noqa: ANN401
        from types import SimpleNamespace

        return SimpleNamespace(tool_registry=None)

    async def inject_user_message(self, text: str, **_: object) -> None:
        self.events.append(text)

    async def step(self, goal: Any, turn_index: int) -> Any:  # noqa: ANN401
        from app.agent_loop_lib.core.types import (
            AgentResult,
            AgentTurn,
            Goal,
            ToolResult,
        )
        from harness_core.kernel.agent.loops import StepOutcome

        self.events.append("<turn>")
        results = self.script.pop(0) if self.script else None
        if results is None:
            return StepOutcome("stop", result=AgentResult(goal=Goal(description="")))
        turn = AgentTurn(
            tool_results=[
                ToolResult(
                    tool_call_id=r.call_id,
                    name=r.name,
                    content=json.loads(r.content) if str(r.content).startswith("{") else r.content,
                    is_error=r.status != "ok",
                )
                for r in results
            ]
        )
        self.scope.turns.append(turn)
        if self.has_plan and any(r.name == "create_plan" for r in results):
            from app.agent_loop_lib.modules.pipeline.planner.base import (
                STRUCTURED_PLAN_SLOT,
                Plan,
                PlanStep,
            )

            self.scope.set(
                STRUCTURED_PLAN_SLOT,
                Plan(
                    goal=Goal(description=""),
                    text="",
                    steps=[PlanStep(id="s", description="d", domain="x")],
                ),
            )
        return StepOutcome("continue", turn=turn)

    async def fail(self, goal: Any, error: str, **_: object) -> Any:  # noqa: ANN401
        self.events.append("<fail>")


async def _legacy_events(script: list[Turn], *, has_plan: bool, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    agent = _Agent(list(script), has_plan)

    async def dispatch(agent_: Any, plan: Any, goal: Any, turn_index: int) -> tuple[bool, int]:  # noqa: ANN401
        agent.events.append("<dispatch>")
        return True, turn_index

    monkeypatch.setattr(legacy, "_programmatic_dispatch", dispatch)
    monkeypatch.setattr(legacy, "build_phase1_planning_instructions", lambda _block: PLAN)
    from app.agent_loop_lib.core.types import Goal

    await legacy.OrchestratorLoop().run(agent, Goal(description="g"))  # type: ignore[arg-type]
    return _trim(agent.events)


def _durable_events(script: list[Turn], *, has_plan: bool) -> list[str]:
    events: list[str] = []
    remaining = list(script)
    phase: DeepPhase | None = None
    previous: list[TurnResult] = []
    turn_index = 0
    planned = False
    while True:
        # The kernel's budget check comes before the director is asked.
        if turn_index >= MAX:
            return _trim(events)
        step = deep_step(
            phase,
            previous,
            turn_index=turn_index,
            max_turns=MAX,
            has_plan=planned,
            planning_message=PLAN,
        )
        events.extend(step.inject)
        phase = step.phase
        if step.dispatch:
            events.append("<dispatch>")
            previous = [_r("spawn_agent", "child answer")]
            continue
        events.append("<turn>")
        results = remaining.pop(0) if remaining else None
        if results is None:
            return _trim(events)
        if has_plan and any(r.name == "create_plan" for r in results):
            planned = True
        previous = results
        turn_index += 1


def _trim(events: list[str]) -> list[str]:
    """Messages injected after the last turn never reach a model (the run ends)."""
    last = max(i for i, e in enumerate(events) if e in ("<turn>", "<dispatch>"))
    return [e for e in events[: last + 1] if e != "<fail>"]


CREATE = _r("create_plan")
PASS = _critique(True)
FAIL = _critique(False)
SPAWN_OK = _r("spawn_agent", "found")
SPAWN_ERR = _r("spawn_agent", "invalid", "error")
VERIFY_OK = _r("verify_result", {"passed": True})
VERIFY_FAIL = _r("verify_result", {"passed": False})

SCRIPTS: dict[str, tuple[list[Turn], bool]] = {
    "direct-answer": ([None], True),
    "plan-pass-dispatch-verify-answer": ([[CREATE, PASS], [VERIFY_OK], None], True),
    "plan-without-critique": ([[CREATE], [PASS], [VERIFY_OK], None], True),
    "three-failed-critiques-finalize": ([[CREATE, FAIL], [CREATE, FAIL], [CREATE, FAIL], [CREATE], [VERIFY_OK], None], True),
    "llm-dispatch-ok": ([[_r("create_plan", "free text plan"), PASS], [SPAWN_OK], [VERIFY_OK], None], False),
    "llm-dispatch-retry": ([[PASS], [SPAWN_ERR], [SPAWN_ERR], [VERIFY_FAIL], [VERIFY_FAIL], [VERIFY_OK], None], False),
    "llm-dispatch-idle-turn": ([[PASS], [_r("create_plan")], [SPAWN_OK], None], False),
    "verify-revise-finish": ([[CREATE, PASS], [VERIFY_FAIL], [VERIFY_FAIL], [VERIFY_FAIL], None], True),
    "budget-exhausted-in-planning": ([[CREATE]] * 20, True),
    "budget-exhausted-in-verify": ([[CREATE, PASS], *([[VERIFY_FAIL]] * 20)], True),
}


@pytest.mark.parametrize("name", sorted(SCRIPTS))
async def test_dp8_the_phase_function_injects_what_orchestrator_loop_injects(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    script, has_plan = SCRIPTS[name]
    legacy_events = await _legacy_events(script, has_plan=has_plan, monkeypatch=monkeypatch)
    durable_events = _durable_events(script, has_plan=has_plan)
    if name.startswith("budget-"):
        # Out of turns, legacy still nudges and dispatches the plan before failing;
        # the durable kernel ends the run at that boundary (listed under Deviations).
        legacy_events = _until_last_turn(legacy_events)
        durable_events = _until_last_turn(durable_events)
        assert legacy_events.count("<turn>") == MAX
    assert durable_events == legacy_events


def _until_last_turn(events: list[str]) -> list[str]:
    last = max(i for i, e in enumerate(events) if e == "<turn>")
    return events[: last + 1]
