"""Precedence chain for `agent/tool_loop.py`'s `_args_summary`/
`_result_summary`: the resolved tool's own declared summarizer (`@tool(
args_summary=..., result_summary=...)`) wins first, `runtime.summarizer`
(the platform registry) is the fallback, and `None` is always the safe
final answer — every layer must be fail-safe (a raising formatter, an
unresolvable tool name, or a missing registry must never propagate).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.agent_loop_lib.agent.tool_loop import _args_summary, _result_summary
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import ToolResult


def _call(name: str = "some_tool", arguments: dict[str, Any] | None = None) -> ToolCall:
    return ToolCall(id="call-1", name=name, arguments=arguments or {})


def _result(content: object, *, is_error: bool = False) -> ToolResult:
    return ToolResult(tool_call_id="call-1", name="some_tool", content=content, is_error=is_error)


class _FakeTool:
    def __init__(self, args_summary=None, result_summary=None, *, raise_on_args=False, raise_on_result=False):
        self._args_summary = args_summary
        self._result_summary = result_summary
        self._raise_on_args = raise_on_args
        self._raise_on_result = raise_on_result

    def summarize_args(self, args: dict[str, Any]) -> str | None:
        if self._raise_on_args:
            raise RuntimeError("tool-declared formatter bug")
        return self._args_summary

    def summarize_result(self, args: dict[str, Any], result: ToolResult) -> str | None:
        if self._raise_on_result:
            raise RuntimeError("tool-declared formatter bug")
        return self._result_summary


class _FakeRegistry:
    def __init__(self, tool: object | None = None, *, raise_on_resolve: bool = False):
        self._tool = tool
        self._raise_on_resolve = raise_on_resolve

    def resolve_by_name(self, name: str) -> object:
        if self._raise_on_resolve:
            raise KeyError(name)
        return self._tool


class _FakeSummarizer:
    def __init__(self, args_summary=None, result_summary=None, *, raise_on_args=False, raise_on_result=False):
        self._args_summary = args_summary
        self._result_summary = result_summary
        self._raise_on_args = raise_on_args
        self._raise_on_result = raise_on_result

    def summarize_args(self, tool_name: str, args: dict[str, Any]) -> str | None:
        if self._raise_on_args:
            raise RuntimeError("registry formatter bug")
        return self._args_summary

    def summarize_result(self, tool_name: str, args: dict[str, Any], result: ToolResult):
        if self._raise_on_result:
            raise RuntimeError("registry formatter bug")
        return SimpleNamespace(result_summary=self._result_summary)


def _agent(tool: object | None = None, *, raise_on_resolve: bool = False):
    registry = _FakeRegistry(tool, raise_on_resolve=raise_on_resolve)
    return SimpleNamespace(_executor=SimpleNamespace(registry=registry))


def _runtime(summarizer: object | None = None):
    return SimpleNamespace(summarizer=summarizer)


class TestArgsSummaryPrecedence:
    def test_tool_declared_wins_over_registry(self) -> None:
        tool = _FakeTool(args_summary="from tool")
        agent = _agent(tool)
        runtime = _runtime(_FakeSummarizer(args_summary="from registry"))

        assert _args_summary(agent, runtime, _call()) == "from tool"

    def test_falls_back_to_registry_when_tool_returns_none(self) -> None:
        tool = _FakeTool(args_summary=None)
        agent = _agent(tool)
        runtime = _runtime(_FakeSummarizer(args_summary="from registry"))

        assert _args_summary(agent, runtime, _call()) == "from registry"

    def test_falls_back_to_registry_when_tool_unresolvable(self) -> None:
        agent = _agent(tool=None, raise_on_resolve=True)
        runtime = _runtime(_FakeSummarizer(args_summary="from registry"))

        assert _args_summary(agent, runtime, _call()) == "from registry"

    def test_falls_back_to_registry_when_tool_formatter_raises(self) -> None:
        tool = _FakeTool(raise_on_args=True)
        agent = _agent(tool)
        runtime = _runtime(_FakeSummarizer(args_summary="from registry"))

        assert _args_summary(agent, runtime, _call()) == "from registry"

    def test_none_when_no_tool_and_no_registry(self) -> None:
        agent = _agent(tool=None, raise_on_resolve=True)
        runtime = _runtime(summarizer=None)

        assert _args_summary(agent, runtime, _call()) is None

    def test_none_when_registry_formatter_raises(self) -> None:
        agent = _agent(tool=None, raise_on_resolve=True)
        runtime = _runtime(_FakeSummarizer(raise_on_args=True))

        assert _args_summary(agent, runtime, _call()) is None


class TestResultSummaryPrecedence:
    def test_tool_declared_wins_over_registry(self) -> None:
        tool = _FakeTool(result_summary="from tool")
        agent = _agent(tool)
        runtime = _runtime(_FakeSummarizer(result_summary="from registry"))

        assert _result_summary(agent, runtime, _call(), _result("ok")) == "from tool"

    def test_falls_back_to_registry_when_tool_returns_none(self) -> None:
        tool = _FakeTool(result_summary=None)
        agent = _agent(tool)
        runtime = _runtime(_FakeSummarizer(result_summary="from registry"))

        assert _result_summary(agent, runtime, _call(), _result("ok")) == "from registry"

    def test_falls_back_to_registry_when_tool_formatter_raises(self) -> None:
        tool = _FakeTool(raise_on_result=True)
        agent = _agent(tool)
        runtime = _runtime(_FakeSummarizer(result_summary="from registry"))

        assert _result_summary(agent, runtime, _call(), _result("ok")) == "from registry"

    def test_none_when_no_tool_and_no_registry(self) -> None:
        agent = _agent(tool=None, raise_on_resolve=True)
        runtime = _runtime(summarizer=None)

        assert _result_summary(agent, runtime, _call(), _result("ok")) is None

    def test_none_when_registry_formatter_raises(self) -> None:
        agent = _agent(tool=None, raise_on_resolve=True)
        runtime = _runtime(_FakeSummarizer(raise_on_result=True))

        assert _result_summary(agent, runtime, _call(), _result("ok")) is None
