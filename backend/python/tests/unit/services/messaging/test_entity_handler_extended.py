"""
Extended tests for entity.py (EntityEventService) targeting uncovered lines:
300-302, 481-483, 503-586, 599-709, 722-741, 754-803.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.kafka.handlers.entity import EntityEventService


def _make_entity_service():
    logger = MagicMock(spec=logging.Logger)
    graph_provider = AsyncMock()
    app_container = MagicMock()
    app_container.messaging_producer = AsyncMock()
    app_container.config_service.return_value = AsyncMock()
    app_container.data_store = AsyncMock()
    return EntityEventService(logger, graph_provider, app_container)


# ===================================================================
# process_event — routes to org_deleted
# ===================================================================


class TestProcessEventOrgDeleted:
    """Cover orgDeleted route (line ~39)."""

    @pytest.mark.asyncio
    async def test_routes_to_org_deleted(self):
        svc = _make_entity_service()
        svc.graph_provider.batch_upsert_nodes = AsyncMock()
        result = await svc.process_event(
            "orgDeleted", {"orgId": "org-1"}
        )
        assert result is True


# ===================================================================
# __handle_user_added — exception handling (lines 300-302)
# ===================================================================


class TestHandleUserAddedExtended:
    """Cover lines 300-302: exception during user creation."""

    @pytest.mark.asyncio
    async def test_user_added_exception_returns_false(self):
        svc = _make_entity_service()
        svc.graph_provider.get_user_by_email = AsyncMock(
            side_effect=Exception("DB error")
        )
        result = await svc.process_event(
            "userAdded",
            {
                "userId": "u1",
                "orgId": "org-1",
                "email": "user@test.com",
                "syncAction": "none",
            },
        )
        assert result is False


# ===================================================================
# __handle_app_disabled (lines 432-483)
# ===================================================================


class TestHandleAppDisabledExtended:
    """Cover lines 481-483: exception in app disabled handler."""

    @pytest.mark.asyncio
    async def test_app_disabled_db_error_returns_false(self):
        svc = _make_entity_service()
        svc.graph_provider.get_document = AsyncMock(
            side_effect=Exception("DB error")
        )
        result = await svc.process_event(
            "appDisabled",
            {"orgId": "org-1", "apps": ["drive"], "connectorId": "c-1"},
        )
        assert result is False


# ===================================================================
# __get_or_create_knowledge_base (lines 503-586)
# ===================================================================


class TestGetOrCreateKnowledgeBase:
    """Cover the __get_or_create_knowledge_base private method via userAdded."""

    @pytest.mark.asyncio
    async def test_user_added_creates_kb_for_new_user(self):
        """Full flow: new user + org found + KB created."""
        svc = _make_entity_service()
        svc.graph_provider.get_user_by_email = AsyncMock(return_value=None)
        svc.graph_provider.get_document = AsyncMock(
            return_value={"accountType": "enterprise"}
        )
        svc.graph_provider.batch_upsert_nodes = AsyncMock()
        svc.graph_provider.batch_create_edges = AsyncMock()
        svc.graph_provider.get_nodes_by_filters = AsyncMock(return_value=[])
        svc.graph_provider.get_org_apps = AsyncMock(
            return_value=[{"_key": "kb-app-1", "type": "KB"}]
        )
        svc.graph_provider.get_edges_from_node = AsyncMock(return_value=[])

        result = await svc.process_event(
            "userAdded",
            {
                "userId": "u1",
                "orgId": "org-1",
                "email": "new@test.com",
                "fullName": "New User",
                "syncAction": "none",
            },
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_user_added_existing_kb_returned(self):
        """KB already exists for user."""
        svc = _make_entity_service()
        svc.graph_provider.get_user_by_email = AsyncMock(return_value=None)
        svc.graph_provider.get_document = AsyncMock(
            return_value={"accountType": "enterprise"}
        )
        svc.graph_provider.batch_upsert_nodes = AsyncMock()
        svc.graph_provider.batch_create_edges = AsyncMock()
        # KB already exists
        svc.graph_provider.get_nodes_by_filters = AsyncMock(
            return_value=[
                {
                    "id": "kb-1",
                    "groupType": "knowledgeBase",
                    "isDeleted": False,
                }
            ]
        )
        svc.graph_provider.get_org_apps = AsyncMock(
            return_value=[{"_key": "kb-app-1", "type": "KB"}]
        )
        svc.graph_provider.get_edges_from_node = AsyncMock(return_value=[])

        result = await svc.process_event(
            "userAdded",
            {
                "userId": "u1",
                "orgId": "org-1",
                "email": "existing@test.com",
                "syncAction": "none",
            },
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_user_added_missing_user_and_org_id_for_kb(self):
        """Missing userId or orgId => returns empty dict."""
        svc = _make_entity_service()
        svc.graph_provider.get_user_by_email = AsyncMock(return_value=None)
        svc.graph_provider.get_document = AsyncMock(
            return_value={"accountType": "enterprise"}
        )
        svc.graph_provider.batch_upsert_nodes = AsyncMock()
        svc.graph_provider.batch_create_edges = AsyncMock()
        svc.graph_provider.get_nodes_by_filters = AsyncMock(return_value=[])
        svc.graph_provider.get_org_apps = AsyncMock(return_value=[])

        # The user added flow does pass userId/orgId, so the KB creation path
        # will work - but __get_or_create_kb_app_for_org may be called and return None
        # Let's test when KB app creation returns None
        with patch.object(
            svc,
            "_EntityEventService__get_or_create_kb_app_for_org",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await svc.process_event(
                "userAdded",
                {
                    "userId": "u1",
                    "orgId": "org-1",
                    "email": "test@test.com",
                    "syncAction": "none",
                },
            )
            # Should still succeed but KB creation will return empty
            assert result is True


# ===================================================================
# __create_kb_connector_app_instance (lines 599-709)
# ===================================================================


class TestCreateKbConnectorAppInstance:
    """Cover the __create_kb_connector_app_instance private method."""

    @pytest.mark.asyncio
    async def test_kb_connector_created_during_org_created(self):
        """orgCreated triggers KB connector instance creation."""
        svc = _make_entity_service()
        svc.graph_provider.batch_upsert_nodes = AsyncMock()
        svc.graph_provider.batch_create_edges = AsyncMock()
        svc.graph_provider.get_nodes_by_filters = AsyncMock(return_value=[])
        svc.graph_provider.get_org_apps = AsyncMock(return_value=[])

        mock_connector = MagicMock()
        with patch(
            "app.services.messaging.kafka.handlers.entity.ConnectorFactory"
        ) as mock_factory:
            mock_factory.create_and_start_sync = AsyncMock(
                return_value=mock_connector
            )

            # Mock the KB connector import
            with patch(
                "app.services.messaging.kafka.handlers.entity.KnowledgeBaseConnector",
                create=True,
            ) as mock_kb_class:
                mock_kb_class._connector_metadata = {
                    "name": "Knowledge Base",
                    "appGroup": "Local Storage",
                }
                with patch(
                    "app.services.messaging.kafka.handlers.entity.KB_CONNECTOR_NAME",
                    "Knowledge Base",
                    create=True,
                ):
                    result = await svc.process_event(
                        "orgCreated",
                        {
                            "orgId": "org-1",
                            "accountType": "enterprise",
                            "registeredName": "Test Org",
                            "userId": "u1",
                        },
                    )
                    assert result is True

    @pytest.mark.asyncio
    async def test_kb_connector_already_exists(self):
        """orgCreated with existing KB connector skips creation."""
        svc = _make_entity_service()
        svc.graph_provider.batch_upsert_nodes = AsyncMock()
        svc.graph_provider.batch_create_edges = AsyncMock()
        svc.graph_provider.get_nodes_by_filters = AsyncMock(return_value=[])
        svc.graph_provider.get_org_apps = AsyncMock(
            return_value=[
                {"_key": "existing-kb", "type": "KB"}
            ]
        )

        with patch(
            "app.services.messaging.kafka.handlers.entity.KnowledgeBaseConnector",
            create=True,
        ) as mock_kb_class:
            mock_kb_class._connector_metadata = {
                "name": "Knowledge Base",
                "appGroup": "Local Storage",
            }
            with patch(
                "app.services.messaging.kafka.handlers.entity.KB_CONNECTOR_NAME",
                "Knowledge Base",
                create=True,
            ):
                result = await svc.process_event(
                    "orgCreated",
                    {
                        "orgId": "org-1",
                        "accountType": "enterprise",
                        "registeredName": "Test Org",
                        "userId": "u1",
                    },
                )
                assert result is True

    @pytest.mark.asyncio
    async def test_kb_connector_exception_does_not_fail(self):
        """Exception in KB connector creation should not fail org creation."""
        svc = _make_entity_service()
        svc.graph_provider.batch_upsert_nodes = AsyncMock()
        svc.graph_provider.batch_create_edges = AsyncMock()
        svc.graph_provider.get_nodes_by_filters = AsyncMock(return_value=[])
        svc.graph_provider.get_org_apps = AsyncMock(return_value=[])

        # Mock to raise ImportError inside __create_kb_connector_app_instance
        with patch(
            "app.services.messaging.kafka.handlers.entity.KnowledgeBaseConnector",
            create=True,
            side_effect=ImportError("No module"),
        ):
            result = await svc.process_event(
                "orgCreated",
                {
                    "orgId": "org-1",
                    "accountType": "enterprise",
                    "registeredName": "Test Org",
                    "userId": "u1",
                },
            )
            # Should still succeed
            assert result is True


# ===================================================================
# __get_or_create_kb_app_for_org (lines 722-741)
# ===================================================================


class TestGetOrCreateKbAppForOrg:
    """Cover __get_or_create_kb_app_for_org."""

    @pytest.mark.asyncio
    async def test_existing_kb_app_found(self):
        """When KB app already exists, returns it directly."""
        svc = _make_entity_service()
        # get_org_apps is called twice: once in __get_or_create_kb_app_for_org,
        # once in __create_kb_connector_app_instance if called
        svc.graph_provider.get_org_apps = AsyncMock(
            return_value=[
                {"_key": "kb-app-1", "type": "KB", "id": "kb-app-1"}
            ]
        )

        result = await svc._EntityEventService__get_or_create_kb_app_for_org(
            "org-1", "u1"
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_creates_kb_app_when_not_found(self):
        """When no KB app exists, creates one."""
        svc = _make_entity_service()
        svc.graph_provider.get_org_apps = AsyncMock(return_value=[])
        svc.graph_provider.batch_upsert_nodes = AsyncMock()
        svc.graph_provider.batch_create_edges = AsyncMock()

        with patch(
            "app.services.messaging.kafka.handlers.entity.KnowledgeBaseConnector",
            create=True,
        ) as mock_kb_class:
            mock_kb_class._connector_metadata = {
                "name": "Knowledge Base",
                "appGroup": "Local Storage",
            }
            with patch(
                "app.services.messaging.kafka.handlers.entity.KB_CONNECTOR_NAME",
                "Knowledge Base",
                create=True,
            ):
                mock_connector = MagicMock()
                with patch(
                    "app.services.messaging.kafka.handlers.entity.ConnectorFactory"
                ) as mock_factory:
                    mock_factory.create_and_start_sync = AsyncMock(
                        return_value=mock_connector
                    )
                    result = await svc._EntityEventService__get_or_create_kb_app_for_org(
                        "org-1", "u1"
                    )
                    assert result is not None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        """Exception => returns None."""
        svc = _make_entity_service()
        svc.graph_provider.get_org_apps = AsyncMock(
            side_effect=Exception("DB error")
        )

        result = await svc._EntityEventService__get_or_create_kb_app_for_org(
            "org-1", "u1"
        )
        assert result is None


# ===================================================================
# __create_user_kb_app_relation (lines 754-803)
# ===================================================================


class TestCreateUserKbAppRelation:
    """Cover __create_user_kb_app_relation."""

    @pytest.mark.asyncio
    async def test_creates_new_edge(self):
        """Creates user-app relation edge when not exists."""
        svc = _make_entity_service()

        # Mock __get_or_create_kb_app_for_org to return a KB app
        with patch.object(
            svc,
            "_EntityEventService__get_or_create_kb_app_for_org",
            new_callable=AsyncMock,
            return_value={"_key": "kb-app-1", "type": "KB"},
        ):
            svc.graph_provider.get_edges_from_node = AsyncMock(return_value=[])
            svc.graph_provider.batch_create_edges = AsyncMock()

            result = await svc._EntityEventService__create_user_kb_app_relation(
                "user-key-1", "org-1"
            )
            assert result is True
            svc.graph_provider.batch_create_edges.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_edge_already_exists(self):
        """Edge already exists, does not create duplicate."""
        svc = _make_entity_service()

        with patch.object(
            svc,
            "_EntityEventService__get_or_create_kb_app_for_org",
            new_callable=AsyncMock,
            return_value={"_key": "kb-app-1", "type": "KB"},
        ):
            svc.graph_provider.get_edges_from_node = AsyncMock(
                return_value=[{"to_id": "kb-app-1"}]
            )
            svc.graph_provider.batch_create_edges = AsyncMock()

            result = await svc._EntityEventService__create_user_kb_app_relation(
                "user-key-1", "org-1"
            )
            assert result is True
            svc.graph_provider.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_kb_app_returns_false(self):
        """No KB app found => returns False."""
        svc = _make_entity_service()

        with patch.object(
            svc,
            "_EntityEventService__get_or_create_kb_app_for_org",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await svc._EntityEventService__create_user_kb_app_relation(
                "user-key-1", "org-1"
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        """Exception => returns False."""
        svc = _make_entity_service()

        with patch.object(
            svc,
            "_EntityEventService__get_or_create_kb_app_for_org",
            new_callable=AsyncMock,
            side_effect=Exception("DB error"),
        ):
            result = await svc._EntityEventService__create_user_kb_app_relation(
                "user-key-1", "org-1"
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_edge_exists_via_arango_to_field(self):
        """Edge exists with _to field format (ArangoDB style)."""
        svc = _make_entity_service()

        with patch.object(
            svc,
            "_EntityEventService__get_or_create_kb_app_for_org",
            new_callable=AsyncMock,
            return_value={"_key": "kb-app-1", "type": "KB"},
        ):
            svc.graph_provider.get_edges_from_node = AsyncMock(
                return_value=[{"_to": "apps/kb-app-1"}]
            )
            svc.graph_provider.batch_create_edges = AsyncMock()

            result = await svc._EntityEventService__create_user_kb_app_relation(
                "user-key-1", "org-1"
            )
            assert result is True
            svc.graph_provider.batch_create_edges.assert_not_awaited()
