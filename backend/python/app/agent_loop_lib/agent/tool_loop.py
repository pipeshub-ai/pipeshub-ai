from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.agent_loop_lib.agent import observability as obs
from app.agent_loop_lib.core.scope import ToolScope
from app.agent_loop_lib.core.types import Artifact, Goal, Message, ToolCall, ToolResult
from app.agent_loop_lib.events.base import EventType
from app.agent_loop_lib.tools.special_route import RouteContext, SpecialRouteRegistry

if TYPE_CHECKING:
    from app.agent_loop_lib.agent.spec import AgentSpec
    from app.agent_loop_lib.core.scope import TurnScope
    from app.agent_loop_lib.core.tool_schema import ToolSchema
    from app.agent_loop_lib.runtime.runtime import AgentRuntime

"""Per-tool-call dispatch, extracted from `Agent.step()`.

Handles exactly one `ToolCall`: duplicate-search detection, looking up a
`SpecialRouteHandler` for tools that need Agent-level state (spawn_agent,
best_of_n, clarify, replan, handoff, write_todos, fetch_tools — see
`tools/special_route.py`), and delegating the whole resolve -> PreToolUse
-> execute -> PostToolUse sequence to `ToolExecutor.call_tool()` — one call
per tool call, regardless of which route it takes, so permission/mode/
approval/audit-log middleware apply uniformly.
"""


@dataclass
class ToolCallOutcome:
    result: ToolResult
    task_done: bool = False
    final_output: object = None
    artifacts: list[Artifact] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.artifacts is None:
            self.artifacts = []


def initial_visible_tools(spec: "AgentSpec", runtime: "AgentRuntime") -> set[str]:
    """Essentials + pinned toolsets, per the lazy-toolsets design tenet:
    only overviews + meta-tools + role-pinned essentials ship upfront.
    Anything not assigned to any toolset is an "essential" by definition."""
    registry = runtime.tool_registry
    all_names = set(registry.names())
    grouped = registry.grouped_tool_names()
    essentials = all_names - grouped
    for toolset_name in spec.pinned_toolsets:
        essentials.update(registry.tools_in_toolset(toolset_name))
    return essentials


def tool_schemas_for_turn(agent, spec: "AgentSpec", runtime: "AgentRuntime") -> list["ToolSchema"]:
    """Which tool schemas the model sees this turn — all of them, unless
    the registry opts into lazy toolsets, in which case it's essentials
    plus whatever `agent.visible_tools` has grown to via fetch_tools."""
    registry = runtime.tool_registry
    if registry is None:
        return []
    if not registry.has_toolsets():
        return registry.schemas(spec.tool_names or None)

    if agent.visible_tools is None:
        agent.visible_tools = initial_visible_tools(spec, runtime)

    if spec.tool_names:
        # spec.tool_names is the explicit tool grant — ALL named tools
        # must be visible from the start. Lazy disclosure (grouping tools
        # into toolsets and hiding them until fetch_tools loads them) only
        # applies to tools BEYOND what the spec explicitly lists. Without
        # this, a child agent spawned with a focused tool list (e.g.
        # ['web_search', 'web_scrape', 'task_complete']) would be
        # permanently locked out of grouped tools it was explicitly given,
        # since it lacks the list_toolsets/fetch_tools meta-tools needed
        # to discover and unlock them.
        return registry.schemas(spec.tool_names)

    return registry.schemas(list(agent.visible_tools))


async def execute_tool_call(
    agent,
    call: ToolCall,
    spec: "AgentSpec",
    runtime: "AgentRuntime",
    goal: Goal,
    messages: list[Message],
    turn_index: int,
    started_at: str,
    seen_tool_calls: set[str],
    response_msg: Message,
    turn_scope: "TurnScope",
) -> ToolCallOutcome:
    """Handles exactly one `ToolCall`. `turn_scope` (created once per
    `Agent.step()` call) is used to build this call's `ToolScope` — carrying
    the tool identity plus the FULL scope chain (`.turn.run...`) down into
    both the PreToolUse/PostToolUse decision contexts (via `ToolExecutor.
    call_tool(scope=...)`) and special-route handlers (via `RouteContext.
    scope`). `messages` (already a fresh `context.messages()` snapshot taken
    by `Agent.step()` AFTER the assistant's response was recorded) becomes
    `ToolScope.messages` — see `core/scope.py` for why that snapshot timing
    is load-bearing for `clarify`'s HIL checkpoint."""
    await agent.emit(EventType.TOOL_CALL, {"tool": call.name, "args": call.arguments})

    # Detect repeated tool calls (same tool + same key argument)
    call_sig = f"{call.name}:{json.dumps(call.arguments, sort_keys=True)}"
    if call_sig in seen_tool_calls and call.name in ("web_search", "web_scrape"):
        tr = ToolResult(
            tool_call_id=call.id, name=call.name,
            content=(
                "[Duplicate call skipped — you already ran this exact search/scrape. "
                "Use the results you already have or call task_complete to finish.]"
            ),
            is_error=False,
        )
        await agent.emit(EventType.TOOL_RESULT, {
            "tool": tr.name, "is_error": tr.is_error,
            "content": str(tr.content)[:200],
        })
        return ToolCallOutcome(result=tr)
    seen_tool_calls.add(call_sig)

    # --- Checkpoint: PRE_TOOL ---
    await obs.save_checkpoint(agent, "pre_tool", goal, messages, turn_index, current_tool=call.name)

    # `tool_path` starts empty — `ToolExecutor.call_tool()` resolves the
    # real path/tags and fills them onto this SAME scope object before
    # dispatching PRE_TOOL_USE, so by the time `override_execute()` below
    # actually runs (from inside `_run()`, after PRE_TOOL_USE), `tool_scope.
    # tool_path`/`.tags` are already correct for handlers that read them.
    tool_scope = ToolScope(turn=turn_scope, call=call, tool_path="", messages=messages)

    handler = SpecialRouteRegistry(runtime.tool_registry).get(call.name)
    override_execute = None
    if handler is not None:
        ctx = RouteContext(agent=agent, scope=tool_scope)

        async def override_execute() -> ToolResult:
            return await handler.handle(call, ctx)
    else:
        # --- Normal tool execution ---
        await obs.write_state(agent, goal, "running_tool", turn_index=turn_index, started_at=started_at, current_tool=call.name)
        await obs.append_timeline(agent, "tool_call", f"Calling tool: {call.name}", "running_tool", {"tool": call.name, "args": call.arguments})

    blocked_reason: str | None = None

    async def _on_denied(reason: str) -> None:
        nonlocal blocked_reason
        blocked_reason = reason
        await agent.emit(EventType.TOOL_BLOCKED, {"tool": call.name, "reason": reason})
        await obs.append_timeline(
            agent, "tool_blocked", f"Tool blocked: {call.name} — {reason}", "running_tool",
            {"tool": call.name, "args": call.arguments, "reason": reason},
        )

    tr = await agent._executor.call_tool(
        call, session_id=agent.session_id, override_execute=override_execute,
        on_denied=_on_denied, scope=tool_scope,
    )

    # --- Record budget tool call ---
    if runtime.budget is not None:
        await runtime.budget.record_tool_call()

    if blocked_reason is not None:
        return ToolCallOutcome(result=tr)

    await agent.emit(EventType.TOOL_RESULT, {
        "tool": tr.name, "is_error": tr.is_error,
        "content": str(tr.content)[:200],
    })

    if tr.sources:
        await obs.append_timeline(
            agent, "tool_result_sources", f"Sources from {call.name}", "running_tool",
            {"tool": call.name, "is_error": tr.is_error, "sources": [s.model_dump() for s in tr.sources]},
        )

    # --- task_complete detection ---
    if call.name == "task_complete" and not tr.is_error:
        from app.agent_loop_lib.tools.builtin.planning.task_complete import (
            TaskCompleteTool,
        )

        outcome = TaskCompleteTool.extract_outcome(tr, call, agent.extract_text(response_msg))
        if outcome.error_result is not None:
            return ToolCallOutcome(result=outcome.error_result)
        return ToolCallOutcome(
            result=tr, task_done=outcome.task_done,
            final_output=outcome.final_output, artifacts=outcome.artifacts,
        )

    return ToolCallOutcome(result=tr)
