"""Deep mode on the durable kernel (K-L).

Legacy ``OrchestratorLoop`` is a phase machine of injected user messages
around the model's turns (PLAN, critique rounds, dispatch, VERIFY) plus one
turn it takes itself, the programmatic dispatch of a structured plan. Here
that machine is a pure function of the journal (``deep_step``), asked before
each model call through the kernel's ``LoopDirector``, with legacy's own
message texts, so a durable run sends what legacy sends.

``spawn_agent`` delegates to durable child runs (``DeepDelegation``): every
rule legacy's phase gate and ``spawn_scheduler`` apply to a batch is decided
from the run's delegations so far (the gate, batch validation, waves of
``depends_on``, a dependent's goal), and each child is the sub-agent legacy's
spawn pool would build (``define_sub_agent``), pinned as ``x.pipeshub.subAgent``
with a grant inside the parent's. A child's worker assembles that sub-agent
(``SubAgent``) and completes with its result and its citation sources, which
the parent adopts on the join (``DeepCitations``).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal, cast

from app.agents.agent_loop.loops.orchestrator import (
    PHASE1_FINALIZE,
    PHASE1_REPLAN,
    PHASE1_VALIDATE,
    PHASE2_DISPATCH,
    PHASE2_RETRY,
    PHASE3_FINISH,
    PHASE3_REVISE,
    PHASE3_VERIFY,
    PROGRAMMATIC_CALL_PREFIX,
    PROGRAMMATIC_DISPATCH,
    SPAWN_BLOCKED,
    task_already_completed,
)
from app.harness.citations import CHILD_SOURCES_KEY
from harness_core.engines.events import PlanItem
from harness_core.journal.children import ChildGrantRequest, ChildRequest
from harness_core.journal.types import NewEvent
from harness_core.kernel.core.messages import AssistantMessage, TextPart, ToolCall
from harness_core.kernel.durable.delegation import (
    TASK_ID,
    WORKSPACE_KEY,
    ChildBudget,
    Decision,
    Delegated,
    DelegatedOutcome,
    DelegationView,
    InterimChildBudget,
    Refuse,
    Wait,
    delegate_effect,
    is_success,
)
from harness_core.kernel.durable.director import model_turn_index
from harness_core.kernel.durable.ports import TurnPlan
from harness_core.tools.durable_port import RouteResult

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from pydantic import JsonValue

    from app.agent_loop_lib.tools.registry import ToolRegistry
    from app.harness.citations import CitationJournal
    from harness_core.effects.types import ToolEffect
    from harness_core.kernel.core.tool_schema import ToolSchema
    from harness_core.kernel.durable.ports import RunEndingOutcome, TurnResult
    from harness_core.kernel.fold import RunState
    from harness_core.kernel.task_ledger import TaskLedger
    from harness_core.logs import HarnessLog
    from harness_core.tools.durable_port import Route
    from harness_core.workspace.forks import ChildMergeReport

DEEP_PHASE_SLOT = "deep.phase"
DEEP_PLAN_SLOT = "deep.plan"
SPAWN_AGENT = "spawn_agent"
# Routed (they need the run's scope and model); spawn_agent is delegated.
ROUTED_TOOLS: tuple[str, ...] = ("create_plan", "critique_plan", "verify_result")
# What only the run that delegates is given (``RunContext.coordinates``).
COORDINATION_TOOLS: frozenset[str] = frozenset((*ROUTED_TOOLS, SPAWN_AGENT))
# `OrchestratorLoop()`'s own defaults.
MAX_PLANNING_ROUNDS = 3
MAX_VERIFY_ROUNDS = 2
# Legacy `domain_spec_factory`'s turn limit for a role sub-agent.
SUB_AGENT_MAX_TURNS = 8
# REPORT 6.9: fan-out per turn is capped; a plan's 3-5 steps fit with room.
DEEP_FAN_OUT_PER_TURN = 8
CITATIONS_KEY = CHILD_SOURCES_KEY

DETACH_REFUSED = (
    "detach is not available here: every sub-agent is joined before you continue. "
    "Call spawn_agent again without detach."
)
_REF = re.compile(r"\bref(\d+)\b")

PhaseName = Literal["plan", "finalize", "programmatic", "dispatch", "verify"]


# ---- the phase machine (pure) ----


@dataclass(frozen=True)
class DeepPhase:
    """Where the orchestrator is between two turns; journaled as ``deep.phase``."""

    phase: PhaseName = "plan"
    rounds: int = 0
    retries: int = 0
    verify_rounds: int = 0
    approved: bool = False

    def slot(self) -> dict[str, JsonValue]:
        return {
            "phase": self.phase,
            "rounds": self.rounds,
            "retries": self.retries,
            "verifyRounds": self.verify_rounds,
            "approved": self.approved,
        }

    @classmethod
    def from_slot(cls, value: JsonValue | None) -> DeepPhase | None:
        if not isinstance(value, dict):
            return None
        phase = value.get("phase")
        return cls(
            phase=phase if phase in ("plan", "finalize", "programmatic", "dispatch", "verify") else "plan",
            rounds=_int(value.get("rounds")),
            retries=_int(value.get("retries")),
            verify_rounds=_int(value.get("verifyRounds")),
            approved=value.get("approved") is True,
        )


@dataclass(frozen=True)
class DeepStep:
    """One boundary's decision: the next phase, what to inject, whether the
    next turn is the programmatic dispatch."""

    phase: DeepPhase
    inject: tuple[str, ...] = ()
    dispatch: bool = False


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _gate(previous: Sequence[TurnResult], name: str) -> tuple[bool, bool]:
    """Whether the turn has a ``name`` result (its last), and whether it passed."""
    found = [r for r in previous if r.name == name]
    if not found:
        return False, False
    content = found[-1].content
    if isinstance(content, str):
        try:
            content = cast("JsonValue", json.loads(content))
        except ValueError:
            return True, False
    return True, isinstance(content, dict) and bool(content.get("passed"))


def _approve(phase: DeepPhase, inject: tuple[str, ...], *, has_plan: bool) -> DeepStep:
    if has_plan:
        return DeepStep(replace(phase, phase="programmatic", approved=True), inject, dispatch=True)
    return DeepStep(replace(phase, phase="dispatch", approved=True), (*inject, PHASE2_DISPATCH))


def _verify(phase: DeepPhase, **changes: int) -> DeepStep:
    return DeepStep(replace(phase, phase="verify", **changes), (PHASE3_VERIFY,))


def deep_step(
    phase: DeepPhase | None,
    previous: Sequence[TurnResult],
    *,
    turn_index: int,
    max_turns: int,
    has_plan: bool,
    planning_message: str,
) -> DeepStep:
    """``OrchestratorLoop.run`` between two of its ``agent.step`` calls.

    ``previous``: the results of the turn that just ended (none before the
    first); ``turn_index``: the model turns taken so far (legacy's
    ``turn_index``). A turn without a tool call ended the run, so this is
    only ever asked after one that had calls (or before the first).
    """
    if phase is None:
        return DeepStep(DeepPhase(), (planning_message,))
    match phase.phase:
        case "plan":
            found, passed = _gate(previous, "critique_plan")
            rounds = phase.rounds
            inject: list[str] = []
            if found:
                rounds += 1
                if passed:
                    return _approve(replace(phase, rounds=rounds), (), has_plan=has_plan)
                inject.append(PHASE1_REPLAN)
            elif any(r.name == "create_plan" for r in previous):
                inject.append(PHASE1_VALIDATE)
            planning = replace(phase, rounds=rounds)
            if rounds < MAX_PLANNING_ROUNDS and turn_index < max_turns:
                return DeepStep(planning, tuple(inject))
            if turn_index < max_turns:
                return DeepStep(replace(planning, phase="finalize"), (*inject, PHASE1_FINALIZE))
            return _approve(planning, tuple(inject), has_plan=has_plan)
        case "finalize":
            return _approve(phase, (), has_plan=has_plan)
        case "programmatic":
            return _verify(phase)
        case "dispatch":
            spawned = [r for r in previous if r.name == SPAWN_AGENT]
            if not spawned:
                return DeepStep(phase)
            if any(r.status == "ok" for r in spawned):
                return _verify(phase)
            retries = phase.retries + 1
            if retries >= 2:
                return _verify(phase, retries=retries)
            return DeepStep(replace(phase, retries=retries), (PHASE2_RETRY,))
        case "verify":
            found, passed = _gate(previous, "verify_result")
            if not found or passed:
                return DeepStep(phase)
            rounds = phase.verify_rounds + 1
            message = PHASE3_FINISH if rounds >= MAX_VERIFY_ROUNDS else PHASE3_REVISE
            return DeepStep(replace(phase, verify_rounds=rounds), (message,))


def plan_steps(slots: Mapping[str, JsonValue]) -> list[Any]:
    """The structured plan ``create_plan`` stored last, as legacy's ``STRUCTURED_PLAN_SLOT``."""
    from app.agent_loop_lib.modules.pipeline.planner.base import Plan

    raw = slots.get(DEEP_PLAN_SLOT)
    if not isinstance(raw, dict):
        return []
    return list(Plan.model_validate(raw).steps or [])


def programmatic_calls(steps: Sequence[Any], turn: int) -> list[ToolCall]:
    """``_programmatic_dispatch``'s synthetic ``spawn_agent`` calls, with ids a
    re-claimed attempt derives the same (legacy's are random)."""
    from app.agents.agent_loop.loops.orchestrator import (
        _step_goal_text,  # pyright: ignore[reportPrivateUsage]
    )

    calls: list[ToolCall] = []
    for step in steps:
        digest = hashlib.sha256(f"{turn}:{step.id}".encode()).hexdigest()[:8]
        calls.append(
            ToolCall(
                id=f"{PROGRAMMATIC_CALL_PREFIX}{step.id}_{digest}",
                name=SPAWN_AGENT,
                arguments={
                    "role": step.domain,
                    "goal": _step_goal_text(step),
                    "task_id": step.id,
                    "depends_on": step.depends_on,
                    "tools": step.tool_names,
                    "reasoning": f"Programmatic dispatch from structured plan step '{step.id}'",
                },
            )
        )
    return calls


class DeepDirector:
    """``OrchestratorLoop`` as the kernel's ``LoopDirector``: active once the
    run's goal step chose the orchestrator loop."""

    def __init__(
        self, *, planning_message: str, max_turns: int, log: HarnessLog, plan: TaskLedger | None = None
    ) -> None:
        self._planning = planning_message
        self._max_turns = max_turns
        self._log = log
        self._plan = plan

    def before(self, turn: int, state: RunState, previous: Sequence[TurnResult]) -> TurnPlan:
        if not is_deep(state.slots):
            return TurnPlan()
        phase = DeepPhase.from_slot(state.slots.get(DEEP_PHASE_SLOT))
        steps = plan_steps(state.slots)
        step = deep_step(
            phase,
            previous,
            turn_index=model_turn_index(state, turn),
            max_turns=self._max_turns,
            has_plan=bool(steps),
            planning_message=self._planning,
        )
        synthetic = None
        if step.dispatch:
            synthetic = AssistantMessage(
                content=[TextPart(text=PROGRAMMATIC_DISPATCH)],
                tool_calls=programmatic_calls(steps, turn),
            )
        if (phase is None) or step.phase.phase != phase.phase:
            self._log.info(
                "deep.phase",
                turn=turn,
                phase=step.phase.phase,
                rounds=step.phase.rounds,
                retries=step.phase.retries,
                verifyRounds=step.phase.verify_rounds,
                dispatch=step.dispatch,
            )
        moved = self._moved(phase, step, steps, previous, state.slots)
        return TurnPlan(
            inject=step.inject,
            synthetic=synthetic,
            slots={DEEP_PHASE_SLOT: step.phase.slot(), **(self._plan.slot() if moved and self._plan else {})},
            events=tuple(moved),
        )

    def _moved(
        self,
        phase: DeepPhase | None,
        step: DeepStep,
        steps: Sequence[Any],
        previous: Sequence[TurnResult],
        slots: Mapping[str, JsonValue],
    ) -> list[NewEvent]:
        """The plan's steps run as children from the dispatch, and end with their spawns' results."""
        if self._plan is None:
            return []
        self._plan.restore(slots)
        if step.dispatch:
            return self._plan.move(dict.fromkeys((s.id for s in steps), "in_progress"))
        if phase is not None and phase.phase == "programmatic":
            return self._plan.move(
                {
                    _dispatched_step(r.call_id): "completed" if r.status == "ok" else "cancelled"
                    for r in previous
                    if r.call_id.startswith(PROGRAMMATIC_CALL_PREFIX)
                }
            )
        return []


def _dispatched_step(call_id: str) -> str:
    """The step id ``programmatic_calls`` put in a call id (``<prefix><step id>_<digest>``)."""
    return call_id[len(PROGRAMMATIC_CALL_PREFIX) :].rpartition("_")[0]


def is_deep(slots: Mapping[str, JsonValue]) -> bool:
    from app.harness.assembly.loop import GOAL_SLOT

    goal = slots.get(GOAL_SLOT)
    return isinstance(goal, dict) and goal.get("loopKind") == "orchestrator"


def planning_message(registry: ToolRegistry) -> str:
    """Phase 1's message over the run's domains, as ``OrchestratorLoop.run`` builds it."""
    from types import SimpleNamespace

    from app.agents.agent_loop.loops.orchestrator import (
        _domain_overview,  # pyright: ignore[reportPrivateUsage]
        build_phase1_planning_instructions,
    )

    shim = SimpleNamespace(runtime=SimpleNamespace(tool_registry=registry))
    return build_phase1_planning_instructions(_domain_overview(shim))  # type: ignore[arg-type]


# ---- the sub-agent a spawn builds ----


def sub_agent_pool(names: Sequence[str]) -> list[str]:
    """The tools a sub-agent with no ``tools`` of its own is given: the run's, but planning and spawning, which only
    the run that delegates can do (a sub-agent offered them planned, then failed its spawn: QA-CLASSIC-FU-5)."""
    return [n for n in names if n not in COORDINATION_TOOLS]


def not_for_sub_agent(name: str) -> str:
    """What a sub-agent's call to a coordination tool is told: the true reason it has none (QAFINAL-FU-PW113)."""
    return (
        f"{name} is not available to a sub-agent: only the agent that handed you this task plans and starts "
        "sub-agents, and a sub-agent cannot start sub-agents of its own. Do the task with your own tools and "
        "answer with what you found."
    )


def withheld_from_sub_agent() -> dict[str, str]:
    """A sub-agent's port's withheld tools: each coordination tool, with ``not_for_sub_agent``'s reason."""
    return {name: not_for_sub_agent(name) for name in sorted(COORDINATION_TOOLS)}


@dataclass(frozen=True)
class SubAgentDefinition:
    """What legacy's spawn pool builds for one ``spawn_agent`` call."""

    role: str
    tool_names: tuple[str, ...]
    max_turns: int
    domain_agent: str | None = None

    def wire(self) -> dict[str, JsonValue]:
        out: dict[str, JsonValue] = {"role": self.role, "toolNames": list(self.tool_names)}
        if self.domain_agent is not None:
            out["domainAgent"] = self.domain_agent
        return out


def define_sub_agent(
    registry: ToolRegistry, pool: Sequence[str], arguments: Mapping[str, Any]
) -> SubAgentDefinition:
    """``build_spawn_child`` + ``domain_spec_factory`` + ``_single_agent_tool``:
    the ``tools`` argument (else the whole pool), or the one domain agent it names."""
    from app.agent_loop_lib.tools.builtin.coordination.agent_tool import AgentTool

    role = str(arguments.get("role") or "")
    tools = arguments.get("tools")
    names = [str(t) for t in cast("list[Any]", tools)] if isinstance(tools, list) else list(pool)
    if names:
        expanded = registry.expand_tool_names(names)
        if len(expanded) == 1:
            tool = registry.resolve_by_name(expanded[0])
            if isinstance(tool, AgentTool) and not tool.share_parent_results:
                return SubAgentDefinition(
                    role=role,
                    tool_names=tuple(tool.spec.tool_names),
                    max_turns=tool.spec.max_turns,
                    domain_agent=tool.name,
                )
    return SubAgentDefinition(role=role, tool_names=tuple(names), max_turns=SUB_AGENT_MAX_TURNS)


def granted_tool_ids(
    registry: ToolRegistry, names: Sequence[str], grants: Mapping[str, str]
) -> tuple[str, ...] | None:
    """The pinned tool ids a sub-agent with ``names`` can reach (through the
    domain agents it holds); ``None`` for no names, which legacy reads as the
    whole registry. UI-only tools never reach a child."""
    from app.agent_loop_lib.tools.builtin.coordination.agent_tool import AgentTool
    from harness_core.tools.tags import TAG_UI_ONLY

    if not names:
        return None
    seen: set[str] = set()
    leaves: set[str] = set()
    pending = registry.expand_tool_names(list(names))
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        if TAG_UI_ONLY in registry.tags_for_name(name):
            continue
        tool = registry.resolve_by_name(name)
        if isinstance(tool, AgentTool):
            pending.extend(registry.expand_tool_names(list(tool.spec.tool_names)))
            continue
        leaves.add(name)
    return tuple(sorted({grants[n] for n in leaves if n in grants}))


# ---- spawn_agent as child runs ----


class DeepDelegation:
    """``spawn_agent`` delegates to durable child runs, decided as legacy's
    phase gate and spawn scheduler decide a batch."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        pool: Sequence[str],
        grants: Mapping[str, str],
        model_name: str,
        max_turns: int,
        max_tokens: int,
        citations: DeepCitations | None,
        log: HarnessLog,
        budget: ChildBudget | None = None,
    ) -> None:
        self._budget = budget or InterimChildBudget()
        self._registry = registry
        self._pool = tuple(pool)
        self._grants = dict(grants)
        self._model = model_name
        self._max_turns = max_turns
        self._max_tokens = max_tokens
        self._citations = citations
        self._log = log

    def delegates(self, call: ToolCall) -> bool:
        return call.name == SPAWN_AGENT

    def effect(self, name: str) -> ToolEffect:
        return delegate_effect(name)

    def schemas(self) -> list[ToolSchema]:
        return self._registry.schemas([SPAWN_AGENT]) if self._registry.has(SPAWN_AGENT) else []

    def decide(self, call: Delegated, view: DelegationView) -> Decision:
        from app.agent_loop_lib.agent.spawn_scheduler import (
            raw_task_id,
            validate_spawn_batch,
        )

        args = call.call.arguments
        if not _approved(view.slots):
            return Refuse(SPAWN_BLOCKED)
        finished = _finished(view)
        task_id = raw_task_id(call.call)
        earlier = finished.get(task_id)
        if earlier is not None and is_success(earlier):
            return Refuse(task_already_completed(task_id))
        if args.get("detach"):
            return Refuse(DETACH_REFUSED)
        model = args.get("model")
        if isinstance(model, str) and model.strip() and model.strip() != self._model:
            return Refuse(
                f"model {model.strip()!r} is not this run's model: a sub-agent runs on the "
                "model of the run that spawns it. Call spawn_agent again without `model`."
            )
        position = [d.invocation_id for d in view.batch].index(call.invocation_id)
        if position >= DEEP_FAN_OUT_PER_TURN:
            return Refuse(
                f"at most {DEEP_FAN_OUT_PER_TURN} sub-agents can be spawned in one turn; "
                "this call was not run. Spawn the rest in a later turn."
            )
        plan = validate_spawn_batch([d.call for d in view.batch], set(finished), self._registry)
        error = plan.errors_by_call_id.get(call.call.id)
        if error is not None:
            return Refuse(error)
        if not isinstance(args.get("role"), str) or not isinstance(args.get("goal"), str):
            return Refuse("spawn_agent needs a `role` and a `goal`.")
        siblings = {raw_task_id(d.call): d for d in view.batch}
        waits: list[str] = []
        sections: list[str] = []
        for dep in plan.depends_by_call_id.get(call.call.id, []):
            sibling = siblings.get(dep)
            if sibling is not None and sibling.state in ("pending", "joining"):
                waits.append(sibling.invocation_id)
                continue
            document = sibling.document if sibling is not None else finished.get(dep)
            if document is None:
                return Refuse(
                    f"prerequisite task '{dep}' for spawn_agent task '{task_id}' never "
                    "produced a result — cannot proceed."
                )
            if not is_success(document):
                error_text = document.get("error") if isinstance(document, dict) else None
                return Refuse(
                    f"prerequisite task '{dep}' failed "
                    f"({error_text or 'unknown error'}) — task '{task_id}' was "
                    "not run. Fix or re-run the prerequisite before retrying this task."
                )
            sections.append(_dependency_section(dep, document))
        if waits:
            return Wait(tuple(waits))
        definition = define_sub_agent(self._registry, self._pool, args)
        share = self._budget.share(
            parent_max_turns=self._max_turns,
            parent_max_tokens=self._max_tokens,
            definition_max_turns=definition.max_turns,
            batch=len(view.batch),
        )
        ids = granted_tool_ids(self._registry, definition.tool_names, self._grants)
        self._log.info(
            "deep.spawn",
            invocationId=call.invocation_id,
            domainAgent=definition.domain_agent,
            tools=len(definition.tool_names),
            grantedTools=None if ids is None else len(ids),
            dependsOn=len(sections),
            maxTurns=share.max_turns,
            maxTokens=share.max_tokens,
        )
        return ChildRequest(
            invocation_id=call.invocation_id,
            goal=_goal(str(args["goal"]), sections, _parent_request(view.slots)),
            grants=ChildGrantRequest(
                tools=ids,
                max_turns=share.max_turns,
                max_tokens=share.max_tokens,
                max_children=share.max_children,
            ),
            task_id=task_id if TASK_ID.match(task_id) else None,
            definition=definition.wire(),
        )

    async def outcome(
        self, call: ToolCall, document: JsonValue, *, workspace: ChildMergeReport | None = None
    ) -> DelegatedOutcome:
        doc = dict(document) if isinstance(document, dict) else {}
        sources = doc.pop(CITATIONS_KEY, None)
        output = doc.get("output")
        events: tuple[NewEvent, ...] = ()
        if self._citations is not None and isinstance(sources, dict):
            output, events = self._citations.adopt(cast("dict[str, Any]", sources), output, call.id)
        success = doc.get("success") is True
        if call.id.startswith(PROGRAMMATIC_CALL_PREFIX):
            # `_programmatic_dispatch`'s tool message: the output text itself.
            text = output if isinstance(output, str) else json.dumps(output, default=str)
            if not success and not text:
                text = str(doc.get("error") or "the sub-agent failed")
            if doc.get("needs_input"):
                text += f"\n\n[ESCALATION] {doc['needs_input']}"
            if workspace is not None:
                text += f"\n\n[WORKSPACE] {workspace.summary()}"
            return DelegatedOutcome(text, error=not success, events=events)
        shown: dict[str, Any] = {"output": output, "success": success}
        if doc.get("confidence") is not None:
            shown["confidence"] = doc["confidence"]
        if doc.get("needs_input"):
            shown["needs_input"] = doc["needs_input"]
        if not success and doc.get("error"):
            shown["error"] = doc["error"]
        if workspace is not None:
            shown[WORKSPACE_KEY] = workspace.as_json()
        # `SpawnAgentTool.handle`'s content: `child_result_content`'s keys, in its order.
        return DelegatedOutcome(json.dumps(shown), error=not success, events=events)


def _approved(slots: Mapping[str, JsonValue]) -> bool:
    phase = DeepPhase.from_slot(slots.get(DEEP_PHASE_SLOT))
    return phase is not None and phase.approved


def _finished(view: DelegationView) -> dict[str, JsonValue]:
    """Every task of an earlier turn that has a result, as legacy's
    ``SPAWN_RESULTS_SLOT``: joined children, and dependents skipped because a
    prerequisite failed (recorded as failed)."""
    from app.agent_loop_lib.agent.spawn_scheduler import raw_task_id

    out: dict[str, JsonValue] = {}
    for item in view.earlier:
        task = raw_task_id(item.call)
        if item.state == "joined" and item.document is not None:
            out[task] = item.document
        elif (
            item.state == "refused"
            and isinstance(item.outcome, str)
            and item.outcome.startswith("prerequisite task '")
        ):
            out[task] = {"output": "", "success": False, "error": item.outcome}
    return out


def _dependency_section(task_id: str, document: JsonValue) -> str:
    from app.agent_loop_lib.agent.spawn_scheduler import (
        _format_dependency_section,  # pyright: ignore[reportPrivateUsage]
    )
    from app.agent_loop_lib.core.types import AgentResult, Goal

    output = document.get("output") if isinstance(document, dict) else None
    return _format_dependency_section(
        task_id, AgentResult(goal=Goal(description=""), output=output, success=True)
    )


def _goal(goal: str, sections: list[str], request: str | None) -> str:
    """The child's task as ``run_child`` hands it over: the prerequisites' results, then the person's
    request ahead of the delegated task (``with_user_request``)."""
    from app.agent_loop_lib.agent.spawn_scheduler import (
        _augment_goal,  # pyright: ignore[reportPrivateUsage]
    )
    from app.agent_loop_lib.core.types import Goal
    from harness_core.kernel.agent.user_request import with_user_request

    augmented = _augment_goal(Goal(description=goal), sections)
    return (with_user_request(augmented, request) if request else augmented).description


def _parent_request(slots: Mapping[str, JsonValue]) -> str | None:
    """The request the parent run sent as its current message (``parent_request``: its last human
    message), read from its journaled goal."""
    from app.harness.assembly.loop import GOAL_SLOT, goal_from_slot

    slot = slots.get(GOAL_SLOT)
    if not isinstance(slot, dict):
        return None
    return goal_from_slot(slot, "").request_text() or None


# ---- create_plan / critique_plan / verify_result ----


class _NoAgent:
    """The planning tools read the run's scope, never the agent's behaviour."""

    async def emit(self, *_: object, **__: object) -> None:
        return None

    def extract_text(self, *_: object, **__: object) -> str:
        return ""


class DeepRoutes:
    """Legacy's special routes for the planning tools, over the run's scope; a
    structured plan ``create_plan`` stores is journaled with its outcome."""

    def __init__(
        self,
        registry: ToolRegistry,
        scope: Any,  # noqa: ANN401 - the turn shaper
        log: HarnessLog,
        *,
        plan: TaskLedger | None = None,
        asks: Callable[[list[str]], bool] | None = None,
    ) -> None:
        self._registry = registry
        self._scope = scope
        self._log = log
        self._plan = plan
        self._asks = asks

    def routes(self) -> dict[str, Route]:
        return {name: self._route(name) for name in ROUTED_TOOLS if self._registry.has(name)}

    def _route(self, name: str) -> Route:
        async def route(call: ToolCall) -> RouteResult:
            from app.agent_loop_lib.core.types import ToolResult
            from app.agent_loop_lib.modules.pipeline.planner.base import (
                STRUCTURED_PLAN_SLOT,
            )
            from harness_core.tools.special_route import RouteContext

            scope = self._scope.tool_scope(call)
            if scope is None:
                return RouteResult(
                    ToolResult(
                        tool_call_id=call.id,
                        name=call.name,
                        content=f"{name} is unavailable outside a turn",
                        is_error=True,
                    )
                )
            run_scope = scope.turn.run
            before = run_scope.get(STRUCTURED_PLAN_SLOT)
            tool: Any = self._registry.resolve_by_name(name)
            result = await tool.handle(call, RouteContext(agent=_NoAgent(), scope=scope))  # type: ignore[arg-type]
            slots: dict[str, JsonValue] = {}
            events: list[NewEvent] = []
            plan = run_scope.get(STRUCTURED_PLAN_SLOT)
            if plan is not None and plan is not before:
                slots[DEEP_PLAN_SLOT] = cast("JsonValue", plan.model_dump(mode="json"))
                self._log.info("deep.plan", steps=len(plan.steps or []))
                if self._plan is not None:
                    events = self._plan.replace([self._item(step) for step in plan.steps or []])
                    slots.update(self._plan.slot())
            return RouteResult(result, slots, tuple(events))

        return route

    def _item(self, step: Any) -> PlanItem:  # noqa: ANN401 - a PlanStep
        return PlanItem(
            step.description,
            "pending",
            task_id=step.id,
            deps=tuple(step.depends_on),
            asks_first=self._asks(list(step.tool_names)) if self._asks is not None and step.tool_names else None,
        )


def restore_plan(run_scope: Any, slots: Mapping[str, JsonValue]) -> None:  # noqa: ANN401 - a RunScope
    """A resumed attempt's scope holds the plan the run journaled, as legacy's run scope does."""
    from app.agent_loop_lib.modules.pipeline.planner.base import (
        STRUCTURED_PLAN_SLOT,
        Plan,
    )

    raw = slots.get(DEEP_PLAN_SLOT)
    if isinstance(raw, dict) and run_scope.get(STRUCTURED_PLAN_SLOT) is None:
        run_scope.set(STRUCTURED_PLAN_SLOT, Plan.model_validate(raw))


# ---- citations across the join ----


class DeepCitations:
    """A child's citation sources adopted by its parent on the join: its refs
    re-numbered in the parent's mapper (legacy's tree shares one mapper), its
    output rewritten to the parent's refs, the delta journaled with the outcome."""

    def __init__(self, journal: CitationJournal, context: Any, log: HarnessLog) -> None:  # noqa: ANN401 - the legacy AgentContext
        self._journal = journal
        self._context = context
        self._log = log

    def adopt(
        self, sources: dict[str, Any], output: JsonValue, call_id: str
    ) -> tuple[JsonValue, tuple[NewEvent, ...]]:
        from app.harness.citations import EVENT
        from app.utils.chat_helpers import CitationRefMapper

        state = self._context.tool_state
        renumbered: dict[str, str] = {}
        refs = sources.get("refs")
        if isinstance(refs, list) and refs:
            mapper = state.get("citation_ref_mapper")
            if mapper is None:
                mapper = state["citation_ref_mapper"] = CitationRefMapper()
            for pair in cast("list[Any]", refs):
                entry = cast("dict[str, Any]", pair) if isinstance(pair, dict) else {}
                if isinstance(entry.get("url"), str):
                    renumbered[str(entry.get("ref"))] = mapper.get_or_create_ref(str(entry["url"]))
        _merge(state, sources)
        if isinstance(output, str) and renumbered:
            output = _REF.sub(lambda m: renumbered.get(m.group(0), m.group(0)), output)
        delta = self._journal.delta(call_id)
        self._log.info(
            "deep.citations_adopted",
            callId=call_id,
            refs=len(renumbered),
            moved=sum(1 for child, parent in renumbered.items() if child != parent),
        )
        # Not critical, as the presenter journals every other delta: a fold on resume skips what it does not read.
        events = (NewEvent(EVENT, delta, critical=False),) if delta else ()
        return output, events


def _merge(state: dict[str, Any], sources: Mapping[str, Any]) -> None:
    """The child's records, blocks and known ids into the parent's state, as a
    restore applies a delta (refs aside: they are re-numbered)."""
    from app.agents.actions.knowledge_graph.ops.results import (
        dedupe_append_final_results,
    )

    records = sources.get("records")
    if isinstance(records, dict) and records:
        existing = state.get("virtual_record_id_to_result")
        state["virtual_record_id_to_result"] = {
            **(existing if isinstance(existing, dict) else {}),
            **records,
        }
    blocks = sources.get("finalResults")
    if isinstance(blocks, list) and blocks:
        state["final_results"] = dedupe_append_final_results(state.get("final_results") or [], cast("list[Any]", blocks))
    for key, field in (("tool_records", "toolRecords"), ("web_records", "webRecords")):
        items = sources.get(field)
        if isinstance(items, list) and items:
            existing_items = state.get(key)
            state[key] = [*(existing_items if isinstance(existing_items, list) else []), *cast("list[Any]", items)]
    known = sources.get("knownRecordIds")
    if isinstance(known, list) and known:
        current = state.get("known_record_ids")
        state["known_record_ids"] = (current if isinstance(current, set) else set()) | {str(k) for k in cast("list[Any]", known)}


# ---- the child's side ----


class SubAgentResult:
    """A sub-agent child's result as legacy hands it back: a domain agent's
    ``result_note`` on an answer, and the citation sources the parent adopts."""

    def __init__(self, *, context: Any, domain_tool: Any = None) -> None:  # noqa: ANN401 - AgentContext, AgentTool
        self._context = context
        self._domain_tool = domain_tool

    async def shape(
        self, document: dict[str, JsonValue], outcome: RunEndingOutcome
    ) -> dict[str, JsonValue]:
        from app.harness.citations import CitationJournal

        out = dict(document)
        output = out.get("output")
        if self._domain_tool is not None and outcome == "answered":
            out["output"] = cast("JsonValue", self._domain_tool.apply_result_note(output))
        # Everything the child's tools cited, from nothing: a fresh journal's first delta.
        sources = CitationJournal(self._context).delta()
        if sources:
            out[CITATIONS_KEY] = cast("JsonValue", sources)
        return out


__all__ = [
    "CITATIONS_KEY",
    "DEEP_FAN_OUT_PER_TURN",
    "DEEP_PHASE_SLOT",
    "DEEP_PLAN_SLOT",
    "ROUTED_TOOLS",
    "SPAWN_AGENT",
    "DeepCitations",
    "DeepDelegation",
    "DeepDirector",
    "DeepPhase",
    "DeepRoutes",
    "DeepStep",
    "SubAgentDefinition",
    "SubAgentResult",
    "deep_step",
    "define_sub_agent",
    "granted_tool_ids",
    "is_deep",
    "plan_steps",
    "planning_message",
    "programmatic_calls",
    "restore_plan",
]
