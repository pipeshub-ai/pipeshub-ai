from __future__ import annotations

from typing import Any

from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.tools.base import Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.special_route import RouteContext

"""`create_plan` — exposes `planner.default.DefaultPlanner` as a tool call
instead of only a programmatic pre-loop step (see `.claude/rules/
principles.md`'s gap map: "Planner via tool call ... Should become a tool
the agent calls mid-loop"). `AgentConfig.planner` + `agent/preamble.py`'s
upfront call remain for backward-compatible, deterministic pre-loop
planning; this tool is the additive, probabilistic path — any agent
(including a sub-agent with no preamble planner configured) can decide FOR
ITSELF, mid-run, that it wants a decomposed plan and call this instead of a
program deciding it on its behalf.

Real work happens in `handle()` (needs the run's resolved transport, same
pattern as `tools/builtin/planning/replan.py`); `execute()` stays a harmless default
for direct/standalone invocation.
"""

__all__ = ["CreatePlanTool"]


class CreatePlanTool(Tool):
    @property
    def name(self) -> str:
        return "create_plan"

    @property
    def short_description(self) -> str:
        return "Decompose the current goal into ordered execution phases."

    @property
    def description(self) -> str:
        return (
            "Decompose the current goal into ordered execution phases (with a "
            "confidence rating) using an LLM-driven planner. Call this when the "
            "goal is complex enough to benefit from an explicit plan before you "
            "start executing — follow up with write_todos to track the phases."
        )

    @property
    def path(self) -> str:
        return "/toolsets/builtin/create_plan"

    @property
    def parameters(self) -> list[ToolParameter]:
        return []

    async def handle(self, call: ToolCall, ctx: RouteContext) -> CoreToolResult:
        from app.agent_loop_lib.modules.pipeline.planner.default import DefaultPlanner

        model = None
        if ctx.runtime.transport_registry is not None:
            try:
                model = ctx.spec.model.resolve(ctx.runtime.transport_registry)
            except Exception:
                model = None
        if model is None:
            return CoreToolResult(
                tool_call_id=call.id, name=call.name,
                content="No model available to plan", is_error=True,
            )

        try:
            plan = await DefaultPlanner(model).plan(ctx.goal)
            content: object = {
                "phases": [p.model_dump() for p in plan.phases],
                "confidence": plan.confidence.value,
            }
            is_error = False
        except Exception as e:
            content = str(e)
            is_error = True

        return CoreToolResult(tool_call_id=call.id, name=call.name, content=content, is_error=is_error)

    async def execute(self, **kwargs: Any) -> ToolOutput:
        return ToolOutput(success=True, data={"phases": [], "confidence": "low"})
