from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING

from app.agent_loop_lib.core.exceptions import ToolError
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter

if TYPE_CHECKING:
    from app.agent_loop_lib.agent.spec import AgentSpec
    from app.agent_loop_lib.runtime.runtime import AgentRuntime

__all__ = ["AgentTool", "MAX_AGENT_TOOL_DEPTH"]

"""Layer 5: composition — agents exposed as tools.

`AgentTool` is a REAL `Tool` subclass: no sentinel `execute()`, no special
route. Registering one on a `ToolRegistry` under any name makes an entire
`AgentSpec` callable exactly like any other tool — PRE_TOOL_USE/
POST_TOOL_USE middleware (permission, approval, budget, audit-log) apply to
the call uniformly, same as calling `web_search`. This is how "any agent
can be a tool for any other agent" composition works: a top-level
orchestrator's `tool_names` can simply include the names of other
`AgentTool`s (see `examples/02_orchestrator.py`).

Unlike `spawn_agent` (dynamic, model-chosen role by NAME, with the
framework's MAX_SPAWN_DEPTH guard tracked via `RunContext.spawn_depth`),
`AgentTool` composition is STATIC — the developer wires the tool graph
ahead of time. It still needs its own recursion guard (two `AgentTool`s
that happen to reference each other, or a very deep static chain, would
otherwise recurse forever) — tracked via a plain `ContextVar` rather than
`RunContext.spawn_depth`, since a fresh top-level `RunContext` is created
per call (no parent run to inherit depth from).
"""

MAX_AGENT_TOOL_DEPTH = 6

_agent_tool_depth: contextvars.ContextVar[int] = contextvars.ContextVar("_agent_tool_depth", default=0)


class AgentTool(Tool):
    """Wraps an `AgentSpec` as a plain, agent-callable `Tool`.

    `execute(goal, context=None)` runs the wrapped spec to completion via
    `runtime.run_child()` (the same primitive `spawn_agent`/`best_of_n`
    use) and returns its final output as an ordinary `ToolOutput` — the
    calling agent never sees a difference between this and a "real" tool.
    """

    def __init__(
        self,
        spec: "AgentSpec",
        runtime: "AgentRuntime",
        *,
        name: str | None = None,
        description: str | None = None,
        parameters: list[ToolParameter] | None = None,
    ) -> None:
        self._spec = spec
        self._runtime = runtime
        self._name = name or spec.name
        self._description = description or spec.description or f"Run the {self._name!r} agent on a goal."
        self._parameters = parameters or [
            ToolParameter(
                name="goal", type=ParameterType.STRING,
                description="The goal for this agent to accomplish.",
                required=True,
            ),
            ToolParameter(
                name="context", type=ParameterType.STRING,
                description="Optional extra context to append to the goal.",
                required=False, default=None,
            ),
        ]

    @property
    def name(self) -> str:
        return self._name

    @property
    def short_description(self) -> str:
        return self._description.splitlines()[0][:120]

    @property
    def description(self) -> str:
        return self._description

    @property
    def path(self) -> str:
        return f"/toolsets/agents/{self._name}"

    @property
    def parameters(self) -> list[ToolParameter]:
        return self._parameters

    async def execute(self, **kwargs) -> ToolOutput:
        depth = _agent_tool_depth.get()
        if depth >= MAX_AGENT_TOOL_DEPTH:
            raise ToolError(
                f"agent_as_tool recursion depth ({MAX_AGENT_TOOL_DEPTH}) exceeded calling {self._name!r} — "
                "check for a cycle in your static agent-tool composition."
            )

        goal_text = kwargs.get("goal", "")
        extra_context = kwargs.get("context")
        description = f"{goal_text}\n\nContext: {extra_context}" if extra_context else goal_text

        token = _agent_tool_depth.set(depth + 1)
        try:
            result = await self._runtime.run_child(self._spec, Goal(description=description), parent_run_ctx=None)
        finally:
            _agent_tool_depth.reset(token)

        if not result.success:
            return ToolOutput(success=False, error=result.error or f"{self._name} agent failed")
        return ToolOutput(success=True, data=result.output)
