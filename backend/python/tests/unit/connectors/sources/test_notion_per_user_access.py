"""Per-user access for the team Notion connector.

The rules under test: a page reaches a user only through their own credential's listing, never by
inheritance; one user's 404 never deletes for everyone; a refused or stale credential takes the
user's access away; nothing a failed or partial read saw takes anyone else's access away.
"""

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import PermissionModel
from app.connectors.core.base.user_access import (
    ACCESS_MODE_PER_USER,
    STALE_AFTER_MS,
    STATUS_REVOKED,
    UserConnection,
    access_mode,
    get_connection,
    list_connections,
)
from app.connectors.sources.notion.connector import NotionConnector
from app.connectors.sources.notion.per_user import (
    NotionAccessError,
    NotionIdentity,
    acting_as,
    active_connection,
    check_identity,
    parse_identity,
    with_user_grant,
)
from app.models.entities import RecordGroup, RecordType
from app.models.permission import EntityType, PermissionType

_MOD = "app.connectors.sources.notion.connector"


@pytest.fixture(autouse=True)
def _no_connect_requests() -> Iterator[None]:
    # Revokes ask the user to reconnect (tested in test_notion_connect_invites); no mail or notice here.
    with patch(f"{_MOD}.send_connect_request", new=AsyncMock(return_value=(True, True))),             patch.dict("app.connectors.sources.notion.per_user._REFRESH_LOCKS", clear=True):
        # Each test runs on its own event loop; a lock another test used belongs to that loop.
        yield


def _connection(user_id: str = "u1", email: str = "ann@acme.com", token: str = "tok-1", **kw) -> UserConnection:
    kw.setdefault("connected_at", 1)
    return UserConnection(user_id=user_id, email=email, token=token, source_user_id=f"n-{user_id}",
                          source_email=email, workspace_id=kw.pop("workspace_id", "ws-1"), **kw)


def _record(external_id: str, record_id: str | None = None, record_type=RecordType.WEBPAGE, **kw) -> SimpleNamespace:
    return SimpleNamespace(
        id=record_id or f"r-{external_id}", external_record_id=external_id, record_type=record_type,
        is_placeholder=kw.get("is_placeholder", False), inherit_permissions=True,
        parent_external_record_id=kw.get("parent"), record_group_id=kw.get("group"),
    )


def _connector(scope: str = "team") -> NotionConnector:
    dep = MagicMock()
    dep.org_id = "org-1"
    for name in (
        "get_user_granted_records", "get_records_by_record_type", "add_permission_to_record", "remove_user_grants",
        "withdraw_user_from_connector", "on_new_app_users", "get_all_app_users", "withdraw_app_user_access",
        "get_user_by_user_id", "reindex_existing_records", "on_new_records", "on_record_deleted",
        "get_users_with_permission_to_node", "get_record_by_external_id",
    ):
        setattr(dep, name, AsyncMock())
    dep.get_all_app_users.return_value = []
    connector = NotionConnector(
        logger=MagicMock(), data_entities_processor=dep, data_store_provider=MagicMock(),
        config_service=AsyncMock(), connector_id="notion-1", scope=scope, created_by="admin",
    )
    connector.notion_client = MagicMock()
    connector.workspace_id = "ws-1"
    return connector


# ---------------------------------------------------------------- who a token belongs to

class TestParseIdentity:
    def test_personal_access_token_is_a_person(self) -> None:
        identity = parse_identity({"type": "person", "id": "p1", "person": {"email": " Ann@Acme.com "}})
        assert identity == NotionIdentity("p1", "Ann@Acme.com", None)

    def test_oauth_token_is_the_owner_of_its_bot(self) -> None:
        identity = parse_identity({"type": "bot", "id": "bot", "bot": {
            "workspace_id": "ws-1", "owner": {"type": "user", "user": {"id": "p1", "person": {"email": "a@x.io"}}}}})
        assert identity == NotionIdentity("p1", "a@x.io", "ws-1")

    def test_integration_secret_is_refused(self) -> None:
        with pytest.raises(NotionAccessError):
            parse_identity({"type": "bot", "id": "bot", "bot": {"workspace_id": "ws-1", "owner": {"type": "workspace"}}})

    def test_unknown_shape_is_refused(self) -> None:
        with pytest.raises(NotionAccessError):
            parse_identity({"object": "error"})


class TestCheckIdentity:
    def test_same_person_and_workspace_passes(self) -> None:
        check_identity(NotionIdentity("p1", "ANN@acme.com", "ws-1"), pipeshub_email="ann@ACME.com",
                       connector_workspace_id="ws-1")

    def test_other_workspace_is_refused(self) -> None:
        with pytest.raises(NotionAccessError, match="different Notion workspace"):
            check_identity(NotionIdentity("p1", "ann@acme.com", "ws-2"), pipeshub_email="ann@acme.com",
                           connector_workspace_id="ws-1")

    def test_another_persons_account_is_refused(self) -> None:
        with pytest.raises(NotionAccessError, match="bob@acme.com"):
            check_identity(NotionIdentity("p1", "bob@acme.com", "ws-1"), pipeshub_email="ann@acme.com",
                           connector_workspace_id="ws-1")

    def test_hidden_email_is_refused(self) -> None:
        with pytest.raises(NotionAccessError, match="email"):
            check_identity(NotionIdentity("p1", None, None), pipeshub_email="ann@acme.com", connector_workspace_id="ws-1")


# ---------------------------------------------------------------- the grant a pass writes

class TestWithUserGrant:
    def test_page_gets_only_the_users_grant_and_stops_inheriting(self) -> None:
        page = _record("p1")
        record, permissions = with_user_grant(page, [MagicMock()], _connection())
        assert record.inherit_permissions is False
        # One user's view: added to other users' grants, never replacing them (not even on a full sync).
        assert record.adds_permissions is True
        assert [(p.email, p.entity_type, p.type) for p in permissions] == [
            ("ann@acme.com", EntityType.USER, PermissionType.READ)]

    def test_data_source_is_granted_too(self) -> None:
        _, permissions = with_user_grant(_record("d1", record_type=RecordType.DATASOURCE), [], _connection())
        assert len(permissions) == 1

    def test_file_keeps_inheriting_from_its_page(self) -> None:
        file = _record("f1", record_type=RecordType.FILE)
        record, permissions = with_user_grant(file, [], _connection())
        assert record.inherit_permissions is True and permissions == []

    def test_placeholder_is_left_alone(self) -> None:
        stub = _record("p1", is_placeholder=True)
        record, permissions = with_user_grant(stub, [], _connection())
        assert record.inherit_permissions is True and permissions == []


# ---------------------------------------------------------------- the connection store

class TestUserAccessStore:
    @pytest.mark.parametrize("config, expected", [
        (None, "workspace"),
        ({"sync": {}}, "workspace"),
        ({"sync": {"accessMode": "per_user"}}, "per_user"),
        ({"sync": {"customValues": {"accessMode": "per_user"}}}, "per_user"),
    ])
    def test_access_mode(self, config, expected) -> None:
        assert access_mode(config) == expected

    def test_public_view_never_carries_the_token(self) -> None:
        public = _connection().public()
        assert "token" not in public and public["email"] == "ann@acme.com"

    def test_round_trip(self) -> None:
        connection = _connection(last_complete_listing_at=5)
        assert UserConnection.from_dict(connection.to_dict()) == connection

    def test_stale_after_the_limit(self) -> None:
        connection = _connection(last_complete_listing_at=1_000)
        assert not connection.stale(1_000 + STALE_AFTER_MS)
        assert connection.stale(1_001 + STALE_AFTER_MS)

    async def test_failed_listing_raises_rather_than_reading_as_nobody(self) -> None:
        config = AsyncMock()
        config.list_keys_in_directory.side_effect = RuntimeError("etcd down")
        with pytest.raises(RuntimeError):
            await list_connections(config, "c1")

    async def test_missing_connection_is_none(self) -> None:
        config = AsyncMock()
        config.get_config.return_value = None
        assert await get_connection(config, "c1", "u1") is None


# ---------------------------------------------------------------- which token a call uses

class TestTokenIsolation:
    async def test_a_pass_reads_with_its_users_own_client(self) -> None:
        connector = _connector()
        with acting_as(_connection(token="tok-ann")):
            datasource = await connector._get_fresh_datasource()
        assert datasource.http_client.headers["Authorization"] == "Bearer tok-ann"
        assert connector._user_clients["tok-ann"].get_client() is datasource.http_client
        await connector._close_user_clients()

    async def test_a_concurrent_task_never_sees_another_tasks_credential(self) -> None:
        seen: dict[str, object] = {}
        started = asyncio.Event()
        release = asyncio.Event()

        async def in_pass() -> None:
            with acting_as(_connection(token="tok-ann")):
                started.set()
                await release.wait()
                seen["pass"] = active_connection()

        async def beside_it() -> None:
            await started.wait()
            seen["other"] = active_connection()
            release.set()

        await asyncio.gather(in_pass(), beside_it())
        assert seen["pass"].token == "tok-ann"
        assert seen["other"] is None


# ---------------------------------------------------------------- mode

class TestAccessMode:
    async def test_personal_connector_is_never_per_user(self) -> None:
        connector = _connector(scope="personal")
        assert await connector._load_access_mode() is False
        connector.config_service.get_config.assert_not_awaited()

    async def test_team_connector_reads_its_config(self) -> None:
        connector = _connector()
        connector.config_service.get_config.return_value = {"sync": {"accessMode": ACCESS_MODE_PER_USER}}
        assert await connector._load_access_mode() is True
        assert connector._per_user is True


# ---------------------------------------------------------------- records written in a pass

class TestSavingInAPass:
    async def test_pages_saved_in_a_pass_carry_the_users_grant(self) -> None:
        connector = _connector()
        connector.data_entities_processor.get_record_by_external_id.return_value = None
        page, file = _record("p1"), _record("f1", record_type=RecordType.FILE)
        with patch(f"{_MOD}.save_synced_records", new=AsyncMock()) as save, \
                patch(f"{_MOD}.carry_stored_version"), patch(f"{_MOD}.synced", side_effect=lambda r, p, e: (r, p)):
            with acting_as(_connection()):
                await connector._save_content_records([(page, []), (file, [])])
        rows = save.await_args.args[1]
        assert rows[0][0].inherit_permissions is False and rows[0][1][0].email == "ann@acme.com"
        assert rows[1][1] == [] and rows[1][0].inherit_permissions is True

    async def test_outside_a_pass_nothing_is_granted(self) -> None:
        connector = _connector()
        connector.data_entities_processor.get_record_by_external_id.return_value = None
        with patch(f"{_MOD}.save_synced_records", new=AsyncMock()) as save, \
                patch(f"{_MOD}.carry_stored_version"), patch(f"{_MOD}.synced", side_effect=lambda r, p, e: (r, p)):
            await connector._save_content_records([(_record("p1"), [])])
        assert save.await_args.args[1][0][1] == []


# ---------------------------------------------------------------- reconcile

def _reconcile_setup(listed: dict, stored: list, granted: list, verdicts: dict | None = None) -> NotionConnector:
    connector = _connector()
    connector._list_visible_objects = AsyncMock(side_effect=lambda t: listed.get(t))
    connector.data_entities_processor.get_records_by_record_type.side_effect = (
        lambda _c, rt: [r for r in stored if r.record_type == rt]
    )
    connector.data_entities_processor.get_user_granted_records.return_value = [
        {"id": r.id, "externalRecordId": r.external_record_id, "recordType": r.record_type.value} for r in granted
    ]
    connector._records_for_search_object = AsyncMock(return_value=None)
    connector._save_content_records = AsyncMock()
    connector._recheck_page_comments = AsyncMock()
    verdicts = verdicts or {}

    async def fetch(record) -> bool | None:
        verdict = verdicts.get(record.external_record_id, ("in_scope", {}))
        if isinstance(verdict, BaseException):
            raise verdict
        return verdict

    connector._fetch_placeholder_object = AsyncMock(side_effect=fetch)
    return connector


class TestReconcile:
    async def test_listed_and_stored_without_a_grant_is_granted(self) -> None:
        page = _record("p1")
        connector = _reconcile_setup({"data_source": {}, "page": {"p1": {"id": "p1"}}}, [page], granted=[])
        with acting_as(_connection()):
            assert await connector._reconcile_user_view(_connection(), SimpleNamespace(id="user-node")) is True
        record, permissions = connector.data_entities_processor.add_permission_to_record.await_args.args
        assert record is page and permissions[0].email == "ann@acme.com"

    async def test_a_page_this_user_lost_loses_only_their_grant(self) -> None:
        page = _record("p1")
        connector = _reconcile_setup({"data_source": {}, "page": {}}, [page], granted=[page],
                                     verdicts={"p1": ("gone", None)})
        with acting_as(_connection()):
            assert await connector._reconcile_user_view(_connection(), SimpleNamespace(id="user-node")) is True
        connector.data_entities_processor.remove_user_grants.assert_awaited_once_with("notion-1", "user-node", ["r-p1"])
        connector.data_entities_processor.on_record_deleted.assert_not_awaited()

    async def test_search_lag_keeps_the_grant(self) -> None:
        page = _record("p1")
        connector = _reconcile_setup({"data_source": {}, "page": {}}, [page], granted=[page],
                                     verdicts={"p1": ("in_scope", {})})
        with acting_as(_connection()):
            assert await connector._reconcile_user_view(_connection(), SimpleNamespace(id="user-node")) is True
        connector.data_entities_processor.remove_user_grants.assert_not_awaited()

    @pytest.mark.parametrize("verdict", [("unknown", None), RuntimeError("429")])
    async def test_an_undecided_check_keeps_the_grant_and_is_incomplete(self, verdict) -> None:
        page = _record("p1")
        connector = _reconcile_setup({"data_source": {}, "page": {}}, [page], granted=[page], verdicts={"p1": verdict})
        with acting_as(_connection()):
            assert await connector._reconcile_user_view(_connection(), SimpleNamespace(id="user-node")) is False
        connector.data_entities_processor.remove_user_grants.assert_not_awaited()

    async def test_an_unfinished_listing_removes_nothing(self) -> None:
        page = _record("p1")
        connector = _reconcile_setup({"data_source": {}, "page": None}, [page], granted=[page],
                                     verdicts={"p1": ("gone", None)})
        with acting_as(_connection()):
            assert await connector._reconcile_user_view(_connection(), SimpleNamespace(id="user-node")) is False
        connector.data_entities_processor.remove_user_grants.assert_not_awaited()
        connector._fetch_placeholder_object.assert_not_awaited()

    async def test_another_users_records_are_not_checked(self) -> None:
        mine, theirs = _record("p1"), _record("p2")
        connector = _reconcile_setup({"data_source": {}, "page": {"p1": {"id": "p1"}}}, [mine, theirs], granted=[mine])
        with acting_as(_connection()):
            await connector._reconcile_user_view(_connection(), SimpleNamespace(id="user-node"))
        connector._fetch_placeholder_object.assert_not_awaited()
        connector.data_entities_processor.add_permission_to_record.assert_not_awaited()


# ---------------------------------------------------------------- a pass and the whole run

class TestUserPass:
    async def test_a_refused_token_revokes_the_connection_and_access(self) -> None:
        connector = _connector()
        datasource = MagicMock()
        datasource.retrieve_bot_user = AsyncMock(return_value=SimpleNamespace(status_code=401, success=False))
        connector._get_fresh_datasource = AsyncMock(return_value=datasource)
        connector._sync_objects_by_type = AsyncMock()
        connection = _connection()
        with patch(f"{_MOD}.save_connection", new=AsyncMock()) as save, \
                patch(f"{_MOD}.get_connection", new=AsyncMock(return_value=_connection())):
            assert await connector._run_user_pass(connection, SimpleNamespace(id="user-node")) is False
        assert connection.status == STATUS_REVOKED and save.await_count == 1
        connector.data_entities_processor.withdraw_user_from_connector.assert_awaited_once_with("notion-1", "user-node")
        connector._sync_objects_by_type.assert_not_awaited()

    async def test_a_401_mid_walk_revokes_too(self) -> None:
        connector = _connector()
        datasource = MagicMock()
        datasource.retrieve_bot_user = AsyncMock(return_value=SimpleNamespace(status_code=200, success=True))
        connector._get_fresh_datasource = AsyncMock(return_value=datasource)
        connector._sync_objects_by_type = AsyncMock(side_effect=Exception("HTTP 401: unauthorized"))
        connection = _connection()
        with patch(f"{_MOD}.save_connection", new=AsyncMock()), \
                patch(f"{_MOD}.get_connection", new=AsyncMock(return_value=_connection())):
            assert await connector._run_user_pass(connection, SimpleNamespace(id="user-node")) is False
        assert connection.status == STATUS_REVOKED
        connector.data_entities_processor.withdraw_user_from_connector.assert_awaited_once()

    async def test_a_revoke_never_brings_back_a_connection_the_user_removed(self) -> None:
        connector = _connector()
        datasource = MagicMock()
        datasource.retrieve_bot_user = AsyncMock(return_value=SimpleNamespace(status_code=401, success=False))
        connector._get_fresh_datasource = AsyncMock(return_value=datasource)
        with patch(f"{_MOD}.save_connection", new=AsyncMock()) as save, \
                patch(f"{_MOD}.get_connection", new=AsyncMock(return_value=None)):
            await connector._run_user_pass(_connection(), SimpleNamespace(id="user-node"))
        save.assert_not_awaited()
        connector.data_entities_processor.withdraw_user_from_connector.assert_awaited_once()


class TestUpdateConnection:
    async def test_a_reconnect_is_not_overwritten(self) -> None:
        connector = _connector()
        with patch(f"{_MOD}.save_connection", new=AsyncMock(return_value=True)) as save, \
                patch(f"{_MOD}.get_connection", new=AsyncMock(return_value=_connection(token="new", connected_at=2))):
            assert await connector._update_connection(_connection(token="old")) is False
        save.assert_not_awaited()

    async def test_a_token_refreshed_meanwhile_is_kept(self) -> None:
        connector = _connector()
        mine = _connection(token="old", last_complete_listing_at=9)
        with patch(f"{_MOD}.save_connection", new=AsyncMock(return_value=True)) as save, \
                patch(f"{_MOD}.get_connection", new=AsyncMock(return_value=_connection(token="refreshed"))):
            assert await connector._update_connection(mine) is True
        saved = save.await_args.args[2]
        assert saved.token == "refreshed" and saved.last_complete_listing_at == 9

    async def test_the_same_connection_is_updated(self) -> None:
        connector = _connector()
        with patch(f"{_MOD}.save_connection", new=AsyncMock(return_value=True)) as save, \
                patch(f"{_MOD}.get_connection", new=AsyncMock(return_value=_connection())):
            assert await connector._update_connection(_connection()) is True
        save.assert_awaited_once()


def _run_setup(connections, users, pass_results) -> tuple:
    connector = _connector()
    datasource = MagicMock()
    connector._get_fresh_datasource = AsyncMock(return_value=datasource)
    connector._assert_required_capabilities = AsyncMock()
    connector._ensure_workspace_from_bot = AsyncMock()
    connector._prepare_per_user_workspace = AsyncMock()
    connector._load_bot_user_ids = AsyncMock()
    connector._delete_records_nobody_sees = AsyncMock()
    connector._run_user_pass = AsyncMock(side_effect=lambda c, u: pass_results[c.user_id])
    connector._update_connection = AsyncMock(return_value=True)
    for user in users.values():
        if user is not None and not hasattr(user, "is_active"):
            user.is_active = True
    connector.data_entities_processor.get_user_by_user_id.side_effect = lambda uid, **kw: users.get(uid)
    return connector, patch(f"{_MOD}.list_connections", new=AsyncMock(return_value=connections)), \
        patch(f"{_MOD}.save_connection", new=AsyncMock()), patch(f"{_MOD}.load_connector_filters",
                                                                 new=AsyncMock(return_value=(MagicMock(), MagicMock())))


class TestPerUserRun:
    async def test_records_nobody_sees_are_deleted_only_after_every_pass_completed(self) -> None:
        users = {"u1": SimpleNamespace(id="n1", email="ann@acme.com", full_name="Ann"),
                 "u2": SimpleNamespace(id="n2", email="bob@acme.com", full_name="Bob")}
        connections = [_connection("u1"), _connection("u2", email="bob@acme.com", token="tok-2")]
        connector, p1, p2, p3 = _run_setup(connections, users, {"u1": True, "u2": False})
        with p1, p2, p3:
            await connector._run_per_user_sync()
        connector._delete_records_nobody_sees.assert_not_awaited()
        assert connector._sync_incomplete is True

        connector, p1, p2, p3 = _run_setup(connections, users, {"u1": True, "u2": True})
        with p1, p2, p3:
            await connector._run_per_user_sync()
        connector._delete_records_nobody_sees.assert_awaited_once()

    async def test_only_connected_users_pass_the_gate(self) -> None:
        users = {"u1": SimpleNamespace(id="n1", email="ann@acme.com", full_name="Ann")}
        connector, p1, p2, p3 = _run_setup([_connection("u1")], users, {"u1": True})
        member = SimpleNamespace(id="n9")
        connector.data_entities_processor.get_all_app_users.return_value = [member, SimpleNamespace(id="n1")]
        with p1, p2, p3:
            await connector._run_per_user_sync()
        linked = connector.data_entities_processor.on_new_app_users.await_args.args[0]
        assert [u.email for u in linked] == ["ann@acme.com"]
        connector.data_entities_processor.withdraw_app_user_access.assert_awaited_once_with("notion-1", [member])

    async def test_a_user_who_disconnected_during_their_pass_loses_what_it_granted(self) -> None:
        users = {"u1": SimpleNamespace(id="n1", email="ann@acme.com", full_name="Ann")}
        connector, p1, p2, p3 = _run_setup([_connection("u1")], users, {"u1": True})
        connector._update_connection = AsyncMock(return_value=False)
        with p1, p2, p3:
            await connector._run_per_user_sync()
        connector.data_entities_processor.withdraw_user_from_connector.assert_awaited_once_with("notion-1", "n1")
        connector._delete_records_nobody_sees.assert_not_awaited()

    async def test_a_user_who_disconnected_during_an_unfinished_pass_loses_what_it_granted(self) -> None:
        users = {"u1": SimpleNamespace(id="n1", email="ann@acme.com", full_name="Ann")}
        connector, p1, p2, p3 = _run_setup([_connection("u1")], users, {"u1": False})
        with p1, p2, p3, patch(f"{_MOD}.get_connection", new=AsyncMock(return_value=None)):
            await connector._run_per_user_sync()
        connector.data_entities_processor.withdraw_user_from_connector.assert_awaited_once_with("notion-1", "n1")

    async def test_an_unfinished_pass_of_a_user_still_connected_keeps_their_access(self) -> None:
        users = {"u1": SimpleNamespace(id="n1", email="ann@acme.com", full_name="Ann")}
        connection = _connection("u1", last_complete_listing_at=10**13)
        connector, p1, p2, p3 = _run_setup([connection], users, {"u1": False})
        with p1, p2, p3, patch(f"{_MOD}.get_connection", new=AsyncMock(return_value=connection)),                 patch(f"{_MOD}.get_epoch_timestamp_in_ms", return_value=10**13):
            await connector._run_per_user_sync()
        connector.data_entities_processor.withdraw_user_from_connector.assert_not_awaited()

    async def test_a_stale_incomplete_user_loses_access(self) -> None:
        users = {"u1": SimpleNamespace(id="n1", email="ann@acme.com", full_name="Ann")}
        old = _connection("u1", last_complete_listing_at=1, connected_at=1)
        connector, p1, p2, p3 = _run_setup([old], users, {"u1": False})
        with p1, p2, p3:
            await connector._run_per_user_sync()
        connector.data_entities_processor.withdraw_user_from_connector.assert_awaited_once_with("notion-1", "n1")

    async def test_a_revoked_connection_gets_no_pass_and_no_access(self) -> None:
        users = {"u1": SimpleNamespace(id="n1", email="ann@acme.com", full_name="Ann")}
        revoked = _connection("u1", status=STATUS_REVOKED)
        connector, p1, p2, p3 = _run_setup([revoked], users, {"u1": True})
        with p1, p2, p3:
            await connector._run_per_user_sync()
        connector._run_user_pass.assert_not_awaited()
        connector.data_entities_processor.withdraw_user_from_connector.assert_awaited_once_with("notion-1", "n1")

    async def test_a_connection_for_another_workspace_is_revoked(self) -> None:
        users = {"u1": SimpleNamespace(id="n1", email="ann@acme.com", full_name="Ann")}
        foreign = _connection("u1", workspace_id="ws-other")
        connector, p1, p2, p3 = _run_setup([foreign], users, {"u1": True})
        with p1, p2, p3:
            await connector._run_per_user_sync()
        connector._run_user_pass.assert_not_awaited()
        assert foreign.status == STATUS_REVOKED


class TestDeleteRecordsNobodySees:
    async def test_only_unheld_pages_are_deleted(self) -> None:
        connector = _connector()
        held, unheld, stub = _record("p1", "r1"), _record("p2", "r2"), _record("p3", "r3", is_placeholder=True)
        connector.data_entities_processor.get_records_by_record_type.side_effect = (
            lambda _c, rt: [held, unheld, stub] if rt == RecordType.WEBPAGE else []
        )
        connector.data_entities_processor.get_user_granted_records.return_value = [{"id": "r1"}]
        connector._delete_records = AsyncMock(return_value=True)
        await connector._delete_records_nobody_sees([SimpleNamespace(id="n1")])
        connector._delete_records.assert_awaited_once_with([unheld])


class TestWorkspaceGroup:
    def _connector(self, existing) -> tuple[NotionConnector, MagicMock]:
        connector = _connector()
        dep = connector.data_entities_processor
        dep.get_record_group_by_external_id = AsyncMock(return_value=existing)
        dep.on_new_record_groups = AsyncMock()
        dep.stop_inheriting_permissions = AsyncMock()
        dep.drop_indexed_copies = AsyncMock()
        dep.get_records_by_record_type.side_effect = lambda _c, rt: [_record("p1")] if rt == RecordType.WEBPAGE else []
        return connector, dep

    @staticmethod
    def _group(model) -> RecordGroup:
        from app.models.entities import RecordGroup, RecordGroupType

        return RecordGroup(name="ws", external_group_id="ws-1", connector_name="NOTION", connector_id="notion-1",
                           group_type=RecordGroupType.NOTION_WORKSPACE, permission_model=model, inherit_permissions=True)

    async def test_a_new_per_user_workspace_reaches_nobody_by_itself(self) -> None:
        connector, dep = self._connector(None)
        await connector._prepare_per_user_workspace()
        group, grants = dep.on_new_record_groups.await_args.args[0][0]
        assert group.permission_model == PermissionModel.RECORD_LEVEL
        assert group.inherit_permissions is False and grants == []

    async def test_switching_from_workspace_mode_closes_the_workspace_then_every_parent_then_marks_it(self) -> None:
        connector, dep = self._connector(self._group(PermissionModel.RECORD_GROUP_LEVEL))
        order = []
        dep.on_new_record_groups.side_effect = lambda rows: order.append(("group", rows[0][0].permission_model,
                                                                           rows[0][0].inherit_permissions))
        dep.stop_inheriting_permissions.side_effect = lambda rid, cid: order.append(("strip", rid))
        dep.drop_indexed_copies.side_effect = lambda records, cid: order.append(("unindex", [r.id for r in records]))
        await connector._prepare_per_user_workspace()
        # A database's rows and synced-block text indexed for everyone leave search before anyone is granted.
        assert ("unindex", ["r-p1"]) in order
        assert [o for o in order if o[0] != "unindex"] == [
            ("group", PermissionModel.RECORD_GROUP_LEVEL, False), ("strip", "r-p1"),
            ("group", PermissionModel.RECORD_LEVEL, False)]
        assert order.index(("unindex", ["r-p1"])) < len(order) - 1

    async def test_a_prepared_workspace_is_not_stripped_again(self) -> None:
        connector, dep = self._connector(self._group(PermissionModel.RECORD_LEVEL))
        await connector._prepare_per_user_workspace()
        dep.stop_inheriting_permissions.assert_not_awaited()
        dep.drop_indexed_copies.assert_not_awaited()

    async def test_a_strip_cut_short_is_done_again_next_run(self) -> None:
        connector, dep = self._connector(self._group(PermissionModel.RECORD_GROUP_LEVEL))
        dep.stop_inheriting_permissions.side_effect = RuntimeError("graph down")
        with pytest.raises(RuntimeError):
            await connector._prepare_per_user_workspace()
        # never marked RECORD_LEVEL, so the next run strips again
        assert all(rows.args[0][0][0].permission_model != PermissionModel.RECORD_LEVEL
                   for rows in dep.on_new_record_groups.await_args_list)

    async def test_back_in_workspace_mode_the_workspace_is_everyones_again(self) -> None:
        connector = _connector()
        existing = self._group(PermissionModel.RECORD_LEVEL)
        tx = MagicMock()
        tx.get_record_group_by_external_id = AsyncMock(return_value=existing)
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = tx
        connector.data_entities_processor.on_new_record_groups = AsyncMock()
        await connector._create_workspace_record_group()
        group, _ = connector.data_entities_processor.on_new_record_groups.await_args.args[0][0]
        assert group.permission_model == PermissionModel.RECORD_GROUP_LEVEL and group.inherit_permissions is True

    async def test_back_in_workspace_mode_every_page_inherits_again_before_the_marker_moves(self) -> None:
        connector = _connector()
        existing = self._group(PermissionModel.RECORD_LEVEL)
        existing.id = "g-1"
        tx = MagicMock()
        tx.get_record_group_by_external_id = AsyncMock(return_value=existing)
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = tx
        dep = connector.data_entities_processor
        top, child = _record("p1"), _record("p2", parent="p1")
        dep.get_records_by_record_type.side_effect = lambda _c, rt: [top, child] if rt == RecordType.WEBPAGE else []
        dep.get_record_by_external_id = AsyncMock(side_effect=lambda _c, ext: {"p1": top}.get(ext))
        order = []
        dep.resume_inheriting_permissions = AsyncMock(side_effect=lambda rid, _c, **kw: order.append((rid, kw)))
        dep.on_new_record_groups = AsyncMock(side_effect=lambda rows: order.append("marker"))
        await connector._create_workspace_record_group()
        assert order == [("r-p1", {"parent_record_id": None, "record_group_id": "g-1"}),
                         ("r-p2", {"parent_record_id": "r-p1", "record_group_id": "g-1"}), "marker"]


# ---------------------------------------------------------------- content that differs per viewer

class TestContentThatDiffersPerViewer:
    async def test_synced_block_from_another_page_renders_empty(self) -> None:
        connector = _connector()
        connector._per_user = True
        connector._process_blocks_recursive = AsyncMock()
        assert await connector._process_children_of("block", "original-elsewhere") == []
        connector._process_blocks_recursive.assert_not_awaited()

    async def test_a_blocks_own_children_still_render(self) -> None:
        connector = _connector()
        connector._per_user = True
        connector._process_blocks_recursive = AsyncMock(return_value=["child"])
        assert await connector._process_children_of("block", "block") == ["child"]

    async def test_a_data_source_table_keeps_only_its_columns(self) -> None:
        connector = _connector()
        connector._per_user = True
        datasource = MagicMock()
        datasource.retrieve_data_source_by_id = AsyncMock(return_value=SimpleNamespace(
            success=True, data=SimpleNamespace(json=lambda: {"properties": {}}), status_code=200, error=None))
        datasource.query_data_source_by_id = AsyncMock()
        connector._get_fresh_datasource = AsyncMock(return_value=datasource)
        parser = MagicMock()
        parser.parse_data_source_to_blocks = AsyncMock(return_value=([], []))
        await connector._fetch_data_source_as_blocks("ds-1", parser)
        datasource.query_data_source_by_id.assert_not_awaited()
        assert parser.parse_data_source_to_blocks.await_args.kwargs["data_source_rows"] == []


class TestNeverDeleteOnOneUsersView:
    async def test_reindex_only_queues_records_again(self) -> None:
        connector = _connector()
        connector._load_access_mode = AsyncMock(return_value=True)
        connector._check_and_fetch_updated_record = AsyncMock()
        records = [_record("p1")]
        await connector.reindex_records(records)
        connector.data_entities_processor.reindex_existing_records.assert_awaited_once_with(records)
        connector._check_and_fetch_updated_record.assert_not_awaited()

    async def test_sweep_leaves_a_stub_one_user_cannot_see(self) -> None:
        connector = _connector()
        stub = _record("p1", is_placeholder=True)
        connector.data_entities_processor.get_placeholder_records = AsyncMock(return_value=[stub])
        connector._fetch_placeholder_object = AsyncMock(return_value=("gone", None))
        connector._detach_placeholder_children = AsyncMock()
        with acting_as(_connection()):
            await connector._sweep_placeholder_records()
        connector.data_entities_processor.on_record_deleted.assert_not_awaited()
        connector._detach_placeholder_children.assert_not_awaited()


class TestReaderForStreaming:
    async def test_a_file_is_read_as_a_connected_user_who_holds_its_page(self) -> None:
        connector = _connector()
        page = _record("p1", "r1")
        file = _record("f1", "rf", record_type=RecordType.FILE, parent="p1")
        connector.data_entities_processor.get_record_by_external_id.return_value = page
        connector.data_entities_processor.get_users_with_permission_to_node.return_value = [
            SimpleNamespace(user_id="u-gone"), SimpleNamespace(user_id="u1")]
        connections = [_connection("u1"), _connection("u2", status=STATUS_REVOKED)]
        with patch(f"{_MOD}.list_connections", new=AsyncMock(return_value=connections)):
            reader = await connector._reader_connection(file)
        assert reader.user_id == "u1"
        connector.data_entities_processor.get_users_with_permission_to_node.assert_awaited_once_with("r1", "records")

    async def test_no_active_reader_falls_back_to_the_connectors_own(self) -> None:
        connector = _connector()
        connector.data_entities_processor.get_users_with_permission_to_node.return_value = [SimpleNamespace(user_id="u2")]
        with patch(f"{_MOD}.list_connections", new=AsyncMock(return_value=[_connection("u2", status=STATUS_REVOKED)])):
            assert await connector._reader_connection(_record("p1")) is None


# ---------------------------------------------------------------- the connect route

class TestConnectRoute:
    def _request(self, connections=()) -> MagicMock:
        request = MagicMock()
        request.state.user = {"userId": "u1"}
        return request

    async def _connect(self, verify, connections=()) -> tuple:
        from app.connectors.api import user_access_router as routes

        graph = AsyncMock()
        with patch.object(routes, "_per_user_connector", new=AsyncMock(return_value={"type": "Notion", "config": {}})), \
                patch.object(routes, "get_user_or_503", new=AsyncMock(return_value={"id": "n1", "userId": "u1",
                                                                                    "email": "ann@acme.com"})), \
                patch.object(routes, "_config_service", return_value=AsyncMock()), \
                patch.object(routes, "verify_token", new=verify), \
                patch.object(routes, "list_connections", new=AsyncMock(return_value=list(connections))), \
                patch.object(routes, "save_connection", new=AsyncMock()) as save, \
                patch.object(routes, "_request_sync", new=AsyncMock()) as sync:
            result = await routes.connect_my_account("c1", self._request(), token=" tok ", graph_provider=graph)
        return result, save, sync

    async def test_a_refused_credential_is_a_400_and_nothing_is_saved(self) -> None:
        with pytest.raises(HTTPException) as e:
            await self._connect(AsyncMock(side_effect=NotionAccessError("This token belongs to bob@acme.com.")))
        assert e.value.status_code == 400

    async def test_notion_unreachable_is_a_503(self) -> None:
        with pytest.raises(HTTPException) as e:
            await self._connect(AsyncMock(side_effect=RuntimeError("timeout")))
        assert e.value.status_code == 503

    async def test_an_account_another_user_connected_is_a_409(self) -> None:
        other = _connection("u9")
        other.source_user_id = "p1"
        with pytest.raises(HTTPException) as e:
            await self._connect(AsyncMock(return_value=NotionIdentity("p1", "ann@acme.com", "ws-1")), [other])
        assert e.value.status_code == 409

    async def test_a_good_credential_is_saved_and_a_sync_requested_without_echoing_it(self) -> None:
        result, save, sync = await self._connect(AsyncMock(return_value=NotionIdentity("p1", "ann@acme.com", "ws-1")))
        saved: UserConnection = save.await_args.args[2]
        assert saved.token == "tok" and saved.email == "ann@acme.com" and saved.source_user_id == "p1"
        assert result["connected"] is True and "token" not in result["connection"]
        sync.assert_awaited_once()


# ---------------------------------------------------------------- framework pieces

class TestConnectorDeletion:
    async def test_every_users_credential_goes_with_the_connector(self) -> None:
        from app.connectors.services.storage_release import delete_connector_credentials

        keys = {
            "/services/connectors/c1/users/": ["/services/connectors/c1/users/u1", "/services/connectors/c1/users/u2"],
            # A sign-in started and never finished.
            "/services/connectors/c1/sign-in/": ["/services/connectors/c1/sign-in/u3"],
        }
        config = AsyncMock()
        config.list_keys_in_directory.side_effect = lambda prefix: keys.get(prefix, [])
        config.delete_config.return_value = True
        assert await delete_connector_credentials(MagicMock(), config, "c1") is True
        deleted = [call.args[0] for call in config.delete_config.await_args_list]
        assert deleted == ["/services/connectors/c1/users/u1", "/services/connectors/c1/users/u2",
                           "/services/connectors/c1/sign-in/u3", "/services/connectors/c1/members",
                           "/services/connectors/c1/config"]

    async def test_a_credential_left_behind_is_reported(self) -> None:
        from app.connectors.services.storage_release import delete_connector_credentials

        config = AsyncMock()
        config.list_keys_in_directory.return_value = ["/services/connectors/c1/users/u1"]
        config.delete_config.side_effect = lambda key: key.endswith("/config")
        config.get_config.return_value = {"token": "still here"}
        assert await delete_connector_credentials(MagicMock(), config, "c1") is False


class TestRemoveUserGrants:
    async def test_only_that_users_edges_go_and_search_access_is_dropped(self) -> None:
        from app.connectors.core.base.data_processor.data_source_entities_processor import (
            DataSourceEntitiesProcessor,
        )

        processor = DataSourceEntitiesProcessor.__new__(DataSourceEntitiesProcessor)
        tx = MagicMock()
        tx.batch_delete_edges = AsyncMock(return_value=2)
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        processor.data_store_provider = MagicMock()
        processor.data_store_provider.transaction.return_value = tx
        processor._access_changed = AsyncMock()
        await processor.remove_user_grants("c1", "n1", ["r1", "r2"])
        edges = tx.batch_delete_edges.await_args.args[0]
        assert {(e["from_id"], e["to_id"]) for e in edges} == {("n1", "r1"), ("n1", "r2")}
        assert all(e["from_collection"] == "users" and e["to_collection"] == "records" for e in edges)
        processor._access_changed.assert_awaited_once_with({"c1"})

    async def test_nothing_to_remove_is_a_no_op(self) -> None:
        from app.connectors.core.base.data_processor.data_source_entities_processor import (
            DataSourceEntitiesProcessor,
        )

        processor = DataSourceEntitiesProcessor.__new__(DataSourceEntitiesProcessor)
        processor.data_store_provider = MagicMock()
        await processor.remove_user_grants("c1", "n1", [])
        processor.data_store_provider.transaction.assert_not_called()


class TestDropIndexedCopies:
    async def test_records_leave_search_until_indexed_again(self) -> None:
        from app.connectors.core.base.data_processor.data_source_entities_processor import (
            DataSourceEntitiesProcessor,
        )
        from app.services.graph_db.common.record_visibility import (
            is_readable_record,
            is_searchable_record,
        )

        processor = DataSourceEntitiesProcessor.__new__(DataSourceEntitiesProcessor)
        tx = MagicMock()
        tx.batch_update_nodes = AsyncMock()
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        processor.data_store_provider = MagicMock()
        processor.data_store_provider.transaction.return_value = tx
        processor._access_changed = AsyncMock()
        processor.reindex_existing_records = AsyncMock()
        records = [_record("d1", record_type=RecordType.DATASOURCE), _record("p1")]
        await processor.drop_indexed_copies(records, "c1")
        updates, collection = tx.batch_update_nodes.await_args.args
        assert collection == "records" and [u["id"] for u in updates] == ["r-d1", "r-p1"]
        assert not any(is_searchable_record(u) for u in updates)
        # Nor readable by id from a parse-now copy stored for the current revision.
        stored = {"parseNowStatus": "STORED", "parseNowRev": "v1", "contentRev": "v1"}
        assert is_readable_record(stored)
        assert not any(is_readable_record({**stored, **u}) for u in updates)
        processor.reindex_existing_records.assert_awaited_once_with(records)


class TestListRoute:
    async def test_only_per_user_connectors_are_listed_with_the_callers_status(self) -> None:
        from app.connectors.api import user_access_router as routes

        graph = AsyncMock()
        graph.get_filtered_connector_instances.return_value = (
            [{"_key": "c1", "name": "Notion A", "type": "Notion"}, {"_key": "c2", "name": "Notion B", "type": "Notion"}], 2)
        config = AsyncMock()
        config.get_config.side_effect = lambda key, **kw: (
            {"sync": {"accessMode": "per_user"}} if "/c1/" in key else {"sync": {}})
        request = MagicMock()
        request.state.user = {"userId": "u1"}
        with patch.object(routes, "current_org_id", return_value="org-1"), \
                patch.object(routes, "get_user_or_503", new=AsyncMock(return_value={"id": "n1", "userId": "u1",
                                                                                    "email": "ann@acme.com"})), \
                patch.object(routes, "_config_service", return_value=config), \
                patch.object(routes, "get_connection", new=AsyncMock(return_value=_connection())):
            result = await routes.list_my_per_user_connectors(request, graph_provider=graph)
        assert [(c["connectorId"], c["connected"]) for c in result["connectors"]] == [("c1", True)]
        assert "token" not in result["connectors"][0]["connection"]


class TestRouteGate:
    async def _gate(self, app, config, same_org=True) -> dict:
        from app.connectors.api import user_access_router as routes

        graph = AsyncMock()
        graph.get_document.return_value = app
        request = MagicMock()
        request.app.state.connector_registry.belongs_to_org = AsyncMock(return_value=same_org)
        cfg = AsyncMock()
        cfg.get_config.return_value = config
        with patch.object(routes, "current_org_id", return_value="org-1"), \
                patch.object(routes, "_config_service", return_value=cfg):
            return await routes._per_user_connector(request, "c1", graph)

    async def test_a_per_user_team_notion_connector_passes(self) -> None:
        app = {"type": "Notion", "scope": "team"}
        config = {"sync": {"accessMode": "per_user"}}
        assert await self._gate(app, config) == {**app, "config": config}

    @pytest.mark.parametrize("app, config, same_org", [
        (None, {"sync": {"accessMode": "per_user"}}, True),
        ({"type": "Notion", "scope": "team"}, {"sync": {"accessMode": "workspace"}}, True),
        ({"type": "Notion", "scope": "team"}, {"sync": {}}, True),
        ({"type": "Notion", "scope": "team"}, {"sync": {"accessMode": "per_user"}}, False),
        ({"type": "Notion Personal", "scope": "personal"}, {"sync": {"accessMode": "per_user"}}, True),
        ({"type": "Notion", "scope": "personal"}, {"sync": {"accessMode": "per_user"}}, True),
        ({"type": "Confluence", "scope": "team"}, {"sync": {"accessMode": "per_user"}}, True),
    ])
    async def test_anything_else_is_the_same_404(self, app, config, same_org) -> None:
        with pytest.raises(HTTPException) as e:
            await self._gate(app, config, same_org)
        assert e.value.status_code == 404


# ---------------------------------------------------------------- signing in with Notion (OAuth)

_PER_USER = "app.connectors.sources.notion.per_user"


class TestStoredSignIn:
    def test_public_view_never_carries_the_refresh_token(self) -> None:
        public = _connection(refresh_token="rt", method="oauth").public()
        assert "refreshToken" not in public and "token" not in public and public["method"] == "oauth"

    def test_round_trip_keeps_the_refresh_token_and_method(self) -> None:
        connection = _connection(refresh_token="rt", method="oauth")
        assert UserConnection.from_dict(connection.to_dict()) == connection

    def test_a_pair_a_refresh_stored_wins_over_the_old_one(self) -> None:
        # OAuthProvider.refresh_access_token writes the new pair under "credentials" before anything else.
        data = {**_connection(token="old", refresh_token="rt-old").to_dict(),
                "credentials": {"access_token": "new", "refresh_token": "rt-new"}}
        connection = UserConnection.from_dict(data)
        assert (connection.token, connection.refresh_token) == ("new", "rt-new")
        assert "credentials" not in connection.to_dict()

    @pytest.mark.parametrize("config, expected", [
        ({"auth": {"authType": "OAUTH"}}, True),
        ({"auth": {"authType": "API_TOKEN"}}, False),
        ({}, False),
        (None, False),
    ])
    def test_sign_in_needs_the_connectors_oauth_app(self, config, expected) -> None:
        from app.connectors.sources.notion.per_user import has_oauth_app
        assert has_oauth_app(config) is expected


class TestRefreshConnection:
    async def _refresh(self, connection, stored, *, refreshed=None, error=None, after=None) -> tuple:
        """``stored``: what get_connection returns first; ``after``: what it returns after the refresh."""
        from app.connectors.sources.notion import per_user

        reads = [stored] + ([after] if after is not None or error is not None or refreshed is not None else [])
        provider = MagicMock()
        provider.refresh_access_token = AsyncMock(side_effect=error, return_value=refreshed)
        provider.close = AsyncMock()
        with patch(f"{_PER_USER}.get_connection", new=AsyncMock(side_effect=[*reads, after])), \
                patch(f"{_PER_USER}.save_connection", new=AsyncMock()) as save, \
                patch(f"{_PER_USER}.delete_connection", new=AsyncMock()) as delete, \
                patch(f"{_PER_USER}.user_oauth_config", new=AsyncMock(return_value=MagicMock())), \
                patch(f"{_PER_USER}.OAuthProvider", return_value=provider):
            result = await per_user.refresh_connection(
                connection, connector_id="c1", config_service=AsyncMock(), org_id="org-1", logger=MagicMock(),
            )
        return result, provider, save, delete

    async def test_a_token_another_task_refreshed_is_adopted_without_a_refresh(self) -> None:
        connection = _connection(token="old", refresh_token="rt")
        result, provider, save, _ = await self._refresh(connection, _connection(token="new", refresh_token="rt2"))
        assert result is True and connection.token == "new" and connection.refresh_token == "rt2"
        provider.refresh_access_token.assert_not_awaited()
        save.assert_not_awaited()

    async def test_a_pasted_token_cannot_be_refreshed(self) -> None:
        result, provider, _, _ = await self._refresh(_connection(), _connection())
        assert result is False
        provider.refresh_access_token.assert_not_awaited()

    async def test_a_disconnected_or_reconnected_user_is_not_refreshed(self) -> None:
        for stored in (None, _connection(refresh_token="rt", connected_at=2)):
            result, provider, _, _ = await self._refresh(_connection(refresh_token="rt"), stored)
            assert result is False
            provider.refresh_access_token.assert_not_awaited()

    async def test_a_refresh_stores_the_new_pair_and_the_running_task_uses_it(self) -> None:
        connection = _connection(token="old", refresh_token="rt")
        stored = _connection(token="old", refresh_token="rt")
        token = SimpleNamespace(access_token="new", refresh_token="rt2")
        result, _, save, _ = await self._refresh(connection, stored, refreshed=token, after=_connection(
            token="old", refresh_token="rt", last_complete_listing_at=7))
        assert result is True and (connection.token, connection.refresh_token) == ("new", "rt2")
        saved = save.await_args.args[2]
        assert (saved.token, saved.refresh_token, saved.last_complete_listing_at) == ("new", "rt2", 7)

    async def test_a_refused_refresh_token_ends_the_sign_in(self) -> None:
        from app.connectors.core.base.token_service.oauth_service import (
            RefreshTokenInvalidError,
        )

        connection = _connection(refresh_token="rt")
        result, _, save, _ = await self._refresh(
            connection, _connection(refresh_token="rt"), error=RefreshTokenInvalidError("invalid_grant"),
            after=_connection(refresh_token="rt"),
        )
        assert result is False
        save.assert_not_awaited()

    async def test_a_refusal_after_another_process_refreshed_adopts_its_token(self) -> None:
        from app.connectors.core.base.token_service.oauth_service import (
            RefreshTokenInvalidError,
        )

        connection = _connection(refresh_token="rt")
        result, _, _, _ = await self._refresh(
            connection, _connection(refresh_token="rt"), error=RefreshTokenInvalidError("invalid_grant"),
            after=_connection(token="other", refresh_token="rt3"),
        )
        assert result is True and connection.token == "other"

    async def test_notion_down_is_neither_success_nor_a_revoke(self) -> None:
        result, _, save, _ = await self._refresh(
            _connection(refresh_token="rt"), _connection(refresh_token="rt"), error=RuntimeError("503"),
        )
        assert result is None
        save.assert_not_awaited()

    async def test_a_disconnect_during_the_refresh_leaves_no_credential_behind(self) -> None:
        token = SimpleNamespace(access_token="new", refresh_token="rt2")
        result, _, save, delete = await self._refresh(
            _connection(refresh_token="rt"), _connection(refresh_token="rt"), refreshed=token, after=None,
        )
        assert result is False
        save.assert_not_awaited()
        delete.assert_awaited_once()


class TestUserPassRefresh:
    def _setup(self, refresh_result, statuses) -> NotionConnector:
        connector = _connector()
        datasource = MagicMock()
        datasource.retrieve_bot_user = AsyncMock(
            side_effect=[SimpleNamespace(status_code=code, success=code == 200) for code in statuses])
        connector._get_fresh_datasource = AsyncMock(return_value=datasource)
        connector._sync_objects_by_type = AsyncMock()
        connector._reconcile_user_view = AsyncMock(return_value=True)
        connector._refresh_touched_data_sources = AsyncMock()
        connector._sweep_placeholder_records = AsyncMock()
        connector._refresh_user_token = AsyncMock(return_value=refresh_result)
        connector._revoke_connection = AsyncMock()
        return connector

    async def test_an_expired_sign_in_is_refreshed_and_the_pass_runs_again(self) -> None:
        connector = self._setup(True, [401, 200])
        assert await connector._run_user_pass(_connection(refresh_token="rt"), SimpleNamespace(id="n")) is True
        connector._revoke_connection.assert_not_awaited()
        assert connector._sync_incomplete is False

    async def test_a_sign_in_that_cannot_be_refreshed_is_revoked(self) -> None:
        connector = self._setup(False, [401])
        assert await connector._run_user_pass(_connection(refresh_token="rt"), SimpleNamespace(id="n")) is False
        connector._revoke_connection.assert_awaited_once()

    async def test_a_refresh_that_could_not_run_keeps_access_but_marks_the_run_incomplete(self) -> None:
        connector = self._setup(None, [401])
        assert await connector._run_user_pass(_connection(refresh_token="rt"), SimpleNamespace(id="n")) is False
        connector._revoke_connection.assert_not_awaited()
        assert connector._sync_incomplete is True

    async def test_a_second_refusal_after_a_refresh_is_revoked(self) -> None:
        connector = self._setup(True, [401, 401])
        assert await connector._run_user_pass(_connection(refresh_token="rt"), SimpleNamespace(id="n")) is False
        connector._refresh_user_token.assert_awaited_once()
        connector._revoke_connection.assert_awaited_once()

    async def test_a_stream_reading_as_a_user_refreshes_that_user_never_the_connector(self) -> None:
        connector = self._setup(True, [])
        connector.notion_client = None
        with acting_as(_connection(refresh_token="rt")):
            assert await connector._refresh_after_unauthorized() is True
        connector._refresh_user_token.assert_awaited_once()


def _session_store() -> AsyncMock:
    store = AsyncMock()
    store.get_config.return_value = {"oauth": {"state": "s1"}}
    return store


class TestSignInRoutes:
    def _request(self) -> MagicMock:
        request = MagicMock()
        request.state.user = {"userId": "u1"}
        return request

    async def test_the_sign_in_url_carries_the_marker_and_keeps_state_per_user(self) -> None:
        from app.connectors.api import router as main_router
        from app.connectors.api import user_access_router as routes

        provider = MagicMock()
        provider.start_authorization = AsyncMock(
            return_value="https://api.notion.com/v1/oauth/authorize?client_id=x&state=s1&owner=user")
        provider.close = AsyncMock()
        with patch.object(routes, "_per_user_connector", new=AsyncMock(return_value={"type": "Notion", "config": {}})), \
                patch.object(routes, "get_user_or_503", new=AsyncMock(return_value={"id": "n1", "userId": "u1",
                                                                                    "email": "ann@acme.com"})), \
                patch.object(routes, "_config_service", return_value=AsyncMock()), \
                patch.object(routes, "current_org_id", return_value="org-1"), \
                patch.object(routes, "user_oauth_config", new=AsyncMock(return_value=MagicMock())), \
                patch.object(routes, "OAuthProvider", return_value=provider) as make_provider:
            result = await routes.start_my_sign_in("c1", self._request(), graph_provider=AsyncMock())
        assert make_provider.call_args.args[2] == "/services/connectors/c1/sign-in/u1"
        assert provider.start_authorization.await_args.kwargs == {"owner": "user"}
        from urllib.parse import parse_qs, urlparse
        state = parse_qs(urlparse(result["authorizationUrl"]).query)["state"][0]
        decoded = main_router._decode_state_with_instance(state)
        assert decoded["state"] == "s1" and decoded["connector_id"] == "c1"
        assert routes.is_user_sign_in_state(decoded)
        assert not routes.is_user_sign_in_state({"state": "s1", "connector_id": "c1"})
        assert not routes.is_user_sign_in_state(["not", "a", "dict"])

    async def test_no_oauth_app_is_a_400(self) -> None:
        from app.connectors.api import user_access_router as routes

        with patch.object(routes, "_per_user_connector", new=AsyncMock(return_value={"type": "Notion", "config": {}})), \
                patch.object(routes, "get_user_or_503", new=AsyncMock(return_value={"id": "n1", "userId": "u1",
                                                                                    "email": "ann@acme.com"})), \
                patch.object(routes, "_config_service", return_value=AsyncMock()), \
                patch.object(routes, "current_org_id", return_value="org-1"), \
                patch.object(routes, "user_oauth_config", new=AsyncMock(side_effect=NotionAccessError("no app"))):
            with pytest.raises(HTTPException) as e:
                await routes.start_my_sign_in("c1", self._request(), graph_provider=AsyncMock())
        assert e.value.status_code == 400

    async def _complete(self, *, gate=None, exchange=None, verify=None) -> tuple:
        from app.connectors.api import user_access_router as routes

        provider = MagicMock()
        provider.handle_callback = exchange or AsyncMock(
            return_value=SimpleNamespace(access_token="at", refresh_token="rt"))
        provider.close = AsyncMock()
        with patch.object(routes, "_per_user_connector",
                          new=gate or AsyncMock(return_value={"type": "Notion", "config": {}})), \
                patch.object(routes, "get_user_or_503", new=AsyncMock(return_value={"id": "n1", "userId": "u1",
                                                                                    "email": "ann@acme.com"})), \
                patch.object(routes, "_config_service", return_value=_session_store()), \
                patch.object(routes, "current_org_id", return_value="org-1"), \
                patch.object(routes, "user_oauth_config", new=AsyncMock(return_value=MagicMock())), \
                patch.object(routes, "OAuthProvider", return_value=provider), \
                patch.object(routes, "verify_token",
                             new=verify or AsyncMock(return_value=NotionIdentity("p1", "ann@acme.com", "ws-1"))), \
                patch.object(routes, "list_connections", new=AsyncMock(return_value=[])), \
                patch.object(routes, "save_connection", new=AsyncMock()) as save, \
                patch.object(routes, "_request_sync", new=AsyncMock()), \
                patch.object(routes, "delete_config_confirmed", new=AsyncMock(return_value=True)) as drop_session:
            result = await routes.complete_user_sign_in(
                self._request(), AsyncMock(), connector_id="c1", state="s1", code="code",
                base_url="https://app", settings_url="https://app/workspace/connectors",
            )
        return result, provider, save, drop_session

    async def test_a_sign_in_connects_the_oauth_token_and_drops_the_session(self) -> None:
        result, provider, save, drop_session = await self._complete()
        assert result["success"] is True and result["redirect_url"].endswith("/c1")
        provider.handle_callback.assert_awaited_once_with("code", "s1")
        saved: UserConnection = save.await_args.args[2]
        assert (saved.token, saved.refresh_token, saved.method) == ("at", "rt", "oauth")
        drop_session.assert_awaited_once_with(ANY, "/services/connectors/c1/sign-in/u1")

    async def test_a_disconnect_during_the_code_exchange_waits_and_wins(self) -> None:
        import asyncio

        from app.connectors.sources.notion.per_user import refresh_lock

        exchanging, release = asyncio.Event(), asyncio.Event()

        async def exchange(*_a: object) -> SimpleNamespace:
            exchanging.set()
            await release.wait()
            return SimpleNamespace(access_token="at", refresh_token="rt")

        sign_in = asyncio.create_task(self._complete(exchange=exchange))
        await exchanging.wait()
        # What _remove does first: it must wait for the sign-in, so the removal comes after the save.
        removal = asyncio.create_task(refresh_lock("c1", "u1").acquire())
        await asyncio.sleep(0.01)
        assert not removal.done()
        release.set()
        result, _, save, _ = await sign_in
        await removal
        refresh_lock("c1", "u1").release()
        assert result["success"] is True and save.await_count == 1

    async def test_a_wrong_account_is_reported_and_nothing_is_saved(self) -> None:
        result, _, save, drop_session = await self._complete(
            verify=AsyncMock(side_effect=NotionAccessError("This token belongs to bob@acme.com.")))
        assert result["success"] is False and "bob@acme.com" in result["error_message"]
        save.assert_not_awaited()
        drop_session.assert_awaited_once()

    async def test_a_failed_exchange_is_reported_and_the_session_dropped(self) -> None:
        result, _, save, drop_session = await self._complete(exchange=AsyncMock(side_effect=ValueError("Invalid state")))
        assert result["success"] is False and result["error_message"]
        save.assert_not_awaited()
        drop_session.assert_awaited_once()

    async def test_it_never_raises_so_the_connector_itself_is_never_marked_signed_out(self) -> None:
        result, _, _, _ = await self._complete(gate=AsyncMock(side_effect=RuntimeError("etcd down")))
        assert result["success"] is False

    async def test_a_connector_not_in_per_user_mode_is_refused(self) -> None:
        result, provider, _, _ = await self._complete(gate=AsyncMock(side_effect=HTTPException(status_code=404)))
        assert result["success"] is False
        provider.handle_callback.assert_not_awaited()


class TestDisconnectAndAdminRoutes:
    def _patches(self, routes, delete_ok=True) -> tuple:
        return (
            patch.object(routes, "_per_user_connector", new=AsyncMock(return_value={"type": "Notion", "config": {}})),
            patch.object(routes, "get_user_or_503", new=AsyncMock(return_value={"id": "n1", "userId": "u1",
                                                                                "email": "ann@acme.com"})),
            patch.object(routes, "_config_service", return_value=AsyncMock()),
            patch.object(routes, "delete_connection", new=AsyncMock(return_value=delete_ok)),
            patch.object(routes, "delete_config_confirmed", new=AsyncMock(return_value=True)),
            patch.object(routes, "_withdraw", new=AsyncMock()),
        )

    async def test_disconnect_drops_the_credential_and_any_pending_sign_in_then_the_access(self) -> None:
        from app.connectors.api import user_access_router as routes

        request = MagicMock()
        request.state.user = {"userId": "u1"}
        gate, user, cfg, delete, drop_session, withdraw = self._patches(routes)
        with gate, user, cfg, delete as delete_mock, drop_session as drop_mock, withdraw as withdraw_mock:
            result = await routes.disconnect_my_account("c1", request, graph_provider=AsyncMock())
        assert result["connected"] is False
        delete_mock.assert_awaited_once_with(ANY, "c1", "u1")
        drop_mock.assert_awaited_once_with(ANY, "/services/connectors/c1/sign-in/u1")
        withdraw_mock.assert_awaited_once_with(ANY, "c1", "n1")

    async def test_disconnect_waits_for_a_token_refresh_in_progress(self) -> None:
        import asyncio

        from app.connectors.api import user_access_router as routes
        from app.connectors.sources.notion.per_user import refresh_lock

        request = MagicMock()
        request.state.user = {"userId": "u1"}
        gate, user, cfg, delete, drop_session, withdraw = self._patches(routes)
        lock = refresh_lock("c1", "u1")
        with gate, user, cfg, delete as delete_mock, drop_session, withdraw:
            await lock.acquire()
            task = asyncio.create_task(routes.disconnect_my_account("c1", request, graph_provider=AsyncMock()))
            await asyncio.sleep(0.01)
            delete_mock.assert_not_awaited()  # a refresh that read the connection writes it back first
            lock.release()
            await task
        delete_mock.assert_awaited_once()

    async def test_a_credential_that_could_not_be_deleted_keeps_nothing_quiet(self) -> None:
        from app.connectors.api import user_access_router as routes

        request = MagicMock()
        request.state.user = {"userId": "u1"}
        gate, user, cfg, delete, drop_session, withdraw = self._patches(routes, delete_ok=False)
        with gate, user, cfg, delete, drop_session, withdraw as withdraw_mock:
            with pytest.raises(HTTPException) as e:
                await routes.disconnect_my_account("c1", request, graph_provider=AsyncMock())
        assert e.value.status_code == 503
        withdraw_mock.assert_not_awaited()

    @pytest.mark.parametrize("call", ["list", "remove"])
    async def test_admin_routes_are_a_404_for_anyone_else(self, call) -> None:
        from app.connectors.api import user_access_router as routes

        request = MagicMock()
        with patch.object(routes, "is_request_admin", return_value=False):
            with pytest.raises(HTTPException) as e:
                if call == "list":
                    await routes.list_connected_users("c1", request, graph_provider=AsyncMock())
                else:
                    await routes.remove_connected_user("c1", "u2", request, graph_provider=AsyncMock())
        assert e.value.status_code == 404
