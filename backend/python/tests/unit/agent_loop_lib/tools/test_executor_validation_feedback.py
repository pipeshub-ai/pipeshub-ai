"""`ToolExecutor._run`'s `ToolValidationError` handling (`tools/executor.py`):
a bad tool call must come back with more than a bare exception string — a
compact usage summary built from the tool's own `parameters`, so a weak
model can fix the call on the very next turn instead of guessing again
until `ToolErrorTracker`'s 3-strike limit blocks the tool outright."""

from __future__ import annotations

from typing import Any

from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.executor import ToolExecutor
from app.agent_loop_lib.tools.registry import ToolRegistry


class _RunCodeLikeTool(Tool):
    """Mirrors `run_code`'s shape closely enough to exercise multi-param
    usage-hint formatting: one required string, one optional enum with a
    default, one optional array."""

    @property
    def name(self) -> str:
        return "run_code"

    @property
    def short_description(self) -> str:
        return "Run code"

    @property
    def description(self) -> str:
        return "Run code in a sandbox"

    @property
    def path(self) -> str:
        return "/toolsets/test/run_code"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="code", type=ParameterType.STRING, required=True, description="Program source."),
            ToolParameter(
                name="language", type=ParameterType.STRING, required=False, default="typescript",
                enum=["typescript", "python"], description="Language to run as.",
            ),
            ToolParameter(
                name="packages", type=ParameterType.ARRAY, required=False, default=None,
                items={"type": "string"}, description="Packages to install first.",
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolOutput:
        return ToolOutput(success=True, data="ran")


def _registry_with(*tools: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register_tool(tool)
    return registry


class TestValidationErrorUsageHint:
    async def test_missing_required_argument_includes_full_parameter_list(self) -> None:
        executor = ToolExecutor(_registry_with(_RunCodeLikeTool()))
        call = ToolCall(id="c1", name="run_code", arguments={})

        result = await executor.call_tool(call)

        assert result.is_error is True
        assert "missing required argument 'code'" in result.content
        assert "Correct usage of `run_code`:" in result.content
        assert "code (string, required)" in result.content
        assert "language (string, optional, default='typescript'" in result.content
        assert "one of ['typescript', 'python']" in result.content

    async def test_wrong_type_argument_includes_usage_hint(self) -> None:
        executor = ToolExecutor(_registry_with(_RunCodeLikeTool()))
        call = ToolCall(id="c1", name="run_code", arguments={"code": 123})

        result = await executor.call_tool(call)

        assert result.is_error is True
        assert "expected type 'string'" in result.content
        assert "Correct usage of `run_code`:" in result.content

    async def test_valid_call_has_no_usage_hint(self) -> None:
        executor = ToolExecutor(_registry_with(_RunCodeLikeTool()))
        call = ToolCall(id="c1", name="run_code", arguments={"code": "print(1)"})

        result = await executor.call_tool(call)

        assert result.is_error is False
        assert "Correct usage of" not in str(result.content)
