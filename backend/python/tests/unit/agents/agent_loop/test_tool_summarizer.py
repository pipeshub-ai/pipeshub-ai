"""Unit tests for PipesHubToolSummarizer — registry, per-tool formatters, and
generic fallbacks."""

from __future__ import annotations

import json

import pytest

from app.agent_loop_lib.core.types import Source, ToolResult
from app.agent_loop_lib.tools.summarizer import ToolCallSummary
from app.agents.agent_loop.tool_summarizer import (
    PipesHubToolSummarizer,
    _extract_record_summaries,
    _generic_args_formatter,
    _generic_result_formatter,
    _humanize_tool_name,
)


def _result(content="", *, is_error=False, sources=None):
    return ToolResult(
        tool_call_id="tc-1",
        name="test",
        content=content,
        is_error=is_error,
        sources=sources or [],
    )


# ---------------------------------------------------------------------------
# _humanize_tool_name
# ---------------------------------------------------------------------------


class TestHumanizeToolName:
    def test_double_underscore_splits(self):
        assert _humanize_tool_name("jira__get_issues") == "Get Issues"

    def test_single_name(self):
        assert _humanize_tool_name("run_code") == "Run Code"

    def test_hyphens(self):
        assert _humanize_tool_name("my-tool-name") == "My Tool Name"

    def test_empty_string(self):
        assert _humanize_tool_name("") == ""

    def test_already_clean(self):
        assert _humanize_tool_name("search") == "Search"


# ---------------------------------------------------------------------------
# _extract_record_summaries
# ---------------------------------------------------------------------------


class TestExtractRecordSummaries:
    def test_single_record(self):
        text = "<record>\nName  : My Doc\nWeb URL  : https://example.com/doc\n</record>"
        result = _extract_record_summaries(text)
        assert result == [("My Doc", "https://example.com/doc")]

    def test_multiple_records(self):
        text = (
            "<record>\nName  : Doc A\n</record>"
            "<record>\nName  : Doc B\nWeb URL  : https://b.com\n</record>"
        )
        result = _extract_record_summaries(text)
        assert len(result) == 2
        assert result[0] == ("Doc A", None)
        assert result[1] == ("Doc B", "https://b.com")

    def test_no_records(self):
        assert _extract_record_summaries("no records here") == []

    def test_missing_name(self):
        text = "<record>\nWeb URL  : https://example.com\n</record>"
        assert _extract_record_summaries(text) == []


# ---------------------------------------------------------------------------
# PipesHubToolSummarizer — registry dispatch
# ---------------------------------------------------------------------------


class TestSummarizerDispatch:
    def setup_method(self):
        self.summarizer = PipesHubToolSummarizer()

    def test_summarize_args_registered(self):
        result = self.summarizer.summarize_args("web_search", {"query": "hello"})
        assert result is not None
        assert "hello" in result

    def test_summarize_args_generic_fallback(self):
        result = self.summarizer.summarize_args("unknown__tool", {"query": "test"})
        assert result is not None
        assert "test" in result

    def test_summarize_args_exception_returns_none(self):
        original = PipesHubToolSummarizer._args_formatters.copy()
        try:
            PipesHubToolSummarizer._args_formatters["_test_bad"] = lambda args: (_ for _ in ()).throw(RuntimeError("boom"))
            result = self.summarizer.summarize_args("_test_bad", {})
            assert result is None
        finally:
            PipesHubToolSummarizer._args_formatters = original

    def test_summarize_result_registered(self):
        r = _result(sources=[Source(url="https://example.com", title="Example")])
        summary = self.summarizer.summarize_result("web_search", {"query": "test"}, r)
        assert isinstance(summary, ToolCallSummary)
        assert summary.result_summary is not None

    def test_summarize_result_generic_fallback(self):
        r = _result(content=json.dumps({"message": "Done", "data": [1, 2]}))
        summary = self.summarizer.summarize_result("unknown__tool", {}, r)
        assert isinstance(summary, ToolCallSummary)
        assert summary.result_summary is not None

    def test_summarize_result_exception_returns_empty(self):
        original = PipesHubToolSummarizer._result_formatters.copy()
        try:
            PipesHubToolSummarizer._result_formatters["_test_bad"] = lambda args, result: (_ for _ in ()).throw(RuntimeError("boom"))
            summary = self.summarizer.summarize_result("_test_bad", {}, _result())
            assert isinstance(summary, ToolCallSummary)
            assert summary.result_summary is None
        finally:
            PipesHubToolSummarizer._result_formatters = original


# ---------------------------------------------------------------------------
# web_search
# ---------------------------------------------------------------------------


class TestWebSearch:
    def setup_method(self):
        self.summarizer = PipesHubToolSummarizer()

    def test_args_with_query(self):
        result = self.summarizer.summarize_args("dynamic__web_search", {"query": "python async"})
        assert result == 'Searched the web for "python async"'

    def test_args_empty_query(self):
        result = self.summarizer.summarize_args("dynamic__web_search", {"query": "  "})
        assert result is None

    def test_result_with_sources(self):
        r = _result(sources=[
            Source(title="Result 1", url="https://example.com"),
            Source(title="Result 2", url="https://other.com"),
        ])
        summary = self.summarizer.summarize_result("web_search", {}, r)
        assert "2 results" in summary.result_summary

    def test_result_single_source(self):
        r = _result(sources=[Source(title="Only One", url="https://one.com")])
        summary = self.summarizer.summarize_result("web_search", {}, r)
        assert "1 result" in summary.result_summary
        assert "results" not in summary.result_summary

    def test_result_json_no_results(self):
        r = _result(content=json.dumps({"web_results": []}))
        summary = self.summarizer.summarize_result("web_search", {}, r)
        assert summary.result_summary == "No results found"

    def test_result_json_error(self):
        r = _result(content=json.dumps({"ok": False, "error": "rate limited"}))
        summary = self.summarizer.summarize_result("web_search", {}, r)
        assert "rate limited" in summary.result_summary

    def test_result_json_with_results(self):
        results = [{"title": f"Result {i}", "link": f"https://r{i}.com"} for i in range(3)]
        r = _result(content=json.dumps({"web_results": results}))
        summary = self.summarizer.summarize_result("web_search", {}, r)
        assert "3 results" in summary.result_summary


# ---------------------------------------------------------------------------
# fetch_url
# ---------------------------------------------------------------------------


class TestFetchUrl:
    def setup_method(self):
        self.summarizer = PipesHubToolSummarizer()

    def test_args(self):
        result = self.summarizer.summarize_args("dynamic__fetch_url", {"url": "https://example.com/page"})
        assert result == "Reading https://example.com/page"

    def test_args_empty_url(self):
        result = self.summarizer.summarize_args("fetch_url", {"url": "  "})
        assert result is None

    def test_result_with_source(self):
        r = _result(sources=[Source(url="https://example.com/page")])
        summary = self.summarizer.summarize_result("fetch_url", {"url": "https://example.com/page"}, r)
        assert "example.com" in summary.result_summary

    def test_result_json_error(self):
        r = _result(content=json.dumps({"ok": False, "error": "timeout"}))
        summary = self.summarizer.summarize_result("fetch_url", {}, r)
        assert "timeout" in summary.result_summary

    def test_result_json_with_url(self):
        r = _result(content=json.dumps({"ok": True, "url": "https://test.dev/page"}))
        summary = self.summarizer.summarize_result("fetch_url", {}, r)
        assert "test.dev" in summary.result_summary


# ---------------------------------------------------------------------------
# sql__execute_sql_query
# ---------------------------------------------------------------------------


class TestSql:
    def setup_method(self):
        self.summarizer = PipesHubToolSummarizer()

    def test_args_with_source(self):
        result = self.summarizer.summarize_args("sql__execute_sql_query", {"source_name": "sales_db"})
        assert result == 'Running SQL query on "sales_db"'

    def test_args_no_source(self):
        result = self.summarizer.summarize_args("sql__execute_sql_query", {})
        assert result == "Running SQL query"

    def test_result_success(self):
        r = _result(content=json.dumps({"row_count": 5, "column_count": 3}))
        summary = self.summarizer.summarize_result("sql__execute_sql_query", {}, r)
        assert "5 rows" in summary.result_summary
        assert "3 columns" in summary.result_summary

    def test_result_single(self):
        r = _result(content=json.dumps({"row_count": 1, "column_count": 1}))
        summary = self.summarizer.summarize_result("sql__execute_sql_query", {}, r)
        assert "1 row," in summary.result_summary
        assert "1 column" in summary.result_summary

    def test_result_error(self):
        r = _result(content=json.dumps({"ok": False, "error": "syntax error"}))
        summary = self.summarizer.summarize_result("sql__execute_sql_query", {}, r)
        assert "syntax error" in summary.result_summary


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------


class TestSlack:
    def setup_method(self):
        self.summarizer = PipesHubToolSummarizer()

    def test_thread_args(self):
        result = self.summarizer.summarize_args("slack__fetch_slack_thread", {})
        assert result == "Fetching Slack thread"

    def test_nearby_args(self):
        result = self.summarizer.summarize_args("slack__fetch_slack_nearby_messages", {})
        assert result == "Fetching nearby Slack messages"

    def test_result_with_count(self):
        r = _result(content=json.dumps({"record_count": 10}))
        summary = self.summarizer.summarize_result("slack__fetch_slack_thread", {}, r)
        assert "10 messages" in summary.result_summary

    def test_result_single_message(self):
        r = _result(content=json.dumps({"record_count": 1}))
        summary = self.summarizer.summarize_result("slack__fetch_slack_thread", {}, r)
        assert "1 message" in summary.result_summary
        assert "messages" not in summary.result_summary

    def test_result_with_records_list(self):
        r = _result(content=json.dumps({"records": ["a", "b", "c"]}))
        summary = self.summarizer.summarize_result("slack__fetch_slack_thread", {}, r)
        assert "3 messages" in summary.result_summary


# ---------------------------------------------------------------------------
# fetch_record
# ---------------------------------------------------------------------------


class TestFetchRecord:
    def setup_method(self):
        self.summarizer = PipesHubToolSummarizer()

    def test_args_with_ids(self):
        result = self.summarizer.summarize_args(
            "knowledgegraph__fetch_record", {"record_ids": ["r1", "r2", "r3"]}
        )
        assert result == "Fetching 3 documents"

    def test_args_single_id(self):
        result = self.summarizer.summarize_args(
            "dynamic_fetch_full_record", {"record_ids": ["r1"]}
        )
        assert result == "Fetching 1 document"

    def test_args_no_ids(self):
        result = self.summarizer.summarize_args("knowledgegraph__fetch_record", {})
        assert result == "Fetching document"

    def test_result_with_records(self):
        text = "<record>\nName  : Report.pdf\nWeb URL  : https://ex.com\n</record>"
        r = _result(content=text)
        summary = self.summarizer.summarize_result("knowledgegraph__fetch_record", {}, r)
        assert "1 document" in summary.result_summary

    def test_result_error(self):
        r = _result(content="Record not found", is_error=True)
        summary = self.summarizer.summarize_result("knowledgegraph__fetch_record", {}, r)
        assert "Fetch failed" in summary.result_summary


# ---------------------------------------------------------------------------
# run_code / install_packages
# ---------------------------------------------------------------------------


class TestSandbox:
    def setup_method(self):
        self.summarizer = PipesHubToolSummarizer()

    def test_run_code_args(self):
        result = self.summarizer.summarize_args("run_code", {"language": "python"})
        assert result == "Running python code"

    def test_run_code_args_no_lang(self):
        result = self.summarizer.summarize_args("run_code", {})
        assert result == "Running code"

    def test_install_packages_args(self):
        result = self.summarizer.summarize_args("install_packages", {"packages": ["numpy", "pandas"]})
        assert result == "Installing numpy, pandas"

    def test_install_packages_empty(self):
        result = self.summarizer.summarize_args("install_packages", {"packages": []})
        assert result == "Installing packages"

    def test_sandbox_result_success(self):
        r = _result(content="output")
        summary = self.summarizer.summarize_result("run_code", {}, r)
        assert summary.result_summary == "Executed successfully"

    def test_sandbox_result_error(self):
        r = _result(content="SyntaxError: invalid syntax", is_error=True)
        summary = self.summarizer.summarize_result("run_code", {}, r)
        assert "SyntaxError" in summary.result_summary


# ---------------------------------------------------------------------------
# create_plan / spawn_agent / task_complete
# ---------------------------------------------------------------------------


class TestAgentMeta:
    def setup_method(self):
        self.summarizer = PipesHubToolSummarizer()

    def test_create_plan_args(self):
        result = self.summarizer.summarize_args("create_plan", {"steps": [1, 2, 3]})
        assert result == "Creating a plan with 3 steps"

    def test_spawn_agent_args(self):
        result = self.summarizer.summarize_args("spawn_agent", {"role": "researcher"})
        assert result == "Delegating to researcher"

    def test_task_complete_args(self):
        result = self.summarizer.summarize_args("task_complete", {})
        assert result == "Marking task complete"

    def test_create_plan_result_success(self):
        summary = self.summarizer.summarize_result("create_plan", {}, _result())
        assert summary.result_summary == "Plan created"

    def test_spawn_agent_result_error(self):
        summary = self.summarizer.summarize_result("spawn_agent", {}, _result(is_error=True))
        assert summary.result_summary is None


# ---------------------------------------------------------------------------
# list_toolsets / fetch_tools / search_tools
# ---------------------------------------------------------------------------


class TestToolsetMeta:
    def setup_method(self):
        self.summarizer = PipesHubToolSummarizer()

    def test_list_toolsets_args(self):
        assert self.summarizer.summarize_args("list_toolsets", {}) == "Listing available capabilities"

    def test_fetch_tools_args(self):
        result = self.summarizer.summarize_args("fetch_tools", {"toolset": "jira"})
        assert result == 'Loading tools for "jira"'

    def test_search_tools_args(self):
        result = self.summarizer.summarize_args("search_tools", {"intent": "find issues"})
        assert result == 'Searching for tools matching "find issues"'

    def test_list_toolsets_result(self):
        r = _result(content=json.dumps({"toolsets": [{"name": "a"}, {"name": "b"}]}))
        summary = self.summarizer.summarize_result("list_toolsets", {}, r)
        assert "2 categories" in summary.result_summary

    def test_list_toolsets_result_single(self):
        r = _result(content=json.dumps({"toolsets": [{"name": "a"}]}))
        summary = self.summarizer.summarize_result("list_toolsets", {}, r)
        assert "1 category" in summary.result_summary

    def test_fetch_tools_result(self):
        r = _result(content=json.dumps({"tools": [1, 2, 3]}))
        summary = self.summarizer.summarize_result("fetch_tools", {}, r)
        assert "3 tools" in summary.result_summary

    def test_search_tools_result(self):
        r = _result(content=json.dumps({"matches": [1]}))
        summary = self.summarizer.summarize_result("search_tools", {}, r)
        assert "1 match" in summary.result_summary
        assert "matches" not in summary.result_summary


# ---------------------------------------------------------------------------
# Generic fallbacks
# ---------------------------------------------------------------------------


class TestGenericArgs:
    def test_picks_first_string_arg(self):
        result = _generic_args_formatter({"query": "search term"}, "my_app__find")
        assert result == 'Find: "search term"'

    def test_truncates_long_value(self):
        long_val = "x" * 100
        result = _generic_args_formatter({"query": long_val}, "app__tool")
        assert len(result) < 120
        assert result.endswith("…\"")

    def test_no_matching_key(self):
        result = _generic_args_formatter({"foo": "bar"}, "app__tool")
        assert result == "Tool"

    def test_skips_empty_string(self):
        result = _generic_args_formatter({"query": "  ", "text": "real"}, "app__tool")
        assert "real" in result


class TestGenericResult:
    def test_error_json(self):
        r = _result(content=json.dumps({"message": "Not found"}), is_error=True)
        result = _generic_result_formatter({}, r)
        assert "Not found" in result

    def test_error_text(self):
        r = _result(content="Something broke", is_error=True)
        result = _generic_result_formatter({}, r)
        assert "Something broke" in result

    def test_success_with_sources(self):
        r = _result(sources=[
            Source(title="Doc A"),
            Source(url="https://b.com"),
        ])
        result = _generic_result_formatter({}, r)
        assert "2 sources" in result

    def test_success_json_with_items_list(self):
        r = _result(content=json.dumps({"message": "OK", "items": [1, 2, 3]}))
        result = _generic_result_formatter({}, r)
        assert "3 items" in result
        assert "OK" in result

    def test_success_json_nested_data(self):
        r = _result(content=json.dumps({"message": "Done", "data": {"issues": [1, 2]}}))
        result = _generic_result_formatter({}, r)
        assert "2 items" in result

    def test_success_json_data_entity_id(self):
        r = _result(content=json.dumps({"message": "Created", "data": {"key": "PROJ-123"}}))
        result = _generic_result_formatter({}, r)
        assert "PROJ-123" in result

    def test_success_json_bare_message(self):
        r = _result(content=json.dumps({"message": "All good"}))
        result = _generic_result_formatter({}, r)
        assert result == "All good"

    def test_success_json_list(self):
        r = _result(content=json.dumps([1, 2, 3, 4]))
        result = _generic_result_formatter({}, r)
        assert "4 items" in result

    def test_success_plain_text(self):
        r = _result(content="plain text response")
        result = _generic_result_formatter({}, r)
        assert result == "plain text response"

    def test_success_empty_content(self):
        r = _result(content=None)
        result = _generic_result_formatter({}, r)
        assert result == "Completed successfully"

    def test_failure_envelope(self):
        r = _result(content=json.dumps({"ok": False, "message": "Denied"}))
        result = _generic_result_formatter({}, r)
        assert "Failed" in result
        assert "Denied" in result
