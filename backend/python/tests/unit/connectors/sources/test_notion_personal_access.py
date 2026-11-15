"""Notion team connector: who may open each page, from the personal access tokens users link, confirmed
page by page with each person's own token; never from workspace membership or publishing alone."""

import contextlib
from collections.abc import AsyncIterator
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
    BlocksContainer,
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
    conn.data_entities_processor.full_sync_running = MagicMock(return_value=False)
    # Content already indexed without other pages' titles: the one-time reindex is done.
    conn.config_service.values[personal_tokens.content_state_path(conn.connector_id)] = {"done": True}
    if token_datasource is not None:
        _TOKEN_DS.append(token_datasource)
    return conn


# The datasource a personal access token's client gets, as each test sets it.
_TOKEN_DS: list[MagicMock] = []


@pytest.fixture(autouse=True)
def _token_client(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.connectors.sources.notion.connector as module

    _TOKEN_DS.clear()
    real = module.NotionDataSource
    monkeypatch.setattr(module, "NotionTokenConfig", lambda **_: MagicMock(create_client=lambda: MagicMock(close=AsyncMock())))
    monkeypatch.setattr(module, "NotionDataSource", lambda client: _TOKEN_DS[-1] if _TOKEN_DS else real(client))


def _object(object_id: str) -> MagicMock:
    return _make_api_response(data={"object": "page", "id": object_id})


def _datasource(bot: MagicMock, pages: MagicMock, sources: MagicMock | None = None,
                opens: set[str] | None = None) -> MagicMock:
    """A token's view: what its search lists, and (``opens``, default everything) what retrieving opens."""
    ds = MagicMock()
    ds.retrieve_bot_user = AsyncMock(return_value=bot)
    ds.search = AsyncMock(side_effect=lambda request_body: {
        "page": pages, "data_source": sources or _search()}[request_body["filter"]["value"]])

    def retrieve(object_id: str, **_: object) -> MagicMock:
        if opens is None or object_id in opens:
            return _object(object_id)
        return _make_api_response(success=False, status=404)

    ds.retrieve_page = AsyncMock(side_effect=retrieve)
    ds.retrieve_data_source_by_id = AsyncMock(side_effect=retrieve)
    return ds


async def _link(conn: NotionConnector, user_id: str = "u-alice", token: str = "ntn_pat_secret") -> None:
    await personal_tokens.link_token(conn.config_service, conn.connector_id, user_id, token)


# ---- what a token proves ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_linked_token_grants_what_its_search_lists_and_retrieving_confirms() -> None:
    conn = _connector(_datasource(_bot(), _search("p1", "p2", trashed=("p2",)), _search("ds1")))
    await _link(conn)
    await conn._load_personal_access()
    assert conn._readers == {"p1": {ALICE}, "ds1": {ALICE}}


@pytest.mark.asyncio
@pytest.mark.parametrize(("bot", "reason"), [
    (_bot(owner_id="notion-bob"), "not yours"),
    (_bot(workspace="ws-other"), "another Notion workspace"),
    (_make_api_response(success=False, status=401), "no longer accepts"),
    (_make_api_response(success=False, status=403), "Notion API access"),
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
    real = conn._confirmed_with_token

    async def relink_then_check(token: str, email: str, user_id: str) -> set[str]:
        await _link(conn, token="ntn_pat_new")
        return await real(token, email, user_id)

    conn._confirmed_with_token = relink_then_check
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


async def _streamed(conn: NotionConnector, record: Record) -> str:
    conn.data_source = MagicMock()
    conn._retry_once_after_401 = lambda call: call()
    response = await conn.stream_record(record)
    return b"".join([c async for c in response.body_iterator]).decode()


def _streamable(ext: str, record_type: RecordType = RecordType.WEBPAGE) -> Record:
    rec = _record(ext, record_type)
    rec.record_name, rec.weburl, rec.external_revision_id = "Plan", None, None
    return rec


def _stored(name: str) -> MagicMock:
    rec = MagicMock(spec=Record)
    rec.id, rec.record_name, rec.is_placeholder = "rid-secret", name, False
    return rec


@pytest.mark.asyncio
async def test_a_streamed_page_shows_no_title_of_a_page_it_links_to() -> None:
    """Streaming is what indexing and preview read: the link's title is filled in after the fetch."""
    conn = _connector()
    link = Block(type=BlockType.TEXT, sub_type=BlockSubType.CHILD_RECORD, format=DataFormat.TXT, data="Page p-secret",
                 source_id="p-secret", source_type="link_to_page", name="WEBPAGE")
    conn._fetch_page_as_blocks = AsyncMock(return_value=BlocksContainer(blocks=[link], block_groups=[]))
    conn._attach_stream_comments = AsyncMock()
    conn.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=_stored("Layoffs 2027"))
    body = await _streamed(conn, _streamable("p1"))
    assert "Layoffs 2027" not in body and "rid-secret" in body, "the link keeps its target, not its title"


@pytest.mark.asyncio
async def test_a_streamed_table_shows_no_title_of_a_rows_subpage() -> None:
    conn = _connector()
    row = Block(type=BlockType.TABLE_ROW, format=DataFormat.JSON, data={}, source_id="row-1")
    conn._fetch_data_source_as_blocks = AsyncMock(return_value=BlocksContainer(blocks=[row], block_groups=[]))
    conn.data_entities_processor.get_records_by_parent = AsyncMock(return_value=[_stored("Layoffs 2027")])
    body = await _streamed(conn, _streamable("ds1", RecordType.DATASOURCE))
    assert "Layoffs 2027" not in body and "rid-secret" in body


# ---- what a page's content may show of other pages --------------------------------------------

def _mention(kind: str, title: str) -> dict[str, Any]:
    return {"type": "mention", "mention": {"type": kind, kind: {"id": "x"}}, "plain_text": title, "href": "https://www.notion.so/x"}


def test_mentions_rollups_and_relation_formulas_lose_what_comes_from_other_pages() -> None:
    body = {"results": [{
        "object": "block", "type": "paragraph", "paragraph": {"rich_text": [
            {"type": "text", "plain_text": "See "}, _mention("page", "Layoffs 2027"), _mention("database", "Salaries"),
            _mention("user", "Alice"), _mention("date", "2027-01-01"),
            {"type": "mention", "mention": {"type": "link_mention", "link_mention": {
                "href": "https://www.notion.so/acme/x", "title": "Layoffs 2027", "description": "Layoffs 2027 plan"}},
             "plain_text": "Layoffs 2027", "href": "https://www.notion.so/acme/x"},
            {"type": "mention", "mention": {"type": "some_new_kind", "some_new_kind": {"id": "y", "name": "Salaries"}},
             "plain_text": "Salaries"}]},
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
    assert [r["plain_text"] for r in rich] == ["See ", "", "", "Alice", "2027-01-01", "https://www.notion.so/acme/x", ""]
    assert rich[1]["mention"]["page"]["id"] == "x", "the link keeps its target"
    assert rich[5]["mention"]["link_mention"] == {"href": "https://www.notion.so/acme/x"}, "a pasted link keeps its URL only"
    assert rich[6]["mention"]["some_new_kind"] == {"id": "y"}, "a mention Notion does not document shows nothing"
    assert "Layoffs" not in str(rich) and "Salaries" not in str(rich)
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


class _Store:
    """The graph's record documents, as the reindex check reads them."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator[MagicMock]:
        yield MagicMock(get_record_by_key=AsyncMock(side_effect=lambda key, **_: self.docs.get(key)))


def _doc(ext: str, status: str, indexed: int = 0) -> dict[str, Any]:
    return {"id": f"rid-{ext}", "orgId": "org-1", "externalRecordId": ext, "recordName": ext, "recordType": "WEBPAGE",
            "connectorName": "NOTION", "connectorId": "notion-conn-1", "origin": "CONNECTOR", "version": 1,
            "createdAtTimestamp": 1, "updatedAtTimestamp": 1, "indexingStatus": status, "lastIndexTimestamp": indexed}


@pytest.mark.asyncio
async def test_a_record_indexed_before_is_granted_only_once_its_new_index_is_confirmed() -> None:
    conn = _connector()
    conn._readers = {"a": {ALICE}, "b": {ALICE}}
    key = personal_tokens.content_state_path(conn.connector_id)
    del conn.config_service.values[key]  # stored by an earlier version
    store = conn.data_store_provider = _Store()
    store.docs = {"rid-a": _doc("a", "COMPLETED", 1), "rid-b": _doc("b", "COMPLETED", 1)}
    reindex = conn.data_entities_processor.reindex_existing_records = AsyncMock(
        side_effect=lambda records: [r.id for r in records if r.id == "rid-a"])
    records = (_record("a"), _record("b"))
    clock = {"now": 1_000}

    async def sync() -> dict[str, list[str]]:
        conn.data_entities_processor.get_records_by_record_type = AsyncMock(
            side_effect=lambda _c, t: list(records) if t == RecordType.WEBPAGE else [])
        conn._withheld, conn._access_written = None, None  # as each sync starts; every grant written
        with patch("app.connectors.sources.notion.connector.get_epoch_timestamp_in_ms", return_value=clock["now"]):
            await conn._reindex_without_linked_content()
        conn.data_entities_processor.on_updated_record_permissions.reset_mock()
        return await _applied(conn, *records)

    assert await sync() == {"a": [], "b": []}, "nobody while it is indexed again"
    assert conn.config_service.values[key]["pending"] == {"rid-a": 1_000, "rid-b": 0}, "b's reindex did not go out"

    clock["now"] = 2_000
    store.docs["rid-a"] = _doc("a", "QUEUED", 1)
    assert await sync() == {"a": [], "b": []}, "a's new index has not finished"
    assert [r.id for r in reindex.await_args.args[0]] == ["rid-b"], "b is asked for again; a waits"

    store.docs["rid-a"] = _doc("a", "COMPLETED", 1_500)
    assert (await sync())["a"] == [ALICE], "a's index finished after it was asked for"

    store.docs["rid-a"] = _doc("a", "COMPLETED", 1_500)
    reindex.side_effect = lambda records: [r.id for r in records]
    clock["now"] = 3_000
    await sync()  # b asked for again, and goes out this time
    store.docs["rid-b"] = _doc("b", "FAILED", 3_500)
    reindex.reset_mock()
    assert (await sync())["b"] == [], "a failed reindex keeps it withheld"
    assert [r.id for r in reindex.await_args.args[0]] == ["rid-b"], "and asks again"

    clock["now"] = 3_000 + 7 * 60 * 60 * 1000
    store.docs["rid-b"] = _doc("b", "IN_PROGRESS", 3_500)
    reindex.reset_mock()
    await sync()
    assert reindex.await_count == 1, "stuck past the bound: asked again"

    store.docs["rid-b"] = _doc("b", "COMPLETED", clock["now"] + 1)
    assert await sync() == {"a": [ALICE], "b": [ALICE]}
    assert conn.config_service.values[key]["pending"] == {}


@pytest.mark.asyncio
async def test_a_connector_with_nothing_stored_yet_needs_no_reindex() -> None:
    conn = _connector()
    key = personal_tokens.content_state_path(conn.connector_id)
    del conn.config_service.values[key]
    conn.data_entities_processor.reindex_existing_records = AsyncMock(return_value=[])
    await conn._reindex_without_linked_content()
    assert conn._withheld == set() and conn.config_service.values[key] == {"done": True, "pending": {}}
    conn.data_entities_processor.reindex_existing_records.assert_not_awaited()


@pytest.mark.asyncio
async def test_until_the_reindex_step_has_run_nobody_is_granted() -> None:
    conn = _connector()
    conn._readers = {"a": {ALICE}}
    conn._withheld = None
    assert await _applied(conn, _record("a")) == {"a": []}



# ---- search proposes, retrieving decides ---------------------------------------------------------

@pytest.mark.asyncio
async def test_a_page_search_still_lists_but_the_token_cannot_open_is_not_granted() -> None:
    """Notion's search index lags a revocation; retrieving the page with the person's token does not."""
    conn = _connector(_datasource(_bot(), _search("p1", "p2"), _search("ds1"), opens={"p1"}))
    await _link(conn)
    await conn._load_personal_access()
    assert conn._readers == {"p1": {ALICE}}


@pytest.mark.asyncio
async def test_a_search_answering_403_drops_the_token() -> None:
    conn = _connector(_datasource(_bot(), _make_api_response(success=False, status=403)))
    await _link(conn)
    await conn._load_personal_access()
    entry = conn.config_service.values[personal_tokens.token_path(conn.connector_id, "u-alice")]
    assert entry["status"] == personal_tokens.FAILED and "Notion API access" in entry["reason"]


NOW = 10 * 24 * 60 * 60 * 1000
DAY = 24 * 60 * 60 * 1000


@pytest.fixture
def _now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.connectors.sources.notion.connector.get_epoch_timestamp_in_ms", lambda: NOW)


@pytest.mark.asyncio
async def test_kept_grants_are_rechecked_oldest_first_and_all_of_them_in_a_full_sync(_now: None) -> None:
    conn = _connector(_datasource(_bot(), _search("p1", "p2", "p3"), opens={"p1", "p2"}))
    conn._RECONFIRM_PER_SYNC = 1
    await _link(conn)
    path = personal_tokens.confirmed_path(conn.connector_id, "u-alice")
    # p3 was confirmed earliest and has been revoked since; search still lists it.
    conn.config_service.values[path] = {"p3": NOW - 3, "p2": NOW - 2, "p1": NOW - 1}
    await conn._load_personal_access()
    assert set(conn._readers) == {"p1", "p2"}, "the oldest kept grant is checked first"

    conn.config_service.values[path] = {"p1": NOW - 3, "p2": NOW - 2, "p3": NOW - 1}
    conn._readers = {}
    await conn._load_personal_access()
    assert "p3" in conn._readers, "a grant confirmed within the TTL is served until its re-check"

    conn._readers = {}
    conn.data_entities_processor.full_sync_running = MagicMock(return_value=True)
    await conn._load_personal_access()
    assert set(conn._readers) == {"p1", "p2"}, "a full sync re-checks every kept grant"


@pytest.mark.asyncio
async def test_a_revoked_page_search_still_lists_is_withheld_once_its_confirmation_expires(_now: None) -> None:
    ds = _datasource(_bot(), _search("p1", "p3"), opens={"p1"})
    conn = _connector(ds)
    conn._RECONFIRM_PER_SYNC = 0
    await _link(conn)
    path = personal_tokens.confirmed_path(conn.connector_id, "u-alice")
    ttl = conn._CONFIRMED_TTL_MS
    assert ttl == DAY
    conn.config_service.values[path] = {"p1": NOW - ttl, "p3": NOW - ttl - 1}
    await conn._load_personal_access()
    assert conn._readers == {"p1": {ALICE}}, "p3 was revoked and its confirmation is past the TTL"
    ds.retrieve_page.assert_not_awaited()


@pytest.mark.asyncio
async def test_grants_the_budget_cannot_recheck_expire_to_withheld_and_are_rechecked_next(_now: None) -> None:
    conn = _connector(_datasource(_bot(), _search("p1", "p2", "p3")))
    conn._RECONFIRM_PER_SYNC = 1
    await _link(conn)
    path = personal_tokens.confirmed_path(conn.connector_id, "u-alice")
    expired = NOW - conn._CONFIRMED_TTL_MS - 1
    conn.config_service.values[path] = {"p1": expired - 2, "p2": expired - 1, "p3": expired}
    await conn._load_personal_access()
    assert set(conn._readers) == {"p1"}, "only the re-confirmed grant is served; the rest wait, withheld"
    conn._readers = {}
    await conn._load_personal_access()
    assert set(conn._readers) == {"p1", "p2"}


@pytest.mark.parametrize("failure", [
    _make_api_response(success=False, status=None),  # a transport error, or a 429 or 5xx after retries
    _make_api_response(success=False, status=503),
    _make_api_response(success=False, status=429),
])
@pytest.mark.asyncio
async def test_a_check_notion_did_not_answer_withholds_the_page_this_sync_and_keeps_it_for_a_recheck(
        _now: None, failure: MagicMock) -> None:
    """Only a 404 or 403 says the person may not open it (developers.notion.com/reference/status-codes)."""
    ds = _datasource(_bot(), _search("p1", "p2"))
    conn = _connector(ds)
    await _link(conn)
    path = personal_tokens.confirmed_path(conn.connector_id, "u-alice")
    conn.config_service.values[path] = {"p1": NOW - 5}
    ds.retrieve_page = AsyncMock(return_value=failure)
    conn.data_entities_processor.full_sync_running = MagicMock(return_value=True)
    await conn._load_personal_access()
    assert conn._readers == {}, "neither is confirmed this sync"
    assert conn.config_service.values[path] == {"p1": NOW - 5}, "not recorded as denied: re-checked next sync"
    assert conn._sync_incomplete

    ds.retrieve_page = AsyncMock(side_effect=lambda object_id, **_: _object(object_id))
    conn._readers = {}
    await conn._load_personal_access()
    assert set(conn._readers) == {"p1", "p2"}


@pytest.mark.asyncio
async def test_new_pages_beyond_the_per_sync_budget_wait_for_a_later_sync() -> None:
    conn = _connector(_datasource(_bot(), _search("p1", "p2", "p3")))
    conn._CONFIRM_NEW_PER_SYNC = 2
    await _link(conn)
    await conn._load_personal_access()
    assert len(conn._readers) == 2
    conn._readers = {}
    await conn._load_personal_access()
    assert set(conn._readers) == {"p1", "p2", "p3"}


@pytest.mark.asyncio
async def test_a_grant_search_no_longer_lists_is_dropped_without_a_check(_now: None) -> None:
    ds = _datasource(_bot(), _search("p1"))
    conn = _connector(ds)
    await _link(conn)
    path = personal_tokens.confirmed_path(conn.connector_id, "u-alice")
    conn.config_service.values[path] = {"p1": NOW - 5, "gone": NOW - 5}
    conn._RECONFIRM_PER_SYNC = 0
    await conn._load_personal_access()
    assert set(conn._readers) == {"p1"} and conn.config_service.values[path] == {"p1": NOW - 5}
    ds.retrieve_page.assert_not_awaited()


@pytest.mark.asyncio
async def test_removing_or_dropping_a_token_forgets_what_it_confirmed() -> None:
    conn = _connector()
    await _link(conn)
    path = personal_tokens.confirmed_path(conn.connector_id, "u-alice")
    conn.config_service.values[path] = {"p1": 1}
    assert await personal_tokens.mark_failed(conn.config_service, conn.connector_id, "u-alice", "x", "ntn_pat_secret")
    assert path not in conn.config_service.values
    conn.config_service.values[path] = {"p1": 1}
    assert await personal_tokens.unlink_token(conn.config_service, conn.connector_id, "u-alice")
    assert path not in conn.config_service.values
    assert personal_tokens.token_path(conn.connector_id, "u-alice") not in conn.config_service.values


@pytest.mark.asyncio
async def test_only_a_member_with_a_verified_email_can_own_a_linked_token() -> None:
    conn = _make_connector()
    people = [
        {"id": "n1", "type": "person", "name": "A", "person": {"email": "a@example.com", "email_verified": True}},
        {"id": "n2", "type": "person", "name": "B", "person": {"email": "b@example.com", "email_verified": False}},
        {"id": "n3", "type": "person", "name": "C", "person": {"email": "c@example.com"}},
    ]
    ds = MagicMock()
    ds.list_users = AsyncMock(return_value=_make_api_response(data={"results": people, "has_more": False, "next_cursor": None}))
    conn._get_fresh_datasource = AsyncMock(return_value=ds)
    conn._remove_departed_workspace_users = AsyncMock()
    await conn._sync_users()
    assert conn._member_ids == {"n1": "a@example.com"}
