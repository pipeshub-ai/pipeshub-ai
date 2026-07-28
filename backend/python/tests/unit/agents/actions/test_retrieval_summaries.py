"""`search_internal_knowledge`'s colocated `args_summary`/`result_summary`
formatters (see `retrieval.py`'s `@tool(...)` declaration) — moved here
from the central `PipesHubToolSummarizer` registry as part of the App Tool
Summarizers plan (tools declare their own summarizers now). Every test is
pure-function / no I/O, using fixture strings that mirror real tool
output.
"""

from __future__ import annotations

from app.agent_loop_lib.core.types import ToolResult
from app.agents.actions.retrieval.retrieval import (
    _search_internal_knowledge_args_summary as args_summary,
    _search_internal_knowledge_result_summary as result_summary,
)


def _result(content: object, *, is_error: bool = False) -> ToolResult:
    return ToolResult(tool_call_id="call-1", name="retrieval__search_internal_knowledge", content=content, is_error=is_error)


class TestSearchInternalKnowledgeArgsSummary:
    def test_query_only(self) -> None:
        assert args_summary({"query": "bug bash"}) == 'Searched for "bug bash"'

    def test_query_and_connector_ids(self) -> None:
        summary = args_summary({"query": "bug bash", "connector_ids": ["c1", "c2"]})
        assert summary == 'Searched for "bug bash" in 2 sources'

    def test_missing_query_returns_none(self) -> None:
        assert args_summary({}) is None


class TestSearchInternalKnowledgeResultSummary:
    def test_success_with_records(self) -> None:
        content = (
            "Retrieved 36 knowledge blocks from 21 documents.\n\n"
            "<record>\nRecord ID       : r1\nName            : Doc A\n"
            "Web URL         : https://example.com/a\n\nRecord blocks (sorted):\n\n"
            "<record>\nRecord ID       : r2\nName            : Doc B\n"
            "Web URL         : https://example.com/b\n\nRecord blocks (sorted):\n\n"
        )
        summary = result_summary({"query": "bug bash"}, _result(content))

        assert summary is not None
        assert summary.startswith("Retrieved 36 blocks from 21 documents")
        assert "Doc A" in summary
        assert "Doc B" in summary

    def test_no_results_found(self) -> None:
        content = '{"status":"success","message":"No results found","results":[],"result_count":0}'
        assert result_summary({"query": "x"}, _result(content)) == "No results found"

    def test_error_envelope(self) -> None:
        content = '{"status":"error","message":"Retrieval error: timeout"}'
        summary = result_summary({"query": "x"}, _result(content, is_error=True))

        assert summary is not None
        assert summary.startswith("Search failed:")
        assert "timeout" in summary

    def test_max_items_capped(self) -> None:
        records = "".join(
            f"<record>\nRecord ID       : r{i}\nName            : Doc {i}\n\nRecord blocks (sorted):\n\n"
            for i in range(20)
        )
        content = f"Retrieved 40 knowledge blocks from 20 documents.\n\n{records}"
        summary = result_summary({"query": "x"}, _result(content))

        assert summary is not None
        assert summary.count("- Doc") == 5
        assert "and 15 more" in summary

    def test_non_string_content_returns_none(self) -> None:
        assert result_summary({}, _result(None)) is None
