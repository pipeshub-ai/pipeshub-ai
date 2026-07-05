from __future__ import annotations

from typing import Any

from app.agent_loop_lib.agent import observability as obs
from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.special_route import RouteContext

"""`replan` — exposes the existing Replanner (modules/pipeline/planner/replanner.py) as a
tool the agent calls when its todos diverge from reality. The real
replanning work happens in `handle()` below (it needs the run's transport/
goal/current todos, unavailable to a stateless `Tool` instance shared
across the whole registry); `execute()` stays a harmless default for
direct/standalone invocation (e.g. tests calling the tool without a full
Agent run)."""


class ReplanTool(Tool):
    @property
    def name(self) -> str:
        return "replan"

    @property
    def short_description(self) -> str:
        return "Regenerate your task list when the current plan no longer matches reality."

    @property
    def description(self) -> str:
        return (
            "Regenerate your task list when the current plan no longer matches "
            "reality (new information invalidates remaining steps, or the goal "
            "turns out to need different work than planned). Explain why."
        )

    @property
    def path(self) -> str:
        return "/toolsets/builtin/replan"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="reason", type=ParameterType.STRING, required=True,
                description="Why the current plan needs to change.",
            ),
        ]

    async def handle(self, call: ToolCall, ctx: RouteContext) -> CoreToolResult:
        from app.agent_loop_lib.core.types import Confidence, Todo, TodoStatus
        from app.agent_loop_lib.modules.pipeline.planner.base import Phase, Plan
        from app.agent_loop_lib.modules.pipeline.planner.replanner import Replanner

        agent = ctx.agent
        goal = ctx.goal

        model = None
        if ctx.runtime.transport_registry is not None:
            try:
                model = ctx.spec.model.resolve(ctx.runtime.transport_registry)
            except Exception:
                model = None

        prior_plan = Plan(
            goal=goal,
            phases=[Phase(name=f"todo_{i}", description=t.content) for i, t in enumerate(agent.todos)],
            confidence=Confidence.MEDIUM,
        )
        reason = call.arguments.get("reason", "")
        replan_goal = goal.model_copy(update={
            "description": f"{goal.description}\n\nReplanning reason: {reason}" if reason else goal.description,
        })

        try:
            new_plan = await Replanner(model=model, prior_plan=prior_plan).plan(replan_goal)
            agent.todos = [Todo(content=p.description, status=TodoStatus.PENDING) for p in new_plan.phases]
            tr_content: object = {
                "todos": [t.model_dump() for t in agent.todos],
                "confidence": new_plan.confidence.value,
            }
            tr_is_error = False
        except Exception as e:
            tr_content = str(e)
            tr_is_error = True

        await obs.write_state(agent, goal, "running_tool", turn_index=ctx.turn_index, started_at=ctx.started_at, current_tool="replan")
        await obs.append_timeline(
            agent, "replan",
            f"Replanned ({len(agent.todos)} item(s))" if not tr_is_error else "Replan failed",
            "running_tool", {"reason": reason, "todos": [t.model_dump() for t in agent.todos]},
        )
        return CoreToolResult(tool_call_id=call.id, name=call.name, content=tr_content, is_error=tr_is_error)

    async def execute(self, reason: str = "", **kwargs: Any) -> ToolOutput:
        return ToolOutput(success=True, data={"status": "ok"})
