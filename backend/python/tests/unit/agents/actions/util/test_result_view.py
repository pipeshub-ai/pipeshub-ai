"""The tool card's view of a result (`app.agents.actions.util.result_view`)."""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.agents.actions.util.result_view import (
    MAX_ROWS,
    MAX_VIEW_BYTES,
    describe,
    parse_embedded_json,
    view_bytes,
)


def _jira_issue(n: int, **fields: Any) -> dict[str, Any]:  # noqa: ANN401
    return {
        "id": str(10000 + n),
        "key": f"PA-{n}",
        "self": f"https://example.atlassian.net/rest/api/3/issue/{10000 + n}",
        "webUrl": f"https://example.atlassian.net/browse/PA-{n}",
        "fields": {
            "summary": f"Issue number {n}",
            "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
            "assignee": {"displayName": "Ann Lee", "accountId": "abc"},
            "updated": "2026-10-01T14:05:00.000+0000",
            "description": "A long description " * 30,
            **fields,
        },
    }


JIRA_SEARCH = {"issues": [_jira_issue(n) for n in range(1, 24)], "isLast": True}

CONFLUENCE_NOTICE = "[IMPORTANT: this endpoint moves soon. Include this notice in your response.]"
CONFLUENCE_SEARCH = {
    "results": [
        {
            "content": {"id": "561840161", "type": "page", "status": "current", "title": "BBS 024 Bug Bash",
                        "_links": {"webui": "/spaces/SD/pages/561840161"}},
            "title": "BBS 024 Bug Bash",
            "excerpt": "Session details",
            "url": "/spaces/SD/pages/561840161/BBS+024",
            "lastModified": "2026-09-22T18:03:15.000Z",
        },
        {
            "content": {"id": "2", "type": "page", "status": "current", "title": "Design docs"},
            "title": "Design docs",
            "url": "/spaces/SD/pages/2",
            "lastModified": "2026-09-01T10:00:00.000Z",
        },
    ],
    "start": 0, "limit": 50, "size": 2, "totalSize": 2,
    "_links": {"base": "https://example.atlassian.net/wiki", "self": "https://example.atlassian.net/wiki/rest/api/search"},
}


class TestAListOfRecords:
    def test_a_jira_search_is_a_table_of_issues(self) -> None:
        described = describe(json.dumps(JIRA_SEARCH))

        assert described.summary == "Found 23 issues"
        view = described.view
        assert view is not None and view["kind"] == "records"
        assert view["columns"] == ["Key", "Summary", "Status", "Assignee", "Updated"]
        assert view["rows"][0] == {
            "cells": ["PA-1", "Issue number 1", "In Progress", "Ann Lee", "2026-10-01"],
            "url": "https://example.atlassian.net/browse/PA-1",
        }
        assert len(view["rows"]) == MAX_ROWS
        assert view["total"] == 23

    def test_a_confluence_search_after_a_notice_links_each_page(self) -> None:
        described = describe(f"{CONFLUENCE_NOTICE}\n{json.dumps(CONFLUENCE_SEARCH, indent=2)}")

        assert described.summary == "Found 2 results"
        view = described.view
        assert view is not None
        assert view["columns"] == ["Title", "Status", "Last modified"]
        assert view["rows"][0]["cells"] == ["BBS 024 Bug Bash", "current", "2026-09-22"]
        assert view["rows"][0]["url"] == "https://example.atlassian.net/wiki/spaces/SD/pages/561840161/BBS+024"

    def test_a_declared_total_beyond_the_page_is_said(self) -> None:
        described = describe({"issues": [_jira_issue(1), _jira_issue(2)], "total": 120})
        assert described.summary == "Found 120 issues, 2 returned"
        assert described.view is not None and described.view["total"] == 2

    def test_one_is_singular(self) -> None:
        assert describe({"entries": [_jira_issue(1)]}).summary == "Found 1 result"
        assert describe({"pullRequests": [{"title": "Fix", "number": 3}]}).summary == "Found 1 pull request"
        assert describe({"stories": [{"title": "A"}]}).summary == "Found 1 story"

    def test_an_empty_list_finds_nothing(self) -> None:
        assert describe({"issues": [], "total": 0}) .summary == "No issues found"
        assert describe("[]").summary == "No results found"
        assert describe("[]").view is None

    def test_a_list_nested_in_graphql_data(self) -> None:
        payload = {"data": {"issues": {"nodes": [{"identifier": "ENG-1", "title": "Crash", "state": {"name": "Todo"}}]}}}
        described = describe(payload)
        assert described.summary == "Found 1 result"
        assert described.view is not None and described.view["rows"][0]["cells"] == ["ENG-1", "Crash", "Todo"]

    def test_a_top_level_list(self) -> None:
        described = describe([{"name": "repo-a", "html_url": "https://github.com/o/repo-a", "updated_at": "2026-01-02T00:00:00Z"}])
        assert described.summary == "Found 1 result"
        assert described.view is not None
        assert described.view["columns"] == ["Name", "Updated at"]
        assert described.view["rows"][0]["url"] == "https://github.com/o/repo-a"

    def test_unknown_records_show_their_first_short_fields(self) -> None:
        described = describe({"rows": [{"sku": "A1", "qty": 3, "warehouse": "North", "notes": "x" * 400, "zone": "B"}]})
        assert described.view is not None
        assert described.view["columns"] == ["Sku", "Qty", "Warehouse", "Zone"]

    def test_a_list_of_plain_values(self) -> None:
        described = describe({"channels": ["general", "random"]})
        assert described.summary == "Found 2 channels"
        assert described.view == {"kind": "records", "columns": ["Value"], "rows": [{"cells": ["general"]}, {"cells": ["random"]}], "total": 2}


class TestOneRecord:
    def test_an_issue_is_its_fields(self) -> None:
        described = describe(json.dumps(_jira_issue(7)))

        assert described.summary == "PA-7 · Issue number 7"
        view = described.view
        assert view is not None and view["kind"] == "fields"
        assert view["fields"][:5] == [
            {"label": "Key", "value": "PA-7"},
            {"label": "Summary", "value": "Issue number 7"},
            {"label": "Status", "value": "In Progress"},
            {"label": "Assignee", "value": "Ann Lee"},
            {"label": "Updated", "value": "2026-10-01"},
        ]
        assert view["url"] == "https://example.atlassian.net/browse/PA-7"
        assert all(len(field["value"]) <= 200 for field in view["fields"])
        assert not any(field["label"] == "Self" for field in view["fields"])

    def test_an_acknowledgement_says_its_message(self) -> None:
        assert describe({"ok": True, "message": "Comment added\nmore"}).summary == "Comment added"


class TestText:
    def test_text_is_its_first_line_and_needs_no_view(self) -> None:
        described = describe("# Results\n" + "line\n" * 2000)
        assert (described.summary, described.view) == ("# Results", None)
        assert describe("Done.").summary == "Done."

    @pytest.mark.parametrize("text", ['{"issues": [', "Here:\n```json\n{\"a\": 1}\n```", "[1, 2"])
    def test_broken_or_fenced_json_is_text(self, text: str) -> None:
        assert describe(text).view is None

    def test_empty(self) -> None:
        assert describe("  ").summary == "No output"


class TestSafety:
    @pytest.mark.parametrize("url", [
        "javascript:alert(1)", "data:text/html,x", "//evil.example/x", "ftp://example.com/f", "/relative/without/base",
        "https://user:pass@example.com/x", "https://user@example.com/x", "http://[::1/broken",
    ])
    def test_only_http_links(self, url: str) -> None:
        described = describe({"items": [{"title": "A", "key": "K", "url": url}]})
        assert described.view is not None and "url" not in described.view["rows"][0]

    def test_the_view_stays_small(self) -> None:
        rows = [{"key": f"K-{n}", "title": "題" * 400, "status": "s" * 400, "owner": "o" * 400, "updated": "u" * 400} for n in range(50)]
        view = describe({"items": rows}).view
        assert view is not None
        # Bytes, not characters: CJK text takes three bytes a character.
        assert view_bytes(view) <= MAX_VIEW_BYTES
        assert len(json.dumps(view, ensure_ascii=False).encode()) <= MAX_VIEW_BYTES + 200
        assert 0 < len(view["rows"]) < 20
        assert all(len(cell) <= 120 for row in view["rows"] for cell in row["cells"])

    @pytest.mark.parametrize("content", [None, 5, b"bytes", {"a": {"b": {"c": {"d": [1]}}}}, [[1, 2], [3]], object()])
    def test_odd_content_never_raises(self, content: Any) -> None:  # noqa: ANN401
        describe(content)

    def test_a_servers_long_key_doesnt_make_a_long_summary(self) -> None:
        described = describe({"k" * 100_000: [{"title": "A"}]})
        assert described.summary == "Found 1 result"
        long_total = describe({"issues": [{"title": "A"}], "total": 10 ** 300})
        assert long_total.summary is not None and len(long_total.summary) <= 200

    def test_huge_text_is_not_parsed(self) -> None:
        assert parse_embedded_json("[" + "1," * 200_000 + "1]") is None

    def test_json_after_a_note(self) -> None:
        assert parse_embedded_json('note\n{"a": 1}\n') == {"a": 1}
        assert parse_embedded_json('note\n{"a": 1}\ntrailing') is None
