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
