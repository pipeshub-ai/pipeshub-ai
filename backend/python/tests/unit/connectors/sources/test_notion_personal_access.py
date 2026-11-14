"""Notion team connector: who may open each page, from the personal access tokens users link and from
pages published to the web; never from workspace membership alone."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.notion import personal_tokens
from app.connectors.sources.notion.block_parser import NotionBlockParser
from app.connectors.sources.notion.connector import (
    NotionConnector,
    _DataSourceWithoutLinkedContent,
    _TokenRejected,
    _without_linked_content,
)
from app.connectors.sources.notion_personal.connector import NotionPersonalConnector
from app.models.blocks import (
    Block,
    BlockSubType,
    BlockType,
    ChildRecord,
    ChildType,
    DataFormat,
    TableRowMetadata,
)
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
    # Content already indexed without other pages' titles: the one-time reindex is done.
    conn.config_service.values[personal_tokens.content_state_path(conn.connector_id)] = {"done": True}
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
    assert conn._readers == {} and personal_tokens.token_path(conn.connector_id, "u-gone") not in conn.config_service.values
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
    rec.inherit_permissions, rec.is_placeholder = True, False
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


# ---- what a page's content may show of other pages --------------------------------------------

def _mention(kind: str, title: str) -> dict[str, Any]:
    return {"type": "mention", "mention": {"type": kind, kind: {"id": "x"}}, "plain_text": title, "href": "https://www.notion.so/x"}


def test_mentions_rollups_and_relation_formulas_lose_what_comes_from_other_pages() -> None:
    body = {"results": [{
        "object": "block", "type": "paragraph", "paragraph": {"rich_text": [
            {"type": "text", "plain_text": "See "}, _mention("page", "Layoffs 2027"), _mention("database", "Salaries"),
            _mention("user", "Alice"), _mention("date", "2027-01-01")]},
    }, {
        "object": "page", "properties": {
            "Project": {"type": "relation", "relation": [{"id": "r1"}]},
            "Budget": {"type": "rollup", "rollup": {"type": "number", "number": 900000}},
            "Owner names": {"type": "formula", "formula": {"type": "string", "string": "Layoffs 2027"}},
        },
    }, {
        "object": "page", "properties": {"Total": {"type": "formula", "formula": {"type": "number", "number": 3}}},
    }]}
    _without_linked_content(body)
    rich = body["results"][0]["paragraph"]["rich_text"]
    assert [r["plain_text"] for r in rich] == ["See ", "", "", "Alice", "2027-01-01"]
    assert rich[1]["mention"]["page"]["id"] == "x", "the link keeps its target"
    related = body["results"][1]["properties"]
    assert related["Budget"]["rollup"] == {"type": "unsupported"}
    assert related["Owner names"]["formula"] == {"type": "unsupported"}
    assert body["results"][2]["properties"]["Total"]["formula"]["number"] == 3, "a formula over its own row stays"


@pytest.mark.asyncio
async def test_the_team_connector_reads_notion_through_the_filter_and_the_personal_one_does_not() -> None:
    raw = MagicMock()
    raw.json.return_value = {"rich_text": [_mention("page", "Layoffs 2027")]}
    inner = MagicMock()
    inner.retrieve_block = AsyncMock(return_value=MagicMock(success=True, data=raw))
    response = await _DataSourceWithoutLinkedContent(inner).retrieve_block("b1")
    assert response.data.json()["rich_text"][0]["plain_text"] == ""
    assert response.data.json() is response.data.json(), "parsed and filtered once"

    team = _connector()
    team.notion_client = MagicMock()
    team.notion_client.get_client.return_value = MagicMock(access_token="tok")
    team.config_service = MagicMock(get_config=AsyncMock(return_value={"auth": {"authType": "API_TOKEN", "apiToken": "tok"}}))
    assert isinstance(await team._get_fresh_datasource(), _DataSourceWithoutLinkedContent)
    team._PER_PAGE_ACCESS = False
    assert not isinstance(await team._get_fresh_datasource(), _DataSourceWithoutLinkedContent)


@pytest.mark.asyncio
async def test_a_synced_copy_of_another_pages_block_shows_nothing_on_the_team_connector() -> None:
    conn = _connector()
    conn._process_blocks_recursive = AsyncMock(return_value=["child"])
    assert await conn._process_children_of("copy-1", "original-1") == []
    conn._process_blocks_recursive.assert_not_awaited()
    assert await conn._process_children_of("block-1", "block-1") == ["child"], "a block's own children stay"


@pytest.mark.asyncio
async def test_a_relation_without_a_title_shows_its_target_id() -> None:
    parser = NotionBlockParser(MagicMock())
    callback = AsyncMock(return_value=ChildRecord(child_type=ChildType.RECORD, child_id="rid", child_name=None))
    value = await parser._extract_property_value_with_resolution(
        {"type": "relation", "relation": [{"id": "page-9"}]}, get_record_child_callback=callback)
    assert value == "page-9"


@pytest.mark.asyncio
async def test_content_indexed_with_other_pages_titles_is_indexed_again_once_and_granted_after() -> None:
    conn = _connector()
    conn._readers = {"a": {ALICE}, "b": {ALICE}}
    key = personal_tokens.content_state_path(conn.connector_id)
    del conn.config_service.values[key]  # stored by an earlier version
    reindex = conn.data_entities_processor.reindex_existing_records = AsyncMock(return_value=["rid-a"])
    records = (_record("a"), _record("b"))

    async def sync() -> dict[str, list[str]]:
        by_type = {RecordType.WEBPAGE: list(records)}
        conn.data_entities_processor.get_records_by_record_type = AsyncMock(side_effect=lambda _c, t: by_type.get(t, []))
        conn._withheld = None  # as each sync starts
        await conn._reindex_without_linked_content()
        conn.data_entities_processor.on_updated_record_permissions.reset_mock()
        return await _applied(conn, *records)

    assert await sync() == {"a": [], "b": []}, "nobody while it is indexed again"
    assert [r.id for r in reindex.await_args.args[0]] == ["rid-a", "rid-b"]
    assert conn.config_service.values[key] == {"done": True, "pending": ["rid-b"]}
    assert await sync() == {"a": [ALICE], "b": []}, "b's reindex did not go out: it waits"
    reindex.return_value = ["rid-b"]
    await sync()
    reindex.reset_mock()
    assert await sync() == {"a": [ALICE], "b": [ALICE]}
    reindex.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_connector_with_nothing_stored_yet_needs_no_reindex() -> None:
    conn = _connector()
    key = personal_tokens.content_state_path(conn.connector_id)
    del conn.config_service.values[key]
    conn.data_entities_processor.reindex_existing_records = AsyncMock(return_value=[])
    await conn._reindex_without_linked_content()
    assert conn._withheld == set() and conn.config_service.values[key] == {"done": True, "pending": []}
    conn.data_entities_processor.reindex_existing_records.assert_not_awaited()


@pytest.mark.asyncio
async def test_until_the_reindex_step_has_run_nobody_is_granted() -> None:
    conn = _connector()
    conn._readers = {"a": {ALICE}}
    conn._withheld = None
    assert await _applied(conn, _record("a")) == {"a": []}
