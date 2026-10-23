"""The Notion agent toolset against Notion-Version 2025-09-03: errors, data sources, titles, long content."""

import asyncio
import json
import threading
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.notion.notion import NotionResponse


def _ok(body: object) -> MagicMock:
    resp = MagicMock()
    resp.success = True
    resp.status_code = 200
    resp.data = MagicMock()
    resp.data.json.return_value = body
    return resp


def _error(status: int, code: str, message: str) -> NotionResponse:
    data = MagicMock()
    data.json.return_value = {"object": "error", "status": status, "code": code, "message": message}
    return NotionResponse(success=False, data=data, error=f"HTTP {status}: ...", status_code=status)


@pytest.fixture
def tool() -> Iterator[tuple[Any, MagicMock]]:
    from app.agents.actions.notion import notion as actions

    with patch.object(actions, "NotionDataSource") as ds_cls:
        yield actions.Notion(MagicMock()), ds_cls.return_value


def test_no_background_thread_is_started() -> None:
    from app.agents.actions.notion import notion as actions

    with patch.object(actions, "NotionDataSource"), patch.object(threading, "Thread") as thread:
        actions.Notion(MagicMock())
    thread.assert_not_called()


def test_errors_carry_notions_message_and_status(tool: tuple[Any, MagicMock]) -> None:
    t, ds = tool
    ds.retrieve_page = AsyncMock(return_value=_error(404, "object_not_found", "Could not find page with ID: x."))
    ok, raw = asyncio.run(t.get_page("x"))
    assert not ok
    body = json.loads(raw)
    assert body["error"] == "Could not find page with ID: x." and body["error_code"] == "object_not_found"
    assert body["status_code"] == 404


def test_error_without_json_body_keeps_the_client_error(tool: tuple[Any, MagicMock]) -> None:
    t, ds = tool
    data = MagicMock()
    data.json.side_effect = ValueError("not json")
    ds.retrieve_user = AsyncMock(return_value=NotionResponse(success=False, data=data, error="HTTP 502: bad gateway",
                                                             status_code=502))
    ok, raw = asyncio.run(t.retrieve_user("u"))
    assert not ok and json.loads(raw) == {"error": "HTTP 502: bad gateway", "status_code": 502}


def test_create_row_uses_the_data_source_and_its_title_property(tool: tuple[Any, MagicMock]) -> None:
    t, ds = tool
    ds.retrieve_database = AsyncMock(return_value=_ok({"object": "database", "data_sources": [{"id": "ds-1"}]}))
    ds.retrieve_data_source_by_id = AsyncMock(return_value=_ok(
        {"properties": {"Status": {"type": "status"}, "Task": {"type": "title"}}}))
    ds.create_page = AsyncMock(return_value=_ok({"object": "page", "id": "p-1"}))
    ok, _ = asyncio.run(t.create_page("db-1", "Ship it", parent_is_database=True))
    assert ok
    body = ds.create_page.await_args.kwargs["request_body"]
    assert body["parent"] == {"data_source_id": "ds-1"}
    assert body["properties"] == {"Task": {"title": [{"text": {"content": "Ship it"}}]}}


def test_long_content_is_split_across_paragraphs_and_requests(tool: tuple[Any, MagicMock]) -> None:
    t, ds = tool
    ds.create_page = AsyncMock(return_value=_ok({"object": "page", "id": "p-1"}))
    ds.append_block_children = AsyncMock(return_value=_ok({"object": "list", "results": []}))
    content = "\n".join(["x" * 4500] + [f"line {i}" for i in range(150)])
    ok, _ = asyncio.run(t.create_page("parent", "Long", content=content))
    assert ok
    first = ds.create_page.await_args.kwargs["request_body"]["children"]
    assert len(first) == 100
    assert [len(b["paragraph"]["rich_text"][0]["text"]["content"]) for b in first[:3]] == [2000, 2000, 500]
    rest = ds.append_block_children.await_args.kwargs
    assert rest["block_id"] == "p-1" and len(rest["request_body"]["children"]) == 153 - 100


def test_update_page_finds_the_title_property(tool: tuple[Any, MagicMock]) -> None:
    t, ds = tool
    ds.retrieve_page = AsyncMock(return_value=_ok({"properties": {"Name": {"id": "title", "type": "title"}}}))
    ds.update_page_properties = AsyncMock(return_value=_ok({"object": "page"}))
    ok, _ = asyncio.run(t.update_page("p-1", title="Renamed"))
    assert ok
    assert list(ds.update_page_properties.await_args.kwargs["request_body"]["properties"]) == ["Name"]


def test_search_sends_data_source_for_database(tool: tuple[Any, MagicMock]) -> None:
    t, ds = tool
    ds.search = AsyncMock(return_value=_ok({"object": "list", "results": []}))
    asyncio.run(t.search(filter={"property": "object", "value": "database"}))
    assert ds.search.await_args.kwargs["request_body"]["filter"] == {"property": "object", "value": "data_source"}


def test_get_database_includes_the_schema(tool: tuple[Any, MagicMock]) -> None:
    t, ds = tool
    ds.retrieve_database = AsyncMock(return_value=_ok({"object": "database", "data_sources": [{"id": "ds-1"}]}))
    ds.retrieve_data_source_by_id = AsyncMock(return_value=_ok({"properties": {"Name": {"type": "title"}}}))
    ok, raw = asyncio.run(t.get_database("db-1"))
    assert ok and json.loads(raw)["data"]["properties"] == {"Name": {"type": "title"}}


def test_query_tolerates_a_database_get_without_json(tool: tuple[Any, MagicMock]) -> None:
    t, ds = tool
    bad = _ok(None)
    bad.data.json.side_effect = ValueError("not json")
    ds.retrieve_database = AsyncMock(return_value=bad)
    ds.query_data_source_by_id = AsyncMock(return_value=_ok({"object": "list", "results": []}))
    ok, _ = asyncio.run(t.query_database("ds-9"))
    assert ok and ds.query_data_source_by_id.await_args.kwargs["data_source_id"] == "ds-9"


def test_get_page_content_follows_the_cursor(tool: tuple[Any, MagicMock]) -> None:
    t, ds = tool
    para = {"id": "b1", "type": "paragraph", "has_children": False,
            "paragraph": {"rich_text": [{"plain_text": "Hello "}, {"plain_text": "world"}]}}
    child = {"id": "b2", "type": "child_page", "has_children": True, "child_page": {"title": "Sub"}}
    ds.retrieve_block_children = AsyncMock(side_effect=[
        _ok({"results": [para], "has_more": True, "next_cursor": "b2"}),
        _ok({"results": [child], "has_more": False, "next_cursor": None})])
    ok, raw = asyncio.run(t.get_page_content("p-1"))
    assert ok
    blocks = json.loads(raw)["data"]["blocks"]
    assert blocks == [{"id": "b1", "type": "paragraph", "text": "Hello world", "has_children": False},
                      {"id": "b2", "type": "child_page", "text": "Sub", "has_children": True}]
    assert ds.retrieve_block_children.await_args_list[1].kwargs["start_cursor"] == "b2"
