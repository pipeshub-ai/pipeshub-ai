"""`@tool(args_summary=..., result_summary=...)` — the decorator-level
summarizer hooks added by the App Tool Summarizers plan (`decorators.py`,
`base.py`). Covers both the standalone-function mode (`FunctionTool`) and
the class-method mode (`BoundMethodTool`, via `ToolsetBuilder`), plus the
`Tool` base class's `None` defaults and the fail-safe contract (a
formatter that raises must degrade to `None`, never propagate).
"""

from __future__ import annotations

from app.agent_loop_lib.core.types import ToolResult
from app.agent_loop_lib.tools.base import Tag, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agent_loop_lib.tools.toolset import ToolsetBuilder


def _result(content: object, *, is_error: bool = False) -> ToolResult:
    return ToolResult(tool_call_id="call-1", name="tool", content=content, is_error=is_error)


class TestToolBaseDefaults:
    """A minimal `Tool` subclass with no decorator involvement should get
    the concrete `None` defaults from the base class — every pre-existing
    `Tool` implementation keeps working unchanged."""

    def test_summarize_args_defaults_to_none(self) -> None:
        class PlainTool(Tool):
            @property
            def name(self) -> str:
                return "plain"

            @property
            def short_description(self) -> str:
                return "plain tool"

            @property
            def description(self) -> str:
                return "a plain tool"

            @property
            def path(self) -> str:
                return "/toolsets/plain/plain"

            @property
            def parameters(self) -> list[ToolParameter]:
                return []

            async def execute(self, **kwargs: object) -> ToolOutput:
                return ToolOutput(success=True, data="ok")

        instance = PlainTool()
        assert instance.summarize_args({}) is None
        assert instance.summarize_result({}, _result("ok")) is None


class TestFunctionToolDeclaresSummarizers:
    """`@tool(...)` on a standalone async function returns a `FunctionTool`
    whose `summarize_args`/`summarize_result` delegate to the declared
    callables."""

    def _make_tool(self, *, args_summary=None, result_summary=None):
        @tool(
            path="/toolsets/util/echo",
            short_description="Echo",
            description="Echoes input",
            parameters=[],
            args_summary=args_summary,
            result_summary=result_summary,
        )
        async def echo(text: str) -> ToolOutput:
            return ToolOutput(success=True, data=text)

        return echo

    def test_delegates_to_declared_args_summary(self) -> None:
        echo = self._make_tool(args_summary=lambda args: f"Echoing {args.get('text')!r}")
        assert echo.summarize_args({"text": "hi"}) == "Echoing 'hi'"

    def test_delegates_to_declared_result_summary(self) -> None:
        echo = self._make_tool(
            result_summary=lambda args, result: f"Echoed: {result.content}"
        )
        assert echo.summarize_result({}, _result("hi")) == "Echoed: hi"

    def test_no_summarizers_declared_returns_none(self) -> None:
        echo = self._make_tool()
        assert echo.summarize_args({"text": "hi"}) is None
        assert echo.summarize_result({}, _result("hi")) is None

    def test_args_summary_exception_degrades_to_none(self) -> None:
        def _boom(args: dict) -> str:
            raise RuntimeError("contributor typo")

        echo = self._make_tool(args_summary=_boom)
        assert echo.summarize_args({"text": "hi"}) is None

    def test_result_summary_exception_degrades_to_none(self) -> None:
        def _boom(args: dict, result: ToolResult) -> str:
            raise RuntimeError("contributor typo")

        echo = self._make_tool(result_summary=_boom)
        assert echo.summarize_result({}, _result("hi")) is None

    def test_none_return_from_formatter_is_preserved(self) -> None:
        echo = self._make_tool(args_summary=lambda args: None)
        assert echo.summarize_args({"text": "hi"}) is None


class _Jiraish:
    """Minimal stand-in for a connector class (e.g. `Jira`) with a
    `@tool`-decorated method, exercising the method-mode path of the
    decorator plus `ToolsetBuilder`'s collection into `BoundMethodTool`."""

    @tool(
        path="/tools/jiraish/search",
        short_description="Search",
        description="Search issues",
        parameters=[],
        args_summary=lambda args: f'Searching: "{args.get("jql")}"' if args.get("jql") else None,
        result_summary=lambda args, result: (
            f"Failed: {result.content}" if result.is_error else f"Found: {result.content}"
        ),
        tags=[Tag(key="category", value="read")],
    )
    async def search(self, jql: str = "") -> tuple[bool, str]:
        return True, "[]"

    @tool(
        path="/tools/jiraish/get",
        short_description="Get",
        description="Get one issue",
        parameters=[],
    )
    async def get(self, issue_key: str = "") -> tuple[bool, str]:
        return True, "{}"


class TestBoundMethodToolSummarizers:
    """Method-mode `@tool(...)` stores a `ToolMeta` on the function;
    `ToolsetBuilder` collects it into a `BoundMethodTool` per instance,
    which must delegate to the same declared callables."""

    def _build_toolset(self):
        instance = _Jiraish()
        return ToolsetBuilder(
            instance,
            name="jiraish",
            description="Fake connector for tests",
            path_prefix="/tools/jiraish",
        )

    def test_declared_summarizers_delegate(self) -> None:
        toolset = self._build_toolset()
        search_tool = next(t for t in toolset.tools if t.name == "jiraish__search")

        assert search_tool.summarize_args({"jql": "project = PA"}) == 'Searching: "project = PA"'
        assert search_tool.summarize_result({}, _result("[]")) == "Found: []"
        assert search_tool.summarize_result({}, _result("boom", is_error=True)) == "Failed: boom"

    def test_missing_arg_key_returns_none_not_raise(self) -> None:
        toolset = self._build_toolset()
        search_tool = next(t for t in toolset.tools if t.name == "jiraish__search")

        assert search_tool.summarize_args({}) is None

    def test_method_without_declared_summarizers_returns_none(self) -> None:
        toolset = self._build_toolset()
        get_tool = next(t for t in toolset.tools if t.name == "jiraish__get")

        assert get_tool.summarize_args({"issue_key": "PA-1"}) is None
        assert get_tool.summarize_result({}, _result("{}")) is None
