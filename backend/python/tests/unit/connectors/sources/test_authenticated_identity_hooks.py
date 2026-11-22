"""Each team connector's read of the source account it is authenticated as.

A connector hands that account's email and id to ``register_authenticated_source_user``
at the start of a sync, so a creator whose PipesHub email differs is linked to it. The
id must be the one the connector's user sync stores as sourceUserId, and a failed read
must never abort the sync. What the helper then does with the pair is covered in
tests/unit/connectors/core/test_authenticated_as_linking.py.
"""

from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.http_status_code import HttpStatusCode
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

    async def test_registers_the_admin_who_generated_the_team_token(self) -> None:
        profile = SimpleNamespace(email="admin@corp.com", team_member_id="dbmid:AAH4f99")
        conn = self._conn(SimpleNamespace(success=True, data=SimpleNamespace(admin_profile=profile)))

        await DropboxConnector._register_authenticated_identity(conn)

        assert _registered(conn) == ("admin@corp.com", "dbmid:AAH4f99")

    async def test_a_token_not_tied_to_an_admin_hands_over_nothing(self) -> None:
        conn = self._conn(SimpleNamespace(success=False, data=None, error="mapping_not_found"))

        await DropboxConnector._register_authenticated_identity(conn)

        assert _registered(conn) == (None, None)

    async def test_a_failed_read_never_reaches_the_link(self) -> None:
        conn = self._conn(error=RuntimeError("connection reset"))

        await DropboxConnector._register_authenticated_identity(conn)

        conn.register_authenticated_source_user.assert_not_awaited()


class TestNotion:
    def _datasource(self, owner: dict) -> MagicMock:
        response = SimpleNamespace(success=True, data=MagicMock())
        response.data.json.return_value = {"object": "user", "type": "bot", "bot": {"owner": owner}}
        datasource = MagicMock()
        datasource.retrieve_bot_user = AsyncMock(return_value=response)
        return datasource

    def _conn(self, client_cls: type) -> MagicMock:
        conn = _connector(NotionConnector, notion_client=MagicMock())
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
                          servicenow_client=None, refresh_token=None)
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
        return _connector(ZoomConnector, _get_fresh_datasource=AsyncMock(return_value=datasource, side_effect=error))

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

    def test_the_oauth_app_asks_for_the_scope_users_me_needs(self) -> None:
        oauth = ZoomConnector._connector_metadata["config"]["auth"]["oauthConfigs"]["OAUTH"]
        assert "user:read:user:admin" in oauth["scopes"]


@pytest.mark.parametrize("connector_cls", [GoogleDriveTeamConnector, GoogleGmailTeamConnector])
class TestGoogleWorkspace:
    def _conn(self, connector_cls: type, admin: dict | None = None, error: Exception | None = None) -> MagicMock:
        admin_data_source = MagicMock()
        admin_data_source.users_get = AsyncMock(return_value=admin, side_effect=error)
        return _connector(
            connector_cls,
            config={"credentials": {"auth": {"adminEmail": "admin@corp.com"}}},
            admin_data_source=admin_data_source,
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


class TestSlackWorkspace:
    def _conn(self, auth: dict | None = None, error: Exception | None = None, authenticator: str | None = None) -> MagicMock:
        datasource = MagicMock()
        datasource.auth_test = AsyncMock(return_value=SimpleNamespace(success=True, data=auth), side_effect=error)
        return _connector(
            SlackConnector,
            _fresh_datasource=AsyncMock(return_value=datasource),
            user_id_to_email_cache={"U0ALICE": "alice@corp.com"},
            authenticator_email=AsyncMock(return_value=authenticator),
            data_entities_processor=MagicMock(unlink_authenticator=AsyncMock()),
        )

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
        conn = _connector(GitLabConnector, data_source=MagicMock(), runtime=MagicMock())
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
            LinearConnector, data_source=MagicMock(), _get_fresh_datasource=AsyncMock(return_value=datasource)
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
        )

    async def test_data_center_identifies_the_account_by_its_key(self) -> None:
        conn = self._conn(HttpStatusCode.OK.value, {"emailAddress": " dev@corp.com ", "key": "JIRAUSER10100", "name": "dev"})

        await JiraDataCenterConnector._register_authenticated_identity(conn)

        assert _registered(conn) == ("dev@corp.com", "JIRAUSER10100")

    async def test_a_refused_read_never_reaches_the_link(self) -> None:
        conn = self._conn(HttpStatusCode.UNAUTHORIZED.value)

        await JiraDataCenterConnector._register_authenticated_identity(conn)

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
