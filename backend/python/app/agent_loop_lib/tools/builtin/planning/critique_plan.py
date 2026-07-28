from __future__ import annotations

import logging
from typing import Any

from app.agent_loop_lib.core.exceptions import ToolError
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
a hardwired pre-loop step.

When a ``STRUCTURED_PLAN_SLOT`` plan exists, structural validation is
performed before the LLM critique: domain names checked against the tool
registry, tool names verified, and the dependency graph re-validated
(defense in depth — ``create_plan`` already checked, but the LLM might
have revised between calls)."""

__all__ = ["CritiquePlanTool"]

logger = logging.getLogger(__name__)


def _validate_structured_plan_against_registry(
    ctx: RouteContext,
) -> list[dict[str, Any]]:
    """Check the structured plan's domain/tool names against the live
    registry.  Returns a list of issue dicts (empty = all good)."""
    from app.agent_loop_lib.modules.pipeline.planner.base import STRUCTURED_PLAN_SLOT
    from app.agent_loop_lib.tools.builtin.coordination.graph_utils import find_cycle

    plan = ctx.scope.turn.run.get(STRUCTURED_PLAN_SLOT)
    if plan is None or plan.steps is None:
        return []

    registry = ctx.runtime.tool_registry
    issues: list[dict[str, Any]] = []

    available_names = set(registry.names()) if registry else set()

    for step in plan.steps:
        # Validate tool_names — each must be resolvable (exact, group, or prefix)
        if registry is not None:
            for tn in step.tool_names:
                if registry.has(tn):
                    continue
                # Check toolset group
                if tn in {g.name for g in registry.toolsets()}:
                    continue
                # Check prefix
                prefix = f"{tn}__"
                if any(n.startswith(prefix) for n in available_names):
                    continue
                issues.append({
                    "severity": "error",
                    "description": (
                        f"Step '{step.id}' references tool '{tn}' but it is not "
                        f"registered. Check Available Domains for exact tool names."
                    ),
                    "location": f"step.{step.id}.tool_names",
                })

    # Re-validate dependency graph (defense in depth)
    all_ids = {s.id for s in plan.steps}
    for step in plan.steps:
        for dep in step.depends_on:
            if dep not in all_ids:
                issues.append({
                    "severity": "error",
                    "description": (
                        f"Step '{step.id}' depends on '{dep}' which does not "
                        f"exist in the plan. Available: {sorted(all_ids)}"
                    ),
                    "location": f"step.{step.id}.depends_on",
                })

    adjacency = {s.id: list(s.depends_on) for s in plan.steps}
    cycle = find_cycle(adjacency)
    if cycle is not None:
        issues.append({
            "severity": "error",
            "description": (
                "Circular dependency: " + " -> ".join(cycle)
            ),
            "location": "plan.dependency_graph",
        })

    return issues


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

        # --- Structural validation of the structured plan ---
        structural_issues = _validate_structured_plan_against_registry(ctx)
        if structural_issues:
            has_errors = any(i.get("severity") == "error" for i in structural_issues)
            content: object = {
                "passed": False,
                "confidence": "high",
                "issues": structural_issues,
                "summary": (
                    f"{len(structural_issues)} structural issue(s) found in the "
                    "plan. Fix these before proceeding — see issues for details."
                ),
            }
            return CoreToolResult(
                tool_call_id=call.id, name=call.name, content=content,
            )

        # --- LLM-driven critique ---
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
            content = critique.model_dump()
            is_error = False
        except Exception as e:
            content = str(e)
            is_error = True

        return CoreToolResult(tool_call_id=call.id, name=call.name, content=content, is_error=is_error)

    async def execute(self, **kwargs: Any) -> ToolOutput:
        raise ToolError(
            "critique_plan is a special route (see SpecialRouteHandler.handle()) "
            "and must not be executed directly"
        )
