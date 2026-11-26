"""A failed tool call reaches the model, the tool card, the saved transcript and the trace with the API's own words but
no key, token or password: the executor masks every failed output once, and a tool that answers a soft failure as a
success builds its payload with `error_data`.

All tokens below are fake."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.registry import HookRegistry
from app.agent_loop_lib.sandbox.browser_sandbox import BrowserSandboxError
from app.agent_loop_lib.sandbox.db_sandbox import DBSandboxError
from app.agent_loop_lib.tools.base import Tool, ToolOutput, error_data
from app.agent_loop_lib.tools.builtin.data.skills import LoadSkillResourceTool
from app.agent_loop_lib.tools.builtin.sandbox.browser_sandbox import (
    BrowserClickTool,
    BrowserFillTool,
    BrowserGetTextTool,
    BrowserNavigateTool,
    BrowserScreenshotTool,
)
from app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox import ReadSandboxFileTool
from app.agent_loop_lib.tools.builtin.sandbox.db_sandbox import DBQueryTool
from app.agent_loop_lib.tools.executor import ToolExecutor
from app.agent_loop_lib.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio

GITLAB = "glpat-FAKE-TOKEN-FOR-TEST-0000"
GOOGLE = "ya29.FAKE-TOKEN-FOR-TEST-0000000000"
DSN_PASSWORD = "FAKE-PW-FOR-TEST"
TOKENS = ("FAKE-TOKEN-FOR-TEST", DSN_PASSWORD)


def _leaks(value: Any) -> list[str]:  # noqa: ANN401
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return [t for t in TOKENS if t in text]


class _Scripted(Tool):
    """A tool whose execute raises or returns what the test hands it."""

    def __init__(self, outcome: ToolOutput | BaseException) -> None:
        self._outcome = outcome

    name = property(lambda self: "scripted")
    short_description = property(lambda self: "scripted")
    description = property(lambda self: "scripted")
    path = property(lambda self: "/toolsets/test/scripted")
    parameters = property(lambda self: [])

    async def execute(self, **kwargs: Any) -> ToolOutput:  # noqa: ANN401
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome


async def _call(tool: Tool, kernel: HookRegistry | None = None, **arguments: Any) -> CoreToolResult:  # noqa: ANN401
    registry = ToolRegistry()
    registry.register_tool(tool)
    return await ToolExecutor(registry, kernel).call_tool(ToolCall(id="c1", name=tool.name, arguments=arguments))


class TestAFailedCall:
    async def test_a_raised_drivers_error_keeps_its_words_and_loses_its_password(self) -> None:
        result = await _call(_Scripted(RuntimeError(f"connect failed: postgresql://app:{DSN_PASSWORD}@db.internal/x")))
        assert result.is_error
        assert "connect failed: postgresql://app:" in result.content
        assert _leaks(result.content) == []

    async def test_a_returned_error_and_its_data_are_masked(self) -> None:
        output = ToolOutput(success=False, error=f"GitLab 401 for PRIVATE-TOKEN {GITLAB}",
                            data={"error": f"refresh {GOOGLE} rejected", "detail": [f"token {GITLAB}"]})
        seen: list[ToolOutput] = []

        async def _post(ctx: Any, next_fn: Any) -> None:  # noqa: ANN401
            seen.append(ctx.tool_response)
            await next_fn()

        kernel = HookRegistry()
        kernel.on(HookEvent.POST_TOOL_USE).use(_post)
        result = await _call(_Scripted(output), kernel)

        assert result.content == "GitLab 401 for PRIVATE-TOKEN [REDACTED]"
        (response,) = seen  # what POST_TOOL_USE hooks and the trace see
        assert _leaks([response.error, response.data]) == []
        assert response.data == {"error": "refresh [REDACTED] rejected", "detail": ["token [REDACTED]"]}

    async def test_an_opaque_value_under_a_credential_name_is_masked(self) -> None:
        """No shape to recognise (`hunter2-opaque`), but the key says what it is; ordinary fields stay readable."""
        data = {"password": "hunter2-opaque", "auth": {"apiKey": "opaque-7f3a", "retries": 3, "enabled": True},
                "message": "permission denied", "status": 403, "token_expires_at": None}
        seen: list[ToolOutput] = []

        async def _post(ctx: Any, next_fn: Any) -> None:  # noqa: ANN401
            seen.append(ctx.tool_response)
            await next_fn()

        kernel = HookRegistry()
        kernel.on(HookEvent.POST_TOOL_USE).use(_post)
        await _call(_Scripted(ToolOutput(success=False, error="login failed", data=data)), kernel)

        assert seen[0].data == {"password": "[REDACTED]", "auth": {"apiKey": "[REDACTED]", "retries": 3, "enabled": True},
                                "message": "permission denied", "status": 403, "token_expires_at": None}

    async def test_a_tuple_in_failed_data_is_masked(self) -> None:
        seen: list[ToolOutput] = []

        async def _post(ctx: Any, next_fn: Any) -> None:  # noqa: ANN401
            seen.append(ctx.tool_response)
            await next_fn()

        kernel = HookRegistry()
        kernel.on(HookEvent.POST_TOOL_USE).use(_post)
        await _call(_Scripted(ToolOutput(success=False, error="failed", data=("token", GITLAB))), kernel)
        assert seen[0].data == ["token", "[REDACTED]"]

    async def test_a_special_routes_failed_content_is_masked(self) -> None:
        registry = ToolRegistry()
        registry.register_tool(_Scripted(ToolOutput(success=True)))
        failed = CoreToolResult(tool_call_id="c1", name="scripted", content={"error": f"key {GITLAB}"}, is_error=True)

        result = await ToolExecutor(registry).call_tool(
            ToolCall(id="c1", name="scripted", arguments={}), override_execute=AsyncMock(return_value=failed),
        )

        assert result.is_error and _leaks(result.content) == []

    async def test_a_success_is_returned_untouched(self) -> None:
        """A document can mention a token-shaped string; only failures are masked."""
        data = {"text": f"the runbook says to rotate {GITLAB}"}
        result = await _call(_Scripted(ToolOutput(success=True, data=data)))
        assert result.content == data


class TestAnActionToolsFailure:
    async def test_a_pipeshub_action_returning_an_error_tuple_is_masked(self) -> None:
        from langchain_core.tools import StructuredTool

        from app.agents.agent_loop.tool_adapter import PipesHubStructuredToolAdapter

        def search(query: str) -> tuple[bool, str]:
            return False, json.dumps({"error": f"Drive API 401: Invalid Credentials for {GOOGLE}"})

        tool = PipesHubStructuredToolAdapter(
            StructuredTool.from_function(search, name="search", description="Searches."), "drive", "search",
        )
        result = await _call(tool, query="q")

        assert result.is_error
        assert "Drive API 401: Invalid Credentials for [REDACTED]" in result.content
        assert _leaks(result.content) == []


class TestASoftFailureAnsweredAsSuccess:
    def test_error_data_keeps_the_words_and_masks_the_secret(self) -> None:
        assert error_data(ValueError(f"no such file under {GOOGLE}")) == {"error": "no such file under [REDACTED]"}

    async def test_the_db_sandbox(self) -> None:
        sandbox = SimpleNamespace(mode="readonly", query=AsyncMock(side_effect=DBSandboxError(f"auth {GITLAB}")))
        output = await DBQueryTool(sandbox).execute(sql="select 1")
        assert output.success and output.data == {"error": "auth [REDACTED]"}

    @pytest.mark.parametrize(("tool_cls", "method", "arguments"), [
        (BrowserNavigateTool, "navigate", {"url": "https://example.test"}),
        (BrowserGetTextTool, "get_text", {}),
        (BrowserClickTool, "click", {"selector": "#go"}),
        (BrowserFillTool, "fill", {"selector": "#q", "text": "x"}),
        (BrowserScreenshotTool, "screenshot", {}),
    ])
    async def test_the_browser_sandbox(self, tool_cls: type, method: str, arguments: dict) -> None:
        sandbox = SimpleNamespace(**{method: AsyncMock(side_effect=BrowserSandboxError(f"blocked {GOOGLE}"))})
        output = await tool_cls(sandbox).execute(**arguments)
        assert output.success and output.data == {"error": "blocked [REDACTED]"}

    @pytest.mark.parametrize("error", [FileNotFoundError, ValueError])  # a missing file; a path traversal refused
    async def test_the_coding_sandbox(self, error: type[Exception]) -> None:
        backend = MagicMock(download_file=AsyncMock(side_effect=error(f"refused /tmp/{GITLAB}.txt")))
        manager = MagicMock(get=MagicMock(return_value=backend))
        output = await ReadSandboxFileTool(manager).execute(sandbox_id="s", path="x")
        assert output.success and output.data == {"error": "refused /tmp/[REDACTED].txt"}

    async def test_the_skill_registry(self) -> None:
        manager = MagicMock(load_resource=AsyncMock(side_effect=RegistryError(f"fetch failed with {GITLAB}")))
        output = await LoadSkillResourceTool(manager).execute(name="s", path="p")
        assert output.success and output.data == {"error": "fetch failed with [REDACTED]"}
