from __future__ import annotations

from typing import Any

from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.special_route import RouteContext

"""`critique_plan` — exposes `critic.plan_critic.PlanCritic` as a tool call
(see `.claude/rules/principles.md`'s gap map: "Double-critique loop ... Not
tool-driven; explicit 'verify' step is implicit, not a named stage"). Any
loop strategy (e.g. `PlanCritiqueExecuteLoop`) or agent can call this
explicitly on a plan (its own `create_plan` output, or one it authored
itself) before committing to executing it — critique is a tool call, never
a hardwired pre-loop step."""

__all__ = ["CritiquePlanTool"]


class CritiquePlanTool(Tool):
    @property
    def name(self) -> str:
        return "critique_plan"

    @property
    def short_description(self) -> str:
        return "Critique a proposed execution plan before running it."

    @property
    def description(self) -> str:
        return (
            "Critique a proposed execution plan for feasibility, completeness, "
            "and correctness against the current goal. Returns "
            "passed/confidence/issues — call this before executing a plan you "
            "are not fully confident in."
        )

    @property
    def path(self) -> str:
        return "/toolsets/builtin/critique_plan"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="plan",
                type=ParameterType.STRING,
                description="The plan to critique, as free-form text (e.g. your create_plan output).",
                required=True,
            ),
        ]

    async def handle(self, call: ToolCall, ctx: RouteContext) -> CoreToolResult:
        from app.agent_loop_lib.modules.pipeline.critic.plan_critic import PlanCritic
        from app.agent_loop_lib.modules.pipeline.planner.base import Plan

        model = None
        if ctx.runtime.transport_registry is not None:
            try:
                model = ctx.spec.model.resolve(ctx.runtime.transport_registry)
            except Exception:
                model = None

        plan_text = call.arguments.get("plan") or ""
        plan = Plan(goal=ctx.goal, text=plan_text)
        try:
            critique = await PlanCritic(model).critique(plan)
            content: object = critique.model_dump()
            is_error = False
        except Exception as e:
            content = str(e)
            is_error = True

        return CoreToolResult(tool_call_id=call.id, name=call.name, content=content, is_error=is_error)

    async def execute(self, **kwargs: Any) -> ToolOutput:
        return ToolOutput(success=True, data={"passed": True, "confidence": "low", "issues": [], "summary": ""})
