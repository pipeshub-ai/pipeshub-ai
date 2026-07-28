"""`OrchestratorLoop`: Phase 10 (deferred) of the agent-loop migration —
replaces the legacy LangGraph deep-agent graph (`app.modules.agents.deep`:
`orchestrator_node` -> `critic_node` -> `execute_sub_agents_node` ->
`aggregator_node`) with a `LoopStrategy` composed entirely over agent-loop's
own generic planning/coordination TOOLS, per the migration plan's mapping:

| Deep Component               | Agent-Loop Equivalent                          |
|-------------------------------|-------------------------------------------------|
| `orchestrator_node`           | `create_plan` tool call (Phase 1)                |
| `critic_node`                 | `critique_plan` tool call, one-shot-then-execute |
| `execute_sub_agents_node`     | Programmatic dispatch from structured plan (Phase 2)|
| `tool_router.group_tools_by_domain` | `_domain_overview()` + per-spawn `tools=[...]`|
| `aggregator_node`             | `verify_result` tool + retry loop (Phase 3)      |

Phase 2 is now PROGRAMMATIC: `create_plan` stores a validated structured
plan (`PlanStep` list in `STRUCTURED_PLAN_SLOT`); the orchestrator reads
it directly and calls `schedule_spawn_batch` with synthetic `ToolCall`s —
no LLM-mediated translation step.  Falls back to the old LLM-mediated
dispatch when the structured plan is absent (backward compatible).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.agent.loops import LoopStrategy, ReActLoop
from app.agent_loop_lib.agent.phase_driver import PhaseDriver
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    TextPart,
    ToolCall,
    ToolMessage,
)
from app.agent_loop_lib.core.scope import StateSlot
from app.agent_loop_lib.tools.builtin.coordination.agent_tool import AgentTool
from app.agent_loop_lib.tools.builtin.coordination.spawn_agent import SpawnAgentTool, child_result_content
from app.agent_loop_lib.tools.builtin.planning.create_plan import CreatePlanTool
from app.agent_loop_lib.tools.builtin.planning.critique_plan import CritiquePlanTool
from app.agent_loop_lib.tools.builtin.planning.verify_result import VerifyResultTool
from app.agent_loop_lib.tools.tags import TAG_SPAWN, TAG_UI_ONLY
from app.agents.agent_loop.sub_agent_prompt import build_sub_agent_prompt

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.agent_loop_lib.agent import Agent
    from app.agent_loop_lib.core.types import AgentResult, Goal
    from app.agent_loop_lib.hooks.middleware.context import ToolCallContext
    from app.agent_loop_lib.hooks.registry import HookRegistry
    from app.agent_loop_lib.modules.pipeline.planner.base import Plan
    from app.agent_loop_lib.tools.registry import ToolRegistry
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

# Set by `OrchestratorLoop.run()` itself — never by the model — the
# instant Phase 1 has run its course (critique passed, OR the FINALIZE
# round deliberately overrode it after max revision rounds). `_phase_gate`
# below reads this to enforce "no spawn_agent before Phase 2" as a real
# PRE_TOOL_USE denial instead of the prompt-only "do not dispatch any
# sub-agents until critique_plan passes" instruction Phase 1's own
# planning message already gives the model — a model that ignores (or a
# smaller/weaker model that simply forgets) that instruction used to
# dispatch anyway.
PLAN_DISPATCH_APPROVED_SLOT: StateSlot[bool] = StateSlot(
    key="orchestrator.plan_dispatch_approved", default_factory=lambda: False,
)

# The only tools the top-level orchestrator agent itself is ever granted
# (`spec.tool_names`) — every other registered tool (connector actions,
# `retrieval`, dynamic tools) stays reachable ONLY through a spawned
# sub-agent's own, domain-scoped `tool_names`. Order matches the plan's
# Decompose -> Critique -> Dispatch -> Verify phases.
COORDINATION_TOOL_NAMES: tuple[str, ...] = (
    "create_plan",
    "critique_plan",
    "spawn_agent",
    "verify_result",
)

_SUB_AGENT_MAX_TURNS = 8


class DomainSpawnAgentTool(SpawnAgentTool):
    """`SpawnAgentTool` with a description steering the LLM toward
    PipesHub's domain-scoped sub-agents (jira/slack/retrieval/...) instead
    of agent-loop's default researcher/writer/planner/critic/verifier
    roles. `role` stays a free-form string either way — resolution is
    entirely up to whatever `AgentRuntime.spec_factory` is wired (see
    `domain_spec_factory` below); only the LLM-facing description changes.
    `handle()`/`execute()`/`parameters` are inherited unchanged."""

    @property
    def description(self) -> str:
        return (
            "Spawn a sub-agent scoped to ONE domain (e.g. 'jira', 'slack', "
            "'retrieval', 'confluence' — see the 'Available Domains' section "
            "of your instructions) to execute one independent workstream in "
            "isolation. ALWAYS pass `tools` with the exact tool names for "
            "that domain — the sub-agent receives ONLY those tools, nothing "
            "else. To parallelize independent domains, call spawn_agent "
            "multiple times in the SAME turn (one call per domain). If one "
            "phase's sub-agent NEEDS another phase's output (e.g. a "
            "'coding'/file-generation phase that must build a PDF FROM a "
            "'jira' phase's ticket data), give both calls a `task_id` "
            "matching the plan's phase ids and set the dependent phase's "
            "`depends_on` to the prerequisite phase's `task_id` — still call "
            "spawn_agent for BOTH in the SAME turn; the runtime runs "
            "independent phases in parallel but holds the dependent one "
            "back until its prerequisite finishes, then automatically "
            "includes the prerequisite's result in the dependent's goal. "
            "Never rely on calling spawn_agent across separate turns to "
            "sequence dependent phases — use `depends_on` instead."
        )


def register_coordination_tools(registry: "ToolRegistry") -> None:
    """Registers the four builtin tools `OrchestratorLoop` composes over
    onto a fresh `ToolRegistry` (call once per request — `PipesHubToolLoader`
    already builds a new `ToolRegistry` per `AgentContext`, so there is no
    cross-request state to worry about; `ToolRegistry.register_tool` raises
    `DuplicateToolNameError`/`DuplicateToolPathError` if called twice on the
    same registry)."""
    for tool in (CreatePlanTool(), CritiquePlanTool(), DomainSpawnAgentTool(), VerifyResultTool()):
        registry.register_tool(tool)


def _phase_gate():
    """PRE_TOOL_USE middleware: deterministic enforcement of
    `OrchestratorLoop`'s Phase 1 -> Phase 2 boundary and of "never
    re-dispatch a `task_id` that already completed successfully this
    run" — both previously prompt-only conventions (see the Phase 1/2
    instructions in `OrchestratorLoop.run()`) that a model could ignore
    with no mechanical consequence.

    Scoped to `TAG_SPAWN` calls only (i.e. `spawn_agent`/`best_of_n`) —
    every other tool call passes through untouched. Install once per
    request via `install_phase_gate()`, alongside `register_coordination_
    tools()` — see `factory.py`.
    """

    async def _middleware(ctx: "ToolCallContext", next_fn) -> None:
        if TAG_SPAWN not in ctx.tags or ctx.scope is None:
            await next_fn()
            return

        run_scope = ctx.scope.turn.run
        if not run_scope.get(PLAN_DISPATCH_APPROVED_SLOT):
            ctx.deny(
                "spawn_agent is blocked until Phase 1 is complete (create_plan "
                "then critique_plan must run first — see your instructions)."
            )
            return

        task_id = ctx.tool_input.get("task_id")
        if task_id:
            from app.agent_loop_lib.agent.spawn_scheduler import SPAWN_RESULTS_SLOT
            completed = run_scope.get(SPAWN_RESULTS_SLOT).get(str(task_id).strip())
            if completed is not None and completed.result.success:
                ctx.deny(
                    f"task_id '{task_id}' already completed successfully earlier "
                    "this run — do not re-dispatch it. Reference its recorded "
                    "result instead, or use a new, different task_id for genuinely "
                    "new work."
                )
                return

        await next_fn()

    return _middleware


_PHASE_GATE_MARKER = "_orchestrator_phase_gate_installed"


def install_phase_gate(kernel: "HookRegistry") -> None:
    """Idempotently registers `_phase_gate()` on `kernel`'s PRE_TOOL_USE
    pipeline — call once per request, right after `register_coordination_
    tools()` (see `factory.py`). Idempotent per kernel instance for the
    same reason `install_turn_guards`/`install_supervisor_confidence_gate`
    are (see `hooks/middleware/builtin/turn_guards.py`): every agent in a
    request's spawn tree shares one runtime's kernel."""
    from app.agent_loop_lib.hooks.events import HookEvent

    if getattr(kernel, _PHASE_GATE_MARKER, False):
        return
    setattr(kernel, _PHASE_GATE_MARKER, True)
    kernel.on(HookEvent.PRE_TOOL_USE).use(_phase_gate())


def domain_spec_factory(
    *,
    provider: str,
    model_name: str,
    default_tool_names: list[str],
    context: "AgentContext | None" = None,
    max_turns: int = _SUB_AGENT_MAX_TURNS,
) -> "Callable[..., AgentSpec]":
    """Builds the `AgentRuntime.spec_factory` callable `spawn_agent` needs
    to resolve a domain "role" string into a concrete `AgentSpec` (see
    `AgentRuntime.spec_for_role`). `role_name` only labels the child's
    `AgentSpec.name` (tracing/timeline) — actual tool scoping always comes
    from `spawn_agent`'s `tools` argument (`overrides["tool_names"]`, see
    `tools/builtin/coordination/spawn_agent.py::build_spawn_child`), falling
    back to `default_tool_names` (every tool this request loaded, minus the
    four coordination tools) if the LLM omitted it."""

    def _factory(role_name: str, **overrides: Any) -> AgentSpec:  # noqa: ANN401
        tool_names = overrides["tool_names"] if "tool_names" in overrides else default_tool_names
        model = overrides.get("model") or model_name
        return AgentSpec(
            name=f"pipeshub-subagent-{role_name}",
            system_prompt=build_sub_agent_prompt(role_name, context),
            tool_names=list(tool_names),
            model=ModelSpec(provider=provider, model=model),
            loop=ReActLoop(),
            max_turns=max_turns,
        )

    return _factory


def _domain_overview(agent: "Agent") -> str:
    """Builds the "## Available Domains" block used in Phase 1's planning
    instructions, computed at RUN time (not wiring time) directly off
    `agent.runtime.tool_registry` so it always reflects exactly what
    `PipesHubToolLoader` + `domain_agents.register_domain_agents()` loaded
    for this request, with no separate state to keep in sync.

    Two sections, since the shared registry now mixes two different kinds
    of "domain" (see `factory.py`'s mode-driven composition):

    - Composed domain agents (`AgentTool` instances — no `app_name`, but
      each carries its own `description` from `domain_agents.py`'s
      catalog) are listed FIRST, one line each with its `short_description`,
      so `spawn_agent`'s `tools` can name one directly instead of the LLM
      re-deriving a raw tool list every request.
    - Any tool no domain agent claimed (composition disabled, or a tool
      outside every domain's `app_names`/`tool_names`) still groups by
      `Tool.app_name` exactly as before — the residual grant every
      `spawn_agent` call falls back to when a claimed domain isn't a fit.
    """
    registry = agent.runtime.tool_registry
    if registry is None:
        return ""

    max_shown_per_domain = 12
    agent_lines: list[str] = []
    groups: dict[str, list[str]] = {}
    for name in registry.names():
        if name in COORDINATION_TOOL_NAMES:
            continue
        # Never suggest a UI-only tool (e.g. ask_user_question) as
        # something a spawned step's `tool_names` could reference —
        # `AgentRuntime.run_child()` would silently strip it from the
        # child's grant anyway (see that module), so listing it here would
        # just be a plan the runtime partially ignores.
        if TAG_UI_ONLY in registry.tags_for_name(name):
            continue
        tool = registry.resolve_by_name(name)
        if isinstance(tool, AgentTool):
            agent_lines.append(f"- **{name}** (domain agent): {tool.short_description}")
            continue
        domain = getattr(tool, "app_name", None)
        if not domain:
            domain = name.split("__", 1)[0] if "__" in name else name
        groups.setdefault(domain, []).append(name)

    if not agent_lines and not groups:
        return "No external tool domains are configured for this request."

    lines = [
        "## Available Domains\n"
        "Use these names in your plan's `tool_names` field. For domain agents, "
        "pass a single name (e.g. `tool_names: [\"coding_agent\"]`). For "
        "residual tool groups, you can pass the group name (e.g. "
        "`tool_names: [\"jira\"]`) and the runtime will expand it to all "
        "tools in that group, OR pass individual tool names."
    ]
    if agent_lines:
        lines.append(
            "**Domain agents** (pass the name as a single tool — the agent "
            "handles its own domain-scoped search/fetch):"
        )
        lines.extend(agent_lines)
    if groups:
        lines.append(
            "**Tool groups** (pass the group name OR individual tool names):"
        )
        for domain, names in sorted(groups.items()):
            shown = ", ".join(f"`{n}`" for n in names[:max_shown_per_domain])
            if len(names) > max_shown_per_domain:
                shown += f", ... ({len(names) - max_shown_per_domain} more)"
            lines.append(f"- **{domain}** (group): {shown}")
    return "\n".join(lines)


def _step_goal_text(step: Any) -> str:
    """The synthetic spawn's actual goal text — `step.description` alone,
    plus `boundaries`/`output_format` when the plan set them (see
    `PlanStep`). A spawned child never sees its sibling steps or the
    original plan, only this goal string — a `boundaries`/`output_format`
    that stayed on the `PlanStep` object without being folded in here
    would be visible to `critique_plan`'s review but invisible to the
    agent that actually has to honor it."""
    parts = [step.description]
    if step.boundaries:
        parts.append("Do NOT do the following — other steps cover this:\n" + "\n".join(f"- {b}" for b in step.boundaries))
    if step.output_format:
        parts.append(f"Required output format: {step.output_format}")
    return "\n\n".join(parts)


async def _programmatic_dispatch(
    agent: "Agent",
    plan: "Plan",
    goal: "Goal",
    turn_index: int,
) -> tuple[bool, int]:
    """Execute the structured plan by building synthetic `spawn_agent`
    `ToolCall`s and routing them through `schedule_spawn_batch` — the same
    scheduler path LLM-mediated dispatch uses, so dependency resolution,
    event-based waiting, and result injection into dependent goals all work
    identically.

    Returns ``(success, new_turn_index)``.
    """
    from app.agent_loop_lib.agent.spawn_scheduler import schedule_spawn_batch

    if plan.steps is None:
        return False, turn_index

    synthetic_calls: list[ToolCall] = []
    for step in plan.steps:
        call_id = f"programmatic_spawn_{step.id}_{uuid.uuid4().hex[:8]}"
        synthetic_calls.append(ToolCall(
            id=call_id,
            name="spawn_agent",
            arguments={
                "role": step.domain,
                "goal": _step_goal_text(step),
                "task_id": step.id,
                "depends_on": step.depends_on,
                "tools": step.tool_names,
                "reasoning": f"Programmatic dispatch from structured plan step '{step.id}'",
            },
        ))

    logger.info(
        "OrchestratorLoop: programmatic dispatch of %d steps: %s",
        len(synthetic_calls),
        [s.id for s in plan.steps],
    )

    tasks = await schedule_spawn_batch(
        agent, agent.runtime, synthetic_calls, agent.scope,
        goal=goal, turn_index=turn_index, started_at=agent.started_at,
    )

    # Await all tasks and collect results — ALWAYS the same `{"output",
    # "success"}` shape regardless of whether the child returned normally
    # or the task itself raised, so nothing downstream has to sniff
    # "is this a dict or a string" (or scan `str(exception)` for the
    # substring "Error") to tell success from failure.
    results: dict[str, dict[str, Any]] = {}
    for call in synthetic_calls:
        try:
            child_result = await tasks[call.id]
            results[call.id] = child_result_content(child_result)
        except Exception as e:
            results[call.id] = {"output": str(e), "success": False}

    # Inject into the agent's conversation so Phase 3 (verify) sees the
    # spawn results in context — same shape as if the LLM had called
    # spawn_agent itself and the turn loop had recorded the results.
    context = agent.context
    assistant_msg = AssistantMessage(
        content=[TextPart(text="Dispatching structured plan programmatically.")],
        tool_calls=synthetic_calls,
    )
    await context.add(assistant_msg)
    for call in synthetic_calls:
        result = results.get(call.id, {"output": "No result", "success": False})
        output = result["output"]
        content_str = json.dumps(output, default=str) if not isinstance(output, str) else output
        if result.get("artifacts"):
            artifact_lines = "\n".join(
                f"- {a['name']} ({a['type']}){': ' + a['description'] if a.get('description') else ''}"
                for a in result["artifacts"]
            )
            content_str += f"\n\n[Artifacts attached — full data staged for dependent steps]\n{artifact_lines}"
        if result.get("needs_input"):
            # Surfaced verbatim (not just buried in a dict key) so Phase 3
            # (verify/finalize) actually notices — this is this step's
            # escalation to the root, which is the only agent with a real
            # UI surface to turn it into an `ask_user_question` call.
            content_str += f"\n\n[ESCALATION] {result['needs_input']}"
        await context.add(ToolMessage(
            content=content_str,
            tool_call_id=call.id,
            is_error=not result["success"],
        ))

    any_success = any(r["success"] for r in results.values())
    return any_success, turn_index


def build_phase1_planning_instructions(domain_block: str) -> str:
    """Phase 1's planning message, built here as a standalone function
    (not inlined into `OrchestratorLoop.run()`) so anything that needs the
    EXACT prompt production dispatches under — not a re-derived
    approximation of it — can reuse it verbatim. Currently: `OrchestratorLoop.
    run()` itself, and `app/agents/agent_loop/evals/decomposition_harness.py`
    (the deep-mode decomposition-quality regression harness), which builds
    a minimal single-phase agent against this SAME text to score real
    `create_plan` output without paying for a full orchestrator run's
    Phase 2/3 sub-agent dispatch."""
    return (
        "Phase 1 -- PLAN: decompose this goal into single-domain phases "
        "by calling create_plan with a structured `steps` array. Each "
        "step must have:\n"
        "- `id`: short stable identifier (e.g. 'fetch_jira')\n"
        "- `description`: clear, self-contained goal for the sub-agent\n"
        "- `domain`: which domain handles this step\n"
        "- `tool_names`: tool names from Available Domains below "
        "(use exact tool names or group names)\n"
        "- `depends_on`: IDs of steps whose output this step needs "
        "(empty if independent)\n"
        "- `boundaries` (2+ step plans): explicit 'do NOT also...' lines "
        "that separate this step's scope from every OTHER step's — the "
        "sub-agent executing a step never sees the other steps, only its "
        "own description, so an implicit boundary is invisible to it. "
        "Every pair of steps whose scopes could plausibly overlap "
        "(e.g. two steps that both touch 'recent tickets') needs this.\n"
        "- `output_format` (steps another step depends on): the exact "
        "shape to return (e.g. 'a table with columns Ticket, Assignee, "
        "Status', or 'a JSON list of {id, title}') — the step consuming "
        "this one's output has no format to rely on otherwise.\n\n"
        "## Effort scaling\n"
        "- A SIMPLE goal (single domain, no dependent steps, no file "
        "creation) needs exactly ONE step, or none at all if you can "
        "answer directly without create_plan/spawn_agent — do not "
        "manufacture multiple steps just to use the machinery.\n"
        "- A COMPLEX goal (multiple domains, or a domain whose work "
        "clearly splits into independent chunks) should use 3-5 steps "
        "with PARTITIONED, NON-OVERLAPPING scopes (see `boundaries` "
        "above) — never one step per domain AND one step per sub-topic "
        "within a domain, which produces duplicate or gapped coverage.\n\n"
        "## Tool assignment rules\n"
        "- Steps that FETCH data from a service should use that service's "
        "tool group (e.g. `tool_names: [\"jira\"]`, `tool_names: [\"confluence\"]`).\n"
        "- Steps that ONLY ANALYZE, SYNTHESIZE, or SUMMARIZE data from "
        "prior steps (no fetching, no file creation) need NO tools — use "
        "`tool_names: []`. The sub-agent receives upstream results "
        "directly in its context and produces text output.\n"
        "- Steps that CREATE FILES (docx, pdf, csv, images) should "
        "use `coding_agent`.\n"
        "- NEVER split analysis and file creation into two consecutive "
        "steps where the first analyzes data and the second creates a "
        "file from that analysis. Merge them into ONE `coding_agent` "
        "step instead — each step runs in an isolated sandbox that "
        "cannot access files from other steps.\n\n"
        "Then call critique_plan with the resulting text — critique "
        "SPECIFICALLY checks for scope overlap/gaps between steps, so "
        "unclear or missing `boundaries` on a multi-step plan is a "
        "likely reject. If critique_plan returns passed=false, call "
        "create_plan again with revised steps. Do not dispatch any "
        "sub-agents until critique_plan passes.\n\n" + domain_block
    )


def _phase1_no_verdict_nudge(agent: "Agent") -> str | None:
    """`PhaseDriver.run_planning_phase`'s `no_verdict_nudge` for Phase 1: a
    round that called `create_plan` but never `critique_plan` gets a
    pointed reminder — silently waiting for the model to notice on its own
    let it drift into Phase 2 with an uncritiqued plan often enough to be
    worth this one extra check."""
    latest_turn = agent.scope.turns[-1] if agent.scope is not None and agent.scope.turns else None
    called_create = latest_turn is not None and any(
        tr.name == "create_plan" for tr in latest_turn.tool_results
    )
    if not called_create:
        return None
    return (
        "Phase 1 -- VALIDATE: you called create_plan but "
        "did not call critique_plan. You MUST call "
        "critique_plan on the plan before it can be dispatched."
    )


class OrchestratorLoop(LoopStrategy):
    """Decompose -> Critique -> Dispatch -> Verify, composed purely over
    `create_plan` / `critique_plan` / `spawn_agent` / `verify_result` TOOLS.

    Phase 2 is programmatic when a structured plan is available (stored by
    `create_plan` in ``STRUCTURED_PLAN_SLOT``): the orchestrator reads the
    validated plan and calls ``schedule_spawn_batch`` directly with
    synthetic ToolCalls — no LLM-mediated translation, no wrong tool names,
    no misspelled task_ids.  Falls back to the old LLM-mediated dispatch
    when the structured plan is absent.
    """

    name = "orchestrator"

    def __init__(self, *, max_planning_rounds: int = 3, max_verify_rounds: int = 2) -> None:
        self._phases = PhaseDriver(max_planning_rounds=max_planning_rounds, max_verify_rounds=max_verify_rounds)

    async def run(self, agent: "Agent", goal: "Goal") -> "AgentResult":
        turn_index = agent.start_turn_index
        domain_block = _domain_overview(agent)

        # --- Phase 1: PLAN + CRITIQUE (orchestrator_node + critic_node) ---
        planning = await self._phases.run_planning_phase(
            agent, goal, turn_index,
            planning_message=build_phase1_planning_instructions(domain_block),
            replan_message=(
                "Phase 1 -- REPLAN: critique_plan did not pass. Revise "
                "the plan to address the issues raised, then call "
                "create_plan again with corrected steps and critique_plan "
                "again."
            ),
            no_verdict_nudge=_phase1_no_verdict_nudge,
        )
        if planning.stopped:
            return planning.result
        turn_index = planning.turn_index
        plan_validated = planning.passed

        if not plan_validated and turn_index < agent.max_turns:
            await agent.inject_user_message(
                "Phase 1 -- FINALIZE: critique did not pass after maximum "
                "revision rounds. Call create_plan ONE MORE TIME with your "
                "best corrected plan incorporating all feedback. This plan "
                "will be dispatched as-is — no further critique."
            )
            outcome = await agent.step(goal, turn_index)
            turn_index += 1
            if outcome.status == "stop":
                return outcome.result

        # Flip the deterministic gate (see `_phase_gate` below) now that
        # Phase 1 has run its course — either critique actually passed, or
        # the FINALIZE round above deliberately overrode it ("dispatched
        # as-is — no further critique"). Either way, THIS loop (not the
        # model) has now decided Phase 2 may begin, so `spawn_agent` calls
        # from here on are allowed regardless of which path got here.
        agent.scope.set(PLAN_DISPATCH_APPROVED_SLOT, True)

        # --- Phase 2: DISPATCH ---
        from app.agent_loop_lib.modules.pipeline.planner.base import STRUCTURED_PLAN_SLOT

        structured_plan = agent.scope.get(STRUCTURED_PLAN_SLOT)
        dispatched = False
        # Distinguishes "programmatic dispatch never actually ran" (plan
        # absent, or `_programmatic_dispatch` raised BEFORE scheduling any
        # child — e.g. building the synthetic ToolCalls itself blew up)
        # from "it ran and every child failed". Only the former is safe to
        # retry via LLM-mediated dispatch: once children have actually been
        # scheduled, their results (success or failure) are already
        # injected into the conversation (see `_programmatic_dispatch`), so
        # falling back to Phase 2's LLM-mediated path would re-run the same
        # plan a second time — re-executing side-effecting steps (a second
        # Jira ticket, a second email) and re-using `task_id`s the
        # scheduler now considers already-completed (`SPAWN_RESULTS_SLOT`),
        # which would just make every re-dispatch attempt fail validation
        # instead of actually retrying anything. When it ran and failed,
        # the correct move is to proceed to Phase 3 with the failures
        # already in context — the model synthesizes what succeeded and
        # can selectively re-`spawn_agent` (a NEW task_id) for what didn't.
        programmatic_dispatch_ran = False

        if structured_plan is not None and structured_plan.steps:
            try:
                programmatic_dispatch_ran = True
                dispatched, turn_index = await _programmatic_dispatch(
                    agent, structured_plan, goal, turn_index,
                )
                logger.info(
                    "OrchestratorLoop: programmatic dispatch %s (%d steps)",
                    "succeeded" if dispatched else "ran but every child failed",
                    len(structured_plan.steps),
                )
            except Exception:
                logger.warning(
                    "OrchestratorLoop: programmatic dispatch failed before "
                    "scheduling any child, falling back to LLM-mediated dispatch",
                    exc_info=True,
                )
                programmatic_dispatch_ran = False
                dispatched = False

        if not dispatched and not programmatic_dispatch_ran:
            # Fallback: LLM-mediated dispatch (original Phase 2) — only
            # reachable when programmatic dispatch never got to schedule a
            # single child, so there is nothing yet to double-execute.
            await agent.inject_user_message(
                "Phase 2 -- DISPATCH: the plan is approved. For EACH phase, "
                "call spawn_agent with `role` set to that phase's domain, "
                "`task_id` set to that phase's id from your plan, and `tools` "
                "set to the exact tool names for that domain (see 'Available "
                "Domains' above — you can use group names like 'jira' and "
                "the runtime will expand them). Call EVERY phase's "
                "spawn_agent IN THE SAME TURN, including phases that depend "
                "on another phase's result — for those, set `depends_on` to "
                "the prerequisite phase's `task_id`; the runtime enforces "
                "the ordering and hands the prerequisite's result to the "
                "dependent phase automatically. Do NOT try to sequence "
                "dependent phases by calling spawn_agent in a later turn "
                "instead — always use `depends_on`."
            )
            dispatch_retries = 0
            max_dispatch_retries = 2
            while turn_index < agent.max_turns:
                outcome = await agent.step(goal, turn_index)
                turn_index += 1
                if outcome.status == "stop":
                    return outcome.result
                if agent.last_tool_result("spawn_agent") is not None:
                    if agent.has_successful_tool_result("spawn_agent"):
                        dispatched = True
                        break
                    dispatch_retries += 1
                    if dispatch_retries >= max_dispatch_retries:
                        dispatched = True
                        break
                    await agent.inject_user_message(
                        "Phase 2 -- RETRY: all spawn_agent calls this turn "
                        "failed (check the error messages above — common "
                        "causes: typo in task_id or depends_on, wrong tool "
                        "names, duplicate task_id). Fix the issues and call "
                        "spawn_agent again for all phases in a single turn."
                    )

            if not dispatched:
                return await agent.fail(
                    goal,
                    f"Exceeded max_turns={agent.max_turns} without dispatching any sub-agent",
                    event="agent_failed",
                )

        # --- Phase 3: VERIFY + retry (aggregator_node) ---
        verify = await self._phases.run_verify_phase(
            agent, goal, turn_index,
            verify_message=(
                "Phase 3 -- VERIFY: synthesize the sub-agents' results into a "
                "candidate final answer, then call verify_result with it before "
                "replying. If verify_result fails, spawn additional sub-agents "
                "or revise your synthesis and call verify_result again."
            ),
            revise_message=(
                "Phase 3 -- REVISE: verify_result did not pass. "
                "Spawn more sub-agents or revise your synthesis, "
                "then call verify_result again."
            ),
            finish_message=(
                "Phase 3 -- FINISH: verification still failing after "
                "retries. Reply now with your best current answer "
                "(no more tool calls)."
            ),
        )
        if verify.stopped:
            return verify.result

        return await agent.fail(goal, f"Exceeded max_turns={agent.max_turns}", event="agent_failed")


__all__ = [
    "COORDINATION_TOOL_NAMES",
    "PLAN_DISPATCH_APPROVED_SLOT",
    "DomainSpawnAgentTool",
    "OrchestratorLoop",
    "build_phase1_planning_instructions",
    "domain_spec_factory",
    "install_phase_gate",
    "register_coordination_tools",
]
