"""Behaviour tests for the Google Docs and Sheets agent tools.

The tools run on a real ``googleapiclient`` Resource built from the bundled Docs v1
and Sheets v4 discovery documents; only the HTTP layer is faked (``gcal_tool_fakes``).
Errors use the shape these newer APIs return: ``status`` plus ``google.rpc.ErrorInfo``
details, not the older ``errors`` list.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest
from gcal_tool_fakes import ACCESS_TOKEN, FakeGoogleHttp, GoogleResponse, result
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build

from app.agents.actions.google.docs.docs import GoogleDocs
from app.agents.actions.google.sheets.sheets import GoogleSheets

DOCS = "/v1/documents"
SHEETS = "/v4/spreadsheets"


def rpc_error(status: int, code: str, message: str, reason: str = "", headers: dict[str, str] | None = None) -> GoogleResponse:
    details = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason, "domain": "googleapis.com"}] if reason else []
    error: dict[str, Any] = {"code": status, "message": message, "status": code}
    if details:
        error["details"] = details
    return GoogleResponse(status, {"error": error}, headers or {})


SCOPE_MISSING = rpc_error(403, "PERMISSION_DENIED", "Request had insufficient authentication scopes.", "ACCESS_TOKEN_SCOPE_INSUFFICIENT")
NOT_FOUND = rpc_error(404, "NOT_FOUND", "Requested entity was not found.")
RATE_LIMITED = rpc_error(429, "RESOURCE_EXHAUSTED", "Quota exceeded for quota metric 'Write requests'.", "RATE_LIMIT_EXCEEDED", {"retry-after": "9"})


def _service(http: FakeGoogleHttp, name: str, version: str) -> Any:  # noqa: ANN401
    authed = AuthorizedHttp(Credentials(token=ACCESS_TOKEN), http=http)
    return build(name, version, http=authed, static_discovery=True, cache_discovery=False)


def assert_safe_error(data: dict[str, Any]) -> str:
    message = data["error"]
    assert ACCESS_TOKEN not in message and "googleapis.com" not in message
    return message


@pytest.fixture
def http() -> FakeGoogleHttp:
    return FakeGoogleHttp()


@pytest.fixture
def docs(http: FakeGoogleHttp) -> GoogleDocs:
    return GoogleDocs(_service(http, "docs", "v1"))


@pytest.fixture
def sheets(http: FakeGoogleHttp) -> GoogleSheets:
    return GoogleSheets(_service(http, "sheets", "v4"))


def _body(text_runs: list[str], table_rows: list[list[str]] | None = None) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"paragraph": {"elements": [{"textRun": {"content": t}} for t in text_runs]}}]
    if table_rows:
        # Google ends every paragraph, including the one in each table cell, with a newline.
        content.append({"table": {"tableRows": [{"tableCells": [
            {"content": [{"paragraph": {"elements": [{"textRun": {"content": f"{c}\n"}}]}}]} for c in row
        ]} for row in table_rows]}})
    return {"content": content}


def _tab(tab_id: str, title: str, body: dict[str, Any], children: list[dict[str, Any]] | None = None,
         parent: str | None = None) -> dict[str, Any]:
    props: dict[str, Any] = {"tabId": tab_id, "title": title, "index": 0}
    if parent:
        props["parentTabId"] = parent
    return {"tabProperties": props, "documentTab": {"body": body}, "childTabs": children or []}


def _document(tabs: list[dict[str, Any]]) -> dict[str, Any]:
    # With includeTabsContent=true Google leaves body empty and puts the text in each tab.
    return {"documentId": "doc-1", "title": "Q3 plan", "revisionId": "rev-9", "tabs": tabs}


class TestDocs:
    async def test_reads_title_and_text_including_tables(self, docs, http) -> None:
        http.on("GET", f"{DOCS}/doc-1", _document([_tab("t.0", "Plan", _body(["Goals\n", "Ship it.\n"], [["Region", "Total"], ["North", "42"]]))]), base="")

        ok, data = result(await docs.get_document("doc-1"))

        assert ok is True
        assert data["title"] == "Q3 plan"
        assert data["tabs"] == [{"tab_id": "t.0", "title": "Plan", "parent_tab_id": None, "text": "Goals\nShip it.\nRegion | Total\nNorth | 42\n"}]
        assert data["url"] == "https://docs.google.com/document/d/doc-1/edit"
        assert data["truncated"] is False
        assert http.requests[0].query["includeTabsContent"] == "true"
        assert http.requests[0].headers["authorization"] == f"Bearer {ACCESS_TOKEN}"

    async def test_reads_every_tab_including_child_tabs(self, docs, http) -> None:
        child = _tab("t.child", "Risks", _body(["Late vendor.\n"]), parent="t.1")
        http.on("GET", f"{DOCS}/doc-1", _document([
            _tab("t.0", "Plan", _body(["Goals\n"])),
            _tab("t.1", "Notes", _body(["Kickoff Monday.\n"]), children=[child]),
        ]), base="")

        ok, data = result(await docs.get_document("doc-1"))

        assert ok is True and data["truncated"] is False
        assert [(t["tab_id"], t["title"], t["parent_tab_id"], t["text"]) for t in data["tabs"]] == [
            ("t.0", "Plan", None, "Goals\n"),
            ("t.1", "Notes", None, "Kickoff Monday.\n"),
            ("t.child", "Risks", "t.1", "Late vendor.\n"),
        ]

    async def test_text_past_the_cap_is_cut_across_tabs_and_flagged(self, docs, http, monkeypatch) -> None:
        monkeypatch.setattr("app.agents.actions.google.docs.docs._MAX_DOCUMENT_TEXT_CHARS", 8)
        http.on("GET", f"{DOCS}/doc-1", _document([_tab("t.0", "A", _body(["12345"])), _tab("t.1", "B", _body(["67890"]))]), base="")

        ok, data = result(await docs.get_document("doc-1"))

        assert ok is True and data["truncated"] is True
        assert [t["text"] for t in data["tabs"]] == ["12345", "678"]

    async def test_create_with_text_creates_then_inserts_at_the_start(self, docs, http) -> None:
        http.on("POST", DOCS, {"documentId": "new-1", "title": "Notes"}, base="")
        http.on("POST", f"{DOCS}/new-1:batchUpdate", {"documentId": "new-1", "replies": [{}]}, base="")

        ok, data = result(await docs.create_document("Notes", text="Hello"))

        assert ok is True and data["document_id"] == "new-1"
        assert http.calls("POST", DOCS, base="")[0].body == {"title": "Notes"}
        assert http.calls("POST", f"{DOCS}/new-1:batchUpdate", base="")[0].body == {
            "requests": [{"insertText": {"location": {"index": 1}, "text": "Hello"}}],
        }

    async def test_text_failing_after_the_create_says_the_document_exists(self, docs, http) -> None:
        http.on("POST", DOCS, {"documentId": "new-1", "title": "Notes"}, base="")
        http.on("POST", f"{DOCS}/new-1:batchUpdate", RATE_LIMITED, base="")

        ok, data = result(await docs.create_document("Notes", text="Hello"))

        message = assert_safe_error(data)
        assert ok is False
        assert data["document_id"] == "new-1"
        assert data["url"] == "https://docs.google.com/document/d/new-1/edit"
        assert "was created" in message and "insert_text" in message and "don't create another" in message
        assert "Wait 9 seconds" in message
        assert len(http.calls("POST", DOCS, base="")) == 1

    async def test_create_without_a_title_is_refused_before_calling_google(self, docs, http) -> None:
        ok, data = result(await docs.create_document("  "))

        assert ok is False and "title" in data["error"]
        assert http.requests == []

    async def test_insert_without_an_index_appends_at_the_end(self, docs, http) -> None:
        http.on("POST", f"{DOCS}/doc-1:batchUpdate", {"replies": [{}]}, base="")

        ok, _ = result(await docs.insert_text("doc-1", "More."))

        assert ok is True
        assert http.requests[0].body == {"requests": [{"insertText": {"endOfSegmentLocation": {}, "text": "More."}}]}

    async def test_insert_into_a_named_tab(self, docs, http) -> None:
        http.on("POST", f"{DOCS}/doc-1:batchUpdate", {"replies": [{}]}, base="")

        await docs.insert_text("doc-1", "Appendix.", tab_id="t.1")
        await docs.insert_text("doc-1", "Top.", index=1, tab_id="t.1")

        assert [r.body for r in http.requests] == [
            {"requests": [{"insertText": {"endOfSegmentLocation": {"tabId": "t.1"}, "text": "Appendix."}}]},
            {"requests": [{"insertText": {"location": {"index": 1, "tabId": "t.1"}, "text": "Top."}}]},
        ]

    async def test_insert_at_an_index_below_one_is_refused(self, docs, http) -> None:
        ok, data = result(await docs.insert_text("doc-1", "x", index=0))

        assert ok is False and "1 or more" in data["error"]
        assert http.requests == []

    async def test_replace_reports_how_many_changed(self, docs, http) -> None:
        http.on("POST", f"{DOCS}/doc-1:batchUpdate", {"replies": [{"replaceAllText": {"occurrencesChanged": 3}}]}, base="")

        ok, data = result(await docs.replace_text("doc-1", "Q3", "Q4", match_case=False))

        assert ok is True and data["occurrences_changed"] == 3
        assert http.requests[0].body == {"requests": [{"replaceAllText": {
            "containsText": {"text": "Q3", "matchCase": False}, "replaceText": "Q4",
        }}]}

    async def test_replace_in_one_tab_only(self, docs, http) -> None:
        http.on("POST", f"{DOCS}/doc-1:batchUpdate", {"replies": [{"replaceAllText": {"occurrencesChanged": 1}}]}, base="")

        await docs.replace_text("doc-1", "Q3", "Q4", tab_id="t.child")

        assert http.requests[0].body["requests"][0]["replaceAllText"]["tabsCriteria"] == {"tabIds": ["t.child"]}

    async def test_replace_without_a_tab_covers_every_tab(self, docs, http) -> None:
        http.on("POST", f"{DOCS}/doc-1:batchUpdate", {"replies": [{"replaceAllText": {"occurrencesChanged": 2}}]}, base="")

        await docs.replace_text("doc-1", "Q3", "Q4")

        assert "tabsCriteria" not in http.requests[0].body["requests"][0]["replaceAllText"]

    async def test_replace_with_no_match_reports_zero(self, docs, http) -> None:
        http.on("POST", f"{DOCS}/doc-1:batchUpdate", {"replies": [{"replaceAllText": {}}]}, base="")

        ok, data = result(await docs.replace_text("doc-1", "absent", "x"))

        assert ok is True and data["occurrences_changed"] == 0

    async def test_batch_update_passes_the_requests_through(self, docs, http) -> None:
        edits = [{"deleteContentRange": {"range": {"startIndex": 1, "endIndex": 5}}}]
        http.on("POST", f"{DOCS}/doc-1:batchUpdate", {"replies": [{}]}, base="")

        ok, data = result(await docs.batch_update_document("doc-1", edits))

        assert ok is True and data["applied_requests"] == 1
        assert http.requests[0].body == {"requests": edits}

    async def test_batch_update_with_no_requests_is_refused(self, docs, http) -> None:
        ok, _ = result(await docs.batch_update_document("doc-1", []))

        assert ok is False and http.requests == []

    async def test_missing_scope_asks_to_reconnect_docs(self, docs, http) -> None:
        http.on("GET", f"{DOCS}/doc-1", SCOPE_MISSING, base="")

        ok, data = result(await docs.get_document("doc-1"))

        message = assert_safe_error(data)
        assert ok is False
        assert "Reconnect Docs under Your actions" in message and "Google Docs access" in message
        assert "Workspace > Actions" in message

    async def test_unknown_document_says_how_to_find_the_id(self, docs, http) -> None:
        http.on("GET", f"{DOCS}/nope", NOT_FOUND, base="")

        ok, data = result(await docs.get_document("nope"))

        assert ok is False and "/document/d/" in assert_safe_error(data)

    async def test_rate_limit_says_how_long_to_wait(self, docs, http) -> None:
        http.on("POST", f"{DOCS}/doc-1:batchUpdate", RATE_LIMITED, base="")

        ok, data = result(await docs.insert_text("doc-1", "x"))

        assert ok is False and "Wait 9 seconds" in assert_safe_error(data)


class TestSheets:
    async def test_create_sends_the_title_and_tab_names(self, sheets, http) -> None:
        http.on("POST", SHEETS, {
            "spreadsheetId": "s-1", "spreadsheetUrl": "https://docs.google.com/spreadsheets/d/s-1/edit",
            "properties": {"title": "Budget"},
            "sheets": [{"properties": {"title": "2026"}}, {"properties": {"title": "2027"}}],
        }, base="")

        ok, data = result(await sheets.create_spreadsheet("Budget", sheet_titles=["2026", "2027"]))

        assert ok is True and data["sheets"] == ["2026", "2027"]
        assert http.requests[0].body == {
            "properties": {"title": "Budget"},
            "sheets": [{"properties": {"title": "2026"}}, {"properties": {"title": "2027"}}],
        }

    async def test_get_spreadsheet_lists_tabs_without_cell_data(self, sheets, http) -> None:
        http.on("GET", f"{SHEETS}/s-1", {
            "spreadsheetId": "s-1", "properties": {"title": "Budget"},
            "sheets": [{"properties": {"title": "2026", "sheetId": 0, "gridProperties": {"rowCount": 1000, "columnCount": 26}}}],
        }, base="")

        ok, data = result(await sheets.get_spreadsheet("s-1"))

        assert ok is True
        assert data["sheets"] == [{"title": "2026", "sheet_id": 0, "rows": 1000, "columns": 26}]
        assert "sheets.properties" in http.requests[0].query["fields"]

    async def test_get_values_reads_a_range(self, sheets, http) -> None:
        http.on("GET", f"{SHEETS}/s-1/values/Sheet1!A1:B2", {"range": "Sheet1!A1:B2", "values": [["a", "b"], ["1", "2"]]}, base="")

        ok, data = result(await sheets.get_values("s-1", "Sheet1!A1:B2"))

        assert ok is True and data["values"] == [["a", "b"], ["1", "2"]] and data["truncated"] is False

    async def test_batch_get_reads_each_range(self, sheets, http) -> None:
        http.on("GET", f"{SHEETS}/s-1/values:batchGet", {"valueRanges": [
            {"range": "A!A1", "values": [["x"]]}, {"range": "B!A1", "values": [["y"]]},
        ]}, base="")

        ok, data = result(await sheets.batch_get_values("s-1", ["A!A1", "B!A1"]))

        assert ok is True and [r["values"] for r in data["ranges"]] == [[["x"]], [["y"]]]
        assert "ranges" in http.requests[0].query

    async def test_batch_reads_share_one_cell_budget(self, sheets, http, monkeypatch) -> None:
        monkeypatch.setattr("app.agents.actions.google.sheets.sheets._MAX_CELLS_RETURNED", 5)
        http.on("GET", f"{SHEETS}/s-1/values:batchGet", {"valueRanges": [
            {"range": "A!A1:B2", "values": [["1", "2"], ["3", "4"]]},
            {"range": "B!A1:B2", "values": [["5", "6"], ["7", "8"]]},
            {"range": "C!A1", "values": [["9"]]},
        ]}, base="")

        ok, data = result(await sheets.batch_get_values("s-1", ["A!A1:B2", "B!A1:B2", "C!A1"]))

        assert ok is True
        assert [(r["range"], r["values"], r["truncated"]) for r in data["ranges"]] == [
            ("A!A1:B2", [["1", "2"], ["3", "4"]], False),
            ("B!A1:B2", [], True),
            ("C!A1", [["9"]], False),
        ]

    async def test_update_writes_rows_as_typed_by_default(self, sheets, http) -> None:
        http.on("PUT", f"{SHEETS}/s-1/values/Sheet1!A1", {"updatedRange": "Sheet1!A1:B2", "updatedRows": 2, "updatedCells": 4}, base="")

        ok, data = result(await sheets.update_values("s-1", "Sheet1!A1", [["Name", "Total"], ["North", 42]]))

        assert ok is True and data["updated_cells"] == 4
        assert http.requests[0].query["valueInputOption"] == "USER_ENTERED"
        assert http.requests[0].body == {"values": [["Name", "Total"], ["North", 42]]}

    async def test_a_flat_row_is_written_as_one_row(self, sheets, http) -> None:
        http.on("PUT", f"{SHEETS}/s-1/values/Sheet1!A1", {"updatedCells": 2}, base="")

        ok, _ = result(await sheets.update_values("s-1", "Sheet1!A1", ["North", 42]))

        assert ok is True and http.requests[0].body == {"values": [["North", 42]]}

    async def test_mixed_rows_are_refused(self, sheets, http) -> None:
        ok, data = result(await sheets.update_values("s-1", "Sheet1!A1", [["North"], "South"]))

        assert ok is False and "list of cells" in data["error"]
        assert http.requests == []

    async def test_append_inserts_new_rows(self, sheets, http) -> None:
        http.on("POST", f"{SHEETS}/s-1/values/Log:append", {"updates": {"updatedRange": "Log!A5:C5", "updatedRows": 1, "updatedCells": 3}}, base="")

        ok, data = result(await sheets.append_values("s-1", "Log", [["2026-10-15", "North", 42]], value_input_option="RAW"))

        assert ok is True and data["appended_rows"] == 1
        assert http.requests[0].query["insertDataOption"] == "INSERT_ROWS"
        assert http.requests[0].query["valueInputOption"] == "RAW"

    async def test_clear_reports_the_cleared_range(self, sheets, http) -> None:
        http.on("POST", f"{SHEETS}/s-1/values/Sheet1!A2:D50:clear", {"clearedRange": "Sheet1!A2:D50"}, base="")

        ok, data = result(await sheets.clear_values("s-1", "Sheet1!A2:D50"))

        assert ok is True and data["cleared_range"] == "Sheet1!A2:D50"

    async def test_clear_without_a_range_is_refused(self, sheets, http) -> None:
        ok, _ = result(await sheets.clear_values("s-1", " "))

        assert ok is False and http.requests == []

    async def test_missing_scope_asks_to_reconnect_sheets(self, sheets, http) -> None:
        http.on("GET", f"{SHEETS}/s-1/values/A1", SCOPE_MISSING, base="")

        ok, data = result(await sheets.get_values("s-1", "A1"))

        message = assert_safe_error(data)
        assert ok is False and "Reconnect Sheets under Your actions" in message and "Google Sheets access" in message

    async def test_unknown_spreadsheet_says_how_to_find_the_id(self, sheets, http) -> None:
        http.on("GET", f"{SHEETS}/nope", NOT_FOUND, base="")

        ok, data = result(await sheets.get_spreadsheet("nope"))

        assert ok is False and "/spreadsheets/d/" in assert_safe_error(data)

    async def test_rate_limit_says_how_long_to_wait(self, sheets, http) -> None:
        http.on("POST", f"{SHEETS}/s-1/values/Log:append", RATE_LIMITED, base="")

        ok, data = result(await sheets.append_values("s-1", "Log", [["x"]]))

        assert ok is False and "Wait 9 seconds" in assert_safe_error(data)

    async def test_large_reads_are_cut_and_flagged(self, sheets, http, monkeypatch) -> None:
        monkeypatch.setattr("app.agents.actions.google.sheets.sheets._MAX_CELLS_RETURNED", 4)
        http.on("GET", f"{SHEETS}/s-1/values/A:B", {"range": "A:B", "values": [["1", "2"], ["3", "4"], ["5", "6"]]}, base="")

        ok, data = result(await sheets.get_values("s-1", "A:B"))

        assert ok is True and data["values"] == [["1", "2"], ["3", "4"]] and data["truncated"] is True


class _Request:
    """A googleapiclient request whose execute() records overlap, like a shared httplib2.Http would suffer."""

    def __init__(self, gauge: dict[str, int], lock: threading.Lock) -> None:
        self.gauge, self.lock = gauge, lock

    def execute(self) -> dict[str, Any]:
        with self.lock:
            self.gauge["now"] += 1
            self.gauge["peak"] = max(self.gauge["peak"], self.gauge["now"])
        time.sleep(0.05)
        with self.lock:
            self.gauge["now"] -= 1
        return {}


@pytest.mark.parametrize("make_tool", [lambda: GoogleDocs(object()), lambda: GoogleSheets(object())])
async def test_overlapping_calls_never_run_at_the_same_time(make_tool) -> None:
    tool = make_tool()
    gauge, lock = {"now": 0, "peak": 0}, threading.Lock()

    await asyncio.gather(*(tool._execute(_Request(gauge, lock)) for _ in range(4)))

    assert gauge["peak"] == 1
