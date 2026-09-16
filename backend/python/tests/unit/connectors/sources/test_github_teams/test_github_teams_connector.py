"""Unit tests for GitHubTeamsConnector lifecycle and helper delegation.

``run_sync`` and most public methods are exercised as unbound methods against
the shared mock connector: instantiating the real class would require a live
config service, OAuth client and graph provider, none of which this behaviour
depends on. ``create_connector`` is the exception — it is the factory that
actually builds the instance.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.github_teams import connector as connector_mod
from app.connectors.sources.github_teams.common.apps import GitHubTeamsApp
from app.connectors.sources.github_teams.connector import GitHubTeamsConnector

from tests.unit.connectors.sources.test_github_teams.conftest import (
    failed_response,
    make_mock_connector,
    ok_response,
)

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


def _runnable_connector() -> object:
    c = make_mock_connector()
    c.repos.timestamps.cancel = AsyncMock()
    c.repos.timestamps.schedule = lambda: None
    c.users.sync_users = AsyncMock()
    c.projects.sync_all_repos = AsyncMock()
    return c


class TestTeamAppEdge:
    """Without a Teams->App edge the record-access query's
    ``connectorId IN user_apps_ids`` pre-filter excludes every GitHub record,
    making a public repo's ORG grant unreachable for users whose GitHub
    account never resolved to an AppUser."""

    async def test_run_sync_ensures_the_team_app_edge(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        c = _runnable_connector()
        monkeypatch.setattr(
            connector_mod, "load_connector_filters", AsyncMock(return_value=({}, {})),
        )

        await GitHubTeamsConnector.run_sync(c)

        c.tx_store.ensure_team_app_edge.assert_awaited_once_with(
            c.connector_id, c.data_entities_processor.org_id,
        )

    async def test_edge_is_established_before_user_sync(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A user sync that raises (e.g. org discovery failure) must not leave
        the connector unreachable for the whole org."""
        c = _runnable_connector()
        c.users.sync_users = AsyncMock(side_effect=RuntimeError("org discovery failed"))
        monkeypatch.setattr(
            connector_mod, "load_connector_filters", AsyncMock(return_value=({}, {})),
        )

        with pytest.raises(RuntimeError):
            await GitHubTeamsConnector.run_sync(c)

        c.tx_store.ensure_team_app_edge.assert_awaited_once()


class TestInit:
    async def test_init_success_builds_client_and_resolves_identity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        c = make_mock_connector()
        client = MagicMock()
        ds = MagicMock()
        monkeypatch.setattr(
            connector_mod.GitHubClient, "build_from_services", AsyncMock(return_value=client)
        )
        monkeypatch.setattr(connector_mod, "GitHubAsyncDataSource", lambda _client: ds)
        c._resolve_creator_identity = AsyncMock()

        assert await GitHubTeamsConnector.init(c) is True
        assert c.external_client is client
        assert c.data_source is ds
        c._resolve_creator_identity.assert_awaited_once()

    async def test_init_failure_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = make_mock_connector()
        monkeypatch.setattr(
            connector_mod.GitHubClient,
            "build_from_services",
            AsyncMock(side_effect=RuntimeError("oauth down")),
        )

        assert await GitHubTeamsConnector.init(c) is False
        c.logger.error.assert_called()


class TestResolveCreatorIdentity:
    async def test_sets_creator_email_and_github_login(self) -> None:
        c = make_mock_connector()
        c.created_by = "user-1"
        c.creator_email = None
        c._github_login = None
        c.data_entities_processor.get_user_by_user_id = AsyncMock(
            return_value=SimpleNamespace(email="creator@example.com")
        )
        c.runtime.ds_call = AsyncMock(
            return_value=ok_response(SimpleNamespace(login="octocat"))
        )

        await GitHubTeamsConnector._resolve_creator_identity(c)

        assert c.creator_email == "creator@example.com"
        assert c._github_login == "octocat"

    async def test_user_lookup_failure_is_logged(self) -> None:
        c = make_mock_connector()
        c.created_by = "user-1"
        c.creator_email = None
        c.data_entities_processor.get_user_by_user_id = AsyncMock(
            side_effect=RuntimeError("graph down")
        )
        c.runtime.ds_call = AsyncMock(
            return_value=ok_response(SimpleNamespace(login="octocat"))
        )

        await GitHubTeamsConnector._resolve_creator_identity(c)

        c.logger.warning.assert_called()
        assert c._github_login == "octocat"

    async def test_missing_data_source_skips_login_lookup(self) -> None:
        c = make_mock_connector()
        c.created_by = None
        c.data_source = None

        await GitHubTeamsConnector._resolve_creator_identity(c)

        c.runtime.ds_call.assert_not_awaited()

    async def test_creator_without_email_is_left_unset(self) -> None:
        c = make_mock_connector()
        c.created_by = "user-1"
        c.creator_email = None
        c.data_entities_processor.get_user_by_user_id = AsyncMock(
            return_value=SimpleNamespace(email=None)
        )
        c.runtime.ds_call = AsyncMock(return_value=ok_response(SimpleNamespace(login="")))

        await GitHubTeamsConnector._resolve_creator_identity(c)

        assert c.creator_email is None
        assert c._github_login is None

    async def test_failed_authenticated_call_warns(self) -> None:
        c = make_mock_connector()
        c.created_by = None
        c.runtime.ds_call = AsyncMock(return_value=failed_response("401"))

        await GitHubTeamsConnector._resolve_creator_identity(c)

        assert c._github_login is None
        c.logger.warning.assert_called()

    async def test_login_lookup_exception_is_logged(self) -> None:
        c = make_mock_connector()
        c.created_by = None
        c.runtime.ds_call = AsyncMock(side_effect=RuntimeError("timeout"))

        await GitHubTeamsConnector._resolve_creator_identity(c)

        c.logger.warning.assert_called()


class TestConnectionAndAccess:
    async def test_no_data_source_returns_false(self) -> None:
        c = make_mock_connector()
        c.data_source = None
        assert await GitHubTeamsConnector.test_connection_and_access(c) is False

    async def test_successful_authenticated_call_returns_true(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call = AsyncMock(return_value=ok_response(SimpleNamespace(login="octocat")))

        assert await GitHubTeamsConnector.test_connection_and_access(c) is True

    async def test_failed_response_returns_false(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call = AsyncMock(return_value=failed_response("unauthorized"))

        assert await GitHubTeamsConnector.test_connection_and_access(c) is False

    async def test_exception_returns_false(self) -> None:
        c = make_mock_connector()
        c.runtime.refresh_token_if_needed = AsyncMock(side_effect=RuntimeError("network"))

        assert await GitHubTeamsConnector.test_connection_and_access(c) is False


class TestRunSyncAndIncremental:
    async def test_run_sync_calls_users_then_projects(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        c = _runnable_connector()
        order: list[str] = []
        c.users.sync_users = AsyncMock(side_effect=lambda: order.append("users"))
        c.projects.sync_all_repos = AsyncMock(side_effect=lambda: order.append("projects"))
        monkeypatch.setattr(
            connector_mod, "load_connector_filters", AsyncMock(return_value=({}, {})),
        )

        await GitHubTeamsConnector.run_sync(c)

        assert order == ["users", "projects"]

    async def test_run_incremental_sync_delegates_to_run_sync(self) -> None:
        c = make_mock_connector()
        c.run_sync = AsyncMock()
        await GitHubTeamsConnector.run_incremental_sync(c)
        c.run_sync.assert_awaited_once()


class TestDelegationAndCleanup:
    async def test_stream_record_delegates(self) -> None:
        c = make_mock_connector()
        c.streaming.stream_record = AsyncMock(return_value="stream")
        record = MagicMock()
        assert await GitHubTeamsConnector.stream_record(c, record) == "stream"
        c.streaming.stream_record.assert_awaited_once_with(record)

    async def test_reindex_records_delegates(self) -> None:
        c = make_mock_connector()
        c.streaming.reindex_records = AsyncMock()
        records = [MagicMock()]
        await GitHubTeamsConnector.reindex_records(c, records)
        c.streaming.reindex_records.assert_awaited_once_with(records)

    async def test_get_filter_options_delegates(self) -> None:
        c = make_mock_connector()
        c.filters.get_filter_options = AsyncMock(return_value="opts")
        assert await GitHubTeamsConnector.get_filter_options(c, "org_ids") == "opts"
        c.filters.get_filter_options.assert_awaited_once()

    async def test_get_signed_url_returns_none(self) -> None:
        c = make_mock_connector()
        assert await GitHubTeamsConnector.get_signed_url(c, MagicMock()) is None

    async def test_handle_webhook_returns_true(self) -> None:
        c = make_mock_connector()
        assert await GitHubTeamsConnector.handle_webhook_notification(c) is True

    async def test_cleanup_cancels_backfill_and_drops_data_source(self) -> None:
        c = make_mock_connector()
        c.repos.timestamps.cancel = AsyncMock()
        data_source = MagicMock()
        data_source.aclose = AsyncMock()
        c.data_source = data_source

        await GitHubTeamsConnector.cleanup(c)

        c.repos.timestamps.cancel.assert_awaited_once()
        data_source.aclose.assert_awaited_once()
        assert c.data_source is None

    async def test_cleanup_survives_aclose_failure(self) -> None:
        c = make_mock_connector()
        c.repos.timestamps.cancel = AsyncMock()
        data_source = MagicMock()
        data_source.aclose = AsyncMock(side_effect=RuntimeError("close failed"))
        c.data_source = data_source

        await GitHubTeamsConnector.cleanup(c)

        assert c.data_source is None
        c.logger.warning.assert_called()

    async def test_run_sync_rebinds_checkpoint_org_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        c = _runnable_connector()
        c.record_sync_point.org_id = "stale-org"
        c.data_entities_processor.org_id = "real-org"
        monkeypatch.setattr(
            connector_mod, "load_connector_filters", AsyncMock(return_value=({}, {})),
        )

        await GitHubTeamsConnector.run_sync(c)

        assert c.record_sync_point.org_id == "real-org"


class TestCreateConnector:
    async def test_factory_builds_initialized_instance(self) -> None:
        processor = MagicMock()
        processor.org_id = "org-1"

        connector = await GitHubTeamsConnector.create_connector(
            logger=MagicMock(),
            data_store_provider=MagicMock(),
            config_service=MagicMock(),
            connector_id="conn-1",
            scope="team",
            created_by="user-1",
            data_entities_processor=processor,
        )

        assert isinstance(connector, GitHubTeamsConnector)
        assert connector.connector_id == "conn-1"
        assert connector.created_by == "user-1"


class TestGitHubTeamsApp:
    def test_registers_under_github_group(self) -> None:
        from app.config.constants.arangodb import AppGroups, Connectors

        app = GitHubTeamsApp("conn-1")
        assert app.get_app_name() == Connectors.GITHUB_TEAMS
        assert app.get_app_group_name() == AppGroups.GITHUB
        assert app.get_connector_id() == "conn-1"
