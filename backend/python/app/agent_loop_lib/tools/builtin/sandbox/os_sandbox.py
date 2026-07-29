from __future__ import annotations

from typing import Any

from app.agent_loop_lib.sandbox.base import SandboxProvider
from app.agent_loop_lib.tools.base import (
    ParameterType,
    Tag,
    Tool,
    ToolOutput,
    ToolParameter,
)

"""`run_shell` — the OS sandbox's tool: executes an arbitrary shell command
through a `SandboxProvider` (normally `sandbox/os_sandbox.py`'s
`ConfinedLocalSandbox`)."""


class RunShellTool(Tool):
    def __init__(self, sandbox: SandboxProvider, timeout: float = 30.0) -> None:
        self._sandbox = sandbox
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "run_shell"

    @property
    def short_description(self) -> str:
        return "Execute a shell command in a sandboxed working directory."

    @property
    def description(self) -> str:
        return "Execute a shell command in a sandboxed working directory. Returns stdout, stderr, and exit_code."

    @property
    def path(self) -> str:
        return "/toolsets/os_sandbox/run_shell"

    @property
    def tags(self) -> list[Tag]:
        return [Tag("risk", "high"), Tag("category", "execute")]

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="command", type=ParameterType.STRING, required=True, description="Shell command to execute."),
        ]

    async def execute(self, command: str, **kwargs: Any) -> ToolOutput:
        result = await self._sandbox.run(command, language="bash", timeout=self._timeout)
        return ToolOutput(success=True, data=result.model_dump())
