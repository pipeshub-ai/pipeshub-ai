"""Unit tests for the Notion Personal connector.

The behaviour that matters here is the permission model: a personal instance must
grant access only through the creator-owned ConnectorGroup, and must never
enumerate or grant to the Notion workspace's members the way the team connector
does.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.sources.notion_personal.connector import NotionPersonalConnector

_FILTERS = "app.connectors.sources.notion_personal.connector.load_connector_filters"


def _make_connector() -> NotionPersonalConnector:
    logger = MagicMock()
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-1"
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()

    data_store_provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_record_group_by_external_id = AsyncMock(return_value=None)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    data_store_provider.transaction.return_value = mock_tx

    connector = NotionPersonalConnector(
        logger=logger,
        data_entities_processor=data_entities_processor,
        data_store_provider=data_store_provider,
        config_service=AsyncMock(),
        connector_id="notion-personal-1",
        scope="personal",
        created_by="user-1",
    )
    connector._mock_tx = mock_tx
    return connector


def _bot_response(workspace_id: str | None = "ws-1") -> MagicMock:
    bot = {"workspace_name": "My Workspace"}
    if workspace_id is not None:
        bot["workspace_id"] = workspace_id
    response = MagicMock()
    response.success = True
    response.data = MagicMock()
    response.data.json = MagicMock(return_value={"object": "user", "type": "bot", "bot": bot})
    return response


class TestNotionPersonalIdentity:
    def test_uses_its_own_connector_name(self):
        """Records must not be stamped as belonging to the team Notion connector."""
        connector = _make_connector()
        assert connector.connector_name == Connectors.NOTION_PERSONAL
        assert connector.app.get_app_name() == Connectors.NOTION_PERSONAL

    def test_registered_in_factory(self):
        # The factory imports every connector module, so it needs the full
        # optional-dependency set (crawl4ai, talon, ...) that a lean local venv
        # may not have. Runs in CI, skips where those are absent.
        factory = pytest.importorskip(
            "app.connectors.core.factory.connector_factory",
            reason="factory pulls in every connector's optional dependencies",
        )

        from app.connectors.sources.notion.connector import NotionConnector

        get = factory.ConnectorFactory.get_connector_class
        assert get("notionpersonal") is NotionPersonalConnector
        # The team connector must still resolve to the team class.
        assert get("notion") is NotionConnector

    def test_resolves_its_own_shared_oauth_app_key(self):
        """Shared OAuth apps are stored per connector type. Looking under the team
        connector's key finds no match for a personal instance's oauthConfigId and
        fails with 'Client ID, client secret, and redirect URI required'."""
        from app.connectors.sources.notion.connector import NotionConnector

        assert _make_connector()._oauth_config_type() == "notionpersonal"

        team = NotionConnector(
            logger=MagicMock(),
            data_entities_processor=MagicMock(),
            data_store_provider=MagicMock(),
            config_service=AsyncMock(),
            connector_id="notion-team-1",
            scope="team",
            created_by="user-1",
        )
        assert team._oauth_config_type() == "notion"

    @pytest.mark.asyncio
    async def test_init_passes_its_own_oauth_key_to_the_client(self):
        connector = _make_connector()
        build = AsyncMock(return_value=MagicMock())
        with patch(
            "app.connectors.sources.notion.connector.NotionClient.build_from_services", build
        ), patch("app.connectors.sources.notion.connector.NotionDataSource"), patch.object(
            NotionPersonalConnector, "_load_creator_email", new=AsyncMock()
        ):
            assert await connector.init() is True
        assert build.await_args.kwargs["connector_type"] == "notionpersonal"

    def test_declares_its_own_rate_limit(self):
        """_connector_metadata is per-class, so the team connector's resilience
        config is NOT inherited — without its own, this connector would hit
        Notion's ~3 req/s limit with no limiter and no retry."""
        from app.connectors.sources.notion.connector import NotionConnector

        personal = NotionPersonalConnector._connector_metadata["resilienceConfig"]
        assert personal.get("rate_limit") == 3
        assert personal == NotionConnector._connector_metadata["resilienceConfig"]

    def test_declares_personal_scope_and_its_own_info(self):
        metadata = NotionPersonalConnector._connector_metadata
        assert metadata["connectorScopes"] == ["personal"]
        assert "Only you" in metadata["connectorInfo"]


class TestNotionTeamWarning:
    """The team connector's callout is the only place a user is told about the
    workspace-wide visibility, so both halves of it are pinned here."""

    def test_team_connector_warns_about_workspace_wide_visibility(self):
        from app.connectors.sources.notion.connector import NotionConnector

        info = NotionConnector._connector_metadata["connectorInfo"]
        assert "everyone in your Notion workspace" in info
        assert "Notion Personal" in info
        # The shared per-user-ACL copy is false for Notion: the API exposes no
        # per-page sharing, so it must not come back.
        assert "identifies users by email" not in info

    def test_team_connector_links_to_the_personal_one(self):
        """The frontend resolves the link by `type`, so a rename must break here."""
        from app.connectors.sources.notion.connector import NotionConnector

        config = NotionConnector._connector_metadata["config"]
        assert config["personalConnectorType"] == NotionPersonalConnector._connector_metadata["name"]

    def test_team_connector_does_not_gate_setup_on_admin_access(self):
        """personalConnectorType supplies the link; it must not trigger the admin dialog."""
        from app.connectors.sources.notion.connector import NotionConnector

        assert NotionConnector._connector_metadata["config"]["isAdminAccessRequired"] is False


class TestNotionPersonalWorkspaceResolution:
    @pytest.mark.asyncio
    async def test_sync_users_resolves_workspace_without_listing_members(self):
        connector = _make_connector()
        datasource = MagicMock()
        datasource.retrieve_bot_user = AsyncMock(return_value=_bot_response("ws-1"))
        datasource.list_users = AsyncMock()
        connector._get_fresh_datasource = AsyncMock(return_value=datasource)

        await connector._sync_users()

        assert connector.workspace_id == "ws-1"
        assert connector.workspace_name == "My Workspace"
        datasource.list_users.assert_not_called()
        connector.data_entities_processor.on_new_app_users.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_users_raises_when_workspace_id_missing(self):
        connector = _make_connector()
        datasource = MagicMock()
        datasource.retrieve_bot_user = AsyncMock(return_value=_bot_response(workspace_id=None))
        connector._get_fresh_datasource = AsyncMock(return_value=datasource)

        with pytest.raises(Exception, match="workspace_id"):
            await connector._sync_users()

    @pytest.mark.asyncio
    async def test_sync_users_raises_on_api_failure(self):
        connector = _make_connector()
        failed = MagicMock()
        failed.success = False
        failed.error = "unauthorized"
        datasource = MagicMock()
        datasource.retrieve_bot_user = AsyncMock(return_value=failed)
        connector._get_fresh_datasource = AsyncMock(return_value=datasource)

        with pytest.raises(Exception, match="unauthorized"):
            await connector._sync_users()


class TestNotionPersonalPermissions:
    @pytest.mark.asyncio
    async def test_workspace_group_carries_only_the_connector_group_permission(self):
        connector = _make_connector()
        connector.workspace_id = "ws-1"
        connector.workspace_name = "My Workspace"
        group_permission = MagicMock()
        connector._connector_group_permission = group_permission

        await connector._apply_creator_workspace_permission()

        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()
        record_group, perms = (
            connector.data_entities_processor.on_new_record_groups.call_args[0][0][0]
        )
        assert perms == [group_permission]
        assert record_group.connector_name == Connectors.NOTION_PERSONAL

    @pytest.mark.asyncio
    async def test_no_creator_group_grants_nothing_rather_than_everyone(self):
        connector = _make_connector()
        connector.workspace_id = "ws-1"
        connector.workspace_name = "My Workspace"
        connector._connector_group_permission = None

        await connector._apply_creator_workspace_permission()

        _rg, perms = (
            connector.data_entities_processor.on_new_record_groups.call_args[0][0][0]
        )
        assert perms == []

    @pytest.mark.asyncio
    async def test_run_sync_materializes_group_before_syncing_objects(self):
        """Order matters: on_new_record_groups drops a GROUP permission that isn't upserted yet."""
        connector = _make_connector()
        calls = []

        connector.creator_email = "creator@example.com"
        connector.ensure_connector_group_permission = AsyncMock(
            side_effect=lambda: calls.append("ensure_group")
        )
        connector._sync_users = AsyncMock(side_effect=lambda: calls.append("sync_users"))
        connector._sync_objects_by_type = AsyncMock(
            side_effect=lambda kind: calls.append(f"sync_{kind}")
        )

        with patch(_FILTERS, new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
            await connector.run_sync()

        assert calls == ["ensure_group", "sync_users", "sync_data_source", "sync_page"]

    @pytest.mark.asyncio
    async def test_run_sync_continues_without_creator_email(self):
        connector = _make_connector()
        connector.creator_email = None
        connector._load_creator_email = AsyncMock()
        connector.ensure_connector_group_permission = AsyncMock()
        connector._sync_users = AsyncMock()
        connector._sync_objects_by_type = AsyncMock()

        with patch(_FILTERS, new=AsyncMock(return_value=(MagicMock(), MagicMock()))):
            await connector.run_sync()

        connector.ensure_connector_group_permission.assert_not_awaited()
        connector.logger.warning.assert_called()
