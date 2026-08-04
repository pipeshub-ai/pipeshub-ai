from __future__ import annotations

from typing import Any

from app.agent_loop_lib.sandbox.rpc import ToolBridge, ToolDispatch
from app.agent_loop_lib.tools.base import (
    ParameterType,
    Tag,
    Tool,
    ToolOutput,
    ToolParameter,
)

"""`execute_code`: programmatic tool calling via a hardened-ish local
sandbox with an RPC bridge (see sandbox/rpc.py) — the model writes Python
that calls OTHER tools with `tool(name, **kwargs)` from inside one script,
collapsing a multi-step pipeline (loop over N items, filter, branch) into a
single turn instead of N tool-call round-trips.

`Tag("risk", "high")` (arbitrary code execution) so it's ASK_EACH_TIME under
the default approval policy and auto-blocked by ModeHook in "plan" mode,
same as any other high-risk tool — the RPC bridge itself re-enforces
per-call approval/mode hooks for whatever tool the sandboxed code calls (see
ControlPlane._execute_code_dispatch), so this isn't a way to route around
those checks.
"""


class ExecuteCodeTool(Tool):
    def __init__(self, dispatch: ToolDispatch, working_dir: str | None = None, timeout: float = 30.0) -> None:
        self._bridge = ToolBridge(dispatch, working_dir=working_dir)
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "execute_code"

    @property
    def short_description(self) -> str:
        return "Execute Python code in a sandboxed subprocess, calling other tools from inside it."

    @property
    def description(self) -> str:
        return (
            "Execute Python code in a sandboxed subprocess. Call other tools "
            "from inside your code with tool(name, **kwargs) — e.g. "
            "tool('read_file', path='a.txt') — to orchestrate multi-step "
            "pipelines (loops, branching, filtering, aggregation) in ONE turn "
            "instead of one tool call per step. Assign your final answer to a "
            "variable named `result` to return it; print() output is captured "
            "and returned as stdout. Tools that need full conversation context "
            "(spawn_agent, clarify, write_todos, fetch_tools, list_toolsets, "
            "execute_code itself) cannot be called this way — call them directly."
        )

    @property
    def path(self) -> str:
        return "/toolsets/code/execute_code"

    @property
    def tags(self) -> list[Tag]:
        return [Tag("risk", "high"), Tag("category", "execute")]

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="code", type=ParameterType.STRING, required=True, description="Python source to execute."),
        ]

    async def execute(self, code: str, **kwargs: Any) -> ToolOutput:
        result = await self._bridge.run(code, timeout=self._timeout)
        return ToolOutput(success=True, data=result.model_dump())
