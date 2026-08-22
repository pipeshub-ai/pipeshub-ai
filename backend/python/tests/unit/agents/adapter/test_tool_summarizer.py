"""`PipesHubToolSummarizer` (Tool Call SSE Result Formatters plan) — the
concrete `ToolSummarizer` injected onto `AgentRuntime.summarizer`. Every
test here is pure-function / no I/O, using fixture result strings/JSON
that mirror real tool outputs (`web_search_tool.py`, `execute_query.py`,
...).

`retrieval__search_internal_knowledge` and `knowledgehub__list_files` are
no longer registered here — they're `@tool`-declared now (colocated
formatters, see `App Tool Summarizers` plan), so their tests moved to
`tests/unit/agents/actions/test_retrieval_summaries.py` and
`tests/unit/agents/actions/test_knowledge_hub_summaries.py` respectively,
targeting the formatters directly at their new home.
"""

from __future__ import annotations

from app.agent_loop_lib.core.types import Source, ToolResult
from app.agents.agent_loop.tool_summarizer import PipesHubToolSummarizer


def _result(content: object, *, is_error: bool = False, sources: list[Source] | None = None) -> ToolResult:
    return ToolResult(
        tool_call_id="call-1", name="tool", content=content, is_error=is_error, sources=sources or [],
    )


class TestWebSearchSummarizer:
    TOOL = "dynamic__web_search"

    def test_summarize_result_success(self) -> None:
        summarizer = PipesHubToolSummarizer()
        content = {
            "ok": True,
            "web_results": [
                {"title": "Python 3.12 release notes", "link": "https://docs.python.org/3.12/", "snippet": "..."},
                {"title": "What's new", "link": "https://python.org/whatsnew", "snippet": "..."},
            ],
        }

        summary = summarizer.summarize_result(self.TOOL, {"query": "python 3.12"}, _result(content))

        assert summary.result_summary is not None
        assert summary.result_summary.startswith("Found 2 results")
        assert "Python 3.12 release notes" in summary.result_summary
        assert "docs.python.org" in summary.result_summary

    def test_summarize_result_error(self) -> None:
        summarizer = PipesHubToolSummarizer()
        content = {"ok": False, "error": "rate limited"}

        summary = summarizer.summarize_result(self.TOOL, {"query": "x"}, _result(content, is_error=True))

        assert summary.result_summary is not None
        assert summary.result_summary.startswith("Web search failed:")
        assert "rate limited" in summary.result_summary


class TestFetchUrlSummarizer:
    TOOL = "dynamic__fetch_url"

    def test_summarize_args_extracts_url(self) -> None:
        summarizer = PipesHubToolSummarizer()
        summary = summarizer.summarize_args(self.TOOL, {"url": "https://docs.example.com/api/v2"})
        assert summary == "Reading https://docs.example.com/api/v2"

    def test_summarize_result_success(self) -> None:
        summarizer = PipesHubToolSummarizer()
        content = {"ok": True, "url": "https://docs.example.com/api/v2", "blocks": [{"type": "text", "content": "hi"}]}

        summary = summarizer.summarize_result(self.TOOL, {"url": "https://docs.example.com/api/v2"}, _result(content))

        assert summary.result_summary == "Fetched content from docs.example.com"


class TestSqlSummarizer:
    TOOL = "sql__execute_sql_query"

    def test_summarize_result_success(self) -> None:
        summarizer = PipesHubToolSummarizer()
        content = {"ok": True, "row_count": 42, "column_count": 5}

        summary = summarizer.summarize_result(self.TOOL, {"source_name": "PostgreSQL"}, _result(content))

        assert summary.result_summary == "Returned 42 rows, 5 columns"


class TestGenericFallback:
    def test_unknown_tool_gets_generic_summary(self) -> None:
        summarizer = PipesHubToolSummarizer()
        content = {"ok": True, "items": [{"id": 1}, {"id": 2}, {"id": 3}]}

        summary = summarizer.summarize_result("jira__search_issues", {}, _result(content))

        assert summary.result_summary is not None
        assert "3 item" in summary.result_summary

    def test_unknown_tool_with_error_result(self) -> None:
        summarizer = PipesHubToolSummarizer()
        content = {"ok": False, "error": "not authorized"}

        summary = summarizer.summarize_result("jira__search_issues", {}, _result(content, is_error=True))

        assert summary.result_summary is not None
        assert summary.result_summary.startswith("Failed:")
        assert "not authorized" in summary.result_summary

    def test_malformed_json_does_not_raise(self) -> None:
        summarizer = PipesHubToolSummarizer()

        summary = summarizer.summarize_result("jira__search_issues", {}, _result("not { valid json"))

        assert summary is not None

    def test_none_result_does_not_raise(self) -> None:
        summarizer = PipesHubToolSummarizer()

        summary = summarizer.summarize_result("jira__search_issues", {}, _result(None))

        assert summary.result_summary == "Completed successfully"

    def test_error_in_formatter_degrades_gracefully(self, monkeypatch) -> None:
        summarizer = PipesHubToolSummarizer()

        def _boom(args: dict, result: ToolResult) -> str:
            raise RuntimeError("formatter bug")

        monkeypatch.setitem(PipesHubToolSummarizer._result_formatters, "some__registered_tool", _boom)

        summary = summarizer.summarize_result("some__registered_tool", {}, _result("some content"))

        assert summary.result_summary is None

    def test_generic_result_prefers_structured_sources_when_present(self) -> None:
        summarizer = PipesHubToolSummarizer()
        sources = [Source(url="https://a.example.com", title="Source A")]

        summary = summarizer.summarize_result("some__tool", {}, _result("plain text", sources=sources))

        assert summary.result_summary is not None
        assert "Source A" in summary.result_summary

    def test_generic_args_uses_first_meaningful_string_param(self) -> None:
        summarizer = PipesHubToolSummarizer()

        summary = summarizer.summarize_args("jira__search_issues", {"query": "open bugs"})

        assert summary is not None
        assert "open bugs" in summary

    def test_args_summarizer_exception_returns_none(self, monkeypatch) -> None:
        summarizer = PipesHubToolSummarizer()

        def _boom(args: dict) -> str:
            raise RuntimeError("formatter bug")

        monkeypatch.setitem(PipesHubToolSummarizer._args_formatters, "some__registered_tool", _boom)

        assert summarizer.summarize_args("some__registered_tool", {"query": "x"}) is None

    def test_generic_descends_into_data_dict(self) -> None:
        """Connector app tools wrap list results one level deeper —
        `{"message": ..., "data": {"issues": [...]}}` — not as a
        top-level list; the generic fallback must scan that nested dict
        rather than collapsing straight to "Completed successfully"."""
        summarizer = PipesHubToolSummarizer()
        content = {
            "message": "Issues fetched successfully",
            "data": {"issues": [{"key": "PA-1"}, {"key": "PA-2"}]},
        }

        summary = summarizer.summarize_result("jira__search_issues", {}, _result(content))

        assert summary.result_summary == "Issues fetched successfully — 2 items"

    def test_generic_uses_message_field(self) -> None:
        """The envelope's own confirmation message should be preferred over
        the hardcoded "Completed successfully" label."""
        summarizer = PipesHubToolSummarizer()
        content = {"message": "Comment added successfully", "data": {"id": "c1"}}

        summary = summarizer.summarize_result("jira__add_comment", {}, _result(content))

        assert summary.result_summary == "Comment added successfully: c1"

    def test_generic_single_entity_data_surfaces_identifier(self) -> None:
        summarizer = PipesHubToolSummarizer()
        content = {"message": "Issue created successfully", "data": {"key": "PA-42"}}

        summary = summarizer.summarize_result("jira__create_issue", {}, _result(content))

        assert summary.result_summary == "Issue created successfully: PA-42"

    def test_generic_nested_data_dict_with_no_recognized_key_falls_back_to_message(self) -> None:
        summarizer = PipesHubToolSummarizer()
        content = {"message": "Done successfully", "data": {"unrecognized_field": "value"}}

        summary = summarizer.summarize_result("some__tool", {}, _result(content))

        assert summary.result_summary == "Done successfully"
