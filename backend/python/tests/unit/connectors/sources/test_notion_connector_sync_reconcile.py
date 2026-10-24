"""Notion connector: reconciliation with the source, checkpoint safety, capability checks, token refresh,
revisions and the workspace (sync side)."""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.sources.notion.connector import (
    NotionConnector,
    _DatabaseUnavailable,
)
from app.connectors.sources.notion_personal.connector import NotionPersonalConnector
from app.models.entities import Record, RecordType
from app.services.notification.types import NotificationType
from app.sources.client.notion.notion import NotionRESTClientViaOAuth
from tests.unit.connectors.sources.test_notion_connector import (
    _make_api_response,
    _make_connector,
)

OLD = "2026-01-01T10:00:00.000Z"


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:00.000Z")


def _page(page_id: str, edited: str = OLD, **extra: object) -> dict[str, Any]:
    return {"object": "page", "id": page_id, "last_edited_time": edited, "created_time": edited,
            "parent": {"type": "workspace", "workspace": True}, "url": f"https://notion.so/{page_id}",
            "properties": {"title": {"type": "title", "title": [{"plain_text": f"Page {page_id}"}]}}, **extra}


def _search(results: list[dict[str, Any]], has_more: bool = False, next_cursor: Optional[str] = None,
            **extra: object) -> MagicMock:
    return _make_api_response(success=True, data={"results": results, "has_more": has_more,
                                                  "next_cursor": next_cursor, **extra})


def _stored(ext_id: str, record_type: RecordType = RecordType.WEBPAGE, revision: str = OLD) -> Record:
    rec = MagicMock(spec=Record)
    rec.id = f"rid-{ext_id}"
    rec.external_record_id = ext_id
    rec.record_type = record_type
    rec.is_placeholder = False
    rec.external_revision_id = revision
    return rec


def _connector_with(datasource: MagicMock) -> NotionConnector:
    conn = _make_connector()
    conn._get_fresh_datasource = AsyncMock(return_value=datasource)
    conn.pages_sync_point = MagicMock()
    conn.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
    conn.pages_sync_point.update_sync_point = AsyncMock()
    proc = conn.data_entities_processor
    proc.get_records_by_parent = AsyncMock(return_value=[])
    proc.on_record_deleted = AsyncMock()
    proc.on_records_detached_from_parent = AsyncMock()
    conn._fetch_page_attachments_and_comments = AsyncMock(return_value=([], {}))
    return conn


# ---- metadata -----------------------------------------------------------------------------------

def test_manual_indexing_filter_is_declared_once() -> None:
    name = CommonFields.enable_manual_sync_filter().name
    found: list[Any] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if node.get("name") == name:
                found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(NotionConnector._connector_metadata)
    assert len(found) == 1


def test_search_and_users_page_at_the_api_maximum() -> None:
    assert NotionConnector._SYNC_PAGE_SIZE == 100


# ---- capability checks (API token) -----------------------------------------------------------------

def _probe_datasource(email: Optional[str] = "a@example.com", users_status: int = 200,
                      comments_status: int = 200) -> MagicMock:
    ds = MagicMock()
    if users_status == 403:
        ds.list_users = AsyncMock(return_value=_make_api_response(success=False, status=403))
    else:
        ds.list_users = AsyncMock(return_value=_make_api_response(data={"results": [{"id": "u1", "type": "person"}]}))
    person = {"email": email} if email else {}
    ds.retrieve_user = AsyncMock(return_value=_make_api_response(data={"id": "u1", "type": "person", "person": person}))
    ds.search = AsyncMock(return_value=_search([_page("p1")]))
    ds.retrieve_comments = AsyncMock(return_value=_make_api_response(
        success=comments_status == 200, data={"results": []} if comments_status == 200 else None,
        status=None if comments_status == 200 else comments_status))
    return ds


@pytest.mark.asyncio
async def test_api_token_without_email_capability_is_refused() -> None:
    conn = _make_connector()
    with pytest.raises(ConnectorInitError, match="email addresses"):
        await conn._assert_api_token_capabilities(_probe_datasource(email=None))
    with pytest.raises(ConnectorInitError, match="email addresses"):
        await conn._assert_api_token_capabilities(_probe_datasource(users_status=403))


@pytest.mark.asyncio
async def test_api_token_without_read_comments_is_refused() -> None:
    conn = _make_connector()
    with pytest.raises(ConnectorInitError, match="Read comments"):
        await conn._assert_api_token_capabilities(_probe_datasource(comments_status=403))


@pytest.mark.asyncio
async def test_api_token_with_capabilities_passes_and_probe_errors_pass() -> None:
    conn = _make_connector()
    await conn._assert_api_token_capabilities(_probe_datasource())
    broken = MagicMock()
    broken.list_users = AsyncMock(side_effect=RuntimeError("network"))
    await conn._assert_api_token_capabilities(broken)


# ---- users: hidden emails --------------------------------------------------------------------------

def _users_datasource(people: list[dict[str, Any]]) -> MagicMock:
    ds = MagicMock()
    ds.list_users = AsyncMock(return_value=_make_api_response(data={
        "results": [{"id": p["id"], "type": "person"} for p in people], "has_more": False, "next_cursor": None}))
    by_id = {p["id"]: p for p in people}
    ds.retrieve_user = AsyncMock(side_effect=lambda uid: _make_api_response(data=by_id[uid]))
    return ds


@pytest.mark.asyncio
async def test_users_all_without_email_fails_closed_without_removing_anyone() -> None:
    conn = _connector_with(_users_datasource([{"id": "u1", "type": "person", "person": {}},
                                              {"id": "u2", "type": "person", "person": {}}]))
    conn._remove_departed_workspace_users = AsyncMock()
    with pytest.raises(ConnectorInitError, match="email address"):
        await conn._sync_users()
    conn._remove_departed_workspace_users.assert_not_awaited()


@pytest.mark.asyncio
async def test_users_some_without_email_keep_only_their_own_access() -> None:
    conn = _connector_with(_users_datasource([
        {"id": "u1", "type": "person", "name": "A", "person": {"email": "a@example.com"}},
        {"id": "u2", "type": "person", "name": "B", "person": {}}]))
    conn.workspace_id = "ws"
    conn._add_users_to_workspace_permissions = AsyncMock()
    conn._remove_departed_workspace_users = AsyncMock()
    await conn._sync_users()
    conn._remove_departed_workspace_users.assert_awaited_once_with(["a@example.com"], ["u2"])
    assert conn._sync_incomplete is False


# ---- search walk: watermark and checkpoint ------------------------------------------------------------

@pytest.mark.asyncio
async def test_watermark_overlap_rereads_ten_minutes_before_the_checkpoint() -> None:
    checkpoint = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    ds = MagicMock()
    ds.search = AsyncMock(return_value=_search([
        _page("recent", _iso(checkpoint - timedelta(minutes=5))),
        _page("old", _iso(checkpoint - timedelta(minutes=15))),
    ]))
    conn = _connector_with(ds)
    conn.pages_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": _iso(checkpoint)})
    await conn._sync_objects_by_type("page")
    saved = [r.external_record_id for r, _ in conn.data_entities_processor.on_new_records.await_args.args[0]]
    assert saved == ["recent"]


@pytest.mark.asyncio
async def test_failed_page_is_not_saved_and_holds_the_checkpoint() -> None:
    ds = MagicMock()
    ds.search = AsyncMock(return_value=_search([_page("a", "2026-01-02T10:00:00.000Z"),
                                                _page("b", "2026-01-01T10:00:00.000Z")]))
    conn = _connector_with(ds)

    async def attachments(page_id: str, url: str = "") -> tuple[list, dict]:
        if page_id == "b":
            raise RuntimeError("HTTP 429 after retries")
        return [], {}

    conn._fetch_page_attachments_and_comments = attachments
    await conn._sync_objects_by_type("page")
    saved = [r.external_record_id for r, _ in conn.data_entities_processor.on_new_records.await_args.args[0]]
    assert saved == ["a"]
    conn.pages_sync_point.update_sync_point.assert_awaited_once()
    assert conn.pages_sync_point.update_sync_point.await_args.args[1] == {"last_sync_time": "2026-01-01T10:00:00.000Z"}
    assert conn._failed_pages == {"b"} and conn.full_sync_coverage().complete is False


@pytest.mark.parametrize("response", [
    _search([], has_more=True, next_cursor="c1"),
    _search([_page("a")], request_status={"type": "incomplete", "incomplete_reason": "query_result_limit_reached"}),
])
@pytest.mark.asyncio
async def test_incomplete_walk_does_not_move_the_checkpoint(response) -> None:
    ds = MagicMock()
    ds.search = AsyncMock(return_value=response)
    conn = _connector_with(ds)
    conn.pages_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": OLD})
    await conn._sync_objects_by_type("page")
    conn.pages_sync_point.update_sync_point.assert_not_awaited()
    assert conn.full_sync_coverage().complete is False


@pytest.mark.asyncio
async def test_cursor_that_does_not_advance_holds_the_checkpoint() -> None:
    ds = MagicMock()
    ds.search = AsyncMock(side_effect=[_search([_page("a")], has_more=True, next_cursor="c1"),
                                       _search([_page("b")], has_more=True, next_cursor="c1")])
    conn = _connector_with(ds)
    await conn._sync_objects_by_type("page")
    conn.pages_sync_point.update_sync_point.assert_not_awaited()


@pytest.mark.asyncio
async def test_unchanged_page_skips_the_block_walk_and_changed_page_prunes_removed_files() -> None:
    ds = MagicMock()
    ds.search = AsyncMock(return_value=_search([_page("same"), _page("changed", "2026-02-01T10:00:00.000Z")]))
    conn = _connector_with(ds)
    conn.data_entities_processor.get_record_by_external_id = AsyncMock(
        side_effect=lambda cid, ext: _stored(ext, revision=OLD))
    kept, gone = _stored("f-keep", RecordType.FILE), _stored("f-gone", RecordType.FILE)
    conn.data_entities_processor.get_records_by_parent = AsyncMock(return_value=[kept, gone])
    current = MagicMock()
    current.external_record_id = "f-keep"
    conn._fetch_page_attachments_and_comments = AsyncMock(return_value=([current], {}))
    await conn._sync_objects_by_type("page")
    conn._fetch_page_attachments_and_comments.assert_awaited_once()
    assert conn._fetch_page_attachments_and_comments.await_args.args[0] == "changed"
    conn.data_entities_processor.on_record_deleted.assert_awaited_once_with("rid-f-gone")


# ---- reconciliation ---------------------------------------------------------------------------------

def _reconcile_connector(listed: dict[str, list[dict[str, Any]]], stored: dict[RecordType, list[Record]]) -> NotionConnector:
    ds = MagicMock()
    ds.search = AsyncMock(side_effect=lambda request_body: _search(listed[request_body["filter"]["value"]]))
    conn = _connector_with(ds)
    conn.data_entities_processor.get_records_by_record_type = AsyncMock(
        side_effect=lambda cid, rtype: stored.get(rtype, []))
    return conn


@pytest.mark.asyncio
async def test_reconcile_deletes_confirmed_gone_with_files_and_detaches_child_pages() -> None:
    gone = _stored("gone")
    conn = _reconcile_connector({"data_source": [], "page": [_page("live")]},
                                {RecordType.WEBPAGE: [_stored("live"), gone]})
    conn._fetch_placeholder_object = AsyncMock(return_value=("gone", None))
    f, child = _stored("f1", RecordType.FILE), _stored("child")
    conn.data_entities_processor.get_records_by_parent = AsyncMock(return_value=[f, child])
    await conn._reconcile_with_source()
    conn.data_entities_processor.on_records_detached_from_parent.assert_awaited_once_with(["rid-child"])
    deleted = [c.args[0] for c in conn.data_entities_processor.on_record_deleted.await_args_list]
    assert deleted == ["rid-f1", "rid-gone"]


@pytest.mark.asyncio
async def test_reconcile_keeps_unconfirmed_and_search_lagged_records() -> None:
    conn = _reconcile_connector({"data_source": [], "page": [_page("live")]},
                                {RecordType.WEBPAGE: [_stored("live"), _stored("lag"), _stored("rate")]})
    conn._fetch_placeholder_object = AsyncMock(
        side_effect=lambda r: ("in_scope", {}) if r.external_record_id == "lag" else ("unknown", None))
    await conn._reconcile_with_source()
    conn.data_entities_processor.on_record_deleted.assert_not_awaited()
    assert conn.full_sync_coverage().complete is False


@pytest.mark.asyncio
async def test_reconcile_confirms_by_id_even_when_search_lists_nothing() -> None:
    # Everything unshared: each stored page is confirmed gone by id and removed.
    conn = _reconcile_connector({"data_source": [], "page": []}, {RecordType.WEBPAGE: [_stored("p")]})
    conn._fetch_placeholder_object = AsyncMock(return_value=("gone", None))
    await conn._reconcile_with_source()
    conn.data_entities_processor.on_record_deleted.assert_awaited_once_with("rid-p")
    # A search that came back empty by mistake: the pages still answer, so nothing goes.
    conn = _reconcile_connector({"data_source": [], "page": []}, {RecordType.WEBPAGE: [_stored("p")]})
    conn._fetch_placeholder_object = AsyncMock(return_value=("in_scope", {}))
    await conn._reconcile_with_source()
    conn.data_entities_processor.on_record_deleted.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_removes_nothing_on_an_unfinished_listing() -> None:
    conn = _reconcile_connector({"data_source": [], "page": []}, {RecordType.WEBPAGE: [_stored("p")]})
    conn._get_fresh_datasource.return_value.search = AsyncMock(
        return_value=_search([], has_more=True, next_cursor="x"))
    conn._fetch_placeholder_object = AsyncMock(return_value=("gone", None))
    await conn._reconcile_with_source()
    conn.data_entities_processor.on_record_deleted.assert_not_awaited()
    assert conn.full_sync_coverage().complete is False


@pytest.mark.asyncio
async def test_reconcile_syncs_listed_pages_that_are_not_stored_whatever_their_age() -> None:
    conn = _reconcile_connector({"data_source": [], "page": [_page("old-shared", "2020-01-01T00:00:00.000Z")]}, {})
    await conn._reconcile_with_source()
    saved = [r.external_record_id for r, _ in conn.data_entities_processor.on_new_records.await_args.args[0]]
    assert saved == ["old-shared"]


@pytest.mark.asyncio
async def test_reindex_deletes_a_page_notion_no_longer_shows() -> None:
    conn = _connector_with(MagicMock())
    conn._ensure_workspace_from_bot = AsyncMock()
    rec = _stored("p1")
    with patch.object(conn, "_check_and_fetch_updated_record", AsyncMock(
            return_value=__import__("app.connectors.sources.notion.connector", fromlist=["RECORD_GONE"]).RECORD_GONE)):
        await conn.reindex_records([rec])
    conn.data_entities_processor.on_record_deleted.assert_awaited_with("rid-p1")


# ---- run_sync: 401 refresh and notifications ------------------------------------------------------------

@pytest.mark.asyncio
async def test_401_on_oauth_refreshes_once_and_reruns() -> None:
    conn = _make_connector()
    conn._run_sync_once = AsyncMock(side_effect=[RuntimeError("Notion API error: HTTP 401: unauthorized"), None])
    conn._refresh_after_unauthorized = AsyncMock(return_value=True)
    conn.notify = AsyncMock()
    await conn.run_sync()
    assert conn._run_sync_once.await_count == 2
    conn.notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_sync_is_notified_and_raised() -> None:
    conn = _make_connector()
    conn._run_sync_once = AsyncMock(side_effect=RuntimeError("Notion API error: HTTP 401: unauthorized"))
    conn._refresh_after_unauthorized = AsyncMock(return_value=False)
    conn.notify = AsyncMock()
    with pytest.raises(RuntimeError):
        await conn.run_sync()
    assert conn.notify.await_args.kwargs["type"] == NotificationType.CONNECTOR_AUTH_ERROR

    conn._run_sync_once = AsyncMock(side_effect=RuntimeError("HTTP 500: boom"))
    with pytest.raises(RuntimeError):
        await conn.run_sync()
    assert conn.notify.await_args.kwargs["type"] == NotificationType.CONNECTOR_SYNC_ERROR


@pytest.mark.asyncio
async def test_refresh_after_unauthorized_uses_the_refresh_service() -> None:
    conn = _make_connector()
    client = MagicMock(spec=NotionRESTClientViaOAuth)
    client.access_token = "old"
    conn.notion_client = MagicMock()
    conn.notion_client.get_client.return_value = client
    conn.config_service.get_config = AsyncMock(return_value={"credentials": {"access_token": "old",
                                                                             "refresh_token": "r1"}})
    service = MagicMock()
    service.refresh_now = AsyncMock(return_value=MagicMock(access_token="new"))
    with patch("app.connectors.sources.notion.connector.startup_service") as startup:
        startup.get_token_refresh_service.return_value = service
        assert await conn._refresh_after_unauthorized() is True
    assert service.refresh_now.await_args.args[2] == "r1"
    conn.notion_client.get_client.return_value = MagicMock()  # an API token: nothing to refresh
    assert await conn._refresh_after_unauthorized() is False


# ---- revisions, parents, workspace ----------------------------------------------------------------------

def test_recent_minute_revision_is_open_and_old_one_is_plain() -> None:
    conn = _make_connector()
    now = _iso(datetime.now(timezone.utc))
    assert conn._revision_for(now) == f"{now}#open"
    assert conn._revision_for(OLD) == OLD


def test_file_records_carry_the_block_revision_and_let_the_processor_version() -> None:
    conn = _make_connector()
    record = conn._transform_to_file_record({
        "id": "b1", "type": "file", "last_edited_time": OLD, "created_time": OLD,
        "file": {"type": "file", "file": {"url": "https://files.example.com/a.pdf"}, "name": "a.pdf"}}, "p1")
    assert record.external_revision_id == OLD and record.version == 0


@pytest.mark.asyncio
async def test_block_parent_rate_limit_is_not_no_parent() -> None:
    ds = MagicMock()
    ds.retrieve_block = AsyncMock(return_value=_make_api_response(success=False, status=429, error="HTTP 429"))
    conn = _connector_with(ds)
    with pytest.raises(_DatabaseUnavailable):
        await conn._resolve_block_parent_recursive("blk")
    ds.retrieve_block = AsyncMock(return_value=_make_api_response(success=False, status=404, error="HTTP 404"))
    assert await conn._resolve_block_parent_recursive("blk") == (None, None)


@pytest.mark.asyncio
async def test_page_with_unreadable_parent_is_retried_not_rerooted() -> None:
    ds = MagicMock()
    ds.search = AsyncMock(return_value=_search([_page("p", parent={"type": "block_id", "block_id": "blk"})]))
    ds.retrieve_block = AsyncMock(return_value=_make_api_response(success=False, status=500, error="HTTP 500"))
    conn = _connector_with(ds)
    await conn._sync_objects_by_type("page")
    conn.data_entities_processor.on_new_records.assert_not_awaited()
    assert conn._failed_pages == {"p"}


@pytest.mark.asyncio
async def test_workspace_without_name_gets_a_default_and_a_swapped_token_moves_on() -> None:
    ds = MagicMock()
    ds.retrieve_bot_user = AsyncMock(return_value=_make_api_response(data={
        "type": "bot", "bot": {"workspace_id": "ws-2", "workspace_name": None}}))
    conn = _connector_with(ds)
    conn.workspace_id, conn.workspace_name = "ws-1", "Old"
    await conn._ensure_workspace_from_bot(refresh=True)
    assert conn.workspace_id == "ws-2" and conn.workspace_name == "Old"
    conn.workspace_name = None
    await conn._ensure_workspace_from_bot(refresh=True)
    assert conn.workspace_name == NotionConnector._DEFAULT_WORKSPACE_NAME


@pytest.mark.asyncio
async def test_personal_connector_runs_the_shared_finishing_passes() -> None:
    proc = MagicMock()
    proc.org_id = "org-1"
    conn = NotionPersonalConnector(MagicMock(), proc, MagicMock(), AsyncMock(), "c1", "personal", "u1")
    conn._get_fresh_datasource = AsyncMock(return_value=MagicMock())
    conn._assert_required_capabilities = AsyncMock()
    conn.creator_email = "me@example.com"
    conn.ensure_creator_user_app_relation = AsyncMock()
    conn._sync_users = AsyncMock()
    conn._sync_objects_by_type = AsyncMock()
    conn._finish_sync = AsyncMock()
    with patch("app.connectors.sources.notion_personal.connector.load_connector_filters",
               AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await conn.run_sync()
    conn._finish_sync.assert_awaited_once()
