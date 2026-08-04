from __future__ import annotations

from typing import Any

from app.agent_loop_lib.agent import observability as obs
from app.agent_loop_lib.core.exceptions import ToolError
from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.special_route import RouteContext


class ClarifyTool(Tool):
    """Runs via `SpecialRouteHandler.handle()` (below) — pauses for HIL via
    `agent/observability.py:handle_clarify`. Never executed directly."""

    @property
    def name(self) -> str:
        return "clarify"

    @property
    def short_description(self) -> str:
        return "Request clarification on ambiguous requirements before proceeding."

    @property
    def description(self) -> str:
        return "Request clarification on ambiguous requirements before proceeding"

    @property
    def path(self) -> str:
        return "/toolsets/builtin/clarify"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="question",
                type=ParameterType.STRING,
                description="The clarification question to ask",
                required=True,
            ),
            ToolParameter(
                name="context",
                type=ParameterType.STRING,
                description="Additional context explaining why clarification is needed",
                required=False,
                default=None,
            ),
        ]

    async def handle(self, call: ToolCall, ctx: RouteContext) -> CoreToolResult:
        agent = ctx.agent
        await obs.write_state(agent, ctx.goal, "waiting_hil", turn_index=ctx.turn_index, started_at=ctx.started_at, current_tool="clarify")
        await obs.append_timeline(
            agent, "hil_pause", f"Waiting for clarification: {call.arguments.get('question', '')[:80]}",
            "waiting_hil", {"question": call.arguments.get("question", "")},
        )
        return await obs.handle_clarify(agent, call, ctx.goal, ctx.messages, ctx.turn_index)

    async def execute(self, **kwargs: Any) -> ToolOutput:
        raise ToolError("clarify is a special route (see SpecialRouteHandler.handle()) and must not be executed directly")
