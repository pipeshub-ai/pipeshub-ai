"""Notion team connector: who may open each page, from the personal access tokens users link and from
pages published to the web; never from workspace membership alone."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.notion import personal_tokens
from app.connectors.sources.notion.connector import NotionConnector, _TokenRejected
from app.connectors.sources.notion_personal.connector import NotionPersonalConnector
from app.models.entities import Record, RecordType, User
from app.models.permission import EntityType
from tests.unit.connectors.sources.test_notion_connector import (
    _make_api_response,
    _make_connector,
)

WS = "ws-1"
ALICE = "alice@example.com"


ALICE_ID = "notion-alice"


def _bot(owner_id: str = ALICE_ID, workspace: str = WS) -> MagicMock:
    """The token's bot user; its owner shown by id only, as Notion does without an email capability."""
    return _make_api_response(data={"object": "user", "type": "bot", "bot": {
        "workspace_id": workspace, "owner": {"type": "user", "user": {"object": "user", "id": owner_id}}}})


def _search(*ids: str, trashed: tuple[str, ...] = ()) -> MagicMock:
    return _make_api_response(data={"results": [{"id": i, "in_trash": i in trashed} for i in ids],
                                    "has_more": False, "next_cursor": None})


class _Config:
    """The configuration store, as a dict."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def set_config(self, key: str, value: object, **_: object) -> bool:
        self.values[key] = value
        return True

    async def get_config(self, key: str, default: object = None, **_: object) -> object:
        return self.values.get(key, default)

    async def delete_config(self, key: str) -> bool:
        self.values.pop(key, None)
        return True

    async def list_keys_in_directory(self, directory: str) -> list[str]:
        return [k for k in self.values if k.startswith(directory)]


def _connector(token_datasource: MagicMock | None = None) -> NotionConnector:
    conn = _make_connector()
    conn.workspace_id = WS
    conn.config_service = _Config()
    conn.notify = AsyncMock()
    conn._member_ids = {ALICE_ID: ALICE, "notion-bob": "bob@example.com"}
    conn.data_entities_processor.get_user_by_user_id = AsyncMock(
        side_effect=lambda uid, **_: User(email=ALICE, user_id=uid, org_id="org-1", is_active=True) if uid == "u-alice" else None
    )
    conn.data_entities_processor.on_updated_record_permissions = AsyncMock()
    if token_datasource is not None:
        conn._visible_with_token = lambda token, email: conn._visible_with_client(token_datasource, email)
    return conn


def _datasource(bot: MagicMock, pages: MagicMock, sources: MagicMock | None = None) -> MagicMock:
    ds = MagicMock()
    ds.retrieve_bot_user = AsyncMock(return_value=bot)
    ds.search = AsyncMock(side_effect=lambda request_body: {
        "page": pages, "data_source": sources or _search()}[request_body["filter"]["value"]])
    return ds


async def _link(conn: NotionConnector, user_id: str = "u-alice", token: str = "ntn_pat_secret") -> None:
    await personal_tokens.link_token(conn.config_service, conn.connector_id, user_id, token)


# ---- what a token proves ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_linked_token_grants_exactly_what_its_search_lists() -> None:
    conn = _connector(_datasource(_bot(), _search("p1", "p2", trashed=("p2",)), _search("ds1")))
    await _link(conn)
    await conn._load_personal_access()
    assert conn._readers == {"p1": {ALICE}, "ds1": {ALICE}}


@pytest.mark.asyncio
@pytest.mark.parametrize(("bot", "reason"), [
    (_bot(owner_id="notion-bob"), "not yours"),
    (_bot(workspace="ws-other"), "another Notion workspace"),
    (_make_api_response(success=False, status=401), "no longer accepts"),
])
async def test_a_token_that_is_not_the_users_own_or_is_refused_grants_nothing_and_is_dropped(bot: MagicMock, reason: str) -> None:
    conn = _connector(_datasource(bot, _search("p1")))
    await _link(conn)
    await conn._load_personal_access()
    assert conn._readers == {}
    entry = conn.config_service.values[personal_tokens.token_path(conn.connector_id, "u-alice")]
    assert entry["status"] == personal_tokens.FAILED and reason in entry["reason"]
    assert "token" not in entry, "the secret of a refused token must not be kept"
    assert conn.notify.await_args.kwargs["recipient_user_ids"] == ["u-alice"]


@pytest.mark.asyncio
async def test_a_search_refused_midway_drops_the_token() -> None:
    conn = _connector(_datasource(_bot(), _make_api_response(success=False, status=401)))
    await _link(conn)
    await conn._load_personal_access()
    assert conn._readers == {}
    assert conn.config_service.values[personal_tokens.token_path(conn.connector_id, "u-alice")]["status"] == personal_tokens.FAILED


@pytest.mark.asyncio
async def test_a_search_that_does_not_finish_grants_that_user_nothing_this_sync_and_keeps_the_token() -> None:
    conn = _connector(_datasource(_bot(), _make_api_response(success=False, status=503)))
    await _link(conn)
    await conn._load_personal_access()
    assert conn._readers == {} and conn._sync_incomplete is True
    assert conn.config_service.values[personal_tokens.token_path(conn.connector_id, "u-alice")]["status"] == personal_tokens.LINKED
    conn.notify.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [
    User(email=ALICE, user_id="u-gone", org_id="org-2", is_active=True),
    User(email=ALICE, user_id="u-gone", org_id="org-1", is_active=False),
    None,
])
async def test_the_token_of_a_user_who_left_the_org_or_was_deactivated_is_deleted_unread(user: User | None) -> None:
    ds = _datasource(_bot(), _search("p1"))
    conn = _connector(ds)
    await _link(conn, "u-gone")
    conn.data_entities_processor.get_user_by_user_id = AsyncMock(return_value=user)
    await conn._load_personal_access()
    assert conn._readers == {} and not conn.config_service.values
    ds.retrieve_bot_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_user_lookup_that_fails_keeps_the_token_and_grants_nothing() -> None:
    conn = _connector(_datasource(_bot(), _search("p1")))
    await _link(conn)
    conn.data_entities_processor.get_user_by_user_id = AsyncMock(side_effect=RuntimeError("store down"))
    await conn._load_personal_access()
    assert conn._readers == {} and conn._sync_incomplete is True
    assert conn.config_service.values[personal_tokens.token_path(conn.connector_id, "u-alice")]["status"] == personal_tokens.LINKED


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["no workspace known", "owner not listed"])
async def test_what_this_sync_does_not_know_keeps_the_token_and_grants_nothing(change: str) -> None:
    conn = _connector(_datasource(_bot(), _search("p1")))
    if change == "no workspace known":
        conn.workspace_id = None  # /users/me of the connector's own token failed this run
    else:
        conn._member_ids = {}  # the owner is no member /v1/users listed: removed, or the listing fell short
    await _link(conn)
    await conn._load_personal_access()
    assert conn._readers == {} and conn._sync_incomplete is True
    assert conn.config_service.values[personal_tokens.token_path(conn.connector_id, "u-alice")]["status"] == personal_tokens.LINKED
    conn.notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_token_relinked_while_the_sync_checked_the_old_one_is_kept() -> None:
    conn = _connector(_datasource(_bot(owner_id="notion-bob"), _search("p1")))
    await _link(conn, token="ntn_pat_old")
    real = conn._visible_with_token

    async def relink_then_check(token: str, email: str) -> set[str]:
        await _link(conn, token="ntn_pat_new")
        return await real(token, email)

    conn._visible_with_token = relink_then_check
    await conn._load_personal_access()
    entry = conn.config_service.values[personal_tokens.token_path(conn.connector_id, "u-alice")]
    assert entry["status"] == personal_tokens.LINKED and entry["token"] == "ntn_pat_new"
    conn.notify.assert_not_awaited()


def test_rejection_reasons_never_carry_the_token() -> None:
    assert "ntn_" not in _TokenRejected("Notion no longer accepts the token").reason


# ---- what each record is given ----------------------------------------------------------------

def _record(ext: str, record_type: RecordType = RecordType.WEBPAGE, parent: str | None = None,
            parent_type: RecordType | None = None) -> Record:
    rec = MagicMock(spec=Record)
    rec.id, rec.external_record_id, rec.record_type = f"rid-{ext}", ext, record_type
    # A record read back from the store does not keep its parent's type.
    rec.parent_external_record_id, rec.parent_record_type = parent, None
    rec.inherit_permissions = True
    return rec


async def _applied(conn: NotionConnector, *records: Record) -> dict[str, list[str]]:
    by_type: dict[Any, list[Record]] = {}
    for r in records:
        by_type.setdefault(r.record_type, []).append(r)
    conn.data_entities_processor.get_records_by_record_type = AsyncMock(side_effect=lambda _c, t: by_type.get(t, []))
    await conn._apply_page_access()
    out = {}
    for call in conn.data_entities_processor.on_updated_record_permissions.await_args_list:
        record, permissions = call.args
        assert record.inherit_permissions is False, "a page must never reach its parent's readers"
        assert all(p.entity_type == EntityType.USER for p in permissions)
        out[record.external_record_id] = [p.email for p in permissions]
    return out


@pytest.mark.asyncio
async def test_a_subpage_the_token_does_not_list_gets_no_reader_from_its_parent() -> None:
    conn = _connector()
    conn._readers = {"parent": {ALICE}}
    got = await _applied(conn, _record("parent"), _record("child", parent="parent", parent_type=RecordType.WEBPAGE))
    assert got == {"parent": [ALICE], "child": []}


@pytest.mark.asyncio
async def test_a_published_page_goes_to_every_listed_member_and_only_once_the_listing_is_read() -> None:
    conn = _connector()
    conn._member_emails = {ALICE, "bob@example.com"}
    assert await _applied(conn, _record("pub")) == {"pub": []}, "no listing read this sync: nothing is proven"
    conn._published = {"pub"}
    conn.data_entities_processor.on_updated_record_permissions.reset_mock()
    assert await _applied(conn, _record("pub"), _record("other")) == {"pub": [ALICE, "bob@example.com"], "other": []}


@pytest.mark.asyncio
async def test_a_data_source_goes_only_to_readers_of_every_row_it_holds() -> None:
    conn = _connector()
    conn._listed_rows = {}
    conn._readers = {"ds": {ALICE, "bob@example.com"}, "row1": {ALICE, "bob@example.com"}, "row2": {ALICE}}
    got = await _applied(
        conn, _record("ds", RecordType.DATASOURCE),
        _record("row1", parent="ds", parent_type=RecordType.DATASOURCE),
        _record("row2", parent="ds", parent_type=RecordType.DATASOURCE),
    )
    assert got["ds"] == [ALICE] and got["row2"] == [ALICE] and got["row1"] == [ALICE, "bob@example.com"]


@pytest.mark.asyncio
async def test_a_data_source_also_needs_every_row_the_listing_put_under_it_and_a_listing() -> None:
    """Its table indexes every row Notion returns, stored as a record or not."""
    conn = _connector()
    conn._readers = {"ds": {ALICE, "bob@example.com"}, "row1": {ALICE, "bob@example.com"}, "row2": {ALICE}}
    records = (_record("ds", RecordType.DATASOURCE), _record("row1", parent="ds"))
    assert (await _applied(conn, *records))["ds"] == [], "no full page listing this sync: nobody"
    conn._listed_rows = {"ds": {"row1", "row2"}}  # row2 is not stored: a walk cut short, or a new row
    conn.data_entities_processor.on_updated_record_permissions.reset_mock()
    conn._access_written = None
    assert (await _applied(conn, *records))["ds"] == [ALICE]


@pytest.mark.asyncio
async def test_a_failed_write_does_not_stop_the_others_and_fails_the_run() -> None:
    conn = _connector()
    conn._readers = {"a": {ALICE}, "b": {ALICE}, "c": {ALICE}}
    writes = conn.data_entities_processor.on_updated_record_permissions
    writes.side_effect = [None, RuntimeError("conflict"), None]
    got = await _applied(conn, _record("a"), _record("b"), _record("c"))
    assert set(got) == {"a", "b", "c"} and conn._access_write_failures == 1 and conn._sync_incomplete is True
    assert conn._access_written is None, "a sync with a failed write must write again next time"


@pytest.mark.asyncio
async def test_an_unchanged_sync_writes_nothing_but_a_full_sync_always_writes() -> None:
    conn = _connector()
    conn._readers = {"a": {ALICE}}
    conn.data_entities_processor.full_sync_running = MagicMock(return_value=False)
    writes = conn.data_entities_processor.on_updated_record_permissions
    await _applied(conn, _record("a"))
    await _applied(conn, _record("a"))
    assert writes.await_count == 1
    conn._readers = {"a": set()}
    await _applied(conn, _record("a"))
    assert writes.await_count == 2, "a changed reader is written"
    conn.data_entities_processor.full_sync_running = MagicMock(return_value=True)
    await _applied(conn, _record("a"))
    assert writes.await_count == 3


@pytest.mark.asyncio
async def test_access_is_rewritten_even_when_the_page_walk_fails() -> None:
    """A token revoked since the last sync must not keep its grants because a later step failed."""
    conn = _connector()
    for step in ("_assert_required_capabilities", "_register_authenticated_identity", "_ensure_workspace_from_bot",
                 "_create_workspace_record_group", "_sync_users", "_load_personal_access", "_finish_sync"):
        setattr(conn, step, AsyncMock())
    conn._get_fresh_datasource = AsyncMock()
    conn._sync_objects_by_type = AsyncMock(side_effect=RuntimeError("walk failed"))
    conn._apply_page_access = AsyncMock()
    with patch("app.connectors.sources.notion.connector.load_connector_filters", AsyncMock(return_value=(MagicMock(), MagicMock()))), \
            pytest.raises(RuntimeError, match="walk failed"):
        await conn._run_sync_once()
    conn._apply_page_access.assert_awaited_once()
    assert NotionPersonalConnector._PER_PAGE_ACCESS is False

    conn._load_personal_access = AsyncMock(side_effect=RuntimeError("store down"))
    conn._apply_page_access.reset_mock()
    with patch("app.connectors.sources.notion.connector.load_connector_filters", AsyncMock(return_value=(MagicMock(), MagicMock()))), \
            pytest.raises(RuntimeError, match="store down"):
        await conn._run_sync_once()
    conn._apply_page_access.assert_awaited_once()


@pytest.mark.asyncio
async def test_pages_carry_their_own_access_on_the_team_connector() -> None:
    conn = _connector()
    record = await conn._transform_to_webpage_record({
        "id": "p1", "parent": {"type": "page_id", "page_id": "p0"}, "properties": {},
    }, "page")
    assert record.inherit_permissions is False


def test_a_page_shows_no_title_of_a_page_or_database_it_links_to_or_holds() -> None:
    from app.models.blocks import Block, BlockSubType, BlockType, ChildRecord, ChildType, DataFormat, TableRowMetadata

    child = Block(type=BlockType.TEXT, sub_type=BlockSubType.CHILD_RECORD, format=DataFormat.TXT, data="Layoffs 2027",
                  source_name="Layoffs 2027", source_id="p-secret",
                  table_row_metadata=TableRowMetadata(children_records=[
                      ChildRecord(child_type=ChildType.RECORD, child_id="r1", child_name="Layoffs 2027")]))
    text = Block(type=BlockType.TEXT, format=DataFormat.TXT, data="Quarterly plan")
    conn = _connector()
    conn._hide_child_titles([child, text], [])
    assert (child.data, child.source_name, child.source_id) == ("", None, "p-secret")
    assert child.table_row_metadata.children_records[0].child_name is None
    assert text.data == "Quarterly plan"
