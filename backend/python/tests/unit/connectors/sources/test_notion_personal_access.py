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


def _bot(email: str = ALICE, workspace: str = WS) -> MagicMock:
    return _make_api_response(data={"object": "user", "type": "bot", "bot": {
        "workspace_id": workspace, "owner": {"type": "user", "user": {"type": "person", "person": {"email": email}}}}})


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
    conn.data_entities_processor.get_user_by_user_id = AsyncMock(
        side_effect=lambda uid: User(email=ALICE, user_id=uid, org_id="org-1", is_active=True) if uid == "u-alice" else None
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
    (_bot(email="bob@example.com"), "not yours"),
    (_bot(email=""), "not yours"),
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
async def test_a_token_of_a_user_of_another_org_or_an_inactive_one_is_not_read() -> None:
    ds = _datasource(_bot(), _search("p1"))
    conn = _connector(ds)
    await _link(conn, "u-elsewhere")
    conn.data_entities_processor.get_user_by_user_id = AsyncMock(
        return_value=User(email=ALICE, user_id="u-elsewhere", org_id="org-2", is_active=True))
    await conn._load_personal_access()
    assert conn._readers == {}
    conn.data_entities_processor.get_user_by_user_id = AsyncMock(
        return_value=User(email=ALICE, user_id="u-elsewhere", org_id="org-1", is_active=False))
    await conn._load_personal_access()
    assert conn._readers == {}
    ds.retrieve_bot_user.assert_not_awaited()


def test_rejection_reasons_never_carry_the_token() -> None:
    assert "ntn_" not in _TokenRejected("Notion no longer accepts the token").reason


# ---- what each record is given ----------------------------------------------------------------

def _record(ext: str, record_type: RecordType = RecordType.WEBPAGE, parent: str | None = None,
            parent_type: RecordType | None = None) -> Record:
    rec = MagicMock(spec=Record)
    rec.external_record_id, rec.record_type = ext, record_type
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
    conn._readers = {"ds": {ALICE, "bob@example.com"}, "row1": {ALICE, "bob@example.com"}, "row2": {ALICE}}
    got = await _applied(
        conn, _record("ds", RecordType.DATASOURCE),
        _record("row1", parent="ds", parent_type=RecordType.DATASOURCE),
        _record("row2", parent="ds", parent_type=RecordType.DATASOURCE),
    )
    assert got["ds"] == [ALICE] and got["row2"] == [ALICE] and got["row1"] == [ALICE, "bob@example.com"]


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


@pytest.mark.asyncio
async def test_pages_carry_their_own_access_on_the_team_connector() -> None:
    conn = _connector()
    record = await conn._transform_to_webpage_record({
        "id": "p1", "parent": {"type": "page_id", "page_id": "p0"}, "properties": {},
    }, "page")
    assert record.inherit_permissions is False
