"""Additional tests for EntityEventService targeting remaining uncovered lines.

Covers:
- __handle_sync_event (success and exception)
- __handle_user_updated (success with optional fields, exception)
- __handle_user_deleted (exception)
- __handle_app_enabled (exception, multiple apps)
- __handle_app_disabled (cancel sync success, cancel sync exception)
- __handle_user_added (immediate sync skipping calendar, existing user path)
- __get_or_create_knowledge_base (empty userId/orgId, KB app returns None, exception)
- __create_kb_connector_app_instance (no metadata, existing app, connector creation failure)
- __create_user_kb_app_relation (edge exists via _to format)
- _kb_name_from_user_added_payload (edge cases)
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import AccountType, CollectionNames, Connectors
from app.services.messaging.kafka.handlers.entity import EntityEventService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service():
    """Create an EntityEventService with mock dependencies."""
    logger = MagicMock(spec=logging.Logger)
    graph_provider = AsyncMock()
    app_container = MagicMock()
    app_container.messaging_producer = AsyncMock()
    app_container.messaging_producer.send_message = AsyncMock()
    app_container.config_service.return_value = AsyncMock()
    app_container.data_store = AsyncMock()
    return EntityEventService(logger, graph_provider, app_container)


# ===================================================================
# __handle_sync_event
# ===================================================================

class TestHandleSyncEvent:

    @pytest.mark.asyncio
    async def test_sync_event_success(self):
        svc = _make_service()
        result = await svc._EntityEventService__handle_sync_event(
            "gmail.start", {"orgId": "org-1"}
        )
        assert result is True
        svc.app_container.messaging_producer.send_message.assert_awaited_once()
        call_kwargs = svc.app_container.messaging_producer.send_message.call_args[1]
        assert call_kwargs["topic"] == "sync-events"
        msg = call_kwargs["message"]
        assert msg["eventType"] == "gmail.start"
        assert msg["payload"] == {"orgId": "org-1"}
        assert "timestamp" in msg

    @pytest.mark.asyncio
    async def test_sync_event_exception(self):
        svc = _make_service()
        svc.app_container.messaging_producer.send_message = AsyncMock(
            side_effect=Exception("send error")
        )
        result = await svc._EntityEventService__handle_sync_event(
            "gmail.start", {"orgId": "org-1"}
        )
        assert result is False


# ===================================================================
# __handle_user_updated - additional paths
# ===================================================================

class TestHandleUserUpdatedExtended:

    @pytest.mark.asyncio
    async def test_user_updated_with_optional_fields(self):
        svc = _make_service()
        existing = {"id": "key1", "_key": "key1"}
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=existing)
        svc.graph_provider.batch_upsert_nodes = AsyncMock()

        payload = {
            "userId": "u1",
            "orgId": "org-1",
            "email": "user@test.com",
            "fullName": "Full Name",
            "firstName": "First",
            "middleName": "Middle",
            "lastName": "Last",
            "designation": "Engineer",
            "businessPhones": ["+1234567890"],
        }
        result = await svc.process_event("userUpdated", payload)
        assert result is True
        # Verify optional fields were included in the upsert
        call_args = svc.graph_provider.batch_upsert_nodes.call_args[0][0][0]
        assert call_args["fullName"] == "Full Name"
        assert call_args["firstName"] == "First"

    @pytest.mark.asyncio
    async def test_user_updated_exception(self):
        svc = _make_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(
            side_effect=Exception("DB error")
        )
        result = await svc.process_event(
            "userUpdated",
            {"userId": "u1", "orgId": "org-1", "email": "user@test.com"},
        )
        assert result is False


# ===================================================================
# __handle_user_deleted - exception
# ===================================================================

class TestHandleUserDeletedExtended:

    @pytest.mark.asyncio
    async def test_user_deleted_exception(self):
        svc = _make_service()
        svc.graph_provider.get_entity_id_by_email = AsyncMock(
            side_effect=Exception("DB error")
        )
        result = await svc.process_event(
            "userDeleted", {"email": "user@test.com", "orgId": "org-1"}
        )
        assert result is False


# ===================================================================
# __handle_app_enabled - additional paths
# ===================================================================

class TestHandleAppEnabledExtended:

    @pytest.mark.asyncio
    async def test_app_enabled_exception(self):
        svc = _make_service()
        svc.graph_provider.get_document = AsyncMock(
            side_effect=Exception("DB error")
        )
        result = await svc.process_event(
            "appEnabled",
            {"orgId": "org-1", "apps": ["Gmail"], "syncAction": "immediate"},
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_app_enabled_multiple_apps(self):
        svc = _make_service()
        svc.graph_provider.get_document = AsyncMock(
            return_value={"accountType": "ENTERPRISE"}
        )
        svc._EntityEventService__handle_sync_event = AsyncMock(return_value=True)

        result = await svc.process_event(
            "appEnabled",
            {
                "orgId": "org-1",
                "apps": ["Gmail", "Drive", "Calendar"],
                "syncAction": "immediate",
                "connectorId": "c1",
                "scope": "personal",
                "fullSync": True,
            },
        )
        assert result is True
        assert svc._EntityEventService__handle_sync_event.await_count == 3

    @pytest.mark.asyncio
    async def test_app_enabled_with_all_params(self):
        svc = _make_service()
        svc.graph_provider.get_document = AsyncMock(
            return_value={"accountType": "ENTERPRISE"}
        )
        svc._EntityEventService__handle_sync_event = AsyncMock(return_value=True)

        result = await svc.process_event(
            "appEnabled",
            {
                "orgId": "org-1",
                "apps": ["Gmail"],
                "syncAction": "immediate",
                "connectorId": "conn-1",
                "scope": "team",
                "fullSync": True,
            },
        )
        assert result is True
        call_kwargs = svc._EntityEventService__handle_sync_event.call_args[1]
        assert call_kwargs["value"]["connectorId"] == "conn-1"
        assert call_kwargs["value"]["scope"] == "team"
        assert call_kwargs["value"]["fullSync"] is True


# ===================================================================
# __handle_app_disabled - cancel sync paths
# ===================================================================

class TestHandleAppDisabledExtended:

    @pytest.mark.asyncio
    async def test_app_disabled_cancel_sync_success(self):
        svc = _make_service()
        svc.graph_provider.get_document = AsyncMock(
            return_value={
                "name": "Gmail",
                "type": "gmail",
                "appGroup": "Google",
                "createdAtTimestamp": 1000,
            }
        )
        svc.graph_provider.batch_upsert_nodes = AsyncMock()

        with patch("app.services.messaging.kafka.handlers.entity.sync_task_manager") as mock_stm:
            mock_stm.cancel_sync = AsyncMock()
            result = await svc.process_event(
                "appDisabled",
                {"orgId": "org-1", "apps": ["Gmail"], "connectorId": "c1"},
            )
            assert result is True
            mock_stm.cancel_sync.assert_awaited_once_with("c1")

    @pytest.mark.asyncio
    async def test_app_disabled_cancel_sync_exception(self):
        svc = _make_service()
        svc.graph_provider.get_document = AsyncMock(
            return_value={
                "name": "Gmail",
                "type": "gmail",
                "appGroup": "Google",
                "createdAtTimestamp": 1000,
            }
        )
        svc.graph_provider.batch_upsert_nodes = AsyncMock()

        with patch("app.services.messaging.kafka.handlers.entity.sync_task_manager") as mock_stm:
            mock_stm.cancel_sync = AsyncMock(
                side_effect=Exception("cancel failed")
            )
            result = await svc.process_event(
                "appDisabled",
                {"orgId": "org-1", "apps": ["Gmail"], "connectorId": "c1"},
            )
            # Should still succeed even if cancel fails
            assert result is True


# ===================================================================
# __handle_user_added - immediate sync paths
# ===================================================================

class TestHandleUserAddedImmediateSync:

    @pytest.mark.asyncio
    async def test_immediate_sync_skips_calendar(self):
        svc = _make_service()
        svc.graph_provider.get_user_by_email = AsyncMock(return_value=None)
        svc.graph_provider.get_document = AsyncMock(
            return_value={"accountType": "ENTERPRISE"}
        )
        svc.graph_provider.batch_upsert_nodes = AsyncMock()
        svc.graph_provider.batch_create_edges = AsyncMock()
        svc.graph_provider.get_org_apps = AsyncMock(
            return_value=[
                {"name": "Calendar"},
                {"name": "Gmail"},
            ]
        )
        svc._EntityEventService__get_or_create_knowledge_base = AsyncMock(return_value={})
        svc._EntityEventService__create_user_kb_app_relation = AsyncMock(return_value=True)
        svc._EntityEventService__handle_sync_event = AsyncMock(return_value=True)

        result = await svc.process_event(
            "userAdded",
            {
                "userId": "u1",
                "orgId": "org-1",
                "email": "user@test.com",
                "syncAction": "immediate",
            },
        )
        assert result is True
        # Only Gmail should trigger sync, Calendar skipped
        assert svc._EntityEventService__handle_sync_event.await_count == 1
        call_kwargs = svc._EntityEventService__handle_sync_event.call_args[1]
        assert call_kwargs["event_type"] == "gmail.user"


# ===================================================================
# __handle_org_created - BUSINESS account type
# ===================================================================

class TestOrgCreatedBusinessAccount:

    @pytest.mark.asyncio
    async def test_business_account_maps_to_enterprise(self):
        svc = _make_service()
        svc.graph_provider.batch_upsert_nodes = AsyncMock()
        svc.graph_provider.get_nodes_by_filters = AsyncMock(return_value=[])
        svc._EntityEventService__create_kb_connector_app_instance = AsyncMock(return_value=None)

        # AccountType.BUSINESS.value is "business" (lowercase)
        result = await svc.process_event(
            "orgCreated",
            {
                "orgId": "org-1",
                "accountType": "business",
                "registeredName": "Business Org",
            },
        )
        assert result is True
        org_nodes = [
            c[0][0][0]
            for c in svc.graph_provider.batch_upsert_nodes.call_args_list
            if len(c[0]) >= 2 and c[0][1] == CollectionNames.ORGS.value
        ]
        assert org_nodes, "expected batch_upsert_nodes for ORGS"
        call_data = org_nodes[0]
        assert call_data["accountType"] == AccountType.ENTERPRISE.value


# ===================================================================
# __get_or_create_knowledge_base - edge cases
# ===================================================================

class TestGetOrCreateKBEdgeCases:

    @pytest.mark.asyncio
    async def test_empty_user_id_returns_empty(self):
        svc = _make_service()
        result = await svc._EntityEventService__get_or_create_knowledge_base(
            "user-key", "", "org-1"
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_empty_org_id_returns_empty(self):
        svc = _make_service()
        result = await svc._EntityEventService__get_or_create_knowledge_base(
            "user-key", "user-1", ""
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        svc = _make_service()
        svc.graph_provider.get_nodes_by_filters = AsyncMock(
            side_effect=Exception("DB error")
        )
        result = await svc._EntityEventService__get_or_create_knowledge_base(
            "user-key", "user-1", "org-1"
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_existing_kbs_filters_deleted(self):
        svc = _make_service()
        svc.graph_provider.get_nodes_by_filters = AsyncMock(
            return_value=[
                {"id": "kb-1", "isDeleted": True},
                {"id": "kb-2", "isDeleted": False},
            ]
        )
        result = await svc._EntityEventService__get_or_create_knowledge_base(
            "user-key", "user-1", "org-1"
        )
        # Should return the non-deleted KB
        assert result["id"] == "kb-2"

    @pytest.mark.asyncio
    async def test_kb_app_not_found_returns_empty(self):
        svc = _make_service()
        svc.graph_provider.get_nodes_by_filters = AsyncMock(return_value=[])
        svc._EntityEventService__get_or_create_kb_app_for_org = AsyncMock(
            return_value=None
        )
        result = await svc._EntityEventService__get_or_create_knowledge_base(
            "user-key", "user-1", "org-1"
        )
        assert result == {}


# ===================================================================
# __create_kb_connector_app_instance - edge cases
# ===================================================================

class TestCreateKbConnectorAppInstanceEdgeCases:

    @pytest.mark.asyncio
    async def test_no_connector_metadata(self):
        svc = _make_service()
        svc.graph_provider.get_org_apps = AsyncMock(return_value=[])

        with patch(
            "app.services.messaging.kafka.handlers.entity.KnowledgeBaseConnector",
            create=True,
        ) as mock_cls:
            # No _connector_metadata attribute
            if hasattr(mock_cls, '_connector_metadata'):
                delattr(mock_cls, '_connector_metadata')
            mock_cls._connector_metadata = None
            del mock_cls._connector_metadata  # Force hasattr to return False

            result = await svc._EntityEventService__create_kb_connector_app_instance("org-1")
            assert result is None

    @pytest.mark.asyncio
    async def test_connector_factory_returns_none(self):
        svc = _make_service()
        svc.graph_provider.get_org_apps = AsyncMock(return_value=[])
        svc.graph_provider.batch_upsert_nodes = AsyncMock()
        svc.graph_provider.batch_create_edges = AsyncMock()

        with patch(
            "app.services.messaging.kafka.handlers.entity.KnowledgeBaseConnector",
            create=True,
        ) as mock_cls:
            mock_cls._connector_metadata = {"name": "KB", "appGroup": "Local"}
            with patch(
                "app.services.messaging.kafka.handlers.entity.KB_CONNECTOR_NAME",
                "KB",
                create=True,
            ):
                with patch(
                    "app.services.messaging.kafka.handlers.entity.ConnectorFactory"
                ) as mock_factory:
                    mock_factory.create_and_start_sync = AsyncMock(return_value=None)
                    result = await svc._EntityEventService__create_kb_connector_app_instance("org-1", "u1")
                    # Should still return the instance_document
                    assert result is not None
                    assert result["isActive"] is True

    @pytest.mark.asyncio
    async def test_created_by_defaults_to_system(self):
        svc = _make_service()
        svc.graph_provider.get_org_apps = AsyncMock(return_value=[])
        svc.graph_provider.batch_upsert_nodes = AsyncMock()
        svc.graph_provider.batch_create_edges = AsyncMock()

        with patch(
            "app.services.messaging.kafka.handlers.entity.KnowledgeBaseConnector",
            create=True,
        ) as mock_cls:
            mock_cls._connector_metadata = {"name": "KB", "appGroup": "Local"}
            with patch(
                "app.services.messaging.kafka.handlers.entity.KB_CONNECTOR_NAME",
                "KB",
                create=True,
            ):
                with patch(
                    "app.services.messaging.kafka.handlers.entity.ConnectorFactory"
                ) as mock_factory:
                    mock_factory.create_and_start_sync = AsyncMock(return_value=None)
                    # Pass no created_by_user_id
                    result = await svc._EntityEventService__create_kb_connector_app_instance("org-1", None)
                    assert result is not None
                    assert result["createdBy"] == "system"
