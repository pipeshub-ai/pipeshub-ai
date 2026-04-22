"""Tests filling the remaining coverage gaps in EntityEventService.

Targets lines 523-524, 534-535, 655-656, and 730-731 in
``app/services/messaging/kafka/handlers/entity.py``.

The existing ``test_entity_handler_coverage.py`` patches the connector class
with a bare MagicMock, which silently auto-creates ``_connector_metadata`` on
attribute access — so the ``if not hasattr(...)`` guard at line 654 is never
actually taken. Here we patch with real classes so attribute lookup behaves
realistically.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.kafka.handlers.entity import EntityEventService


def _make_service(*, strict_container: bool = False) -> EntityEventService:
    """Build an EntityEventService. If ``strict_container`` is True, the app
    container is a plain object so ``hasattr(container, 'connectors_map')``
    actually returns False until we assign it."""
    logger = MagicMock(spec=logging.Logger)
    graph_provider = AsyncMock()
    if strict_container:
        class _Container:
            pass
        app_container = _Container()
        app_container.config_service = MagicMock(return_value=AsyncMock())
        app_container.data_store = AsyncMock()
        app_container.messaging_producer = AsyncMock()
    else:
        app_container = MagicMock()
        app_container.messaging_producer = AsyncMock()
        app_container.config_service.return_value = AsyncMock()
        app_container.data_store = AsyncMock()
    return EntityEventService(logger, graph_provider, app_container)


class TestCreateAllTeamForOrgException:
    """Lines 523-524: exception during ``batch_upsert_nodes`` is logged and swallowed."""

    @pytest.mark.asyncio
    async def test_exception_logged_and_swallowed(self):
        svc = _make_service()
        svc.graph_provider.batch_upsert_nodes = AsyncMock(
            side_effect=RuntimeError("arango down"),
        )
        # Must not raise
        result = await svc._EntityEventService__create_all_team_for_org("org-1", "u1")
        assert result is None
        svc.logger.error.assert_called_once()
        msg = svc.logger.error.call_args[0][0]
        assert "org-1" in msg
        assert "arango down" in msg

    @pytest.mark.asyncio
    async def test_defaults_created_by_to_system(self):
        svc = _make_service()
        svc.graph_provider.batch_upsert_nodes = AsyncMock()
        await svc._EntityEventService__create_all_team_for_org("org-1")
        args, _ = svc.graph_provider.batch_upsert_nodes.call_args
        nodes = args[0]
        assert nodes[0]["createdBy"] == "system"


class TestGetOrCreateAllTeamAndAddUserException:
    """Lines 534-535: exception during ``add_user_to_all_team`` is logged and swallowed."""

    @pytest.mark.asyncio
    async def test_exception_logged_and_swallowed(self):
        svc = _make_service()
        svc.graph_provider.add_user_to_all_team = AsyncMock(
            side_effect=RuntimeError("edge failed"),
        )
        result = await svc._EntityEventService__get_or_create_all_team_and_add_user(
            "org-1", "user-key-1",
        )
        assert result is None
        svc.logger.error.assert_called_once()
        # Verify the exc_info=True kwarg was used (matches source)
        _, kwargs = svc.logger.error.call_args
        assert kwargs.get("exc_info") is True

    @pytest.mark.asyncio
    async def test_success_logs_info(self):
        svc = _make_service()
        svc.graph_provider.add_user_to_all_team = AsyncMock()
        await svc._EntityEventService__get_or_create_all_team_and_add_user(
            "org-1", "user-key-1",
        )
        svc.graph_provider.add_user_to_all_team.assert_awaited_once_with(
            "org-1", "user-key-1",
        )


class TestCreateKbConnectorNoMetadataReal:
    """Lines 655-656: guard that the class lacks ``_connector_metadata``.

    Using a real class (no auto-attr magic) so ``hasattr`` actually returns False.
    """

    @pytest.mark.asyncio
    async def test_returns_none_when_real_class_has_no_metadata(self):
        class FakeConnector:
            pass

        svc = _make_service()
        svc.graph_provider.get_org_apps = AsyncMock(return_value=[])

        with patch(
            "app.connectors.sources.localKB.connector.KnowledgeBaseConnector",
            FakeConnector,
        ), patch(
            "app.connectors.sources.localKB.connector.KB_CONNECTOR_NAME",
            "kb",
            create=True,
        ):
            result = await svc._EntityEventService__create_kb_connector_app_instance("org-1")

        assert result is None
        svc.logger.warning.assert_called_once()
        assert "metadata not found" in svc.logger.warning.call_args[0][0]


class TestCreateKbConnectorMissingConnectorsMap:
    """Lines 730-731: ``connectors_map`` attribute missing on app_container.

    Default ``MagicMock`` auto-creates attributes, so the ``hasattr`` guard is
    only meaningfully exercised with a real container object.
    """

    @pytest.mark.asyncio
    async def test_connectors_map_created_when_missing(self):
        svc = _make_service(strict_container=True)
        svc.graph_provider.get_org_apps = AsyncMock(return_value=[])
        svc.graph_provider.batch_upsert_nodes = AsyncMock()
        svc.graph_provider.batch_create_edges = AsyncMock()

        class FakeKB:
            _connector_metadata = {"name": "KB", "appGroup": "Local Storage"}

        with patch(
            "app.connectors.sources.localKB.connector.KnowledgeBaseConnector",
            FakeKB,
        ), patch(
            "app.connectors.sources.localKB.connector.KB_CONNECTOR_NAME",
            "kb",
            create=True,
        ), patch(
            "app.services.messaging.kafka.handlers.entity.ConnectorFactory"
        ) as mock_factory:
            fake_connector = MagicMock()
            mock_factory.create_and_start_sync = AsyncMock(return_value=fake_connector)

            result = await svc._EntityEventService__create_kb_connector_app_instance(
                "org-1", "u1",
            )

        assert result is not None
        assert hasattr(svc.app_container, "connectors_map")
        # The new instance should have been added to the map.
        assert "knowledgeBase_org-1" in svc.app_container.connectors_map
        assert svc.app_container.connectors_map["knowledgeBase_org-1"] is fake_connector
        # The "Creating connectors_map" info log must have fired.
        info_msgs = [c.args[0] for c in svc.logger.info.call_args_list if c.args]
        assert any("Creating connectors_map" in msg for msg in info_msgs)
