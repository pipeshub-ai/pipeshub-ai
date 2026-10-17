import json

from app.agent_loop_lib.core.types import ToolResult
from app.agents.actions.util.result_view import MAX_ROWS, MAX_VIEW_BYTES, view_bytes
from app.agents.agent_loop.tool_summarizer import PipesHubToolSummarizer
from app.agents.agent_loop.web_tool_adapter import WebToolAdapter
from app.web.tools.views import page_view, search_view, web_view


def results(n: int, **extra: object) -> dict:
    return {
        "result_type": "web_search",
        "web_results": [
            {"title": f"Result {i}", "link": f"https://site{i}.example/page", "snippet": "s", "domain": f"site{i}.example", **extra}
            for i in range(n)
        ],
    }


class TestSearchView:
    def test_every_result_with_title_site_and_link(self) -> None:  # WP-2-VIEW-01
        view = search_view(results(3))
        assert view["kind"] == "records"
        assert view["columns"] == ["Title", "Site"]
        assert view["total"] == 3
        assert view["rows"][0] == {"cells": ["Result 0", "site0.example"], "url": "https://site0.example/page"}

    def test_date_column_only_when_a_result_has_a_date(self) -> None:
        payload = results(2)
        payload["web_results"][1]["published"] = "2026-01-02"
        view = search_view(payload)
        assert view["columns"] == ["Title", "Site", "Date"]
        assert [r["cells"][2] for r in view["rows"]] == ["", "2026-01-02"]

    def test_rows_are_capped_but_the_total_is_kept(self) -> None:
        view = search_view(results(MAX_ROWS + 5))
        assert len(view["rows"]) == MAX_ROWS
        assert view["total"] == MAX_ROWS + 5

    def test_stays_within_the_stored_size(self) -> None:
        payload = results(MAX_ROWS, title="T" * 500)
        payload["web_results"] = [{**r, "link": r["link"] + "?" + "q" * 1500} for r in payload["web_results"]]
        view = search_view(payload)
        assert view_bytes(view) <= MAX_VIEW_BYTES
        assert all(len(r["cells"][0]) <= 120 for r in view["rows"])

    def test_unsafe_links_are_not_linked(self) -> None:
        payload = {"result_type": "web_search", "web_results": [
            {"title": "js", "link": "javascript:alert(1)"},
            {"title": "creds", "link": "https://user:pass@example.com/"},
            {"title": "ok", "link": "https://example.com/"},
        ]}
        view = search_view(payload)
        assert ["url" in r for r in view["rows"]] == [False, False, True]

    def test_no_results_no_view(self) -> None:
        assert search_view({"result_type": "web_search", "web_results": []}) is None


class TestPageView:
    def test_page(self) -> None:
        view = page_view({"result_type": "url_content", "url": "https://www.example.com/a", "title": "A page"})
        assert view == {
            "kind": "fields",
            "fields": [{"label": "Page", "value": "A page"}, {"label": "Site", "value": "example.com"}],
            "url": "https://www.example.com/a",
        }

    def test_untitled_page_uses_the_site(self) -> None:
        assert page_view({"url": "https://example.com/"})["fields"][0]["value"] == "example.com"

    def test_unsafe_url_no_view(self) -> None:
        assert page_view({"url": "file:///etc/passwd"}) is None


def test_web_view_dispatch() -> None:
    assert web_view({"result_type": "other"}) is None
    assert web_view(results(1))["kind"] == "records"


class TestAdapter:
    def test_view_is_returned_once_for_the_text_the_call_produced(self) -> None:
        adapter = WebToolAdapter.__new__(WebToolAdapter)
        adapter._views = {}
        adapter._views["formatted text"] = {"kind": "records"}
        hit = ToolResult(tool_call_id="1", name="web_search", content="formatted text")
        assert adapter.result_view({}, hit) == {"kind": "records"}
        assert adapter.result_view({}, hit) is None
        assert adapter.result_view({}, ToolResult(tool_call_id="2", name="web_search", content={"x": 1})) is None


class TestSummaries:
    summarizer = PipesHubToolSummarizer()

    def failed(self, tool: str, error_type: str, error: str = "raw") -> str | None:
        content = json.dumps({"ok": False, "error_type": error_type, "error": error})
        return self.summarizer.summarize_result(tool, {}, ToolResult(tool_call_id="1", name=tool, content=content)).result_summary

    def test_search_failures_in_plain_words(self) -> None:
        assert self.failed("dynamic__web_search", "blocked") == "Web search failed: The search engines blocked the request"
        assert self.failed("dynamic__web_search", "something_new", "Odd thing") == "Web search failed: Odd thing"

    def test_fetch_failures_in_plain_words(self) -> None:
        assert self.failed("dynamic__fetch_url", "unsafe_url") == "Failed to read: That address isn't a public website"
        assert self.failed("dynamic__fetch_url", "no_text", "PDF has no text layer") == "Failed to read: PDF has no text layer"

    def test_a_citation_alias_is_not_shown_as_the_page(self) -> None:
        assert self.summarizer.summarize_args("dynamic__fetch_url", {"url": "https://ref3.xyz"}) == "Reading a page from the results"
        assert self.summarizer.summarize_args("dynamic__fetch_url", {"url": "https://example.com/a"}) == "Reading https://example.com/a"

    def test_a_read_page_is_named(self) -> None:
        from app.agent_loop_lib.core.types import Source

        result = ToolResult(tool_call_id="1", name="dynamic__fetch_url", content="x",
                            sources=[Source(url="https://www.example.com/a#:~:text=x", title="A page")])
        assert self.summarizer.summarize_result("dynamic__fetch_url", {}, result).result_summary == "Read “A page” (www.example.com)"
