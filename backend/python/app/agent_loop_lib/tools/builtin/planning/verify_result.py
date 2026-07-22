from __future__ import annotations

from typing import Any

from app.agent_loop_lib.core.exceptions import ToolError
from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.special_route import RouteContext

"""`verify_result` — exposes `critic.result_critic.ResultCritic` as a tool
call (see `.claude/rules/principles.md`'s gap map entry on the double-
critique loop). This tool lets an agent explicitly self-verify a candidate
answer BEFORE calling task_complete, closing the loop's "critique again
after verify, before declaring complete" step from the design's
Intent -> Spec -> Execute pipeline diagram. A `LoopStrategy` (e.g.
`PlanCritiqueExecuteLoop`, `IncrementalLoop`) can also call this
programmatically between steps."""

__all__ = ["VerifyResultTool"]


class VerifyResultTool(Tool):
    @property
    def name(self) -> str:
        return "verify_result"

    @property
    def short_description(self) -> str:
        return "Verify a candidate final answer against the goal's success criteria."

    @property
    def description(self) -> str:
        return (
            "Verify a candidate final answer against the goal's success "
            "criteria before calling task_complete. Returns passed/confidence/"
            "issues — call this when you are about to finish but want a "
            "second, structured check on whether the answer actually satisfies "
            "the goal."
        )

    @property
    def path(self) -> str:
        return "/toolsets/builtin/verify_result"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="output",
                type=ParameterType.STRING,
                description="The candidate final output to verify.",
                required=True,
            ),
        ]

    async def handle(self, call: ToolCall, ctx: RouteContext) -> CoreToolResult:
        from app.agent_loop_lib.core.types import AgentResult
        from app.agent_loop_lib.modules.pipeline.critic.result_critic import (
            ResultCritic,
        )

        model = None
        if ctx.runtime.transport_registry is not None:
            try:
                model = ctx.spec.model.resolve(ctx.runtime.transport_registry)
            except Exception:
                model = None

        output = call.arguments.get("output", "")
        candidate = AgentResult(goal=ctx.goal, output=output, turns=[], success=True)
        try:
            critique = await ResultCritic(model).critique(candidate)
            content: object = critique.model_dump()
            is_error = False
        except Exception as e:
            content = str(e)
            is_error = True

        return CoreToolResult(tool_call_id=call.id, name=call.name, content=content, is_error=is_error)

    async def execute(self, **kwargs: Any) -> ToolOutput:
        raise ToolError(
            "verify_result is a special route (see SpecialRouteHandler.handle()) "
            "and must not be executed directly"
        )
