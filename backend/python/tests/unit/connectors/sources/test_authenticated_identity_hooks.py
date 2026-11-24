"""Each team connector's read of the source account it is authenticated as.

A connector hands that account's email and id to ``register_authenticated_source_user``
at the start of a sync, so a creator whose PipesHub email differs is linked to it. The
id must be the one the connector's user sync stores as sourceUserId, and a failed read
must never abort the sync. What the helper then does with the pair is covered in
tests/unit/connectors/core/test_authenticated_as_linking.py.
"""

import sys
from collections.abc import Callable
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.sources.atlassian.confluence_cloud.connector import (
    ConfluenceConnector,
)
from app.connectors.sources.atlassian.confluence_datacenter.connector import (
    ConfluenceDataCenterConnector,
)
from app.connectors.sources.atlassian.jira_cloud.connector import JiraConnector
from app.connectors.sources.atlassian.jira_data_center.connector import (
    JiraDataCenterConnector,
)
from app.connectors.sources.dropbox.connector import DropboxConnector
from app.connectors.sources.github_teams.connector import GitHubTeamsConnector
from app.connectors.sources.gitlab.connector import GitLabConnector
from app.connectors.sources.google.drive.individual.connector import (
    GoogleDriveIndividualConnector,
)
from app.connectors.sources.google.drive.team.connector import GoogleDriveTeamConnector
from app.connectors.sources.google.gmail.team.connector import GoogleGmailTeamConnector
from app.connectors.sources.linear.connector import LinearConnector
from app.connectors.sources.notion.connector import NotionConnector
from app.connectors.sources.salesforce.connector import SalesforceConnector
from app.connectors.sources.servicenow.servicenow.connector import ServiceNowConnector
from app.connectors.sources.slack.team.connector import SlackConnector
from app.connectors.sources.zoom.connector import ZoomConnector
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.notion.notion import (
    NotionRESTClientViaOAuth,
    NotionRESTClientViaToken,
)
from app.sources.external.servicenow.models import TableAPIRecord


def _connector(cls: type, **attrs: object) -> MagicMock:
    conn = MagicMock(spec=cls)
    conn.logger = MagicMock()
    conn.connector_id = "connector-1"
    conn.register_authenticated_source_user = AsyncMock()
    for name, value in attrs.items():
        setattr(conn, name, value)
    return conn


def _registered(conn: MagicMock) -> tuple:
    conn.register_authenticated_source_user.assert_awaited_once()
    return conn.register_authenticated_source_user.await_args.args


def _json_response(status: int, body: dict) -> MagicMock:
    response = MagicMock(status=status)
    response.json.return_value = body
    return response


# ---------------------------------------------------------------------------
# Connectors that start registering in this change
# ---------------------------------------------------------------------------


class TestDropbox:
    def _conn(self, response: object = None, error: Exception | None = None) -> MagicMock:
        conn = _connector(DropboxConnector, data_source=MagicMock())
        conn.data_source.team_token_get_authenticated_admin = AsyncMock(return_value=response, side_effect=error)
        return conn

    async def test_reads_the_admin_who_generated_the_team_token(self) -> None:
        profile = SimpleNamespace(email="admin@corp.com", team_member_id="dbmid:AAH4f99")
        conn = self._conn(SimpleNamespace(success=True, data=SimpleNamespace(admin_profile=profile)))

        assert await DropboxConnector._read_authenticated_admin(conn) == ("admin@corp.com", "dbmid:AAH4f99")

    async def test_a_token_not_tied_to_an_admin_reads_nothing(self) -> None:
        conn = self._conn(SimpleNamespace(success=False, data=None, error="mapping_not_found"))

        assert await DropboxConnector._read_authenticated_admin(conn) == (None, None)

    async def test_a_failed_read_reads_nothing_and_never_raises(self) -> None:
        conn = self._conn(error=RuntimeError("connection reset"))

        assert await DropboxConnector._read_authenticated_admin(conn) == (None, None)


class _DropboxStop(Exception):
    pass


class TestDropboxSyncOrder:
    def _conn(self, order: list[str], complete: bool = True, auth_error: bool = False) -> MagicMock:
        conn = _connector(
            DropboxConnector, config_service=MagicMock(), _health=MagicMock(auth_error=None),
            _members_complete=False,
        )

        def step(name: str, effect: object = None) -> AsyncMock:
            async def _run(*_: object, **__: object) -> object:
                order.append(name)
                if name == "members":
                    conn._members_complete = complete
                if name == "register":
                    raise _DropboxStop()
                return effect
            return AsyncMock(side_effect=_run)

        conn.drop_authenticator_link_if_listed = step("drop")
        conn._read_authenticated_admin = step("read", ("admin@corp.com", "dbmid:1"))
        conn._sync_members = step("members", [])
        conn.register_authenticated_source_user = step("register")
        return conn

    async def _run(self, conn: MagicMock) -> None:
        loaded = AsyncMock(return_value=(MagicMock(), MagicMock()))
        with patch("app.connectors.sources.dropbox.connector.load_connector_filters", loaded), \
                pytest.raises(_DropboxStop):
            await DropboxConnector._sync_steps(conn)

    async def test_removal_pass_runs_first_and_full_pass_after_the_member_sync(self) -> None:
        order: list[str] = []
        conn = self._conn(order)

        await self._run(conn)

        assert order == ["drop", "read", "members", "register"]

    async def test_an_incomplete_member_listing_is_an_incomplete_listing(self) -> None:
        conn = self._conn([], complete=False)

        await self._run(conn)

        assert conn.register_authenticated_source_user.await_args.kwargs["listing_complete"] is False

    async def test_a_complete_member_listing_is_passed_on(self) -> None:
        conn = self._conn([])

        await self._run(conn)

        assert conn.register_authenticated_source_user.await_args.args == ("admin@corp.com", "dbmid:1")
        assert conn.register_authenticated_source_user.await_args.kwargs["listing_complete"] is True

    async def test_a_stale_complete_flag_never_survives_into_a_sync_whose_listing_did_not_run(self) -> None:
        conn = self._conn([])
        conn._members_complete = True
        conn._sync_members = AsyncMock(return_value=[])

        await self._run(conn)

        assert conn.register_authenticated_source_user.await_args.kwargs["listing_complete"] is False

    async def test_auth_error_listing_never_links(self) -> None:
        members = SimpleNamespace(raw=[], complete=False, error="expired_access_token", auth_error=True)
        conn = _connector(
            DropboxConnector, data_source=MagicMock(), _health=MagicMock(), _members_complete=True,
        )
        with patch("app.connectors.sources.dropbox.connector.read_team_members", AsyncMock(return_value=members)):
            assert await DropboxConnector._sync_members(conn) == []

        assert conn._members_complete is False
        conn._health.auth_failed.assert_called_once()

    @pytest.mark.parametrize("tag", ["suspended", "invited"])
    async def test_a_suspended_listed_creator_stays_unlinked(self, tag: str) -> None:
        from app.models.entities import AppUser

        creator = SimpleNamespace(
            team_member_id="dbmid:C", email="Creator@Corp.com",
            status=SimpleNamespace(_tag=tag, is_active=lambda: False),
            name=SimpleNamespace(display_name="C"),
        )
        member = SimpleNamespace(profile=creator, role=SimpleNamespace(_tag="member_only"))
        members = SimpleNamespace(raw=[member], complete=True, error=None, auth_error=False,
                                  active_ids=lambda: set())
        processor = MagicMock()
        processor.on_new_app_users = AsyncMock()
        processor.get_all_app_users = AsyncMock(return_value=[AppUser(
            app_name="DROPBOX", connector_id="connector-1", source_user_id="dbmid:C",
            email="creator@corp.com", full_name="C",
        )])
        processor.withdraw_app_user_access = AsyncMock()
        conn = _connector(
            DropboxConnector, config_service=MagicMock(), data_source=MagicMock(),
            _health=MagicMock(auth_error=None), _members_complete=False, _team_emails=set(),
            data_entities_processor=processor, connector_name="DROPBOX",
        )
        conn.get_app_users = partial(DropboxConnector.get_app_users, conn)
        conn._sync_members = partial(DropboxConnector._sync_members, conn)
        conn.drop_authenticator_link_if_listed = AsyncMock()
        conn._read_authenticated_admin = AsyncMock(return_value=("admin@corp.com", "dbmid:A"))
        conn.register_authenticated_source_user = AsyncMock(side_effect=_DropboxStop())
        loaded = AsyncMock(return_value=(MagicMock(), MagicMock()))
        with patch("app.connectors.sources.dropbox.connector.read_team_members", AsyncMock(return_value=members)), \
                patch("app.connectors.sources.dropbox.connector.load_connector_filters", loaded), \
                pytest.raises(_DropboxStop):
            await DropboxConnector._sync_steps(conn)

        processor.withdraw_app_user_access.assert_awaited_once()
        kwargs = conn.register_authenticated_source_user.await_args.kwargs
        assert kwargs["listing_complete"] is True
        assert "creator@corp.com" in kwargs["extra_present_emails"]


class TestNotion:
    def _datasource(self, owner: dict) -> MagicMock:
        response = SimpleNamespace(success=True, data=MagicMock())
        response.data.json.return_value = {"object": "user", "type": "bot", "bot": {"owner": owner}}
        datasource = MagicMock()
        datasource.retrieve_bot_user = AsyncMock(return_value=response)
        return datasource

    def _conn(self, client_cls: type) -> MagicMock:
        conn = _connector(NotionConnector, notion_client=MagicMock(), _users_listing_complete=True,
                          _listed_user_emails=set())
        conn.notion_client.get_client.return_value = MagicMock(spec=client_cls)
        return conn

    async def test_registers_the_person_who_authorized_the_oauth_bot(self) -> None:
        datasource = self._datasource(
            {"type": "user", "user": {"id": "6794760a-1f15", "type": "person", "person": {"email": " owner@corp.com "}}}
        )
        conn = self._conn(NotionRESTClientViaOAuth)

        await NotionConnector._register_authenticated_identity(conn, datasource)

        assert _registered(conn) == ("owner@corp.com", "6794760a-1f15")

    async def test_an_integration_secret_has_no_person_to_link(self) -> None:
        datasource = self._datasource({"type": "workspace", "workspace": True})
        conn = self._conn(NotionRESTClientViaToken)

        await NotionConnector._register_authenticated_identity(conn, datasource)

        datasource.retrieve_bot_user.assert_not_awaited()
        conn.register_authenticated_source_user.assert_not_awaited()


class TestServiceNow:
    def _conn(self, rows: list) -> MagicMock:
        datasource = MagicMock()
        datasource.get_now_table_tableName = AsyncMock(return_value=SimpleNamespace(result=rows))
        conn = _connector(ServiceNowConnector, _get_fresh_datasource=AsyncMock(return_value=datasource),
                          servicenow_client=None, refresh_token=None, _users_listing_complete=True,
                          _listed_user_emails=set())
        conn._table_get = partial(ServiceNowConnector._table_get, conn)
        conn._with_token_retry = partial(ServiceNowConnector._with_token_retry, conn)
        return conn

    async def test_registers_the_user_the_token_belongs_to(self) -> None:
        conn = self._conn([TableAPIRecord(sys_id="6816f79cc0a8016401c5a33be04be441", email=" admin@corp.com ")])

        await ServiceNowConnector._register_authenticated_identity(conn)

        assert _registered(conn) == ("admin@corp.com", "6816f79cc0a8016401c5a33be04be441")
        query = conn._get_fresh_datasource.return_value.get_now_table_tableName.await_args.kwargs
        assert (query["tableName"], query["sysparm_query"]) == ("sys_user", "sys_id=javascript:gs.getUserID()")

    @pytest.mark.parametrize(
        "rows",
        [[], [TableAPIRecord(sys_id="a1", email="a@corp.com"), TableAPIRecord(sys_id="b2", email="b@corp.com")]],
        ids=["no-row", "condition-dropped"],
    )
    async def test_anything_but_exactly_one_row_links_nobody(self, rows: list) -> None:
        conn = self._conn(rows)

        await ServiceNowConnector._register_authenticated_identity(conn)

        assert _registered(conn) == (None, None)

    async def test_a_row_without_an_email_still_reaches_the_helper(self) -> None:
        conn = self._conn([TableAPIRecord(sys_id="6816f79cc0a8016401c5a33be04be441")])

        await ServiceNowConnector._register_authenticated_identity(conn)

        assert _registered(conn) == (None, "6816f79cc0a8016401c5a33be04be441")


class TestSalesforce:
    USER_ID = "005x00000012Q9PAAU"
    IDENTITY_URL = f"https://login.salesforce.com/id/00Dx0000000BV7zEAG/{USER_ID}"

    def _conn(self, credentials: dict, record: dict | None = None) -> MagicMock:
        conn = _connector(
            SalesforceConnector,
            config_service=MagicMock(),
            _fetch_first_record=AsyncMock(return_value=record),
            _users_listing_complete=True,
            _listed_user_emails=set(),
        )
        conn.config_service.get_config = AsyncMock(return_value={"credentials": credentials})
        conn._authenticated_user_id = partial(SalesforceConnector._authenticated_user_id, conn)
        return conn

    async def test_reads_the_user_the_tokens_identity_url_names(self) -> None:
        conn = self._conn({"id": self.IDENTITY_URL}, {"Id": self.USER_ID, "Email": "admin@corp.com"})

        await SalesforceConnector._register_authenticated_identity(conn, "59.0")

        assert _registered(conn) == ("admin@corp.com", self.USER_ID)
        assert f"WHERE Id = '{self.USER_ID}'" in conn._fetch_first_record.await_args.kwargs["soql"]

    async def test_a_token_stored_before_the_identity_url_was_kept_syncs_as_before_without_a_warning(self) -> None:
        conn = self._conn({"access_token": "tok"})

        await SalesforceConnector._register_authenticated_identity(conn, "59.0")

        conn._fetch_first_record.assert_not_awaited()
        conn.register_authenticated_source_user.assert_not_awaited()

    async def test_an_id_that_is_not_a_salesforce_id_never_reaches_soql(self) -> None:
        conn = self._conn({"id": "https://login.salesforce.com/id/00D/005' OR Email != '"})

        await SalesforceConnector._register_authenticated_identity(conn, "59.0")

        conn._fetch_first_record.assert_not_awaited()
        conn.register_authenticated_source_user.assert_not_awaited()


class TestZoom:
    def _conn(self, response: object = None, error: Exception | None = None) -> MagicMock:
        datasource = MagicMock()
        datasource.user = AsyncMock(return_value=response)
        return _connector(
            ZoomConnector, _users_listing_complete=True,
            _get_fresh_datasource=AsyncMock(return_value=datasource, side_effect=error),
        )

    async def test_registers_the_user_who_authorized_the_app_as_the_user_sync_stores_it(self) -> None:
        conn = self._conn(SimpleNamespace(success=True, data={"id": " KDcuGIm1QgePTO8WbOqwIQ ", "email": " Admin@Corp.com "}))

        await ZoomConnector._register_authenticated_identity(conn)

        conn._get_fresh_datasource.return_value.user.assert_awaited_once_with("me")
        assert _registered(conn) == ("admin@corp.com", "KDcuGIm1QgePTO8WbOqwIQ")

    async def test_a_connection_authorized_before_the_scope_was_added_warns_with_zooms_reason(self) -> None:
        error = {"code": 4711, "message": "Invalid access token, does not contain scopes:[user:read:user:admin]."}
        conn = self._conn(SimpleNamespace(success=False, data=error, error=None, message="Failed with status 400"))

        await ZoomConnector._register_authenticated_identity(conn)

        conn.register_authenticated_source_user.assert_not_awaited()
        conn.logger.warning.assert_called_once()
        assert error in conn.logger.warning.call_args.args

    async def test_a_connector_that_is_not_ready_never_reaches_the_link(self) -> None:
        conn = self._conn(error=RuntimeError("Zoom connector is not ready"))

        await ZoomConnector._register_authenticated_identity(conn)

        conn.register_authenticated_source_user.assert_not_awaited()

    async def test_the_full_pass_is_told_whether_the_user_listing_was_complete(self) -> None:
        conn = self._conn(SimpleNamespace(success=True, data={"id": "z1", "email": "admin@corp.com"}))
        conn._users_listing_complete = False

        await ZoomConnector._register_authenticated_identity(conn)

        conn.register_authenticated_source_user.assert_awaited_once()
        assert conn.register_authenticated_source_user.await_args.kwargs == {"listing_complete": False}

    def test_the_oauth_app_asks_for_the_scope_users_me_needs(self) -> None:
        oauth = ZoomConnector._connector_metadata["config"]["auth"]["oauthConfigs"]["OAUTH"]
        assert "user:read:user:admin" in oauth["scopes"]


class _ZoomStop(Exception):
    pass


class TestZoomSyncOrder:
    def _conn(self, order: list[str], users_error: Exception | None = None) -> MagicMock:
        def step(name: str, result: object = None, error: Exception | None = None) -> AsyncMock:
            async def _run(*_: object, **__: object) -> object:
                order.append(name)
                if error:
                    raise error
                return result
            return AsyncMock(side_effect=_run)

        conn = _connector(ZoomConnector, data_source=MagicMock(), _users_listing_complete=True)
        conn.drop_authenticator_link_if_listed = step("drop")
        conn._load_filters = step("filters")
        conn._today = MagicMock(return_value=MagicMock())
        conn._date_str = MagicMock(return_value="2026-01-01")
        conn._list_users = step("users", [MagicMock()], users_error)
        conn._build_app_users = MagicMock(return_value=[MagicMock()])
        conn.data_entities_processor = MagicMock(on_new_app_users=step("write"))
        conn._register_authenticated_identity = step("register", error=_ZoomStop())
        return conn

    async def test_removal_pass_runs_before_the_user_listing_and_full_pass_after_the_users_are_written(self) -> None:
        order: list[str] = []
        conn = self._conn(order)

        with pytest.raises(_ZoomStop):
            await ZoomConnector.run_sync(conn)

        assert order == ["drop", "filters", "users", "write", "register"]

    async def test_no_full_pass_when_the_user_listing_raises(self) -> None:
        order: list[str] = []
        conn = self._conn(order, users_error=RuntimeError("zoom down"))

        with pytest.raises(RuntimeError):
            await ZoomConnector.run_sync(conn)

        assert "register" not in order

    async def test_a_stale_complete_flag_is_reset_before_the_listing_runs(self) -> None:
        conn = self._conn([])
        conn._users_listing_complete = True
        conn._list_users = AsyncMock(side_effect=RuntimeError("zoom down"))

        with pytest.raises(RuntimeError):
            await ZoomConnector.run_sync(conn)

        assert conn._users_listing_complete is False

    async def test_a_failed_page_is_an_incomplete_listing(self) -> None:
        ok = MagicMock(success=True, data={"users": [], "next_page_token": "t"})
        failed = MagicMock(success=False, data=None, message="429")
        conn = _connector(ZoomConnector, _users_listing_complete=True)
        conn._get_fresh_datasource = AsyncMock(return_value=MagicMock(users=AsyncMock(side_effect=[ok, failed])))

        await ZoomConnector._list_users(conn)

        assert conn._users_listing_complete is False

    async def test_a_page_with_no_data_is_an_incomplete_listing(self) -> None:
        conn = _connector(ZoomConnector, _users_listing_complete=True)
        datasource = MagicMock(users=AsyncMock(return_value=MagicMock(success=True, data={})))
        conn._get_fresh_datasource = AsyncMock(return_value=datasource)

        await ZoomConnector._list_users(conn)

        assert conn._users_listing_complete is False

    async def test_a_listing_read_to_its_last_page_is_complete(self) -> None:
        pages = [
            MagicMock(success=True, data={"users": [], "next_page_token": "t"}),
            MagicMock(success=True, data={"users": [{"id": "u1", "email": "a@corp.com"}], "next_page_token": ""}),
        ]
        conn = _connector(ZoomConnector, _users_listing_complete=False)
        conn._get_fresh_datasource = AsyncMock(return_value=MagicMock(users=AsyncMock(side_effect=pages)))

        await ZoomConnector._list_users(conn)

        assert conn._users_listing_complete is True

    async def test_an_empty_listing_is_incomplete(self) -> None:
        empty = MagicMock(success=True, data={"users": [], "next_page_token": ""})
        conn = _connector(ZoomConnector, _users_listing_complete=False)
        conn._get_fresh_datasource = AsyncMock(return_value=MagicMock(users=AsyncMock(return_value=empty)))

        await ZoomConnector._list_users(conn)

        assert conn._users_listing_complete is False

    async def test_a_listing_that_raises_is_an_incomplete_listing(self) -> None:
        conn = _connector(ZoomConnector, _users_listing_complete=True)
        conn._get_fresh_datasource = AsyncMock(return_value=MagicMock(users=AsyncMock(side_effect=OSError("reset"))))

        with pytest.raises(OSError):
            await ZoomConnector._list_users(conn)

        assert conn._users_listing_complete is False


@pytest.mark.parametrize("connector_cls", [GoogleDriveTeamConnector, GoogleGmailTeamConnector])
class TestGoogleWorkspace:
    def _conn(self, connector_cls: type, admin: dict | None = None, error: Exception | None = None) -> MagicMock:
        admin_data_source = MagicMock()
        admin_data_source.users_get = AsyncMock(return_value=admin, side_effect=error)
        return _connector(
            connector_cls,
            config={"credentials": {"auth": {"adminEmail": "admin@corp.com"}}},
            admin_data_source=admin_data_source,
            _users_listing_complete=True,
            _listed_aliases=set(),
        )

    async def test_registers_the_admin_the_service_account_acts_as(self, connector_cls: type) -> None:
        # adminEmail may be an alias; the user sync keys the admin by primaryEmail and id
        conn = self._conn(connector_cls, admin={"id": "103458374651823091", "primaryEmail": "it-admin@corp.com"})

        await connector_cls._register_authenticated_identity(conn)

        conn.admin_data_source.users_get.assert_awaited_once_with(userKey="admin@corp.com")
        assert _registered(conn) == ("it-admin@corp.com", "103458374651823091")

    async def test_a_failed_directory_read_never_reaches_the_link(self, connector_cls: type) -> None:
        conn = self._conn(connector_cls, error=RuntimeError("Not Authorized to access this resource/api"))

        await connector_cls._register_authenticated_identity(conn)

        conn.register_authenticated_source_user.assert_not_awaited()

    async def test_the_registration_passes_the_listing_state_and_the_listed_aliases(self, connector_cls: type) -> None:
        conn = self._conn(connector_cls, admin={"id": "1", "primaryEmail": "admin@corp.com"})
        conn._users_listing_complete = False
        conn._listed_aliases = {"a@corp.com"}

        await connector_cls._register_authenticated_identity(conn)

        assert conn.register_authenticated_source_user.await_args.kwargs == {
            "listing_complete": False,
            "extra_present_emails": {"a@corp.com"},
        }


_ENTRY = {GoogleDriveTeamConnector: "_run_sync_steps", GoogleGmailTeamConnector: "run_sync"}


class _Stop(Exception):
    pass


@pytest.mark.parametrize("connector_cls", [GoogleDriveTeamConnector, GoogleGmailTeamConnector])
class TestGoogleWorkspaceSyncOrder:
    def _conn(self, connector_cls: type, order: list[str], users_error: Exception | None = None) -> MagicMock:
        def step(name: str, error: Exception | None = None) -> AsyncMock:
            async def _run(*_: object, **__: object) -> None:
                order.append(name)
                if error:
                    raise error
            return AsyncMock(side_effect=_run)

        conn = _connector(connector_cls, config_service=MagicMock())
        conn.drop_authenticator_link_if_listed = step("drop")
        conn._register_authenticated_identity = step("register")
        conn._sync_users = step("users", users_error)
        conn._sync_user_groups = step("groups", _Stop())
        return conn

    async def _run(self, connector_cls: type, conn: MagicMock, expected: type[Exception] = _Stop) -> None:
        module = sys.modules[connector_cls.__module__]
        loaded = AsyncMock(return_value=(MagicMock(), MagicMock()))
        with patch.object(module, "load_connector_filters", loaded), pytest.raises(expected):
            await getattr(connector_cls, _ENTRY[connector_cls])(conn)

    async def test_removal_pass_runs_before_the_user_sync(self, connector_cls: type) -> None:
        order: list[str] = []
        conn = self._conn(connector_cls, order)

        await self._run(connector_cls, conn)

        assert order.index("drop") < order.index("users")

    async def test_full_pass_runs_after_the_user_sync(self, connector_cls: type) -> None:
        order: list[str] = []
        conn = self._conn(connector_cls, order)

        await self._run(connector_cls, conn)

        assert order == ["drop", "users", "register", "groups"]

    async def test_no_full_pass_when_the_user_sync_raises(self, connector_cls: type) -> None:
        order: list[str] = []
        conn = self._conn(connector_cls, order, users_error=RuntimeError("directory down"))

        await self._run(connector_cls, conn, RuntimeError)

        assert order == ["drop", "users"]

    async def test_the_removal_pass_survives_a_failing_filter_load(self, connector_cls: type) -> None:
        order: list[str] = []
        conn = self._conn(connector_cls, order)
        module = sys.modules[connector_cls.__module__]
        failing = AsyncMock(side_effect=RuntimeError("config down"))

        with patch.object(module, "load_connector_filters", failing), pytest.raises(RuntimeError):
            await getattr(connector_cls, _ENTRY[connector_cls])(conn)

        assert order == ["drop"]


_APP_NAME = {GoogleDriveTeamConnector: "DRIVE WORKSPACE", GoogleGmailTeamConnector: "GMAIL WORKSPACE"}


def _directory_conn(connector_cls: type, pages: list[dict]) -> MagicMock:
    admin_data_source = MagicMock()
    admin_data_source.users_list = AsyncMock(side_effect=pages)
    return _connector(
        connector_cls,
        config={"credentials": {"auth": {"adminEmail": "admin@corp.com"}}},
        admin_data_source=admin_data_source,
        connector_name=_APP_NAME[connector_cls],
        data_entities_processor=MagicMock(on_new_app_users=AsyncMock()),
        _user_status=MagicMock(
            forget_fingerprint=AsyncMock(), remember_fingerprint=AsyncMock(), fingerprint_is_remembered=AsyncMock()
        ),
        _sync_ledger=MagicMock(),
        _hold_back_on_cut_listing=AsyncMock(),
        _apply_user_status=AsyncMock(),
        _write_audience_groups=AsyncMock(),
        synced_users=[],
        synced_user_emails=set(),
    )


def _row(email: str, uid: str, aliases: list[str] | None = None, hidden: list[str] | None = None) -> dict:
    return {"id": uid, "primaryEmail": email, "aliases": aliases or [], "nonEditableAliases": hidden or []}


class TestGoogleWorkspaceListing:
    async def test_an_empty_page_with_a_next_token_is_an_incomplete_listing(self) -> None:
        conn = _directory_conn(GoogleGmailTeamConnector, [{"users": [], "nextPageToken": "more"}])

        await GoogleGmailTeamConnector._sync_users(conn)

        assert conn._users_listing_complete is False

    async def test_a_complete_gmail_listing_is_complete(self) -> None:
        conn = _directory_conn(GoogleGmailTeamConnector, [{"users": [_row("alice@corp.com", "1")]}])

        await GoogleGmailTeamConnector._sync_users(conn)

        assert conn._users_listing_complete is True

    async def test_a_cut_listing_is_incomplete(self) -> None:
        conn = _directory_conn(
            GoogleDriveTeamConnector,
            [{"users": [], "nextPageToken": "more"}, {"users": [_row("alice@corp.com", "1")]}],
        )
        conn._listing_cut = False

        await GoogleDriveTeamConnector._sync_users(conn)

        assert conn._listing_cut is True
        assert conn._users_listing_complete is False

    async def test_an_empty_page_after_users_is_an_incomplete_listing(self) -> None:
        conn = _directory_conn(
            GoogleGmailTeamConnector, [{"users": [_row("alice@corp.com", "1")], "nextPageToken": "t"}, {"users": []}]
        )

        await GoogleGmailTeamConnector._sync_users(conn)

        assert conn._users_listing_complete is False

    @pytest.mark.parametrize("connector_cls", [GoogleDriveTeamConnector, GoogleGmailTeamConnector])
    async def test_an_empty_listing_is_incomplete(self, connector_cls: type) -> None:
        conn = _directory_conn(connector_cls, [{"users": []}])
        conn._listing_cut = False

        await connector_cls._sync_users(conn)

        assert conn._users_listing_complete is False

    @pytest.mark.parametrize("connector_cls", [GoogleDriveTeamConnector, GoogleGmailTeamConnector])
    async def test_a_failed_listing_does_not_keep_the_previous_syncs_result(self, connector_cls: type) -> None:
        conn = _directory_conn(connector_cls, [{"users": [_row("alice@corp.com", "1", ["a@corp.com"])]}, RuntimeError("down")])
        conn._listing_cut = False
        await connector_cls._sync_users(conn)
        assert conn._users_listing_complete is True

        with pytest.raises(RuntimeError):
            await connector_cls._sync_users(conn)

        assert conn._users_listing_complete is False
        assert conn._listed_aliases == set()

    @pytest.mark.parametrize("connector_cls", [GoogleDriveTeamConnector, GoogleGmailTeamConnector])
    async def test_a_row_that_fails_to_process_makes_the_listing_incomplete(self, connector_cls: type) -> None:
        broken = {"id": "2", "primaryEmail": "bob@corp.com", "name": "not-a-mapping"}
        conn = _directory_conn(connector_cls, [{"users": [_row("alice@corp.com", "1"), broken]}])
        conn._listing_cut = False

        await connector_cls._sync_users(conn)

        assert conn._users_listing_complete is False

    @pytest.mark.parametrize("connector_cls", [GoogleDriveTeamConnector, GoogleGmailTeamConnector])
    async def test_a_row_without_an_email_does_not_make_the_listing_incomplete(self, connector_cls: type) -> None:
        conn = _directory_conn(connector_cls, [{"users": [_row("alice@corp.com", "1"), {"id": "9"}]}])
        conn._listing_cut = False

        await connector_cls._sync_users(conn)

        assert conn._users_listing_complete is True

    @pytest.mark.parametrize("connector_cls", [GoogleDriveTeamConnector, GoogleGmailTeamConnector])
    async def test_aliases_of_listed_users_are_passed_but_not_the_admins(self, connector_cls: type) -> None:
        conn = _directory_conn(
            connector_cls,
            [{
                "users": [
                    _row("alice@corp.com", "1", ["Alice.A@corp.com "], ["alice@old.corp.com"]),
                    _row("it-admin@corp.com", "2", ["admin@corp.com"], ["it@corp.com"]),
                ]
            }],
        )
        conn._listing_cut = False

        await connector_cls._sync_users(conn)

        assert conn._listed_aliases == {"alice.a@corp.com", "alice@old.corp.com"}

class _Kv:
    """The configuration store as a dict, shared by connector instances like the real one."""

    def __init__(
        self, values: dict | None = None, error: Exception | None = None, *, writes_fail: bool = False,
    ) -> None:
        self.values = dict(values or {})
        self.error = error
        self.writes_fail = writes_fail

    async def get_config(self, key: str, **_: object) -> object:
        if self.error:
            raise self.error
        return self.values.get(key)

    async def set_config(self, key: str, value: object) -> bool:
        # The real service answers False on a store error instead of raising.
        if self.writes_fail:
            return False
        self.values[key] = value
        return True

    async def delete_config(self, key: str) -> bool:
        return self.values.pop(key, None) is not None


GRANT_KEY = "/services/connectors/connector-1/authenticatorGrant"


def _with_grant_record(conn: MagicMock, kv: _Kv) -> MagicMock:
    conn.config_service = kv
    for name in (
        "decide_authenticator_grant", "authenticator_grant_kept", "remember_authenticator_grant", "_marker_may_list",
    ):
        setattr(conn, name, partial(getattr(BaseConnector, name), conn))
    return conn


class TestSlackWorkspace:
    def _conn(
        self, auth: dict | None = None, error: Exception | None = None, authenticator: str | None = None,
        kv: _Kv | None = None,
    ) -> MagicMock:
        datasource = MagicMock()
        datasource.auth_test = AsyncMock(return_value=SimpleNamespace(success=True, data=auth), side_effect=error)
        conn = _connector(
            SlackConnector,
            _fresh_datasource=AsyncMock(return_value=datasource),
            user_id_to_email_cache={"U0ALICE": "alice@corp.com"},
            _users_read_failed=False, _users_listing_cut=False, _listed_user_emails=set(),
            authenticator_email=AsyncMock(return_value=authenticator),
            data_entities_processor=MagicMock(unlink_authenticator=AsyncMock()),
        )
        return _with_grant_record(conn, kv or _Kv())

    async def test_a_user_token_registers_the_account_it_belongs_to(self) -> None:
        conn = self._conn({"user_id": "U0ALICE"})

        await SlackConnector._register_authenticated_identity(conn)

        assert _registered(conn) == ("alice@corp.com", "U0ALICE")
        conn.data_entities_processor.unlink_authenticator.assert_not_awaited()

    async def test_a_bot_token_links_nobody_and_drops_an_earlier_link(self) -> None:
        conn = self._conn({"user_id": "U0BOTUSER", "bot_id": "B0BOT"})

        await SlackConnector._register_authenticated_identity(conn)

        conn.register_authenticated_source_user.assert_not_awaited()
        conn.data_entities_processor.unlink_authenticator.assert_awaited_once_with("connector-1")

    async def test_a_failed_auth_test_never_reaches_the_link(self) -> None:
        conn = self._conn(error=RuntimeError("invalid_auth"))

        await SlackConnector._register_authenticated_identity(conn)

        conn.register_authenticated_source_user.assert_not_awaited()
        conn.data_entities_processor.unlink_authenticator.assert_not_awaited()

    async def test_the_authenticator_is_granted_read(self) -> None:
        conn = self._conn(authenticator="admin@corp.com")

        grant = await SlackConnector._authenticator_grant(conn)

        assert (grant.email, grant.entity_type, grant.type) == ("admin@corp.com", EntityType.USER, PermissionType.READ)

    async def test_a_listed_authenticator_gets_no_grant_even_when_suspended(self) -> None:
        conn = self._conn(authenticator="Admin@corp.com")
        conn._listed_user_emails = {"admin@corp.com"}

        assert await SlackConnector._authenticator_grant(conn) is None

    @pytest.mark.parametrize("flag", ["_users_read_failed", "_users_listing_cut"])
    async def test_an_incomplete_listing_adds_no_new_grant(self, flag: str) -> None:
        conn = self._conn(authenticator="admin@corp.com")
        setattr(conn, flag, True)

        assert await SlackConnector._authenticator_grant(conn) is None

    @pytest.mark.parametrize("granted", [True, False])
    async def test_a_complete_listing_stores_the_decision(self, granted: bool) -> None:
        kv = _Kv()
        conn = self._conn(authenticator="Admin@corp.com", kv=kv)
        conn._listed_user_emails = set() if granted else {"admin@corp.com"}

        await SlackConnector._authenticator_grant(conn)

        assert kv.values[GRANT_KEY] == {"email": "admin@corp.com", "granted": granted}

    @pytest.mark.parametrize("flag", ["_users_read_failed", "_users_listing_cut"])
    async def test_after_a_restart_an_incomplete_listing_keeps_the_stored_grant(self, flag: str) -> None:
        kv = _Kv()
        await SlackConnector._authenticator_grant(self._conn(authenticator="admin@corp.com", kv=kv))
        fresh = self._conn(authenticator="admin@corp.com", kv=kv)
        setattr(fresh, flag, True)

        grant = await SlackConnector._authenticator_grant(fresh)

        assert grant is not None and grant.email == "admin@corp.com"

    @pytest.mark.parametrize("stored", [None, {"email": "admin@corp.com", "granted": False}])
    async def test_an_incomplete_listing_with_no_stored_grant_grants_nothing(self, stored: object) -> None:
        conn = self._conn(authenticator="admin@corp.com", kv=_Kv({GRANT_KEY: stored}))
        conn._users_listing_cut = True

        assert await SlackConnector._authenticator_grant(conn) is None

    async def test_a_stored_grant_for_another_authenticator_is_not_this_ones(self) -> None:
        conn = self._conn(authenticator="admin@corp.com", kv=_Kv({GRANT_KEY: {"email": "old@corp.com", "granted": True}}))
        conn._users_listing_cut = True

        assert await SlackConnector._authenticator_grant(conn) is None

    async def test_a_withdrawn_grant_whose_write_fails_is_not_revived_by_an_incomplete_listing(self) -> None:
        kv = _Kv({GRANT_KEY: {"email": "admin@corp.com", "granted": True}}, writes_fail=True)
        conn = self._conn(authenticator="admin@corp.com", kv=kv)
        conn._listed_user_emails = {"admin@corp.com"}
        await SlackConnector._authenticator_grant(conn)
        fresh = self._conn(authenticator="admin@corp.com", kv=kv)
        fresh._users_listing_cut = True

        assert await SlackConnector._authenticator_grant(fresh) is None

    async def test_an_incomplete_listing_that_names_the_authenticator_stores_no_grant(self) -> None:
        kv = _Kv({GRANT_KEY: {"email": "admin@corp.com", "granted": True}})
        conn = self._conn(authenticator="admin@corp.com", kv=kv)
        conn._users_listing_cut = True
        conn._listed_user_emails = {"admin@corp.com"}

        assert await SlackConnector._authenticator_grant(conn) is None
        assert kv.values[GRANT_KEY] == {"email": "admin@corp.com", "granted": False}

    async def test_two_incomplete_listings_the_first_naming_the_authenticator_both_grant_nothing(self) -> None:
        kv = _Kv({GRANT_KEY: {"email": "admin@corp.com", "granted": True}})
        first = self._conn(authenticator="admin@corp.com", kv=kv)
        first._users_listing_cut = True
        first._listed_user_emails = {"admin@corp.com"}
        second = self._conn(authenticator="admin@corp.com", kv=kv)
        second._users_read_failed = True

        assert await SlackConnector._authenticator_grant(first) is None
        assert await SlackConnector._authenticator_grant(second) is None

    async def test_a_stored_grant_is_not_kept_for_an_authenticator_the_marker_lists(self) -> None:
        kv = _Kv({
            GRANT_KEY: {"email": "admin@corp.com", "granted": True},
            "/services/connectors/connector-1/listedAuthenticators": ["admin-key"],
        })
        conn = self._conn(authenticator="admin@corp.com", kv=kv)
        conn.created_by = "admin-id"
        conn.data_entities_processor.get_app_authenticator_user = AsyncMock(
            return_value={"_key": "admin-key", "email": "admin@corp.com"},
        )
        conn._users_listing_cut = True

        assert await SlackConnector._authenticator_grant(conn) is None

    @pytest.mark.parametrize(("listed", "withheld"), [({"admin@corp.com"}, "admin@corp.com"), (set(), None)])
    async def test_the_authenticator_a_listing_names_is_kept_for_the_channel_writes(
        self, listed: set[str], withheld: str | None,
    ) -> None:
        conn = self._conn(authenticator="admin@corp.com")
        conn._listed_user_emails = listed

        await SlackConnector._authenticator_grant(conn)

        assert conn._withheld_authenticator_email == withheld

    async def test_a_failed_read_of_the_stored_grant_grants_nothing(self) -> None:
        conn = self._conn(authenticator="admin@corp.com", kv=_Kv(error=RuntimeError("kv down")))
        conn._users_read_failed = True

        assert await SlackConnector._authenticator_grant(conn) is None
        conn.logger.warning.assert_called_once()

    async def test_an_incomplete_listing_that_shows_the_authenticator_drops_the_grant(self) -> None:
        conn = self._conn(authenticator="admin@corp.com", kv=_Kv({GRANT_KEY: {"email": "admin@corp.com", "granted": True}}))
        conn._users_listing_cut = True
        conn._listed_user_emails = {"admin@corp.com"}

        assert await SlackConnector._authenticator_grant(conn) is None

    async def test_no_authenticator_means_no_grant(self) -> None:
        conn = self._conn(authenticator=None)

        assert await SlackConnector._authenticator_grant(conn) is None
        conn.logger.warning.assert_called_once()

    def test_the_grant_joins_a_channel_once(self) -> None:
        grant = Permission(email="admin@corp.com", entity_type=EntityType.USER, type=PermissionType.READ)
        conn = self._conn()
        conn._authenticator_permission = grant
        member = Permission(email="bob@corp.com", entity_type=EntityType.USER, type=PermissionType.READ)
        same = Permission(email="Admin@Corp.com", entity_type=EntityType.USER, type=PermissionType.READ)

        assert SlackConnector._with_authenticator(conn, [member]) == [member, grant]
        assert SlackConnector._with_authenticator(conn, [member, same]) == [member, same]
        conn._authenticator_permission = None
        assert SlackConnector._with_authenticator(conn, [member]) == [member]


# ---------------------------------------------------------------------------
# Connectors that already registered
# ---------------------------------------------------------------------------


class TestGitLab:
    def _conn(self, result: object = None, error: Exception | None = None) -> MagicMock:
        conn = _connector(GitLabConnector, data_source=MagicMock(), runtime=MagicMock(),
                          _users_listing_complete=True, _listed_user_emails=set())
        conn.runtime.ds_call = AsyncMock(return_value=result, side_effect=error)
        return conn

    async def test_registers_the_token_owner_by_numeric_id(self) -> None:
        conn = self._conn(SimpleNamespace(success=True, data=SimpleNamespace(email=" dev@corp.com ", public_email=None, id=42)))

        await GitLabConnector._register_authenticated_identity(conn)

        assert _registered(conn) == ("dev@corp.com", "42")

    async def test_falls_back_to_the_public_email(self) -> None:
        conn = self._conn(SimpleNamespace(success=True, data=SimpleNamespace(email=None, public_email="dev@public.com", id=42)))

        await GitLabConnector._register_authenticated_identity(conn)

        assert _registered(conn) == ("dev@public.com", "42")

    async def test_a_failed_read_never_reaches_the_link(self) -> None:
        conn = self._conn(error=RuntimeError("401 Unauthorized"))

        await GitLabConnector._register_authenticated_identity(conn)

        conn.register_authenticated_source_user.assert_not_awaited()


class TestGitHubTeams:
    def _conn(self, result: object = None, error: Exception | None = None) -> MagicMock:
        # users: the user sync's email binding, which the link prefers (none bound here).
        conn = _connector(GitHubTeamsConnector, data_source=MagicMock(), runtime=MagicMock(),
                          users=MagicMock(resolved_email=MagicMock(return_value=None)))
        conn.runtime.ds_call = AsyncMock(return_value=result, side_effect=error)
        return conn

    async def test_registers_the_authenticated_user_by_numeric_id(self) -> None:
        conn = self._conn(SimpleNamespace(success=True, data=SimpleNamespace(email=" dev@corp.com ", id=583231)))

        await GitHubTeamsConnector._register_authenticated_identity(conn)

        assert _registered(conn) == ("dev@corp.com", "583231")

    async def test_prefers_the_email_the_user_sync_bound(self) -> None:
        """The grants carry that email; GET /user gives only the public one, often none."""
        conn = self._conn(SimpleNamespace(success=True, data=SimpleNamespace(email=None, id=583231)))
        conn.users.resolved_email.return_value = "dev@corp.com"

        await GitHubTeamsConnector._register_authenticated_identity(conn)

        assert _registered(conn) == ("dev@corp.com", "583231")

    async def test_a_failed_read_never_reaches_the_link(self) -> None:
        conn = self._conn(error=RuntimeError("Bad credentials"))

        await GitHubTeamsConnector._register_authenticated_identity(conn)

        conn.register_authenticated_source_user.assert_not_awaited()


class TestLinear:
    def _conn(self, response: object = None, error: Exception | None = None) -> MagicMock:
        datasource = MagicMock()
        datasource.viewer = AsyncMock(return_value=response, side_effect=error)
        return _connector(
            LinearConnector, data_source=MagicMock(), _get_fresh_datasource=AsyncMock(return_value=datasource),
            _users_listing_complete=True, _listed_user_emails=set(),
        )

    async def test_registers_the_viewer(self) -> None:
        conn = self._conn(SimpleNamespace(success=True, data={"viewer": {"id": "a1b2c3", "email": " dev@corp.com "}}))

        await LinearConnector._register_authenticated_identity(conn)

        assert _registered(conn) == ("dev@corp.com", "a1b2c3")

    async def test_a_failed_read_never_reaches_the_link(self) -> None:
        conn = self._conn(error=RuntimeError("Authentication required"))

        await LinearConnector._register_authenticated_identity(conn)

        conn.register_authenticated_source_user.assert_not_awaited()


class TestJiraDataCenter:
    def _conn(self, status: int, profile: dict | None = None) -> MagicMock:
        datasource = MagicMock()
        datasource.get_current_user_v2 = AsyncMock(return_value=SimpleNamespace(status=status))
        return _connector(
            JiraDataCenterConnector,
            data_source=MagicMock(),
            _get_fresh_datasource=AsyncMock(return_value=datasource),
            _safe_json_parse=MagicMock(return_value=profile),
            _own_key=None,
        )

    async def test_data_center_identifies_the_account_by_its_key(self) -> None:
        conn = self._conn(HttpStatusCode.OK.value, {"emailAddress": " dev@corp.com ", "key": "JIRAUSER10100", "name": "dev"})

        assert await JiraDataCenterConnector._read_own_account(conn) == ("dev@corp.com", "JIRAUSER10100")
        conn.register_authenticated_source_user.assert_not_awaited()

    async def test_own_key_is_set_before_eligibility(self) -> None:
        conn = self._conn(HttpStatusCode.OK.value, {"emailAddress": "dev@corp.com", "key": "JIRAUSER10100"})

        await JiraDataCenterConnector._read_own_account(conn)

        assert conn._own_key == "JIRAUSER10100"

    async def test_a_refused_read_gives_nothing_to_link(self) -> None:
        conn = self._conn(HttpStatusCode.UNAUTHORIZED.value)

        assert await JiraDataCenterConnector._read_own_account(conn) == (None, None)
        conn.register_authenticated_source_user.assert_not_awaited()


class TestConfluenceDataCenter:
    def _conn(self, current: MagicMock, detail: MagicMock | None = None) -> MagicMock:
        datasource = MagicMock()
        datasource.get_current_user_v1 = AsyncMock(return_value=current)
        datasource.get_user_v1 = AsyncMock(return_value=detail)
        return _connector(
            ConfluenceDataCenterConnector,
            data_source=MagicMock(),
            _get_fresh_datasource=AsyncMock(return_value=datasource),
            _users_listing_complete=True,
            _listed_user_emails={"dev@corp.com"},
        )

    async def test_registers_the_current_user_by_user_key(self) -> None:
        conn = self._conn(_json_response(200, {"userKey": "8a7f808a", "email": "dev@corp.com"}))

        await ConfluenceDataCenterConnector._register_authenticated_identity(conn)

        assert _registered(conn) == ("dev@corp.com", "8a7f808a")

    async def test_reads_the_email_from_the_profile_when_current_user_omits_it(self) -> None:
        conn = self._conn(
            _json_response(200, {"userKey": "8a7f808a", "username": "dev"}),
            _json_response(200, {"email": " dev@corp.com "}),
        )

        await ConfluenceDataCenterConnector._register_authenticated_identity(conn)

        conn._get_fresh_datasource.return_value.get_user_v1.assert_awaited_once_with(username="dev")
        assert _registered(conn) == ("dev@corp.com", "8a7f808a")


class TestJiraCloud:
    """Cloud reads the account once at init; run_sync hands the cached pair over."""

    def test_caches_the_myself_account(self) -> None:
        profile = {"emailAddress": " dev@corp.com ", "accountId": "5b10ac8d82e05b22cc7d4ef5"}
        conn = _connector(
            JiraConnector,
            _authenticated_jira_email=None,
            _authenticated_jira_account_id=None,
            _safe_json_parse=MagicMock(return_value=profile),
        )

        JiraConnector._cache_authenticated_jira_profile(conn, SimpleNamespace(status=HttpStatusCode.OK.value))

        assert (conn._authenticated_jira_email, conn._authenticated_jira_account_id) == (
            "dev@corp.com", "5b10ac8d82e05b22cc7d4ef5",
        )

    def test_a_refused_read_caches_nothing(self) -> None:
        conn = _connector(JiraConnector, _authenticated_jira_email=None, _authenticated_jira_account_id=None)

        JiraConnector._cache_authenticated_jira_profile(conn, SimpleNamespace(status=HttpStatusCode.UNAUTHORIZED.value))

        assert (conn._authenticated_jira_email, conn._authenticated_jira_account_id) == (None, None)


class TestConfluenceCloud:
    """Cloud reads the account once at init; run_sync hands the cached pair over."""

    def _conn(self, response: object = None, error: Exception | None = None) -> MagicMock:
        conn = _connector(
            ConfluenceConnector,
            data_source=MagicMock(),
            _authenticated_confluence_email=None,
            _authenticated_confluence_account_id=None,
        )
        conn.data_source.get_current_user_v1 = AsyncMock(return_value=response, side_effect=error)
        return conn

    async def test_caches_the_current_user(self) -> None:
        conn = self._conn(_json_response(200, {"email": " dev@corp.com ", "accountId": "5b10ac8d82e05b22cc7d4ef5"}))

        await ConfluenceConnector._cache_authenticated_confluence_email(conn)

        assert (conn._authenticated_confluence_email, conn._authenticated_confluence_account_id) == (
            "dev@corp.com", "5b10ac8d82e05b22cc7d4ef5",
        )

    async def test_a_failed_read_never_fails_init(self) -> None:
        conn = self._conn(error=RuntimeError("401 Unauthorized"))

        await ConfluenceConnector._cache_authenticated_confluence_email(conn)

        assert (conn._authenticated_confluence_email, conn._authenticated_confluence_account_id) == (None, None)


class TestGoogleDriveIndividual:
    async def test_registers_the_drive_owner_by_permission_id(self) -> None:
        class _StopAfterRegistering(Exception):
            pass

        sync_filters = MagicMock()
        sync_filters.get_value.return_value = None
        conn = _connector(
            GoogleDriveIndividualConnector,
            config_service=MagicMock(),
            drive_data_source=MagicMock(),
            drive_delta_sync_point=AsyncMock(),
            _get_fresh_datasource=AsyncMock(),
            _create_app_user=AsyncMock(side_effect=_StopAfterRegistering),
        )
        # run_sync now delegates to this. On a spec mock that would be an auto mock, so bind the
        # real steps: the registration still happens before the sync is stopped.
        conn._run_sync_steps = partial(GoogleDriveIndividualConnector._run_sync_steps, conn)
        conn.drive_data_source.about_get = AsyncMock(
            return_value={"user": {"emailAddress": "me@gmail.com", "permissionId": "09120187146347425491"}}
        )

        with patch(
            "app.connectors.sources.google.drive.individual.connector.load_connector_filters",
            AsyncMock(return_value=(sync_filters, MagicMock())),
        ), pytest.raises(_StopAfterRegistering):
            await GoogleDriveIndividualConnector.run_sync(conn)

        assert _registered(conn) == ("me@gmail.com", "09120187146347425491")


# ---------------------------------------------------------------------------
# Atlassian: the removal pass first, the full pass after the users are written
# ---------------------------------------------------------------------------


class _AtlassianStop(Exception):
    pass


def _steps(order: list[str]) -> Callable[..., AsyncMock]:
    def step(name: str, result: object = None, error: Exception | None = None) -> AsyncMock:
        async def _run(*_: object, **__: object) -> object:
            order.append(name)
            if error:
                raise error
            return result
        return AsyncMock(side_effect=_run)
    return step


class TestJiraCloudSyncOrder:
    def _conn(self, order: list[str], *, active_users: list, complete: bool = True) -> MagicMock:
        step = _steps(order)
        conn = _connector(
            JiraConnector, data_source=MagicMock(), config_service=MagicMock(),
            _authenticated_jira_email="admin@corp.com", _authenticated_jira_account_id="acc-admin",
            _listed_user_emails=set(), _deactivated_account_ids=set(), _shown_inactive_ids=set(),
            _user_read_unfinished=False, _listed_account_ids={"acc-1"}, _listing_complete_for_link=complete,
        )
        conn._expire_org_gate = step("gate")
        conn.drop_authenticator_link_if_listed = step("drop")
        conn.data_entities_processor = MagicMock(
            get_all_active_users=step("active", active_users), on_new_app_users=step("write"),
        )

        async def fetch(**_: object) -> list:
            order.append("fetch")
            conn._listed_user_emails = {"creator@corp.com"}
            return [MagicMock()]

        conn._fetch_users = AsyncMock(side_effect=fetch)
        conn._directory_read_complete = MagicMock(return_value=True)
        conn._fetch_application_roles_to_groups_mapping = step("roles", {})
        conn._read_level_roles = step("level")
        conn._keep_licensed_users = step("licence", [MagicMock()])
        conn._unlicensed_ids = MagicMock(return_value=set())
        conn._note_newly_listed = step("noted", set())
        conn._withdraw_ineligible_app_users = step("withdraw")
        conn.register_authenticated_source_user = step("register", error=_AtlassianStop())
        return conn

    async def _run(self, conn: MagicMock) -> None:
        with patch(
            "app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
            AsyncMock(return_value=(MagicMock(), MagicMock())),
        ):
            await JiraConnector.run_sync(conn)

    async def test_the_removal_pass_runs_even_when_the_run_stops_for_no_active_users(self) -> None:
        order: list[str] = []
        conn = self._conn(order, active_users=[])

        await self._run(conn)

        assert order == ["drop", "gate", "active"]

    async def test_the_removal_pass_runs_before_the_org_gate_that_can_raise(self) -> None:
        order: list[str] = []
        conn = self._conn(order, active_users=[MagicMock()])
        conn._expire_org_gate = AsyncMock(side_effect=RuntimeError("gate read failed"))

        with pytest.raises(RuntimeError):
            await self._run(conn)

        assert order == ["drop"]

    async def test_an_empty_first_listing_is_not_complete(self) -> None:
        conn = self._conn([], active_users=[MagicMock()], complete=False)

        with pytest.raises(_AtlassianStop):
            await self._run(conn)

        assert conn.register_authenticated_source_user.await_args.kwargs["listing_complete"] is False

    async def test_the_full_pass_runs_after_the_users_are_written_and_withdrawn(self) -> None:
        order: list[str] = []
        conn = self._conn(order, active_users=[MagicMock()])

        with pytest.raises(_AtlassianStop):
            await self._run(conn)

        assert order.index("drop") < order.index("fetch")
        assert order.index("write") < order.index("withdraw") < order.index("register")
        kwargs = conn.register_authenticated_source_user.await_args.kwargs
        assert kwargs["extra_present_emails"] == {"creator@corp.com"}

    @pytest.mark.parametrize("complete", [True, False])
    async def test_listing_complete_follows_the_directory_read(self, complete: bool) -> None:
        conn = self._conn([], active_users=[MagicMock()], complete=complete)

        with pytest.raises(_AtlassianStop):
            await self._run(conn)

        assert conn.register_authenticated_source_user.await_args.kwargs["listing_complete"] is complete

    async def test_the_emails_of_a_previous_run_are_not_this_runs_listing(self) -> None:
        conn = self._conn([], active_users=[MagicMock()])
        conn._listed_user_emails = {"stale@corp.com"}
        conn._fetch_users = AsyncMock(return_value=[MagicMock()])

        with pytest.raises(_AtlassianStop):
            await self._run(conn)

        assert conn.register_authenticated_source_user.await_args.kwargs["extra_present_emails"] == set()

    async def test_every_listed_account_counts_as_listed_whatever_its_status(self) -> None:
        raw = [
            {"accountId": "a1", "accountType": "atlassian", "active": True, "emailAddress": "Live@corp.com"},
            {"accountId": "a2", "accountType": "atlassian", "active": False, "emailAddress": "Gone@Corp.com"},
            {"accountId": "a3", "accountType": "app", "active": True, "emailAddress": "bot@corp.com"},
            {"accountId": "a4", "accountType": "atlassian", "active": True},
        ]
        conn = _connector(
            JiraConnector, data_source=MagicMock(), connector_name="JIRA", _user_bulk_forbidden=False,
            _user_read_unfinished=False, _listed_user_emails={"stale@corp.com"}, _listing_complete_for_link=False,
        )
        conn.data_entities_processor = MagicMock(
            org_id="org", get_all_app_users=AsyncMock(return_value=[]),
        )
        conn._fetch_all_jira_users_bulk = AsyncMock(return_value=raw)

        await JiraConnector._fetch_users(conn, active_pipeshub_users=[])

        assert conn._listed_user_emails == {"live@corp.com", "gone@corp.com"}


class TestJiraDataCenterSyncOrder:
    def _conn(self, order: list[str], *, active_users: list, complete: bool = True) -> MagicMock:
        step = _steps(order)
        conn = _connector(
            JiraDataCenterConnector, data_source=MagicMock(), config_service=MagicMock(), _listed_user_emails=set(),
            _user_read_unfinished=False, _listed_user_keys={"key-1"}, _listing_complete_for_link=complete,
        )
        conn.drop_authenticator_link_if_listed = step("drop")
        conn._read_own_account = step("own", ("admin@corp.com", "key-admin"))
        conn._read_server_version = step("version")
        conn.data_entities_processor = MagicMock(
            get_all_active_users=step("active", active_users), on_new_app_users=step("write"),
        )

        async def fetch() -> list:
            order.append("fetch")
            conn._listed_user_emails = {"creator@corp.com"}
            return [MagicMock()]

        conn._fetch_users = AsyncMock(side_effect=fetch)
        conn._directory_read_complete = MagicMock(return_value=True)
        conn._eligible_users = step("eligible", [MagicMock()])
        conn._note_newly_listed = step("noted", set())
        conn._withdraw_unlisted_app_users = step("withdraw")
        conn.register_authenticated_source_user = step("register", error=_AtlassianStop())
        return conn

    async def _run(self, conn: MagicMock) -> None:
        with patch(
            "app.connectors.sources.atlassian.jira_data_center.connector.load_connector_filters",
            AsyncMock(return_value=(MagicMock(), MagicMock())),
        ):
            await JiraDataCenterConnector.run_sync(conn)

    async def test_the_removal_pass_runs_even_when_the_run_stops_for_no_active_users(self) -> None:
        order: list[str] = []
        conn = self._conn(order, active_users=[])

        await self._run(conn)

        assert order[0] == "drop" and "own" in order and "register" not in order

    async def test_the_removal_pass_runs_even_when_init_fails(self) -> None:
        order: list[str] = []
        conn = self._conn(order, active_users=[MagicMock()])
        conn.data_source = None
        conn.init = AsyncMock(return_value=False)

        with pytest.raises(RuntimeError):
            await self._run(conn)

        assert order == ["drop"]

    async def test_an_empty_first_listing_is_not_complete(self) -> None:
        conn = self._conn([], active_users=[MagicMock()], complete=False)

        with pytest.raises(_AtlassianStop):
            await self._run(conn)

        assert conn.register_authenticated_source_user.await_args.kwargs["listing_complete"] is False

    async def test_the_account_is_read_early_and_registered_after_the_users(self) -> None:
        order: list[str] = []
        conn = self._conn(order, active_users=[MagicMock()])

        with pytest.raises(_AtlassianStop):
            await self._run(conn)

        assert order.index("own") < order.index("eligible")
        assert order.index("write") < order.index("withdraw") < order.index("register")
        args = conn.register_authenticated_source_user.await_args
        assert args.args == ("admin@corp.com", "key-admin")
        assert args.kwargs["extra_present_emails"] == {"creator@corp.com"}

    @pytest.mark.parametrize("complete", [True, False])
    async def test_listing_complete_follows_the_directory_read(self, complete: bool) -> None:
        conn = self._conn([], active_users=[MagicMock()], complete=complete)

        with pytest.raises(_AtlassianStop):
            await self._run(conn)

        assert conn.register_authenticated_source_user.await_args.kwargs["listing_complete"] is complete

    async def test_the_emails_of_a_previous_run_are_not_this_runs_listing(self) -> None:
        conn = self._conn([], active_users=[MagicMock()])
        conn._listed_user_emails = {"stale@corp.com"}
        conn._fetch_users = AsyncMock(return_value=[MagicMock()])

        with pytest.raises(_AtlassianStop):
            await self._run(conn)

        assert conn.register_authenticated_source_user.await_args.kwargs["extra_present_emails"] == set()

    async def test_every_listed_account_counts_as_listed_whatever_its_status(self) -> None:
        raw = [
            {"key": "k1", "active": True, "emailAddress": "Live@corp.com", "name": "live"},
            {"key": "k2", "active": False, "emailAddress": "Gone@Corp.com", "name": "gone"},
        ]
        conn = _connector(
            JiraDataCenterConnector, data_source=MagicMock(), connector_name="JIRA", _user_bulk_forbidden=False,
            _user_bulk_incomplete=False, _user_read_unfinished=False, _listed_user_emails={"stale@corp.com"},
            _dc_name_to_source_id={}, _seen_user_keys=set(), _listing_complete_for_link=False,
        )
        conn.data_entities_processor = MagicMock(
            org_id="org", get_all_app_users=AsyncMock(return_value=[]),
            get_all_active_users=AsyncMock(return_value=[]),
        )
        conn._fetch_all_jira_users_bulk = AsyncMock(return_value=raw)

        await JiraDataCenterConnector._fetch_users(conn)

        assert conn._listed_user_emails == {"live@corp.com", "gone@corp.com"}


class TestConfluenceCloudSyncOrder:
    def _conn(self, order: list[str], *, complete: bool = True) -> MagicMock:
        step = _steps(order)
        conn = _connector(
            ConfluenceConnector, data_source=MagicMock(), external_client=MagicMock(), config_service=MagicMock(),
            _authenticated_confluence_email="admin@corp.com", _authenticated_confluence_account_id="acc-admin",
            _user_list_complete=complete, _listed_user_emails={"creator@corp.com"}, _in_use=0, _close_after_sync=False,
        )
        conn.data_entities_processor = MagicMock(org_id="org")
        conn.drop_authenticator_link_if_listed = step("drop")
        conn._open_audit_window = step("audit")
        conn._sync_users = step("users")
        conn._sync_user_groups = step("groups")
        conn._reconcile_inactive_app_users = step("reconcile")
        conn.register_authenticated_source_user = step("register", error=_AtlassianStop())
        conn._report_sync_problems = AsyncMock()
        conn._release = AsyncMock()
        return conn

    async def _run(self, conn: MagicMock) -> None:
        with patch(
            "app.connectors.sources.atlassian.confluence_cloud.connector.load_connector_filters",
            AsyncMock(return_value=(MagicMock(), MagicMock())),
        ):
            await ConfluenceConnector.run_sync(conn)

    async def test_the_removal_pass_runs_first_and_the_full_pass_after_the_user_writes_and_withdrawals(self) -> None:
        order: list[str] = []
        conn = self._conn(order)

        with pytest.raises(_AtlassianStop):
            await self._run(conn)

        assert order == ["drop", "audit", "users", "groups", "reconcile", "register"]

    async def test_the_removal_pass_runs_even_without_a_client(self) -> None:
        order: list[str] = []
        conn = self._conn(order)
        conn.external_client = None

        with pytest.raises(Exception, match="not initialized"):
            await self._run(conn)

        assert order == ["drop"]

    @pytest.mark.parametrize("complete", [True, False])
    async def test_listing_complete_follows_the_user_search(self, complete: bool) -> None:
        conn = self._conn([])
        conn._sync_users = AsyncMock(side_effect=lambda: setattr(conn, "_user_list_complete", complete))

        with pytest.raises(_AtlassianStop):
            await self._run(conn)

        kwargs = conn.register_authenticated_source_user.await_args.kwargs
        assert kwargs["listing_complete"] is complete
        assert kwargs["extra_present_emails"] is conn._listed_user_emails

    async def test_a_flag_left_by_an_earlier_run_is_reset_before_the_search(self) -> None:
        conn = self._conn([], complete=True)
        conn._sync_users = AsyncMock(side_effect=RuntimeError("search down"))

        with pytest.raises(RuntimeError):
            await self._run(conn)

        assert conn._user_list_complete is False and conn._listed_user_emails == set()


def _confluence_cloud_search(pages: list) -> MagicMock:
    responses = [SimpleNamespace(status=status, json=lambda body=body: body) for status, body in pages]
    return MagicMock(search_users=AsyncMock(side_effect=responses))


def _cloud_user(account_id: str, email: str = "") -> dict:
    return {"user": {"accountId": account_id, "email": email}}


class TestConfluenceCloudUserListing:
    def _conn(self, datasource: MagicMock, inactive: set[str] | None = None) -> MagicMock:
        conn = _connector(
            ConfluenceConnector, _held_back_users=[], _user_list_complete=True, _listed_user_emails={"stale@corp.com"},
            _account_statuses_known=True, _inactive_account_ids=inactive or set(),
        )
        conn._get_fresh_datasource = AsyncMock(return_value=datasource)
        conn._note_problem = MagicMock()
        conn._save_app_users = AsyncMock()
        conn._withdraw_users_gone_from_source = AsyncMock()
        conn._link_platform_users_via_jira = AsyncMock()
        conn._transform_to_app_user = lambda data: SimpleNamespace(
            source_user_id=data["accountId"], email=data["email"],
        )
        return conn

    async def test_a_search_read_to_an_empty_page_is_complete_and_names_every_listed_email(self) -> None:
        datasource = _confluence_cloud_search([
            (200, {"results": [_cloud_user("a1", "Live@corp.com"), _cloud_user("a2", "Gone@corp.com")]}),
            (200, {"results": []}),
        ])
        conn = self._conn(datasource, inactive={"a2"})

        await ConfluenceConnector._sync_users(conn)

        assert conn._user_list_complete is True
        assert conn._listed_user_emails == {"live@corp.com", "gone@corp.com"}

    async def test_a_failed_page_is_an_incomplete_search(self) -> None:
        datasource = _confluence_cloud_search([
            (200, {"results": [_cloud_user("a1", "live@corp.com")]}),
            (503, {}),
        ])
        conn = self._conn(datasource)

        await ConfluenceConnector._sync_users(conn)

        assert conn._user_list_complete is False

    async def test_a_repeated_page_is_an_incomplete_search(self) -> None:
        page = (200, {"results": [_cloud_user("a1", "live@corp.com")]})
        conn = self._conn(_confluence_cloud_search([page, page]))

        await ConfluenceConnector._sync_users(conn)

        assert conn._user_list_complete is False

    async def test_a_capped_search_is_incomplete(self) -> None:
        datasource = _confluence_cloud_search([
            (200, {"results": [_cloud_user("a1", "a@corp.com"), _cloud_user("a2", "b@corp.com")]}),
            (200, {"results": []}),
        ])
        conn = self._conn(datasource)

        with patch("app.connectors.sources.atlassian.confluence_cloud.connector.USER_SEARCH_CAP", 2):
            await ConfluenceConnector._sync_users(conn)

        assert conn._user_list_complete is False

    async def test_an_empty_search_is_incomplete(self) -> None:
        conn = self._conn(_confluence_cloud_search([(200, {"results": []})]))

        await ConfluenceConnector._sync_users(conn)

        assert conn._user_list_complete is False

    async def test_a_search_that_raises_never_leaves_a_complete_flag(self) -> None:
        conn = self._conn(MagicMock(search_users=AsyncMock(side_effect=OSError("reset"))))

        with pytest.raises(OSError):
            await ConfluenceConnector._sync_users(conn)

        assert conn._user_list_complete is False


class TestConfluenceDataCenterSyncOrder:
    def _conn(self, order: list[str]) -> MagicMock:
        step = _steps(order)
        conn = _connector(
            ConfluenceDataCenterConnector, external_client=MagicMock(), data_source=MagicMock(),
            config_service=MagicMock(), _users_listing_complete=True, _listed_user_emails={"stale@corp.com"},
        )
        conn.data_entities_processor = MagicMock(org_id="org")
        conn.drop_authenticator_link_if_listed = step("drop")
        conn._sync_users = step("users")
        conn._register_authenticated_identity = step("register", error=_AtlassianStop())
        return conn

    async def test_the_removal_pass_runs_first_and_the_full_pass_after_the_user_writes(self) -> None:
        order: list[str] = []
        conn = self._conn(order)

        with patch(
            "app.connectors.sources.atlassian.confluence_datacenter.connector.load_connector_filters",
            AsyncMock(return_value=(MagicMock(), MagicMock())),
        ), pytest.raises(_AtlassianStop):
            await ConfluenceDataCenterConnector.run_sync(conn)

        assert order == ["drop", "users", "register"]

    async def test_a_flag_left_by_an_earlier_run_is_reset_before_the_listing(self) -> None:
        conn = self._conn([])
        conn._sync_users = AsyncMock(side_effect=RuntimeError("down"))

        with patch(
            "app.connectors.sources.atlassian.confluence_datacenter.connector.load_connector_filters",
            AsyncMock(return_value=(MagicMock(), MagicMock())),
        ), pytest.raises(RuntimeError):
            await ConfluenceDataCenterConnector.run_sync(conn)

        assert conn._users_listing_complete is False and conn._listed_user_emails == set()

    async def test_the_full_pass_carries_the_listing_state(self) -> None:
        conn = _connector(
            ConfluenceDataCenterConnector, data_source=MagicMock(), _users_listing_complete=False,
            _listed_user_emails={"creator@corp.com"},
        )
        datasource = MagicMock(get_current_user_v1=AsyncMock(
            return_value=_json_response(200, {"userKey": "k", "email": "dev@corp.com"})))
        conn._get_fresh_datasource = AsyncMock(return_value=datasource)

        await ConfluenceDataCenterConnector._register_authenticated_identity(conn)

        kwargs = conn.register_authenticated_source_user.await_args.kwargs
        assert kwargs["listing_complete"] is False
        assert kwargs["extra_present_emails"] == {"creator@corp.com"}


class TestConfluenceDataCenterUserListing:
    def _conn(self, pages: list) -> MagicMock:
        conn = _connector(ConfluenceDataCenterConnector, _users_listing_complete=False, _listed_user_emails=set())
        responses = [SimpleNamespace(status=status, json=lambda body=body: body) for status, body in pages]
        self.datasource_calls = responses
        datasource = MagicMock(get_user_list_v1=AsyncMock(side_effect=responses))
        conn._get_fresh_datasource = AsyncMock(return_value=datasource)
        conn.data_entities_processor = MagicMock(
            on_new_app_users=AsyncMock(), migrate_group_to_user_by_external_id=AsyncMock(),
        )
        conn._transform_to_app_user = lambda data: SimpleNamespace(
            source_user_id=data["userKey"], email=data["email"],
        )
        return conn

    @staticmethod
    def _users(count: int, prefix: str = "u") -> dict:
        return {"results": [{"userKey": f"{prefix}{i}", "email": f"{prefix}{i}@corp.com"} for i in range(count)]}

    async def test_a_short_page_ends_a_complete_listing_and_names_every_email(self) -> None:
        conn = self._conn([(200, self._users(3))])

        await ConfluenceDataCenterConnector._sync_users(conn)

        assert conn._users_listing_complete is True
        assert conn._listed_user_emails == {"u0@corp.com", "u1@corp.com", "u2@corp.com"}

    async def test_a_full_page_then_an_empty_one_is_complete(self) -> None:
        conn = self._conn([(200, self._users(200)), (200, {"results": []})])

        await ConfluenceDataCenterConnector._sync_users(conn)

        assert conn._users_listing_complete is True

    async def test_a_failed_page_is_an_incomplete_listing(self) -> None:
        conn = self._conn([(200, self._users(200)), (500, {})])

        await ConfluenceDataCenterConnector._sync_users(conn)

        assert conn._users_listing_complete is False

    async def test_an_empty_listing_is_incomplete(self) -> None:
        conn = self._conn([(200, {"results": []})])

        await ConfluenceDataCenterConnector._sync_users(conn)

        assert conn._users_listing_complete is False

    async def test_a_short_page_with_a_next_link_keeps_paging_to_the_last_page(self) -> None:
        first = {**self._users(3), "_links": {"next": "/rest/api/user/list?start=3"}}
        last = {**self._users(2, "v"), "_links": {}}
        conn = self._conn([(200, first), (200, last)])

        await ConfluenceDataCenterConnector._sync_users(conn)

        assert conn._users_listing_complete is True
        assert conn._listed_user_emails == {"u0@corp.com", "u1@corp.com", "u2@corp.com", "v0@corp.com", "v1@corp.com"}
        starts = [c.kwargs["start"] for c in conn._get_fresh_datasource.return_value.get_user_list_v1.await_args_list]
        assert starts == [0, 3]

    async def test_an_empty_page_with_a_next_link_is_an_incomplete_listing(self) -> None:
        first = {**self._users(3), "_links": {"next": "/x"}}
        conn = self._conn([(200, first), (200, {"results": [], "_links": {"next": "/x"}})])

        await ConfluenceDataCenterConnector._sync_users(conn)

        assert conn._users_listing_complete is False

    async def test_a_last_page_without_a_next_link_is_complete(self) -> None:
        conn = self._conn([(200, {**self._users(3), "_links": {}})])

        await ConfluenceDataCenterConnector._sync_users(conn)

        assert conn._users_listing_complete is True

    async def test_a_row_that_raises_is_never_a_complete_listing(self) -> None:
        conn = self._conn([(200, self._users(3))])
        conn._transform_to_app_user = MagicMock(side_effect=ValueError("bad row"))

        with pytest.raises(ValueError):
            await ConfluenceDataCenterConnector._sync_users(conn)

        assert conn._users_listing_complete is False


# ---------------------------------------------------------------------------
# Slack Workspace and GitHub Teams: link only after a complete user listing
# ---------------------------------------------------------------------------


class _SlackStop(Exception):
    pass


def _slack_member(uid: str, email: str = "", **flags: object) -> dict:
    return {"id": uid, "name": uid.lower(), "profile": {"email": email, "real_name": uid}, **flags}


class TestSlackSyncOrder:
    def _conn(self, order: list[str]) -> MagicMock:
        step = _steps(order)
        conn = _connector(
            SlackConnector, external_client=MagicMock(), config_service=MagicMock(),
            _users_read_failed=False, _users_listing_cut=False, _listed_user_emails={"stale@corp.com"},
        )
        conn.data_entities_processor = MagicMock(org_id="org")
        conn.drop_authenticator_link_if_listed = step("drop")
        conn._sync_users = step("users")
        conn._register_authenticated_identity = step("register", error=_SlackStop())
        return conn

    async def test_removal_pass_runs_first(self) -> None:
        order: list[str] = []
        conn = self._conn(order)

        with patch(
            "app.connectors.sources.slack.team.connector.load_connector_filters",
            AsyncMock(return_value=(MagicMock(), MagicMock())),
        ), pytest.raises(_SlackStop):
            await SlackConnector.run_sync(conn)

        assert order == ["drop", "users", "register"]

    async def test_the_removal_pass_survives_a_failing_filter_load(self) -> None:
        order: list[str] = []
        conn = self._conn(order)

        with patch(
            "app.connectors.sources.slack.team.connector.load_connector_filters",
            AsyncMock(side_effect=RuntimeError("config down")),
        ), pytest.raises(RuntimeError):
            await SlackConnector.run_sync(conn)

        assert order == ["drop"]

    async def test_the_removal_pass_runs_even_without_a_client(self) -> None:
        order: list[str] = []
        conn = self._conn(order)
        conn.external_client = None

        with pytest.raises(RuntimeError, match="init"):
            await SlackConnector.run_sync(conn)

        assert order == ["drop"]


class TestSlackUserListing:
    def _conn(self, pages: list[object]) -> MagicMock:
        conn = _connector(
            SlackConnector, user_id_to_email_cache={}, user_id_to_internal_id_cache={}, user_id_to_name_cache={},
            rate_limiter=MagicMock(acquire=AsyncMock()), _full_sync_read_failed=False,
            _users_read_failed=False, _users_listing_cut=False, _listed_user_emails=set(),
        )
        datasource = MagicMock(users_list=AsyncMock(side_effect=pages))
        conn._fresh_datasource = AsyncMock(return_value=datasource)
        conn._fail_if_token_refused = AsyncMock()
        conn._build_user_id_caches = AsyncMock()
        conn._to_app_user = partial(SlackConnector._to_app_user, conn)
        conn.data_entities_processor = MagicMock(org_id="org", on_new_app_users=AsyncMock())
        return conn

    @staticmethod
    def _page(members: list[dict], cursor: str = "") -> SimpleNamespace:
        return SimpleNamespace(success=True, data={"members": members, "response_metadata": {"next_cursor": cursor}})

    @staticmethod
    def _complete(conn: MagicMock) -> bool:
        return not (conn._users_read_failed or conn._users_listing_cut)

    async def test_a_listing_read_to_its_last_page_is_complete(self) -> None:
        conn = self._conn([
            self._page([_slack_member("U1", "a@corp.com")], "c1"), self._page([_slack_member("U2", "b@corp.com")]),
        ])

        await SlackConnector._sync_users(conn)

        assert self._complete(conn)
        assert conn._listed_user_emails == {"a@corp.com", "b@corp.com"}

    async def test_every_listed_human_is_present_whatever_their_status(self) -> None:
        members = [
            _slack_member("U1", "Active@Corp.com"),
            _slack_member("U2", "gone@corp.com", deleted=True),
            _slack_member("U3", "guest@corp.com", is_restricted=True),
            _slack_member("U4", "bot@corp.com", is_bot=True),
        ]
        conn = self._conn([self._page(members)])

        await SlackConnector._sync_users(conn)

        assert conn._listed_user_emails == {"active@corp.com", "gone@corp.com", "guest@corp.com"}

    async def test_a_failed_or_cut_user_page_never_links(self) -> None:
        failed = self._conn([
            self._page([_slack_member("U1", "a@corp.com")], "c1"), SimpleNamespace(success=False, error="ratelimited"),
        ])
        empty_with_more = self._conn([self._page([_slack_member("U1", "a@corp.com")], "c1"), self._page([], "c2")])
        nothing = self._conn([self._page([])])
        bad_row = self._conn([self._page([_slack_member("U1", "a@corp.com")])])
        bad_row._to_app_user = MagicMock(return_value=None)
        for conn in (failed, empty_with_more, nothing, bad_row):
            await SlackConnector._sync_users(conn)

        assert [self._complete(c) for c in (failed, empty_with_more, nothing, bad_row)] == [False] * 4

    async def test_a_flag_left_by_an_earlier_sync_is_reset(self) -> None:
        conn = self._conn([self._page([_slack_member("U1", "a@corp.com")])])
        conn._users_listing_cut = True
        conn._listed_user_emails = {"stale@corp.com"}

        await SlackConnector._sync_users(conn)

        assert self._complete(conn) and conn._listed_user_emails == {"a@corp.com"}

    async def test_the_full_pass_carries_the_listing_state(self) -> None:
        conn = self._conn([])
        conn._users_listing_cut = True
        conn._listed_user_emails = {"creator@corp.com"}
        conn.user_id_to_email_cache = {"U0ALICE": "alice@corp.com"}
        auth = SimpleNamespace(success=True, data={"user_id": "U0ALICE"})
        conn._fresh_datasource = AsyncMock(return_value=MagicMock(auth_test=AsyncMock(return_value=auth)))

        await SlackConnector._register_authenticated_identity(conn)

        kwargs = conn.register_authenticated_source_user.await_args.kwargs
        assert kwargs["listing_complete"] is False
        assert kwargs["extra_present_emails"] == {"creator@corp.com"}

    async def test_bot_token_still_unlinks(self) -> None:
        conn = self._conn([])
        auth = SimpleNamespace(success=True, data={"bot_id": "B1"})
        conn._fresh_datasource = AsyncMock(return_value=MagicMock(auth_test=AsyncMock(return_value=auth)))
        conn.data_entities_processor.unlink_authenticator = AsyncMock()

        await SlackConnector._register_authenticated_identity(conn)

        conn.data_entities_processor.unlink_authenticator.assert_awaited_once_with("connector-1")
        conn.register_authenticated_source_user.assert_not_awaited()


class TestGitHubTeamsListing:
    def _users(self, member_pages: dict[str, object], outside: object | None = None) -> object:
        from app.connectors.sources.github_teams.users import UsersSync

        conn = MagicMock()
        conn.data_source = MagicMock()
        conn.sync_filters = None

        async def ds_call(fn: object, *args: object, **__: object) -> object:
            if fn is conn.data_source.list_org_members:
                return member_pages[args[0]]
            if fn is conn.data_source.list_org_outside_collaborators:
                return outside if outside is not None else SimpleNamespace(success=True, data=[])
            return SimpleNamespace(success=True, data=[])

        conn.runtime.ds_call = ds_call
        users = UsersSync(conn)
        users._resolve_target_orgs = AsyncMock(return_value=(list(member_pages), True))
        users._resolve_cached_users = AsyncMock(return_value={})
        users._resolve_via_verified_domains = AsyncMock(return_value={})
        users._enrich_members_with_full_profile = AsyncMock(return_value={})
        users._persist_app_users = AsyncMock()
        return users

    @staticmethod
    def _member(uid: int, email: str | None = None) -> SimpleNamespace:
        return SimpleNamespace(id=uid, login=f"u{uid}", type="User", email=email)

    @staticmethod
    def _ok(*members: object) -> SimpleNamespace:
        return SimpleNamespace(success=True, data=list(members))

    async def test_every_org_read_to_its_end_is_a_complete_listing(self) -> None:
        users = self._users({
            "o1": self._ok(self._member(1, "A@corp.com")), "o2": self._ok(self._member(2, "b@corp.com")),
        })

        await users.sync_users()

        assert users.listing_complete is True
        assert users.listed_emails() == {"a@corp.com", "b@corp.com"}

    async def test_one_failed_org_is_an_incomplete_listing(self) -> None:
        users = self._users({
            "o1": self._ok(self._member(1, "a@corp.com")),
            "o2": SimpleNamespace(success=False, error="boom", status_code=500),
        })

        await users.sync_users()

        assert users.listing_complete is False

    async def test_a_failed_outside_collaborator_listing_is_incomplete(self) -> None:
        users = self._users(
            {"o1": self._ok(self._member(1, "a@corp.com"))},
            outside=SimpleNamespace(success=False, error="forbidden"),
        )

        await users.sync_users()

        assert users.listing_complete is False

    async def test_an_empty_listing_and_a_non_org_login_are_incomplete(self) -> None:
        empty = self._users({"o1": self._ok()})
        not_an_org = self._users({"me": SimpleNamespace(success=False, error="nf", status_code=404)})
        for users in (empty, not_an_org):
            await users.sync_users()

        assert (empty.listing_complete, not_an_org.listing_complete) == (False, False)

    async def test_a_failed_persist_is_never_a_complete_listing(self) -> None:
        users = self._users({"o1": self._ok(self._member(1, "a@corp.com"))})
        users._persist_app_users = AsyncMock(side_effect=RuntimeError("store down"))

        with pytest.raises(RuntimeError):
            await users.sync_users()

        assert users.listing_complete is False

    async def test_the_state_of_an_earlier_sync_is_reset(self) -> None:
        users = self._users({"o1": self._ok(self._member(1, "a@corp.com"))})
        await users.sync_users()
        users._resolve_target_orgs = AsyncMock(return_value=([], True))

        await users.sync_users()

        assert users.listing_complete is False and users.listed_emails() == set()

    async def test_registration_still_uses_the_resolved_email(self) -> None:
        conn = _connector(
            GitHubTeamsConnector, data_source=MagicMock(), runtime=MagicMock(),
            users=MagicMock(
                resolved_email=MagicMock(return_value="dev@corp.com"), listing_complete=False,
                listed_emails=MagicMock(return_value={"creator@corp.com"}),
            ),
        )
        me = SimpleNamespace(success=True, data=SimpleNamespace(email=None, id=7))
        conn.runtime.ds_call = AsyncMock(return_value=me)

        await GitHubTeamsConnector._register_authenticated_identity(conn)

        assert _registered(conn) == ("dev@corp.com", "7")
        kwargs = conn.register_authenticated_source_user.await_args.kwargs
        assert kwargs["listing_complete"] is False
        assert kwargs["extra_present_emails"] == {"creator@corp.com"}


class TestGitHubTeamsSyncOrder:
    async def test_the_removal_pass_runs_before_anything_that_can_raise(self) -> None:
        order: list[str] = []
        conn = _connector(GitHubTeamsConnector, record_sync_point=MagicMock(), repos=MagicMock(), runtime=MagicMock())
        conn.data_entities_processor = MagicMock(org_id="org")
        conn.drop_authenticator_link_if_listed = _steps(order)("drop")
        conn.repos.timestamps.cancel = AsyncMock(side_effect=RuntimeError("down"))

        with pytest.raises(RuntimeError):
            await GitHubTeamsConnector.run_sync(conn)

        assert order == ["drop"]


class TestGitHubTeamsListingReads:
    def _users(self, ds_call: object) -> object:
        from app.connectors.sources.github_teams.users import UsersSync

        conn = MagicMock()
        conn.data_source = MagicMock()
        conn.data_entities_processor.org_id = "org-fix1"
        conn.runtime.ds_call = ds_call
        users = UsersSync(conn)
        users._listing_read_to_the_end = True
        return users

    @staticmethod
    def _members(count: int, base: int = 5000) -> dict:
        return {base + i: SimpleNamespace(id=base + i, login=f"login{base + i}", type="User") for i in range(count)}

    async def test_a_failed_profile_fetch_is_an_incomplete_listing(self) -> None:
        async def ds_call(fn: object, *args: object) -> object:
            return SimpleNamespace(success=False, error="502", data=None)

        users = self._users(ds_call)
        members = self._members(2, base=7000)

        await users._enrich_members_with_full_profile(members, set(members))

        assert users._listing_read_to_the_end is False

    async def test_profiles_all_fetched_leave_the_listing_complete(self) -> None:
        async def ds_call(fn: object, login: str) -> object:
            return SimpleNamespace(success=True, data=SimpleNamespace(login=login, email=f"{login}@corp.com"))

        users = self._users(ds_call)
        members = self._members(2, base=8000)

        await users._enrich_members_with_full_profile(members, set(members))

        assert users._listing_read_to_the_end is True

    async def test_a_failure_after_an_earlier_verified_domain_chunk_is_incomplete(self) -> None:
        answers = iter([SimpleNamespace(success=True, data={}), SimpleNamespace(success=False, error="502")])

        async def ds_call(fn: object, *args: object) -> object:
            return next(answers)

        users = self._users(ds_call)
        members = self._members(250)
        users._member_ids = set(members)

        await users._resolve_via_verified_domains(members, set(members), ["org1"])

        assert users._listing_read_to_the_end is False

    async def test_a_failure_on_the_first_verified_domain_chunk_stays_complete(self) -> None:
        async def ds_call(fn: object, *args: object) -> object:
            return SimpleNamespace(success=False, error="403 not an owner")

        users = self._users(ds_call)
        members = self._members(250)
        users._member_ids = set(members)

        await users._resolve_via_verified_domains(members, set(members), ["org1"])

        assert users._listing_read_to_the_end is True


class TestUserSyncRaisesNoFullPass:
    async def test_slack(self) -> None:
        order: list[str] = []
        step = _steps(order)
        conn = _connector(SlackConnector, external_client=MagicMock(), config_service=MagicMock())
        conn.data_entities_processor = MagicMock(org_id="org")
        conn.drop_authenticator_link_if_listed = step("drop")
        conn._sync_users = step("users", error=RuntimeError("users.list down"))
        conn._register_authenticated_identity = step("register")

        with patch(
            "app.connectors.sources.slack.team.connector.load_connector_filters",
            AsyncMock(return_value=(MagicMock(), MagicMock())),
        ), pytest.raises(RuntimeError):
            await SlackConnector.run_sync(conn)

        assert order == ["drop", "users"]

    async def test_github_teams(self) -> None:
        order: list[str] = []
        step = _steps(order)
        conn = _connector(
            GitHubTeamsConnector, record_sync_point=MagicMock(), repos=MagicMock(), runtime=MagicMock(),
            users=MagicMock(), projects=MagicMock(), config_service=MagicMock(),
        )
        conn.data_entities_processor = MagicMock(org_id="org")
        conn.repos.timestamps.cancel = AsyncMock()
        conn.runtime.refresh_token_if_needed = AsyncMock()
        conn.drop_authenticator_link_if_listed = step("drop")
        conn.users.sync_users = step("users", error=RuntimeError("org discovery failed"))
        conn._register_authenticated_identity = step("register")
        conn._require_scope_filter = MagicMock()

        with patch(
            "app.connectors.sources.github_teams.connector.load_connector_filters",
            AsyncMock(return_value=(MagicMock(), MagicMock())),
        ), pytest.raises(RuntimeError):
            await GitHubTeamsConnector.run_sync(conn)

        assert order == ["drop", "users"]
