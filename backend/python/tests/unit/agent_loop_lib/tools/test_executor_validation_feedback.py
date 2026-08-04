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


class TestOptionalParameterExplicitNull:
    """A model's tool call JSON can carry an explicit `null` for an optional
    parameter it doesn't intend to set (e.g. `"sandbox_id": null`) rather
    than omitting the key. `json.loads` turns that into Python `None`, so
    the key IS present in `kwargs` — `validate()` must treat it the same as
    the key being absent (apply the declared default), not reject it as a
    type mismatch. See `Tool.validate()` in `tools/base.py`."""

    async def test_optional_string_param_with_none_value_uses_default(self) -> None:
        executor = ToolExecutor(_registry_with(_RunCodeLikeTool()))
        call = ToolCall(
            id="c1", name="run_code", arguments={"code": "print(1)", "language": None},
        )

        result = await executor.call_tool(call)

        assert result.is_error is False

    async def test_optional_array_param_with_none_value_uses_default(self) -> None:
        executor = ToolExecutor(_registry_with(_RunCodeLikeTool()))
        call = ToolCall(
            id="c1", name="run_code", arguments={"code": "print(1)", "packages": None},
        )

        result = await executor.call_tool(call)

        assert result.is_error is False

    async def test_required_param_with_none_value_still_fails(self) -> None:
        executor = ToolExecutor(_registry_with(_RunCodeLikeTool()))
        call = ToolCall(id="c1", name="run_code", arguments={"code": None})

        result = await executor.call_tool(call)

        assert result.is_error is True
        assert "expected type 'string'" in result.content
        assert "Correct usage of `run_code`:" in result.content

    async def test_optional_param_explicit_null_reaches_execute_as_default(self) -> None:
        """End-to-end: a tool whose `execute()` records the kwargs it
        actually received confirms the None -> default substitution happens
        before `execute()` is called, not just that validation passes."""

        class _RecordingTool(_RunCodeLikeTool):
            def __init__(self) -> None:
                self.received: dict[str, Any] | None = None

            async def execute(self, **kwargs: Any) -> ToolOutput:
                self.received = kwargs
                return ToolOutput(success=True, data="ran")

        tool = _RecordingTool()
        executor = ToolExecutor(_registry_with(tool))
        call = ToolCall(
            id="c1", name="run_code", arguments={"code": "print(1)", "language": None},
        )

        result = await executor.call_tool(call)

        assert result.is_error is False
        assert tool.received is not None
        assert tool.received["language"] == "typescript"
