from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.agent import observability as obs
from app.agent_loop_lib.core.exceptions import ToolError
from app.agent_loop_lib.core.types import AgentResult, Goal, ToolCall
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.events.base import EventType, ToolCallStatus
from app.agent_loop_lib.tools.base import ParameterType, Tag, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.builtin.coordination.agent_tool import AgentTool
from app.agent_loop_lib.tools.special_route import RouteContext
from app.agent_loop_lib.tools.tags import TAG_SPAWN, TAG_SPAWN_BATCH

if TYPE_CHECKING:
    from app.agent_loop_lib.agent.spec import AgentSpec
    from app.agent_loop_lib.core.context import RunContext
    from app.agent_loop_lib.runtime.runtime import AgentRuntime

__all__ = ["SpawnAgentTool", "build_spawn_child", "child_result_content", "run_spawned_child"]


def child_result_content(child_result: AgentResult) -> dict[str, Any]:
    """The tool-result content a completed child's `AgentResult` becomes
    for its caller — shared by `SpawnAgentTool.handle()` and
    `orchestrator.py`'s `_programmatic_dispatch` (LLM-mediated and
    programmatic dispatch must expose the same shape to the calling
    agent's context, or which dispatch path ran would leak into what the
    orchestrator model sees).

    Artifacts are refs only (name/type/description), never the full
    `content` — the full bytes reach a DEPENDENT, not this caller, via
    `spawn_scheduler._artifact_files`/`stage_input_files`. `confidence`/
    `record_ids` are included whenever the child set them (the typed
    output contract, see `AgentResult` in `core/types.py`). `needs_input`
    is called out with an explicit prefix: it means the child could not
    fully complete its goal and is escalating upward — burying that key
    inside an otherwise-ordinary dict risks the calling model treating a
    partial result as a complete one."""
    content: dict[str, Any] = {"output": child_result.output, "success": child_result.success}
    if child_result.artifacts:
        content["artifacts"] = [
            {"name": a.name, "type": a.type.value, "description": a.description}
            for a in child_result.artifacts
        ]
    if child_result.confidence is not None:
        content["confidence"] = child_result.confidence.value
    if child_result.record_ids:
        content["record_ids"] = child_result.record_ids
    if child_result.needs_input:
        content["needs_input"] = (
            f"This sub-agent could not fully complete its task — it needs: {child_result.needs_input}"
        )
    return content


def build_spawn_child(runtime: "AgentRuntime", call: ToolCall) -> tuple["AgentSpec", Goal]:
    """Parse a `spawn_agent` tool call's arguments into a concrete
    `(AgentSpec, Goal)` ready for `run_spawned_child()` — shared by
    `SpawnAgentTool.handle()` and `Agent.step()`'s parallel-batch
    pre-launch, so both build the exact same child regardless of whether
    it ends up running solo or alongside sibling spawns."""
    args = call.arguments
    role_name: str = args["role"]
    goal_desc: str = args["goal"]
    overrides: dict[str, Any] = {}
    explicit_tools = args.get("tools")
    if explicit_tools is not None:
        overrides["tool_names"] = list(explicit_tools)
    model_override = args.get("model")
    if model_override:
        overrides["model"] = model_override
    spec = runtime.spec_for_role(role_name, **overrides)
    return spec, Goal(description=goal_desc)


def _single_agent_tool(runtime: "AgentRuntime", spec: "AgentSpec") -> AgentTool | None:
    """If `spec.tool_names` resolves to EXACTLY one registered tool and
    that tool is an `AgentTool`, returns it — this is the "the plan step
    delegated to one domain agent" shape a generic ReAct wrapper spec
    would do nothing with except phrase ONE tool call to it and then
    lossily re-narrate its result as its own final answer (an extra LLM
    round-trip AND a fidelity loss — see `run_spawned_child`). Returns
    `None` for every other shape (no tools, several tools, or a tool
    that isn't agent-shaped), meaning "run the wrapper normally".

    Excludes an `AgentTool` with `share_parent_results=True`: that
    behavior needs the CALLING agent's own conversation, which only
    `AgentTool.handle()` (dispatched with a real `RouteContext`) has
    access to — bypassing it here would silently drop that child's
    parent-results digest/staged file."""
    registry = runtime.tool_registry
    if registry is None or not spec.tool_names:
        return None
    expanded = registry.expand_tool_names(list(spec.tool_names))
    if len(expanded) != 1:
        return None
    tool = registry.resolve_by_name(expanded[0])
    if not isinstance(tool, AgentTool) or tool.share_parent_results:
        return None
    return tool


async def run_spawned_child(
    runtime: "AgentRuntime",
    spec: "AgentSpec",
    goal: Goal,
    parent_run_ctx: "RunContext | None",
    **run_child_kwargs: Any,
) -> AgentResult:
    """Runs one spawned child, transparently skipping the generic ReAct
    wrapper `spec` describes when it would do nothing but relay to a
    single composed domain `AgentTool` (see `_single_agent_tool`) —
    dispatches straight to that `AgentTool`'s OWN spec instead (its
    tuned `max_turns`/name/system prompt), applying its `result_note`
    the same way `AgentTool.handle()` would. One fewer LLM turn (the
    wrapper deciding to make the one call it always makes), and the
    domain's own findings reach the caller verbatim instead of being
    re-phrased by the wrapper's own final-answer turn — the reason
    `internal_exploration_agent` needs a `result_note` at all is that a
    condensing rewrite was already observed happening exactly at that
    now-removed hop.

    Every other spawn shape (no single-`AgentTool` match) runs exactly
    as before, via `runtime.run_child(spec, ...)` unchanged."""
    direct_tool = _single_agent_tool(runtime, spec)
    if direct_tool is None:
        return await runtime.run_child(spec, goal, parent_run_ctx, **run_child_kwargs)

    result = await runtime.run_child(direct_tool.spec, goal, parent_run_ctx, **run_child_kwargs)
    if result.success:
        # `finalize_output` (not the narrower `apply_result_note`) so a
        # `needs_input` escalation survives this bypass too — see
        # `AgentTool.finalize_output`'s docstring.
        result = result.model_copy(update={"output": direct_tool.finalize_output(result)})
    return result


async def _run_detached_spawn(agent, call: ToolCall, goal, turn_index: int, started_at: str) -> None:
    """Background body for a detach=true spawn_agent call — awaits the
    child on its own task (never on the parent's turn loop) and reports
    the outcome via the timeline/events once it lands.

    This is the ONLY place a `detach=true` call's child ever gets
    launched — `Agent.step()`'s `spawn_agent` pre-launch batch excludes
    detached calls specifically so `handle()` below never launches a
    SECOND child for the same call (see `agent/__init__.py`'s
    `spawn_calls` filter). The completion is still recorded into
    `SPAWN_RESULTS_SLOT` (same as a scheduled spawn) so a `depends_on`
    from a LATER turn can reference this task_id once it lands."""
    # Deferred import: `spawn_scheduler` imports `tools.builtin.coordination
    # .graph_utils`, and `tools/builtin/__init__.py` imports THIS module —
    # a module-level import here would be circular.
    from app.agent_loop_lib.agent.spawn_scheduler import raw_task_id, record_completed_spawn

    task_id = raw_task_id(call)
    try:
        child_spec, child_goal = build_spawn_child(agent.runtime, call)
        child_result = await run_spawned_child(
            agent.runtime, child_spec, child_goal, agent.run_ctx,
            session_id=agent.session_id, parent_scope=agent.scope,
        )
        if agent.scope is not None:
            record_completed_spawn(agent.scope, task_id, child_result)
        await obs.append_timeline(
            agent, "spawn_agent_detached_complete",
            f"Detached child agent finished (role={call.arguments.get('role', '?')})",
            "completed" if child_result.success else "failed",
            {"success": child_result.success, "output": str(child_result.output)[:200]},
        )
        await agent.emit(EventType.TOOL_RESULT, {
            "tool": "spawn_agent",
            "is_error": not child_result.success,
            "content": f"[detached] {str(child_result.output)[:200]}",
            "status": ToolCallStatus.SUCCESS if child_result.success else ToolCallStatus.ERROR,
        })
    except Exception as e:
        if agent.scope is not None:
            failed = AgentResult(
                goal=Goal(description=call.arguments.get("goal", "")),
                success=False,
                error=f"detached spawn_agent task '{task_id}' failed with an infrastructure error: {e}",
            )
            record_completed_spawn(agent.scope, task_id, failed)
        await obs.append_timeline(
            agent, "spawn_agent_detached_failed",
            f"Detached child agent failed: {e}", "failed", {"error": str(e)},
        )
    finally:
        agent._detached_tasks.discard(asyncio.current_task())


class SpawnAgentTool(Tool):
    """Runs via `SpecialRouteHandler.handle()` (below), not the sentinel
    `execute()` — spawning a child run needs the parent Agent's `runtime`/
    `run_ctx`/`_detached_tasks`/`_pending_spawn_tasks`, none of which a
    stateless `Tool` instance shared across the whole registry can hold.
    """

    @property
    def name(self) -> str:
        return "spawn_agent"

    @property
    def short_description(self) -> str:
        return "Spawn a sub-agent to handle one independent workstream."

    @property
    def description(self) -> str:
        return (
            "Spawn a sub-agent to handle one independent workstream. "
            "USE ONLY when the task has 3 or more truly independent sub-tasks that each require "
            "deep, dedicated research (5+ searches each). For single-domain tasks — even multi-part "
            "ones — use web_search directly instead; it is faster and cheaper. "
            "Available roles: researcher, writer, planner, critic, verifier. "
            "If one sub-task NEEDS another sub-task's output (e.g. 'summarize X' then 'write a "
            "report from that summary'), give both a `task_id` and set the dependent's "
            "`depends_on` to the prerequisite's `task_id` — you may still call spawn_agent for "
            "BOTH in the same turn; the runtime runs independent tasks in parallel but holds the "
            "dependent one back until its prerequisite finishes, then automatically includes the "
            "prerequisite's result in the dependent's context. Never rely on turn order alone to "
            "sequence dependent work — use depends_on."
        )

    @property
    def path(self) -> str:
        return "/toolsets/builtin/spawn_agent"

    @property
    def tags(self) -> list[Tag]:
        return [TAG_SPAWN, TAG_SPAWN_BATCH]

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="role",
                type=ParameterType.STRING,
                description="One of: researcher, writer, planner, critic, verifier",
                required=True,
            ),
            ToolParameter(
                name="goal",
                type=ParameterType.STRING,
                description="Clear, self-contained description of exactly what this sub-agent must accomplish",
                required=True,
            ),
            ToolParameter(
                name="task_id",
                type=ParameterType.STRING,
                description=(
                    "A short, stable name for this sub-task (e.g. 'fetch_tickets'), used to "
                    "reference it from another spawn_agent call's `depends_on`. Optional — "
                    "defaults to this call's own id if omitted, but an explicit name makes "
                    "`depends_on` readable when dispatching several tasks together."
                ),
                required=False,
                default=None,
            ),
            ToolParameter(
                name="depends_on",
                type=ParameterType.ARRAY,
                description=(
                    "task_ids of other spawn_agent calls (this turn, or from an earlier turn "
                    "in this same run) whose output this sub-task needs before it can start. "
                    "The runtime waits for every listed prerequisite to finish and automatically "
                    "includes its result in this sub-task's context — do not describe the "
                    "prerequisite's expected output yourself, just reference its task_id. Leave "
                    "empty (default) for a fully independent sub-task."
                ),
                required=False,
                default=None,
                items={"type": "string"},
            ),
            ToolParameter(
                name="reasoning",
                type=ParameterType.STRING,
                description=(
                    "Why this sub-task cannot be handled by you directly with web_search. "
                    "Must name the specific independent domain and why dedicated focus is needed. "
                    "If you cannot give a clear reason, do not spawn — use web_search instead."
                ),
                required=True,
            ),
            ToolParameter(
                name="tools",
                type=ParameterType.ARRAY,
                description="Tools to give the sub-agent, e.g. ['web_search','web_scrape','task_complete']",
                required=False,
                default=None,
                items={"type": "string"},
            ),
            ToolParameter(
                name="model",
                type=ParameterType.STRING,
                description="Override the model for this sub-agent (leave blank to inherit)",
                required=False,
                default=None,
            ),
            ToolParameter(
                name="detach",
                type=ParameterType.BOOLEAN,
                description=(
                    "If true, launch this sub-agent asynchronously and continue immediately "
                    "instead of waiting for it — use for background workstreams whose results "
                    "aren't needed to keep making progress right now. The outcome is reported "
                    "later via the timeline, not as this call's result."
                ),
                required=False,
                default=None,
            ),
        ]

    async def handle(self, call: ToolCall, ctx: RouteContext) -> CoreToolResult:
        agent = ctx.agent
        goal = ctx.goal
        turn_index = ctx.turn_index
        started_at = ctx.started_at

        # --- Async subagents: fire-and-forget ---
        if call.arguments.get("detach"):
            await obs.write_state(agent, goal, "spawning_agent", turn_index=turn_index, started_at=started_at, current_tool="spawn_agent")
            await obs.append_timeline(
                agent, "spawn_agent", "Spawning child agent (detached/async)", "spawning_agent",
                {"args": {**call.arguments, "_detached": True}},
            )
            task = asyncio.create_task(_run_detached_spawn(agent, call, goal, turn_index, started_at))
            agent._detached_tasks.add(task)
            return CoreToolResult(
                tool_call_id=call.id, name=call.name,
                content={
                    "detached": True,
                    "note": "Sub-agent launched in the background. Its result will appear "
                            "in the timeline/events later, not as a tool_result in this turn.",
                },
            )

        # `Agent.step()` pre-launches EVERY spawn_agent call in a turn (one
        # call or many) as a concurrent, dependency-aware `asyncio.Task` via
        # `spawn_scheduler.schedule_spawn_batch` — this call just awaits its
        # own slot. The `call.id not in spawn_tasks` fallback below only
        # matters for a handler invoked outside that normal turn loop (e.g.
        # a test driving `handle()` directly): it runs the SAME scheduler
        # as a batch-of-one against the agent's own `RunScope`, so
        # `task_id`/`depends_on` behave identically either way.
        spawn_tasks = getattr(agent, "_pending_spawn_tasks", {}) or {}
        try:
            if call.id in spawn_tasks:
                child_result = await spawn_tasks[call.id]
            else:
                from app.agent_loop_lib.agent.spawn_scheduler import schedule_spawn_batch
                solo_tasks = await schedule_spawn_batch(
                    agent, ctx.runtime, [call], ctx.scope.turn.run,
                    goal=goal, turn_index=turn_index, started_at=started_at,
                )
                child_result = await solo_tasks[call.id]
            return CoreToolResult(
                tool_call_id=call.id, name=call.name, content=child_result_content(child_result),
            )
        except Exception as e:
            return CoreToolResult(tool_call_id=call.id, name=call.name, content=str(e), is_error=True)

    async def execute(self, **kwargs: Any) -> ToolOutput:
        raise ToolError("spawn_agent is a special route (see SpecialRouteHandler.handle()) and must not be executed directly")
