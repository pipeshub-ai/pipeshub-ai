"""Missing required arguments on method and dynamic tools.

A model that leaves out a required argument (``ask_user_question`` sent as
``{}``) used to get Python's bare ``TypeError`` back ("missing 1 required
positional argument"), reported as an internal failure with nothing to correct
against. It now gets a validation error naming the argument plus the tool's
usage, the tool is not run, and the failure reads as a bad call, not a crash.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from langchain_core.tools import StructuredTool

from app.agent_loop_lib.core.types import ToolCall, ToolResult
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolParameter
from app.agent_loop_lib.tools.decorators import (
    BoundMethodTool,
    ToolMeta,
    required_argument_names,
)
from app.agent_loop_lib.tools.executor import ToolExecutor
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.tool_adapter import PipesHubStructuredToolAdapter
from app.utils.logger import log_record_fields


class _Tickets:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, project: str, summary: str, labels: list[str] | None = None) -> tuple[bool, str]:
        self.calls.append({"project": project, "summary": summary, "labels": labels})
        return True, "created"

    async def keyword_only(self, *, ticket: str, note: str = "") -> tuple[bool, str]:
        self.calls.append({"ticket": ticket, "note": note})
        return True, "noted"


def _method_tool(tickets: _Tickets, name: str = "create") -> BoundMethodTool:
    meta = ToolMeta(
        path=f"/toolsets/jira/{name}",
        short_description=name,
        description=name,
        parameters=[
            ToolParameter(name="project", type=ParameterType.STRING, required=True, description="Project key."),
            ToolParameter(name="summary", type=ParameterType.STRING, required=True, description="One line."),
            ToolParameter(name="labels", type=ParameterType.ARRAY, required=False, description="Labels."),
        ],
    )
    return BoundMethodTool(getattr(tickets, name), meta)


async def _call(tool: Tool, arguments: dict[str, Any]) -> ToolResult:
    registry = ToolRegistry()
    registry.register_tool(tool)
    return await ToolExecutor(registry).call_tool(ToolCall(id="c1", name=tool.name, arguments=arguments))


class TestMethodTool:
    async def test_a_missing_argument_is_named_with_the_usage_and_the_tool_not_run(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG)
        tickets = _Tickets()

        result = await _call(_method_tool(tickets), {"project": "PA"})

        assert result.is_error is True
        assert "Missing required argument(s): summary" in result.content
        assert "Correct usage of `jira__create`:" in result.content
        assert "positional argument" not in result.content
        assert tickets.calls == []
        # Logged as the model's bad call, not as a crash (it was reason=exception, error_type=TypeError).
        (end,) = [r for r in caplog.records if r.getMessage() == "tool_call.end"]
        fields = log_record_fields(end)
        assert (fields["reason"], fields["error_class"]) == ("bad_args", "bad_request")

    async def test_every_missing_argument_is_named_at_once(self) -> None:
        result = await _call(_method_tool(_Tickets()), {})
        assert "Missing required argument(s): project, summary" in result.content

    async def test_an_optional_argument_may_be_left_out(self) -> None:
        tickets = _Tickets()

        result = await _call(_method_tool(tickets), {"project": "PA", "summary": "Launch"})

        assert result.is_error is False
        assert tickets.calls == [{"project": "PA", "summary": "Launch", "labels": None}]

    async def test_an_explicit_null_is_an_argument_the_tool_judges_itself(self) -> None:
        tickets = _Tickets()
        result = await _call(_method_tool(tickets), {"project": "PA", "summary": None})
        assert result.is_error is False and tickets.calls[0]["summary"] is None

    async def test_a_keyword_only_argument_is_required_too(self) -> None:
        tickets = _Tickets()
        result = await _call(_method_tool(tickets, "keyword_only"), {"note": "x"})
        assert "Missing required argument(s): ticket" in result.content and tickets.calls == []

    async def test_an_unknown_argument_is_reported_before_a_missing_one(self) -> None:
        """An invented name often stands in for the missing one (``title`` for ``summary``)."""
        result = await _call(_method_tool(_Tickets()), {"project": "PA", "title": "Launch"})
        assert "Unknown argument(s): title" in result.content


class TestDynamicTool:
    async def test_a_missing_argument_is_named_and_the_tool_not_run(self) -> None:
        calls: list[str] = []

        async def fetch(url: str, max_chars: int = 1000) -> str:
            calls.append(url)
            return "page"

        adapter = PipesHubStructuredToolAdapter(
            StructuredTool.from_function(coroutine=fetch, name="fetch", description="Fetch."), "web", "fetch",
        )

        rejected = await _call(adapter, {"max_chars": 10})
        accepted = await _call(adapter, {"url": "https://example.com"})

        assert rejected.is_error is True
        assert "Missing required argument(s): url" in rejected.content
        assert accepted.is_error is False
        assert calls == ["https://example.com"]


class TestRequiredArgumentNames:
    def test_parameters_without_a_default(self) -> None:
        def fn(a: int, b: int = 1, *args: int, c: int, d: int = 2, **kwargs: int) -> None: ...

        assert required_argument_names(fn) == {"a", "c"}

    def test_a_bound_method_does_not_require_self(self) -> None:
        assert required_argument_names(_Tickets().create) == {"project", "summary"}

    @pytest.mark.parametrize("fn", [print, len])
    def test_an_unreadable_signature_requires_nothing(self, fn: Any) -> None:  # noqa: ANN401
        assert isinstance(required_argument_names(fn), frozenset)


class TestAskUserQuestion:
    """#5957 gave its parameters defaults so ``{}`` reaches the tool's own refusal; that still holds."""

    async def test_an_empty_call_still_gets_the_tools_own_refusal(self) -> None:
        from app.agent_loop_lib.tools.decorators import TOOL_META_ATTR
        from app.agents.actions.internal_tools.intrim_tools import InternalTools

        tools = InternalTools()
        tool = BoundMethodTool(tools.ask_user_question, getattr(InternalTools.ask_user_question, TOOL_META_ATTR))

        result = await _call(tool, {})

        assert result.is_error is True
        assert "without a question" in str(result.content)
