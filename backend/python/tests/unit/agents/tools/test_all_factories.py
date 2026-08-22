"""Tests for all agent tool factory create_client methods.

Each factory's create_client delegates to the respective client's build_from_toolset.
These tests mock that call and verify the factory correctly passes through config.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestDropboxFactory:
    @pytest.mark.asyncio
    async def test_create_client(self):
        from app.agents.tools.factories.dropbox import DropboxClientFactory
        factory = DropboxClientFactory()
        with patch("app.agents.tools.factories.dropbox.DropboxClient") as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            result = await factory.create_client(
                config_service=MagicMock(), logger=MagicMock(),
                toolset_config={"key": "val"}, state=None
            )
            MockClient.build_from_toolset.assert_awaited_once()
            assert result is not None



class TestLinearFactory:
    @pytest.mark.asyncio
    async def test_create_client(self):
        from app.agents.tools.factories.linear import LinearClientFactory
        factory = LinearClientFactory()
        with patch("app.agents.tools.factories.linear.LinearClient") as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            result = await factory.create_client(
                config_service=MagicMock(), logger=MagicMock(),
                toolset_config={"key": "val"}, state=None
            )
            assert result is not None


class TestMariadbFactory:
    @pytest.mark.asyncio
    async def test_create_client(self):
        from app.agents.tools.factories.mariadb import MariaDBClientFactory
        factory = MariaDBClientFactory()
        with patch("app.agents.tools.factories.mariadb.MariaDBClient") as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            result = await factory.create_client(
                config_service=MagicMock(), logger=MagicMock(),
                toolset_config={"key": "val"}, state=None
            )
            assert result is not None


class TestNotionFactory:
    @pytest.mark.asyncio
    async def test_create_client(self):
        from app.agents.tools.factories.notion import NotionClientFactory
        factory = NotionClientFactory()
        with patch("app.agents.tools.factories.notion.NotionClient") as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            result = await factory.create_client(
                config_service=MagicMock(), logger=MagicMock(),
                toolset_config={"key": "val"}, state=None
            )
            assert result is not None


class TestSlackFactory:
    @pytest.mark.asyncio
    async def test_create_client(self):
        from app.agents.tools.factories.slack import SlackClientFactory
        factory = SlackClientFactory()
        with patch("app.agents.tools.factories.slack.SlackClient") as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            result = await factory.create_client(
                config_service=MagicMock(), logger=MagicMock(),
                toolset_config={"key": "val"}, state=None
            )
            assert result is not None


class TestSalesforceFactory:
    @pytest.mark.asyncio
    async def test_create_client(self):
        from app.agents.tools.factories.salesforce import SalesforceClientFactory
        factory = SalesforceClientFactory()
        with patch("app.agents.tools.factories.salesforce.SalesforceClient") as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            result = await factory.create_client(
                config_service=MagicMock(), logger=MagicMock(),
                toolset_config={"key": "val"}, state=None
            )
            assert result is not None


class TestConfluenceFactory:
    @pytest.mark.asyncio
    async def test_create_client(self):
        from app.agents.tools.factories.confluence import ConfluenceClientFactory
        factory = ConfluenceClientFactory()
        with patch("app.agents.tools.factories.confluence.ConfluenceClient") as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            result = await factory.create_client(
                config_service=MagicMock(), logger=MagicMock(),
                toolset_config={"key": "val"}, state=None
            )
            assert result is not None


class TestConfluenceDataCenterFactory:
    """The DC factory adds an auth-type guardrail before delegating to the shared
    ConfluenceClient.build_from_toolset: only API_TOKEN / BASIC_AUTH may reach it, so
    a DC instance never routes through the Cloud OAuth / cloud-id proxy."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("auth_type", ["API_TOKEN", "BASIC_AUTH", "basic_auth", " api_token "])
    async def test_supported_auth_delegates_to_client(self, auth_type):
        from app.agents.tools.factories.confluence import ConfluenceDataCenterClientFactory
        factory = ConfluenceDataCenterClientFactory()
        with patch("app.agents.tools.factories.confluence.ConfluenceClient") as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            result = await factory.create_client(
                config_service=MagicMock(), logger=MagicMock(),
                toolset_config={"authType": auth_type}, state=None,
            )
            assert result is not None
            MockClient.build_from_toolset.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("auth_type", ["OAUTH", "oauth", "BEARER_TOKEN", "unexpected"])
    async def test_unsupported_auth_rejected(self, auth_type):
        from app.agents.tools.factories.confluence import ConfluenceDataCenterClientFactory
        factory = ConfluenceDataCenterClientFactory()
        with patch("app.agents.tools.factories.confluence.ConfluenceClient") as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            with pytest.raises(ValueError, match="API_TOKEN"):
                await factory.create_client(
                    config_service=MagicMock(), logger=MagicMock(),
                    toolset_config={"authType": auth_type}, state=None,
                )
            MockClient.build_from_toolset.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_auth_type_rejected(self):
        from app.agents.tools.factories.confluence import ConfluenceDataCenterClientFactory
        factory = ConfluenceDataCenterClientFactory()
        with pytest.raises(ValueError, match="missing"):
            await factory.create_client(
                config_service=MagicMock(), logger=MagicMock(),
                toolset_config={}, state=None,
            )


class TestJiraFactory:
    @pytest.mark.asyncio
    async def test_create_client(self):
        from app.agents.tools.factories.jira import JiraClientFactory
        factory = JiraClientFactory()
        with patch("app.agents.tools.factories.jira.JiraClient") as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            result = await factory.create_client(
                config_service=MagicMock(), logger=MagicMock(),
                toolset_config={"key": "val"}, state=None
            )
            assert result is not None

    @pytest.mark.asyncio
    async def test_create_client_translates_multi_site_to_toolset_auth_error(self):
        """A provider-specific AtlassianMultiSiteError from build_from_toolset is
        translated to the generic ToolsetAuthError the wrapper handles."""
        from app.agents.tools.factories.base import ToolsetAuthError
        from app.agents.tools.factories.jira import JiraClientFactory
        from app.sources.external.common.atlassian import AtlassianMultiSiteError
        factory = JiraClientFactory()
        with patch("app.agents.tools.factories.jira.JiraClient") as MockClient:
            MockClient.build_from_toolset = AsyncMock(
                side_effect=AtlassianMultiSiteError("multiple Jira sites")
            )
            with pytest.raises(ToolsetAuthError, match="multiple Jira sites"):
                await factory.create_client(
                    config_service=MagicMock(), logger=MagicMock(),
                    toolset_config={"key": "val"}, state=None
                )


class TestJiraDataCenterFactory:
    """The DC factory adds an auth-type guardrail before delegating to the shared
    JiraClient.build_from_toolset: only API_TOKEN / BASIC_AUTH may reach it, so a
    DC instance never routes through the Cloud OAuth / cloud-id proxy."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("auth_type", ["API_TOKEN", "BASIC_AUTH", "basic_auth", " api_token "])
    async def test_supported_auth_delegates_to_client(self, auth_type):
        from app.agents.tools.factories.jira import JiraDataCenterClientFactory
        factory = JiraDataCenterClientFactory()
        with patch("app.agents.tools.factories.jira.JiraClient") as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            result = await factory.create_client(
                config_service=MagicMock(), logger=MagicMock(),
                toolset_config={"authType": auth_type}, state=None,
            )
            assert result is not None
            MockClient.build_from_toolset.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("auth_type", ["OAUTH", "oauth", "SAML", "unexpected"])
    async def test_unsupported_auth_rejected(self, auth_type):
        from app.agents.tools.factories.jira import JiraDataCenterClientFactory
        factory = JiraDataCenterClientFactory()
        with patch("app.agents.tools.factories.jira.JiraClient") as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            with pytest.raises(ValueError, match="API_TOKEN"):
                await factory.create_client(
                    config_service=MagicMock(), logger=MagicMock(),
                    toolset_config={"authType": auth_type}, state=None,
                )
            MockClient.build_from_toolset.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_auth_type_rejected(self):
        from app.agents.tools.factories.jira import JiraDataCenterClientFactory
        factory = JiraDataCenterClientFactory()
        with pytest.raises(ValueError, match="missing"):
            await factory.create_client(
                config_service=MagicMock(), logger=MagicMock(),
                toolset_config={}, state=None,
            )


class TestZoomFactory:
    @pytest.mark.asyncio
    async def test_create_client(self):
        from app.agents.tools.factories.zoom import ZoomClientFactory
        factory = ZoomClientFactory()
        with patch("app.agents.tools.factories.zoom.ZoomClient") as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            result = await factory.create_client(
                config_service=MagicMock(), logger=MagicMock(),
                toolset_config={"key": "val"}, state=None
            )
            assert result is not None


class TestClickupFactory:
    @pytest.mark.asyncio
    async def test_create_client(self):
        from app.agents.tools.factories.clickup import ClickUpClientFactory
        factory = ClickUpClientFactory()
        with patch("app.agents.tools.factories.clickup.ClickUpClient") as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            result = await factory.create_client(
                config_service=MagicMock(), logger=MagicMock(),
                toolset_config={"key": "val"}, state=None
            )
            assert result is not None

class TestMSGraphOneDriveFactory:
    @pytest.mark.asyncio
    async def test_create_client(self):
        from app.agents.tools.factories.microsoft import MSGraphClientFactory
        factory = MSGraphClientFactory(service_name="one_drive")
        with patch("app.agents.tools.factories.microsoft.MSGraphClient") as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=MagicMock())
            result = await factory.create_client(
                config_service=MagicMock(), logger=MagicMock(),
                toolset_config={"key": "val"}, state=None
            )
            assert result is not None


class TestGoogleDriveFactory:
    @pytest.mark.asyncio
    async def test_create_client(self):
        from app.agents.tools.factories.google import GoogleClientFactory
        factory = GoogleClientFactory(service_name="drive")
        mock_google_client = MagicMock()
        mock_google_client.get_client.return_value = MagicMock()
        with patch("app.agents.tools.factories.google.GoogleClient") as MockClient:
            MockClient.build_from_toolset = AsyncMock(return_value=mock_google_client)
            result = await factory.create_client(
                config_service=MagicMock(), logger=MagicMock(),
                toolset_config={"key": "val"}, state=None
            )
            MockClient.build_from_toolset.assert_awaited_once()
            assert result is not None
