"""`list_files`'s colocated `args_summary`/`result_summary` formatters
(see `knowledge_hub.py`'s `@tool(...)` declaration) — moved here from the
central `PipesHubToolSummarizer` registry as part of the App Tool
Summarizers plan (tools declare their own summarizers now).
"""

from __future__ import annotations

from app.agent_loop_lib.core.types import ToolResult
from app.agents.actions.knowledge_hub.knowledge_hub import (
    _list_files_args_summary as args_summary,
    _list_files_result_summary as result_summary,
)


def _result(content: object, *, is_error: bool = False) -> ToolResult:
    return ToolResult(tool_call_id="call-1", name="knowledgehub__list_files", content=content, is_error=is_error)


class TestListFilesArgsSummary:
    def test_with_query(self) -> None:
        assert args_summary({"query": "quarterly report"}) == 'Searched Knowledge Hub for "quarterly report"'

    def test_browse_without_query(self) -> None:
        assert args_summary({"parent_id": "p1"}) == "Listed files"


class TestListFilesResultSummary:
    def test_success_with_items(self) -> None:
        content = {
            "success": True,
            "items": [
                {"name": "Report.pdf"}, {"name": "Notes.docx"}, {"name": "Budget.xlsx"},
            ],
        }
        summary = result_summary({}, _result(content))

        assert summary is not None
        assert summary.startswith("Found 3 items")
        assert "Report.pdf" in summary

    def test_empty_items(self) -> None:
        content = {"success": True, "items": []}
        assert result_summary({}, _result(content)) == "No items found"

    def test_error_envelope(self) -> None:
        content = {"success": False, "error": "Access denied"}
        summary = result_summary({}, _result(content, is_error=True))

        assert summary is not None
        assert summary.startswith("Listing failed:")
        assert "Access denied" in summary

    def test_non_dict_content_returns_none(self) -> None:
        assert result_summary({}, _result("not a dict")) is None
