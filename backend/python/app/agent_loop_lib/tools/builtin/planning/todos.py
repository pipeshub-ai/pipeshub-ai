from __future__ import annotations

from typing import Any

from app.agent_loop_lib.agent import observability as obs
from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.special_route import RouteContext


class WriteTodosTool(Tool):
    """Structured, in-loop task list — the agent-driven replacement for the
    Planner's pre-loop-only, free-form `Plan.text` (see
    modules/pipeline/planner/base.py), updatable mid-run as priorities
    shift or steps complete.

    This tool itself is stateless: the actual list lives on the calling
    `Agent` instance (`agent.todos`, part of the `AgentHandle` surface),
    mutated by `handle()` below — same split as fetch_tools/list_toolsets
    use for tool-visibility state.
    `execute()` just echoes back a confirmation payload, for direct/
    standalone invocation (e.g. tests calling the tool without a full
    Agent run).
    """

    @property
    def name(self) -> str:
        return "write_todos"

    @property
    def short_description(self) -> str:
        return "Create or update your in-loop task list."

    @property
    def description(self) -> str:
        return (
            "Create or update your task list for this run. ALWAYS pass the "
            "COMPLETE list (this replaces the previous one, it does not "
            "append) with one status per item: 'pending', 'in_progress', "
            "or 'completed'. Keep exactly one item 'in_progress' at a time. "
            "Use this to plan multi-step work upfront and mark items "
            "'completed' as soon as they're done — the list is shown back "
            "to you every turn so it stays useful for tracking progress."
        )

    @property
    def path(self) -> str:
        return "/toolsets/builtin/write_todos"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="todos",
                type=ParameterType.ARRAY,
                required=True,
                description=(
                    "Full task list: objects with 'content' (str) and "
                    "'status' ('pending' | 'in_progress' | 'completed')."
                ),
                items={"type": "object"},
            ),
        ]

    async def handle(self, call: ToolCall, ctx: RouteContext) -> CoreToolResult:
        from app.agent_loop_lib.core.types import Todo

        agent = ctx.agent
        raw_todos = call.arguments.get("todos", [])
        agent.todos = [t if isinstance(t, Todo) else Todo(**t) for t in raw_todos]
        await obs.write_state(agent, ctx.goal, "running_tool", turn_index=ctx.turn_index, started_at=ctx.started_at, current_tool="write_todos")
        await obs.append_timeline(
            agent, "write_todos", f"Updated todo list ({len(agent.todos)} item(s))", "running_tool",
            {"todos": [t.model_dump() for t in agent.todos]},
        )
        return CoreToolResult(tool_call_id=call.id, name=call.name, content={"count": len(agent.todos)})

    async def execute(self, todos: list[dict] | None = None, **kwargs: Any) -> ToolOutput:
        todos = todos or []
        return ToolOutput(success=True, data={"status": "ok", "count": len(todos), "todos": todos})
