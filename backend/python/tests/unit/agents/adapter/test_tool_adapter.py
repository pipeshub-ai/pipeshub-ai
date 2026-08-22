"""`PipesHubStructuredToolAdapter` (`app/agents/agent_loop/tool_adapter.py`) —
dynamic LangChain `StructuredTool`s execute correctly through agent-loop's
`Tool` ABC, with success/failure verdicts matching the legacy extraction
logic."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from app.agents.agent_loop.tool_adapter import (
    PipesHubStructuredToolAdapter,
    _to_tool_output,
    split_original_tool_name,
)


class _EchoArgs(BaseModel):
    text: str = ""


async def _echo_coroutine(**kwargs: Any) -> str:
    if kwargs.get("fail"):
        raise ValueError("dynamic tool failure")
    return f"echo: {kwargs.get('text', '')}"


def _make_structured_tool() -> StructuredTool:
    return StructuredTool.from_function(
        name="web_search",
        description="Search the web",
        args_schema=_EchoArgs,
        coroutine=_echo_coroutine,
    )


class TestPipesHubStructuredToolAdapter:
    def test_name_and_path(self) -> None:
        adapter = PipesHubStructuredToolAdapter(_make_structured_tool(), "dynamic", "web_search")
        assert adapter.name == "dynamic__web_search"
        assert adapter.path == "/dynamic/dynamic/web_search"

    async def test_execute_success_calls_coroutine(self) -> None:
        adapter = PipesHubStructuredToolAdapter(_make_structured_tool(), "dynamic", "web_search")
        output = await adapter.execute(text="hello")
        assert output.success is True
        assert "hello" in output.data

    async def test_execute_failure_wraps_exception(self) -> None:
        adapter = PipesHubStructuredToolAdapter(_make_structured_tool(), "dynamic", "web_search")
        output = await adapter.execute(fail=True)
        assert output.success is False
        assert "dynamic tool failure" in output.error


class TestToTooOutputRetrievalFastPath:
    """`_to_tool_output()`'s `<record>` fast path — retrieved document
    content routinely contains words like "error"/"failed"/"traceback"
    (bug reports, incident postmortems, ...), which the generic
    `ToolResultExtractor` substring heuristic would otherwise
    false-positive on, misclassifying a successful search as a failure."""

    def test_record_content_with_error_like_words_is_success(self) -> None:
        payload = (
            "Retrieved 3 knowledge blocks from 2 documents.\n\n<record>\n"
            "Record ID : abc-123\n\nRecord blocks (sorted):\n\n"
            "* Block Content: The deployment failed with a traceback showing "
            "an error in the retry handler.\n"
        )
        output = _to_tool_output(payload)
        assert output.success is True
        assert output.data == payload
        assert output.error is None

    def test_record_content_without_error_words_is_still_success(self) -> None:
        payload = "Retrieved 1 knowledge blocks from 1 documents.\n\n<record>\nAll good here.\n"
        output = _to_tool_output(payload)
        assert output.success is True
        assert output.data == payload

    def test_non_record_string_with_error_words_still_fails(self) -> None:
        """No `<record>` marker — falls through to the normal heuristic, so
        genuine tool errors are still classified as failures."""
        output = _to_tool_output("Error: something failed with a traceback")
        assert output.success is False

    def test_plain_success_string_without_record_marker_unaffected(self) -> None:
        output = _to_tool_output("searched for cats")
        assert output.success is True
        assert output.data == "searched for cats"


class TestSplitOriginalToolName:
    def test_dotted_name_splits_into_app_and_tool(self) -> None:
        tool = _make_structured_tool()
        tool._original_name = "slack.fetch_slack_thread"
        app_name, tool_name = split_original_tool_name(tool)
        assert app_name == "slack"
        assert tool_name == "fetch_slack_thread"

    def test_no_dot_falls_back_to_dynamic_bucket(self) -> None:
        tool = _make_structured_tool()
        app_name, tool_name = split_original_tool_name(tool)
        assert app_name == "dynamic"
        assert tool_name == "web_search"
