from __future__ import annotations

from typing import Any

from app.agent_loop_lib.core.exceptions import ToolError
from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.tools.base import ParameterType, Tag, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.special_route import RouteContext
from app.agent_loop_lib.tools.tags import TAG_SPAWN


class BestOfNTool(Tool):
    """Runs via `SpecialRouteHandler.handle()` (below), which delegates to
    `agent/best_of_n.py:run_best_of_n()` — kept in its own module (not
    inlined here) for unit-testability without a full Agent/turn loop,
    same convention as other special routes. execute() is a sentinel,
    never called directly, same pattern as SpawnAgentTool.

    Run-level best-of-N: launches N independent candidate sub-agent runs
    for the SAME goal in parallel (through the same RuntimeRouter spawn_agent
    uses, so the depth guard/config scoping/cancellation propagation apply
    unchanged), then one judge LLM call picks a winner. This is the loop's
    answer to in-loop tree search (LATS/ToT) WITHOUT changing the turn-loop
    kernel — branching happens across whole runs, never mid-trajectory.
    """

    @property
    def name(self) -> str:
        return "best_of_n"

    @property
    def short_description(self) -> str:
        return "Run N independent candidate attempts at the same goal, then pick the best one."

    @property
    def description(self) -> str:
        return (
            "Run N independent candidate attempts at the SAME goal in parallel, then "
            "have a judge pick the best one. USE ONLY when the output is judgeable and "
            "candidates have no critical side effects — give candidates read-only/"
            "idempotent tools (e.g. web_search) since every candidate's actions happen "
            "regardless of who wins; there is no undo for the ones that lose. For tasks "
            "needing side-effecting actions, use a single agent or spawn_agent instead."
        )

    @property
    def path(self) -> str:
        return "/toolsets/builtin/best_of_n"

    @property
    def tags(self) -> list[Tag]:
        return [TAG_SPAWN]

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
                description="Clear, self-contained goal every candidate attempts independently",
                required=True,
            ),
            ToolParameter(
                name="n",
                type=ParameterType.INTEGER,
                description="Number of parallel candidates to run (2-5)",
                required=True,
            ),
            ToolParameter(
                name="criteria",
                type=ParameterType.STRING,
                description="What the judge should score candidates against when picking a winner",
                required=True,
            ),
            ToolParameter(
                name="tools",
                type=ParameterType.ARRAY,
                description="Tools to give each candidate, e.g. ['web_search','task_complete']",
                required=False,
                default=None,
                items={"type": "string"},
            ),
            ToolParameter(
                name="model",
                type=ParameterType.STRING,
                description="Override the model for the candidates (leave blank to inherit)",
                required=False,
                default=None,
            ),
        ]

    async def handle(self, call: ToolCall, ctx: RouteContext) -> CoreToolResult:
        from app.agent_loop_lib.agent.best_of_n import run_best_of_n
        return await run_best_of_n(ctx.agent, call, ctx.goal, ctx.turn_index, ctx.started_at)

    async def execute(self, **kwargs: Any) -> ToolOutput:
        raise ToolError("best_of_n is a special route (see SpecialRouteHandler.handle()) and must not be executed directly")
