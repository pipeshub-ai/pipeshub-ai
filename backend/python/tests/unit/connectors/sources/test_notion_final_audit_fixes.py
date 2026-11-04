"""Notion connector: fixes from the final audit. File records kept through a full sync, page-level comment
attachment blocks, the email capability probe, lists ended by a table, a token refresh on a 401 while
streaming, the comment cache, the comment recheck slice, failure notifications, the toolset's create_page,
the API base URL, and the touched data sources kept across a restart."""

import asyncio
import json
import logging
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.error.stream_errors import map_source_status
from app.connectors.core.sync.sync_runner import (
    _begin_full_sync_run,
    _unlink_what_full_sync_missed,
)
from app.connectors.sources.notion import connector as notion_module
from app.connectors.sources.notion.block_parser import NotionBlockParser
from app.connectors.sources.notion.connector import (
    NotionConnector,
    _failure_summary,
    _origin,
)
from app.models.blocks import BlockGroup, BlockGroupChildren, BlockSubType, GroupType
from app.models.entities import RecordType
from app.sources.client.notion import notion as client_module
from app.sources.client.notion.notion import (
    DEFAULT_NOTION_API_BASE_URL,
    notion_api_base_url,
)
from tests.unit.connectors.sources.test_notion_connector import _make_api_response
from tests.unit.connectors.sources.test_notion_connector_sync_reconcile import (
    OLD,
    _connector_with,
    _page,
    _search,
    _stored,
)
from tests.unit.connectors.sources.test_notion_content_qa_fixes import (
    PAGE,
    _item,
    _parse,
    _rt,
)

# ---- 1. a full sync keeps the file records of unchanged pages linked -------------------------------------

def _real_processor(conn: NotionConnector, children: list[Any]) -> DataSourceEntitiesProcessor:
    """The processor's own full-sync bookkeeping, with its graph reads mocked."""
    proc = DataSourceEntitiesProcessor.__new__(DataSourceEntitiesProcessor)
    proc.get_record_by_external_id = AsyncMock(return_value=_stored("p1"))
    proc.get_records_by_parent = AsyncMock(return_value=children)
    proc.on_new_records = AsyncMock()
    proc.finish_full_sync_memberships = AsyncMock()
    conn.data_entities_processor = proc
    conn._transform_to_webpage_record = AsyncMock(side_effect=lambda obj, *_a, **_k: MagicMock(
        external_record_id=obj["id"], external_revision_id=OLD))
    return proc


@pytest.mark.asyncio
async def test_full_sync_keeps_the_files_of_an_unchanged_page_linked() -> None:
    conn = _connector_with(MagicMock())
    attachment, comment_file = _stored("blk-1", RecordType.FILE), _stored("ca_c1_a.pdf", RecordType.FILE)
    child_page = _stored("child", RecordType.WEBPAGE)
    proc = _real_processor(conn, [attachment, comment_file, child_page])
    _, seen = _begin_full_sync_run(conn, conn.connector_id)
    assert seen is not None

    records = await conn._records_for_search_object(_page("p1"), "page")
    assert [r.external_record_id for r, _ in records] == ["p1"]  # block walk skipped
    conn._fetch_page_attachments_and_comments.assert_not_awaited()

    # What the runner unlinks after the sync: everything stored but not in the seen set.
    graph = MagicMock()
    graph.unlink_nodes_missed_by_full_sync = AsyncMock(return_value=(0, True))
    assert await _unlink_what_full_sync_missed(conn, conn.connector_id, graph, logging.getLogger("t"), seen)
    kept = graph.unlink_nodes_missed_by_full_sync.await_args.args[1][CollectionNames.RECORDS.value]
    assert {attachment.id, comment_file.id} <= kept
    assert child_page.id not in kept  # a child page is listed and written on its own
    proc.end_full_sync_run(conn.connector_id, seen)


@pytest.mark.asyncio
async def test_incremental_sync_does_not_look_up_the_files_of_an_unchanged_page() -> None:
    conn = _connector_with(MagicMock())
    proc = _real_processor(conn, [])
    records = await conn._records_for_search_object(_page("p1"), "page")
    assert len(records) == 1
    proc.get_records_by_parent.assert_not_awaited()


@pytest.mark.asyncio
async def test_page_readable_but_not_listed_stays_linked_through_a_full_sync() -> None:
    conn = _connector_with(MagicMock())
    stored_page, its_file = _stored("p1"), _stored("blk-1", RecordType.FILE)
    proc = _real_processor(conn, [its_file])
    proc.get_records_by_record_type = AsyncMock(side_effect=lambda _c, rt: [stored_page] if rt == RecordType.WEBPAGE else [])
    conn._list_visible_objects = AsyncMock(return_value={})
    conn._fetch_placeholder_object = AsyncMock(return_value=("in_scope", _page("p1")))
    _, seen = _begin_full_sync_run(conn, conn.connector_id)

    await conn._reconcile_with_source()
    assert {stored_page.id, its_file.id} <= seen.by_collection[CollectionNames.RECORDS.value]
    proc.end_full_sync_run(conn.connector_id, seen)


# ---- 2. a page-level comment links its attachment ------------------------------------------------------

@pytest.mark.asyncio
async def test_page_level_comment_attachment_becomes_a_child_record_block() -> None:
    conn = _connector_with(MagicMock())
    conn._resolve_author_name = AsyncMock(return_value="Ana")
    fresh = MagicMock(id="fresh-uuid", external_record_id="ca_c1_report.pdf", record_name="report.pdf", weburl=None)
    conn._transform_to_comment_file_record = AsyncMock(return_value=fresh)
    conn.get_record_by_external_id = AsyncMock(return_value=MagicMock(id="stored-id", record_name="report.pdf"))
    comment = {
        "id": "c1", "discussion_id": "d1", "rich_text": _rt("see the file"),
        "created_time": OLD, "last_edited_time": OLD, "created_by": {"id": "u1"},
        "attachments": [{"category": "productivity", "file": {"url": "https://files.example/ws/f/report.pdf?s=1"}}],
    }
    blocks: list[Any] = []
    groups: list[BlockGroup] = []

    files = await conn._create_page_level_comment_groups(
        groups, blocks, [(comment, "p1")], "p1", NotionBlockParser(logger=MagicMock()), PAGE)

    assert [f.id for f in files] == ["stored-id"]
    linked = [b for b in blocks if b.sub_type == BlockSubType.CHILD_RECORD]
    assert [b.source_id for b in linked] == ["ca_c1_report.pdf"]


# ---- 3. the email capability probe ---------------------------------------------------------------------

def _users_probe(people: list[dict[str, Any]], retrieved_email: str | None = None) -> MagicMock:
    ds = MagicMock()
    ds.list_users = AsyncMock(return_value=_make_api_response(data={"results": people}))
    ds.retrieve_user = AsyncMock(return_value=_make_api_response(
        data={"id": "u1", "type": "person", "person": {"email": retrieved_email} if retrieved_email else {}}))
    ds.search = AsyncMock(return_value=_search([]))
    return ds


@pytest.mark.asyncio
async def test_probe_passes_when_any_listed_person_shows_an_email() -> None:
    conn = _connector_with(MagicMock())
    ds = _users_probe([{"id": "u1", "type": "person", "person": {}},
                       {"id": "u2", "type": "person", "person": {"email": "b@example.com"}}])
    await conn._assert_api_token_capabilities(ds)
    ds.retrieve_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_probe_refuses_when_no_listed_person_shows_an_email() -> None:
    conn = _connector_with(MagicMock())
    ds = _users_probe([{"id": "u1", "type": "person", "person": {}}, {"id": "u2", "type": "person"}])
    with pytest.raises(ConnectorInitError, match="email addresses"):
        await conn._assert_api_token_capabilities(ds)


@pytest.mark.asyncio
async def test_rejected_api_token_says_the_token_is_invalid() -> None:
    conn = _connector_with(MagicMock())
    ds = MagicMock()
    ds.list_users = AsyncMock(return_value=_make_api_response(success=False, status=401))
    ds.search = AsyncMock(return_value=_make_api_response(success=False, status=401))
    with pytest.raises(ConnectorInitError, match="invalid or was revoked"):
        await conn._assert_api_token_capabilities(ds)


# ---- 4. a table between list items ends the list --------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["numbered_list_item", "bulleted_list_item"])
async def test_table_between_list_items_makes_two_lists(kind: str) -> None:
    parser = NotionBlockParser(logger=MagicMock())
    groups = [BlockGroup(index=0, parent_index=None, type=GroupType.TABLE, children=BlockGroupChildren())]
    blocks = [
        await _parse(parser, _item("a", "A", kind), None),
        await _parse(parser, {"id": "r", "type": "paragraph", "paragraph": {"rich_text": _rt("cell")},
                              "has_children": False}, 0),
        await _parse(parser, _item("b", "B", kind), None),
    ]
    for i, b in enumerate(blocks):
        b.index = i
    groups[0].children.add_block_index(1)

    parser.post_process_blocks(blocks, groups)

    lists = [g for g in groups if g.type in (GroupType.LIST, GroupType.ORDERED_LIST)]
    assert len(lists) == 2
    assert [blocks[0].parent_index, blocks[2].parent_index] == [lists[0].index, lists[1].index]
    if kind == "numbered_list_item":
        assert [blocks[0].data, blocks[2].data] == ["1. A", "1. B"]


@pytest.mark.asyncio
async def test_list_inside_a_column_does_not_end_at_its_own_items() -> None:
    parser = NotionBlockParser(logger=MagicMock())
    groups = [BlockGroup(index=0, parent_index=None, type=GroupType.COLUMN_LIST, children=BlockGroupChildren()),
              BlockGroup(index=1, parent_index=0, type=GroupType.COLUMN, children=BlockGroupChildren())]
    blocks = [await _parse(parser, _item("a", "A"), 1), await _parse(parser, _item("b", "B"), 1)]
    for i, b in enumerate(blocks):
        b.index = i
        groups[1].children.add_block_index(i)
    parser.post_process_blocks(blocks, groups)
    assert [b.data for b in blocks] == ["1. A", "2. B"]
    assert len([g for g in groups if g.type == GroupType.ORDERED_LIST]) == 1


# ---- 5. a 401 while streaming refreshes the token once -----------------------------------------------------

def _reconnect(conn: NotionConnector) -> HTTPException:
    return map_source_status(401, connector=conn.display_name)


@pytest.mark.asyncio
async def test_stream_after_a_401_refreshes_once_and_retries() -> None:
    conn = _connector_with(MagicMock())
    conn._refresh_after_unauthorized = AsyncMock(return_value=True)
    conn._stream_record_once = AsyncMock(side_effect=[_reconnect(conn), "streamed"])
    assert await conn.stream_record(MagicMock()) == "streamed"
    conn._refresh_after_unauthorized.assert_awaited_once()
    conn._get_fresh_datasource.assert_awaited()


@pytest.mark.asyncio
async def test_signed_url_after_a_401_refreshes_once_and_a_second_401_raises() -> None:
    conn = _connector_with(MagicMock())
    conn._refresh_after_unauthorized = AsyncMock(return_value=True)
    conn._get_signed_url_once = AsyncMock(side_effect=[_reconnect(conn), _reconnect(conn)])
    with pytest.raises(HTTPException) as caught:
        await conn.get_signed_url(MagicMock())
    assert caught.value.status_code == 409
    conn._refresh_after_unauthorized.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_other_errors_and_failed_refreshes_are_not_retried() -> None:
    conn = _connector_with(MagicMock())
    conn._refresh_after_unauthorized = AsyncMock(return_value=False)
    conn._stream_record_once = AsyncMock(side_effect=[map_source_status(404, connector=conn.display_name)])
    with pytest.raises(HTTPException):
        await conn.stream_record(MagicMock())
    conn._refresh_after_unauthorized.assert_not_awaited()

    conn._stream_record_once = AsyncMock(side_effect=[_reconnect(conn), "unused"])
    with pytest.raises(HTTPException):
        await conn.stream_record(MagicMock())
    assert conn._stream_record_once.await_count == 1


# ---- 6. the comment cache ------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reindex_reads_comments_live() -> None:
    conn = _connector_with(MagicMock())
    conn._ensure_workspace_from_bot = AsyncMock()
    conn._check_and_fetch_updated_record = AsyncMock(return_value=None)
    conn._sync_comments = {"p1": ("rev", {}), "p2": ("rev", {})}
    await conn.reindex_records([_stored("p1")])
    assert set(conn._sync_comments) == {"p2"}


@pytest.mark.asyncio
async def test_unchanged_recheck_is_not_cached_and_each_sync_starts_empty() -> None:
    conn = _connector_with(MagicMock())
    conn.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=_stored("p1"))
    await conn._records_for_search_object(_page("p1"), "page", recheck_comments=True)
    assert conn._sync_comments == {}

    conn._sync_comments = {"old": ("rev", {})}
    conn._get_fresh_datasource = AsyncMock(return_value=MagicMock(search=AsyncMock(return_value=_search([]))))
    await conn._sync_objects_by_type("data_source")
    assert conn._sync_comments == {}


# ---- 7. the comment recheck slice ----------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(("stored", "per_sync"), [(30, 15), (2400, 100), (50_000, 500)])
async def test_recheck_slice_scales_with_the_workspace(stored: int, per_sync: int) -> None:
    conn = _connector_with(MagicMock())
    conn._records_for_search_object = AsyncMock(return_value=[(MagicMock(), [])])
    ids = [f"p{i:06d}" for i in range(stored)]
    await conn._recheck_page_comments({i: _page(i) for i in ids}, set(ids))
    assert conn._records_for_search_object.await_count == per_sync


@pytest.mark.asyncio
async def test_failed_recheck_is_retried_first_next_sync() -> None:
    conn = _connector_with(MagicMock())
    conn._COMMENT_RECHECK_PAGES_PER_SYNC = 2
    live = {pid: _page(pid) for pid in ("a", "b", "c", "d")}
    conn._records_for_search_object = AsyncMock(side_effect=lambda obj, *_a, **_k: [] if obj["id"] == "a" else [(MagicMock(), [])])
    await conn._recheck_page_comments(live, set(live))
    point = conn.pages_sync_point.update_sync_point.await_args.args[1]
    assert point == {"after": "b", "retry": ["a"]} and conn._sync_incomplete

    conn._records_for_search_object = AsyncMock(return_value=[(MagicMock(), [])])
    conn.pages_sync_point.read_sync_point = AsyncMock(return_value=point)
    await conn._recheck_page_comments(live, set(live))
    assert [c.args[0]["id"] for c in conn._records_for_search_object.await_args_list] == ["a", "c", "d"]
    assert conn.pages_sync_point.update_sync_point.await_args.args[1] == {"after": "d"}


# ---- 8. failure notifications de-duplicate -------------------------------------------------------------------

def test_failure_message_leaves_out_the_request_id() -> None:
    def err(request_id: str) -> Exception:
        body = json.dumps({"object": "error", "status": 401, "code": "unauthorized",
                           "message": "API token is invalid.", "request_id": request_id})
        return Exception(f"Notion API error while fetching users: HTTP 401: {body}")

    assert _failure_summary(err("r-1")) == _failure_summary(err("r-2")) == "Notion answered HTTP 401 (unauthorized)"
    assert _failure_summary(ValueError("plain")) == "plain"


@pytest.mark.asyncio
async def test_two_failures_with_different_request_ids_notify_the_same_message() -> None:
    conn = _connector_with(MagicMock())
    conn.notify = AsyncMock()
    for rid in ("r-1", "r-2"):
        await conn._notify_sync_failure(Exception(f'HTTP 503: {{"code":"service_unavailable","request_id":"{rid}"}}'))
    first, second = (c.kwargs["message"] for c in conn.notify.await_args_list)
    assert first == second and "request_id" not in first


# ---- 9. toolset create_page --------------------------------------------------------------------------------

def _ok(body: object) -> MagicMock:
    resp = MagicMock(success=True, status_code=200)
    resp.data.json.return_value = body
    return resp


def _fail(status: int, code: str) -> MagicMock:
    resp = MagicMock(success=False, status_code=status, error=f"HTTP {status}")
    resp.data.json.return_value = {"object": "error", "status": status, "code": code, "message": code}
    return resp


@pytest.fixture
def tool() -> Iterator[tuple[Any, MagicMock]]:
    from app.agents.actions.notion import notion as actions

    with patch.object(actions, "NotionDataSource") as ds_cls:
        yield actions.Notion(MagicMock()), ds_cls.return_value


def test_create_page_failure_returns_the_create_error_and_appends_nothing(tool: tuple[Any, MagicMock]) -> None:
    t, ds = tool
    ds.create_page = AsyncMock(return_value=_fail(400, "validation_error"))
    ds.append_block_children = AsyncMock()
    ok, raw = asyncio.run(t.create_page("parent", "T", content="\n".join(f"l{i}" for i in range(150))))
    assert not ok and json.loads(raw)["error_code"] == "validation_error"
    ds.append_block_children.assert_not_awaited()


def test_create_page_append_failure_reports_the_created_page(tool: tuple[Any, MagicMock]) -> None:
    t, ds = tool
    ds.create_page = AsyncMock(return_value=_ok({"object": "page", "id": "p-1", "url": "https://notion.so/p-1"}))
    ds.append_block_children = AsyncMock(return_value=_fail(429, "rate_limited"))
    ok, raw = asyncio.run(t.create_page("parent", "T", content="\n".join(f"l{i}" for i in range(150))))
    body = json.loads(raw)
    assert not ok and body["partially_written"] is True
    assert (body["page_id"], body["url"], body["error_code"]) == ("p-1", "https://notion.so/p-1", "rate_limited")


# ---- 10. NOTION_API_BASE_URL ----------------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["notion.test/v1", "ftp://notion.test/v1", "http:///v1", "http://h:bad/v1"])
def test_invalid_base_url_falls_back_to_notion(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("NOTION_API_BASE_URL", value)
    assert notion_api_base_url() == DEFAULT_NOTION_API_BASE_URL


def test_override_host_is_logged_once_without_the_path(monkeypatch: pytest.MonkeyPatch,
                                                       caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(client_module, "_reported_overrides", set())
    monkeypatch.setenv("NOTION_API_BASE_URL", "http://notion.test:8103/v1")
    with caplog.at_level(logging.WARNING, logger=client_module.__name__):
        notion_api_base_url()
        notion_api_base_url()
    lines = [r.getMessage() for r in caplog.records if r.name == client_module.__name__]
    assert lines == ["NOTION_API_BASE_URL is set: Notion API calls go to http://notion.test:8103"]


def test_origin_compares_scheme_host_and_port() -> None:
    assert _origin("http://notion.test:8103/f.png") == _origin("http://notion.test:8103/v1")
    assert _origin("https://api.notion.com/x") == _origin("https://api.notion.com:443/v1")
    assert _origin("http://notion.test:9999/f.png") != _origin("http://notion.test:8103/v1")
    assert _origin("https://notion.test:8103/f.png") != _origin("http://notion.test:8103/v1")
    assert _origin("not a url") is None


@pytest.mark.asyncio
async def test_image_on_another_port_of_the_api_host_is_guarded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTION_API_BASE_URL", "http://notion.test:8103/v1")
    conn = _connector_with(MagicMock())
    fetcher = MagicMock()
    fetcher.return_value.get = AsyncMock(return_value="guarded")
    with patch.object(notion_module, "PublicUrlFetcher", fetcher):
        assert await conn._fetch_image("http://notion.test:6379/x.png") == "guarded"
    fetcher.return_value.get.assert_awaited_once()


# ---- 12. the touched data sources survive a restart -------------------------------------------------------------

@pytest.mark.asyncio
async def test_touched_data_sources_are_saved_before_their_rows_and_loaded_next_sync() -> None:
    conn = _connector_with(MagicMock())
    order: list[str] = []
    conn.pages_sync_point.update_sync_point = AsyncMock(side_effect=lambda key, data: order.append(f"point:{data}"))
    conn.data_entities_processor.on_new_records = AsyncMock(side_effect=lambda batch: order.append("rows"))

    async def row(obj: dict[str, Any], _type: str, **_kw: object) -> list[Any]:
        conn._touched_data_sources.add("ds-1")
        return [(MagicMock(), [])]

    conn._records_for_search_object = AsyncMock(side_effect=row)
    conn._get_fresh_datasource = AsyncMock(return_value=MagicMock(search=AsyncMock(return_value=_search([_page("r1")]))))
    await conn._sync_objects_by_type("page")
    assert order[:2] == ["point:{'ids': ['ds-1']}", "rows"]

    fresh = _connector_with(MagicMock())
    fresh.pages_sync_point.read_sync_point = AsyncMock(
        side_effect=lambda key: {"ids": ["ds-1"]} if "touched" in key else None)
    fresh._get_fresh_datasource = AsyncMock(return_value=MagicMock(search=AsyncMock(return_value=_search([]))))
    await fresh._sync_objects_by_type("data_source")
    assert fresh._touched_data_sources == {"ds-1"}
