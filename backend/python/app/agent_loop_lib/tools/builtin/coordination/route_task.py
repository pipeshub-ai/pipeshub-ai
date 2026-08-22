from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.types import Goal, ToolCall, UserMessage
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.special_route import RouteContext

if TYPE_CHECKING:
    from app.agent_loop_lib.models.base import SupportsStructuredComplete

"""`route_task` — the probabilistic solo-vs-multi_agent classification call.
`classify_task()` is a plain, standalone LLM call any agent can invoke
mid-loop (via this tool) to ask "should this be solo, or worth fanning out
via spawn_agent?" — a probabilistic decision the agent makes for itself,
never a deterministic preamble decided on its behalf (see
`.claude/rules/principles.md` rule 1: "everything via tool calls").
"""

__all__ = ["classify_task", "RouteTaskTool"]

_SYSTEM = (
    "You are a task router. Classify the task as exactly one of:\n"
    "  solo        — one agent can answer it with sequential web searches (most tasks)\n"
    "  multi_agent — requires 3+ INDEPENDENT workstreams each needing 5+ searches in parallel\n\n"
    "Default to solo. Only choose multi_agent when the task clearly lists several distinct\n"
    "subjects that each demand deep dedicated research (e.g. 'analyse these 5 companies\n"
    "individually', 'research 4 different countries for market entry').\n\n"
    "Solo examples: trip planning, product research, writing help, how-to guides, comparisons.\n"
    "Multi-agent examples: competitive analysis of 5+ named companies, market entry for 3+ countries."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "route": {"type": "string", "enum": ["solo", "multi_agent"]},
        "reason": {"type": "string"},
    },
    "required": ["route", "reason"],
}


async def classify_task(model: "SupportsStructuredComplete", goal: Goal, model_name: str | None = None) -> dict:
    """One cheap structured LLM call that returns {'route': 'solo'|'multi_agent', 'reason': str}.

    'solo'        -> task can be answered by one agent with web_search directly.
    'multi_agent' -> task has 3+ truly independent workstreams each needing 5+ searches.
    Defaults to 'solo' (with a fallback reason) on any failure so a broken/
    unavailable model never accidentally adds multi-agent cost.
    """
    try:
        response = await model.complete_structured(
            messages=[UserMessage(content=goal.description)],
            system=_SYSTEM,
            output_schema=_SCHEMA,
            model=model_name,
        )
        result = response.data
        return {"route": result.get("route", "solo"), "reason": result.get("reason", "")}
    except Exception as exc:
        return {"route": "solo", "reason": f"classification failed, defaulted to solo: {exc}"}


class RouteTaskTool(Tool):
    """Agent-callable wrapper around `classify_task()` — lets any agent ask
    "should I tackle this solo or fan out via spawn_agent?" mid-loop, instead
    of only getting this classification automatically injected by the
    root-agent preamble (see `agent/preamble.py`)."""

    @property
    def name(self) -> str:
        return "route_task"

    @property
    def short_description(self) -> str:
        return "Classify a goal as solo (one agent) or multi_agent (fan out via spawn_agent)."

    @property
    def description(self) -> str:
        return (
            "Classify a task as 'solo' (one agent can handle it with sequential tool "
            "calls) or 'multi_agent' (3+ independent workstreams that each need deep, "
            "dedicated research — worth spawning sub-agents for via spawn_agent). "
            "Use this before committing to a multi-agent fan-out on an ambiguous task."
        )

    @property
    def path(self) -> str:
        return "/toolsets/builtin/route_task"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="goal_description",
                type=ParameterType.STRING,
                description="The goal/task description to classify.",
                required=True,
            ),
        ]

    async def handle(self, call: ToolCall, ctx: RouteContext) -> CoreToolResult:
        goal_desc = call.arguments.get("goal_description") or ctx.goal.description
        model = None
        if ctx.runtime.transport_registry is not None:
            try:
                model = ctx.spec.model.resolve(ctx.runtime.transport_registry)
            except Exception:
                model = None
        if model is None:
            return CoreToolResult(
                tool_call_id=call.id, name=call.name,
                content="No model available to classify the task", is_error=True,
            )
        classification = await classify_task(model, Goal(description=goal_desc), ctx.spec.model.model)
        return CoreToolResult(tool_call_id=call.id, name=call.name, content=classification, is_error=False)

    async def execute(self, **kwargs: Any) -> ToolOutput:
        return ToolOutput(success=True, data={"route": "solo", "reason": "no model in standalone execute()"})
