"""Notion connector: comment changes, OAuth refresh on an inactive token, data source tables after row
changes, members without an email, fewer user and comment calls, the read-content probe, and one
unreadable database not stopping the sync."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.sources.notion.connector import (
    NotionConnector,
    _DatabaseUnavailable,
    _edit_part,
    _with_comment_digest,
)
from app.models.blocks import BlocksContainer
from app.models.entities import RecordType
from app.sources.client.notion.notion import NotionRESTClientViaOAuth
from tests.unit.connectors.sources.test_notion_connector import (
    _make_api_response,
    _make_connector,
)
from tests.unit.connectors.sources.test_notion_connector_sync_reconcile import (
    OLD,
    _connector_with,
    _page,
    _probe_datasource,
    _search,
    _stored,
)


def _comment(comment_id: str, edited: str = OLD) -> dict[str, Any]:
    return {"id": comment_id, "last_edited_time": edited, "attachments": []}


def _saved(conn: NotionConnector) -> list[Any]:
    return [r for call in conn.data_entities_processor.on_new_records.await_args_list for r, _ in call.args[0]]


# ---- 1. comments ------------------------------------------------------------------------------------

def test_comment_digest_follows_the_comments_and_keeps_the_edit_part() -> None:
    plain = _with_comment_digest(OLD, {})
    one = _with_comment_digest(OLD, {"p": [(_comment("c1"), "p")]})
    edited = _with_comment_digest(OLD, {"p": [(_comment("c1", "2026-01-02T00:00:00.000Z"), "p")]})
    two = _with_comment_digest(OLD, {"p": [(_comment("c1"), "p")], "b": [(_comment("c2"), "b")]})
    assert plain == OLD
    assert len({one, edited, two}) == 3 and all(_edit_part(r) == OLD for r in (one, edited, two))


@pytest.mark.asyncio
async def test_unchanged_page_keeps_its_comment_digest_and_skips_the_walk() -> None:
    stored_rev = f"{OLD}|comments:abc"
    conn = _connector_with(MagicMock())
    conn.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=_stored("p1", revision=stored_rev))
    records = await conn._records_for_search_object(_page("p1"), "page")
    assert records[0][0].external_revision_id == stored_rev
    conn._fetch_page_attachments_and_comments.assert_not_awaited()


@pytest.mark.asyncio
async def test_recheck_picks_up_a_new_comment_and_prunes_a_deleted_one() -> None:
    conn = _connector_with(MagicMock())
    conn.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=_stored("p1"))
    conn._fetch_page_attachments_and_comments = AsyncMock(return_value=([], {"p1": [(_comment("c1"), "p1")]}))
    conn._extract_comment_attachment_file_records = AsyncMock(return_value=[])
    records = await conn._records_for_search_object(_page("p1"), "page", recheck_comments=True)
    added = records[0][0].external_revision_id
    assert added != OLD and _edit_part(added) == OLD

    # The comment is deleted: the revision changes again and its attachment record goes.
    old_attachment = _stored("ca_c1_file", RecordType.FILE)
    conn.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=_stored("p1", revision=added))
    conn.data_entities_processor.get_records_by_parent = AsyncMock(return_value=[old_attachment])
    conn._fetch_page_attachments_and_comments = AsyncMock(return_value=([], {}))
    records = await conn._records_for_search_object(_page("p1"), "page", recheck_comments=True)
    assert records[0][0].external_revision_id == OLD
    conn.data_entities_processor.on_record_deleted.assert_awaited_once_with("rid-ca_c1_file")


@pytest.mark.asyncio
async def test_recheck_rotates_through_the_stored_pages() -> None:
    conn = _connector_with(MagicMock())
    conn._COMMENT_RECHECK_PAGES_PER_SYNC = 2
    conn._records_for_search_object = AsyncMock(return_value=[(MagicMock(), [])])
    live = {pid: _page(pid) for pid in ("a", "b", "c", "walked", "unstored")}
    conn._walked_pages = {"walked"}
    await conn._recheck_page_comments(live, {"a", "b", "c", "walked"})
    assert [c.args[0]["id"] for c in conn._records_for_search_object.await_args_list] == ["a", "b"]
    assert all(c.kwargs == {"recheck_comments": True} for c in conn._records_for_search_object.await_args_list)
    assert conn.pages_sync_point.update_sync_point.await_args.args[1] == {"after": "b"}

    conn._records_for_search_object.reset_mock()
    conn.pages_sync_point.read_sync_point = AsyncMock(return_value={"after": "b"})
    await conn._recheck_page_comments(live, {"a", "b", "c", "walked"})
    assert [c.args[0]["id"] for c in conn._records_for_search_object.await_args_list] == ["c", "a"]


# ---- 2. OAuth: an inactive access token is refreshed once ---------------------------------------------

def _oauth_connector(*payloads: dict[str, Any], refreshed: bool) -> NotionConnector:
    conn = _make_connector()
    client = MagicMock(spec=NotionRESTClientViaOAuth)
    client.access_token = "old"
    client.introspect_access_token = AsyncMock(side_effect=list(payloads))
    conn.notion_client = MagicMock()
    conn.notion_client.get_client.return_value = client
    conn._refresh_after_unauthorized = AsyncMock(return_value=refreshed)
    conn._get_fresh_datasource = AsyncMock()
    return conn


@pytest.mark.asyncio
async def test_inactive_oauth_token_is_refreshed_and_checked_again() -> None:
    active = {"active": True, "scope": "read_content read_comment"}
    conn = _oauth_connector({"active": False}, active, refreshed=True)
    await conn._assert_required_capabilities(MagicMock())
    conn._refresh_after_unauthorized.assert_awaited_once()
    assert conn.notion_client.get_client().introspect_access_token.await_count == 2


@pytest.mark.asyncio
async def test_inactive_oauth_token_needs_reauthorization_only_when_the_refresh_fails() -> None:
    conn = _oauth_connector({"active": False}, refreshed=False)
    with pytest.raises(ConnectorInitError, match="no longer active"):
        await conn._assert_required_capabilities(MagicMock())
    assert conn.notion_client.get_client().introspect_access_token.await_count == 1


# ---- 3. a data source's table record after row changes ----------------------------------------------------

def _row(row_id: str, ds_id: str, edited: str = OLD) -> dict[str, Any]:
    return _page(row_id, edited, parent={"type": "data_source_id", "data_source_id": ds_id})


def _data_source(ds_id: str, edited: str = OLD) -> dict[str, Any]:
    return {"object": "data_source", "id": ds_id, "last_edited_time": edited, "created_time": edited,
            "parent": {"type": "workspace", "workspace": True}, "title": [{"plain_text": "Table"}],
            "url": f"https://notion.so/{ds_id}"}


@pytest.mark.asyncio
async def test_changed_row_gives_its_data_source_a_new_revision() -> None:
    ds = MagicMock()
    ds.retrieve_data_source_by_id = AsyncMock(return_value=_make_api_response(data=_data_source("ds1")))
    conn = _connector_with(ds)
    conn.data_entities_processor.get_record_by_external_id = AsyncMock(
        side_effect=lambda cid, ext: _stored(ext) if ext == "ds1" else None)
    await conn._records_for_search_object(_row("r1", "ds1"), "page")
    assert conn._touched_data_sources == {"ds1"}

    await conn._refresh_touched_data_sources()
    (record,) = _saved(conn)
    assert record.external_record_id == "ds1"
    assert _edit_part(record.external_revision_id) == OLD and "|rows:" in record.external_revision_id
    assert conn._touched_data_sources == set()

    # The data source walk re-reads it unchanged: the row marker stays, so it is not indexed again.
    conn.data_entities_processor.get_record_by_external_id = AsyncMock(
        return_value=_stored("ds1", RecordType.DATASOURCE, revision=record.external_revision_id))
    again = await conn._records_for_search_object(_data_source("ds1"), "data_source")
    assert again[0][0].external_revision_id == record.external_revision_id


@pytest.mark.asyncio
async def test_data_source_saved_anew_this_sync_is_not_saved_twice_and_failures_are_kept() -> None:
    ds = MagicMock()
    ds.retrieve_data_source_by_id = AsyncMock(return_value=_make_api_response(success=False, status=503))
    conn = _connector_with(ds)
    conn.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=_stored("x"))
    conn._touched_data_sources = {"new", "busy"}
    conn._fresh_data_sources = {"new"}
    await conn._refresh_touched_data_sources()
    assert ds.retrieve_data_source_by_id.await_args_list[0].args == ("busy",)
    assert ds.retrieve_data_source_by_id.await_count == 1
    assert conn._touched_data_sources == {"busy"} and conn._sync_incomplete is True


# ---- 4. members without an email --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_member_without_email_keeps_access_while_a_departed_user_loses_it() -> None:
    conn = _make_connector()
    stored = []
    for uid, email in (("u-left", "left@example.com"), ("u-hidden", "hidden@example.com"),
                       ("u-here", "here@example.com")):
        user = MagicMock()
        user.id, user.email, user.source_user_id = f"key-{uid}", email, uid
        stored.append(user)
    tx = MagicMock()
    tx.get_app_users = AsyncMock(return_value=stored)
    tx.delete_edge = AsyncMock()

    @asynccontextmanager
    async def transaction() -> AsyncIterator[MagicMock]:
        yield tx

    conn.data_entities_processor.data_store_provider.transaction = transaction
    await conn._remove_departed_workspace_users(["here@example.com"], ["u-hidden"])
    assert [c.args[0] for c in tx.delete_edge.await_args_list] == ["key-u-left"]


# ---- 5. fewer calls -----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_listed_users_with_email_are_not_read_one_by_one() -> None:
    ds = MagicMock()
    ds.list_users = AsyncMock(return_value=_make_api_response(data={"results": [
        {"id": "u1", "type": "person", "name": "A", "person": {"email": "a@example.com"}},
        {"id": "u2", "type": "person", "name": "B", "person": {}},
    ], "has_more": False, "next_cursor": None}))
    ds.retrieve_user = AsyncMock(return_value=_make_api_response(
        data={"id": "u2", "type": "person", "name": "B", "person": {"email": "b@example.com"}}))
    conn = _connector_with(ds)
    conn._remove_departed_workspace_users = AsyncMock()
    await conn._sync_users()
    ds.retrieve_user.assert_awaited_once_with("u2")
    users = conn.data_entities_processor.on_new_app_users.await_args.args[0]
    assert sorted(u.email for u in users) == ["a@example.com", "b@example.com"]


@pytest.mark.asyncio
async def test_stream_reuses_the_comments_the_sync_read_for_that_revision() -> None:
    conn = _connector_with(MagicMock())
    conn._fetch_page_attachments_and_comments = AsyncMock(return_value=([], {"p1": [(_comment("c1"), "p1")]}))
    conn._extract_comment_attachment_file_records = AsyncMock(return_value=[])
    records = await conn._records_for_search_object(_page("p1"), "page")
    revision = records[0][0].external_revision_id
    conn._fetch_comments_for_blocks = AsyncMock(return_value=[])
    conn._attach_comments_to_blocks = AsyncMock()

    await conn._attach_stream_comments(BlocksContainer(blocks=[], block_groups=[]), "p1", None, MagicMock(),
                                       revision=revision)
    conn._fetch_comments_for_blocks.assert_not_awaited()
    assert conn._attach_comments_to_blocks.await_args.args[2] == {"p1": [(_comment("c1"), "p1")]}

    # Used once; another revision (or none cached) reads the comments again.
    await conn._attach_stream_comments(BlocksContainer(blocks=[], block_groups=[]), "p1", None, MagicMock(),
                                       revision=revision)
    conn._fetch_comments_for_blocks.assert_awaited_once()


# ---- 6. read content -------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_token_without_read_content_is_refused() -> None:
    conn = _make_connector()
    ds = _probe_datasource()
    ds.search = AsyncMock(return_value=_make_api_response(success=False, status=403))
    with pytest.raises(ConnectorInitError, match="Read content"):
        await conn._assert_api_token_capabilities(ds)


# ---- 7. one unreadable database --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unreadable_database_skips_only_its_data_source_and_holds_the_checkpoint() -> None:
    failing = {**_data_source("ds-bad", "2026-01-01T09:00:00.000Z"),
               "parent": {"type": "database_id", "database_id": "db-bad"}}
    ds = MagicMock()
    ds.search = AsyncMock(return_value=_search([_data_source("ds-ok", "2026-01-01T11:00:00.000Z"), failing]))
    conn = _connector_with(ds)
    conn._get_database_parent_ref = AsyncMock(side_effect=_DatabaseUnavailable("HTTP 503: unavailable"))
    await conn._sync_objects_by_type("data_source")
    assert [r.external_record_id for r in _saved(conn)] == ["ds-ok"]
    assert conn.pages_sync_point.update_sync_point.await_args.args[1] == {
        "last_sync_time": "2026-01-01T09:00:00.000Z"}
    assert "ds-bad" in conn._failed_pages and conn._sync_incomplete is True


@pytest.mark.asyncio
async def test_unauthorized_database_read_still_fails_the_sync() -> None:
    failing = {**_data_source("ds-bad"), "parent": {"type": "database_id", "database_id": "db-bad"}}
    conn = _connector_with(MagicMock())
    conn._get_database_parent_ref = AsyncMock(side_effect=_DatabaseUnavailable("HTTP 401: unauthorized"))
    with pytest.raises(_DatabaseUnavailable):
        await conn._records_for_search_object(failing, "data_source")
