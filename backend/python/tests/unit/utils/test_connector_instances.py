"""Unit tests for app.utils.connector_instances."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.utils.connector_instances import fetch_user_connector_instances


@pytest.mark.asyncio
class TestFetchUserConnectorInstances:
    async def test_returns_instances_and_uses_the_visible_connector_scopes(self) -> None:
        instances = [{"_key": "slack-1", "type": "SLACK", "isConfigured": True}]
        graph_provider = MagicMock()
        graph_provider.get_user_connector_instances = AsyncMock(return_value=instances)
        logger = MagicMock()

        result = await fetch_user_connector_instances(graph_provider, "user-1", "org-1", logger)

        assert result is instances
        graph_provider.get_user_connector_instances.assert_awaited_once_with(
            collection=CollectionNames.APPS.value,
            user_id="user-1",
            org_id="org-1",
            team_scope=ConnectorScope.TEAM.value,
            personal_scope=ConnectorScope.PERSONAL.value,
        )
        logger.warning.assert_not_called()

    async def test_returns_empty_list_when_the_provider_returns_none(self) -> None:
        graph_provider = MagicMock()
        graph_provider.get_user_connector_instances = AsyncMock(return_value=None)

        result = await fetch_user_connector_instances(
            graph_provider, "user-1", "org-1", MagicMock()
        )

        assert result == []

    async def test_logs_and_returns_empty_list_when_the_lookup_fails(self) -> None:
        graph_provider = MagicMock()
        error = RuntimeError("graph unavailable")
        graph_provider.get_user_connector_instances = AsyncMock(
            side_effect=error
        )
        logger = MagicMock()

        result = await fetch_user_connector_instances(graph_provider, "user-1", "org-1", logger)

        assert result == []
        logger.warning.assert_called_once_with(
            "connector instance lookup failed: %s", error
        )
