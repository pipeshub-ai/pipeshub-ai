"""Unit tests for app.services.messaging.kafka.handlers.entity.EntityEventService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import (
    AccountType,
    CollectionNames,
    Connectors,
    ConnectorScopes,
)
import logging
from app.config.constants.arangodb import AccountType, Connectors
from app.services.messaging.kafka.handlers.entity import EntityEventService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entity_service(graph_provider=None, app_container=None, logger=None):
    """Create an EntityEventService with mock dependencies."""
    from app.services.messaging.kafka.handlers.entity import EntityEventService

    logger = logger or MagicMock()
    graph_provider = graph_provider or AsyncMock()
    app_container = app_container or MagicMock()
    app_container.messaging_producer = AsyncMock()
    app_container.messaging_producer.send_message = AsyncMock()

    return EntityEventService(
        logger=logger,
        graph_provider=graph_provider,
        app_container=app_container,
    )


# ===================================================================
# process_event — routing
# ===================================================================

class TestProcessEvent:
    """Tests for the main event routing dispatcher."""

    @pytest.mark.asyncio
    async def test_unknown_event_returns_false(self):
        svc = _make_entity_service()
        result = await svc.process_event("unknownEvent", {})
        assert result is False

    @pytest.mark.asyncio
    async def test_routes_to_org_created(self):
        svc = _make_entity_service()
        svc._EntityEventService__handle_org_created = AsyncMock(return_value=True)
        result = await svc.process_event("orgCreated", {"orgId": "org-1"})
        assert result is True
        svc._EntityEventService__handle_org_created.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_routes_to_org_updated(self):
        svc = _make_entity_service()
        svc._EntityEventService__handle_org_updated = AsyncMock(return_value=True)
        result = await svc.process_event("orgUpdated", {"orgId": "org-1"})
        assert result is True

    @pytest.mark.asyncio
    async def test_routes_to_user_added(self):
        svc = _make_entity_service()
        svc._EntityEventService__handle_user_added = AsyncMock(return_value=True)
        result = await svc.process_event("userAdded", {"userId": "u-1"})
        assert result is True

    @pytest.mark.asyncio
    async def test_routes_to_user_updated(self):
        svc = _make_entity_service()
        svc._EntityEventService__handle_user_updated = AsyncMock(return_value=True)
        result = await svc.process_event("userUpdated", {"userId": "u-1"})
        assert result is True

    @pytest.mark.asyncio
    async def test_routes_to_user_deleted(self):
        svc = _make_entity_service()
        svc._EntityEventService__handle_user_deleted = AsyncMock(return_value=True)
        result = await svc.process_event("userDeleted", {"email": "test@test.com"})
        assert result is True

    @pytest.mark.asyncio
    async def test_routes_to_app_enabled(self):
        svc = _make_entity_service()
        svc._EntityEventService__handle_app_enabled = AsyncMock(return_value=True)
        result = await svc.process_event("appEnabled", {"orgId": "org-1"})
        assert result is True

    @pytest.mark.asyncio
    async def test_routes_to_app_disabled(self):
        svc = _make_entity_service()
        svc._EntityEventService__handle_app_disabled = AsyncMock(return_value=True)
        result = await svc.process_event("appDisabled", {"orgId": "org-1"})
        assert result is True

    @pytest.mark.asyncio
    async def test_exception_in_handler_returns_false(self):
        svc = _make_entity_service()
        svc._EntityEventService__handle_org_created = AsyncMock(
            side_effect=Exception("boom")
        )
        result = await svc.process_event("orgCreated", {"orgId": "org-1"})
        assert result is False


# ===================================================================
# orgCreated — deeper tests
# ===================================================================

class TestOrgCreated:
    """Tests for __handle_org_created."""

    @pytest.mark.asyncio
    async def test_enterprise_account_type(self):
        """Business/enterprise payload maps to ENTERPRISE account type."""
        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock()
        graph_provider.get_nodes_by_filters = AsyncMock(return_value=[])
        svc = _make_entity_service(graph_provider=graph_provider)
        svc._EntityEventService__create_kb_connector_app_instance = AsyncMock()

        payload = {
            "orgId": "org-1",
            "accountType": AccountType.ENTERPRISE.value,
            "registeredName": "Acme Corp",
            "userId": "u-1",
        }
        result = await svc.process_event("orgCreated", payload)
        assert result is True

        # Verify org data
        call_args = graph_provider.batch_upsert_nodes.call_args_list[0]
        org_data = call_args[0][0][0]
        assert org_data["accountType"] == AccountType.ENTERPRISE.value
        assert org_data["name"] == "Acme Corp"

    @pytest.mark.asyncio
    async def test_business_account_type_maps_to_enterprise(self):
        """Business account type should map to ENTERPRISE."""
        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock()
        graph_provider.get_nodes_by_filters = AsyncMock(return_value=[])
        svc = _make_entity_service(graph_provider=graph_provider)
        svc._EntityEventService__create_kb_connector_app_instance = AsyncMock()

        payload = {
            "orgId": "org-2",
            "accountType": AccountType.BUSINESS.value,
            "userId": "u-1",
        }
        result = await svc.process_event("orgCreated", payload)
        assert result is True

        call_args = graph_provider.batch_upsert_nodes.call_args_list[0]
        org_data = call_args[0][0][0]
        assert org_data["accountType"] == AccountType.ENTERPRISE.value

    @pytest.mark.asyncio
    async def test_individual_account_type(self):
        """Non-business/enterprise account type maps to INDIVIDUAL."""
        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock()
        graph_provider.get_nodes_by_filters = AsyncMock(return_value=[])
        svc = _make_entity_service(graph_provider=graph_provider)
        svc._EntityEventService__create_kb_connector_app_instance = AsyncMock()

        payload = {
            "orgId": "org-3",
            "accountType": AccountType.INDIVIDUAL.value,
            "userId": "u-1",
        }
        result = await svc.process_event("orgCreated", payload)
        assert result is True

        call_args = graph_provider.batch_upsert_nodes.call_args_list[0]
        org_data = call_args[0][0][0]
        assert org_data["accountType"] == AccountType.INDIVIDUAL.value

    @pytest.mark.asyncio
    async def test_with_departments(self):
        """When departments exist with orgId=None, edges are created."""
        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock()
        graph_provider.get_nodes_by_filters = AsyncMock(
            return_value=[
                {"id": "dept-1", "departmentName": "Engineering"},
                {"_key": "dept-2", "departmentName": "Sales"},
            ]
        )
        graph_provider.batch_create_edges = AsyncMock()
        svc = _make_entity_service(graph_provider=graph_provider)
        svc._EntityEventService__create_kb_connector_app_instance = AsyncMock()

        payload = {
            "orgId": "org-1",
            "accountType": AccountType.ENTERPRISE.value,
            "userId": "u-1",
        }
        result = await svc.process_event("orgCreated", payload)
        assert result is True

        # Should create department edges
        graph_provider.batch_create_edges.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_departments_no_edge_creation(self):
        """When no departments exist, no edge creation call is made."""
        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock()
        graph_provider.get_nodes_by_filters = AsyncMock(return_value=[])
        graph_provider.batch_create_edges = AsyncMock()
        svc = _make_entity_service(graph_provider=graph_provider)
        svc._EntityEventService__create_kb_connector_app_instance = AsyncMock()

        payload = {
            "orgId": "org-1",
            "accountType": AccountType.INDIVIDUAL.value,
            "userId": "u-1",
        }
        result = await svc.process_event("orgCreated", payload)
        assert result is True

        graph_provider.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_registered_name_uses_default(self):
        """When registeredName is absent, uses 'Individual Account'."""
        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock()
        graph_provider.get_nodes_by_filters = AsyncMock(return_value=[])
        svc = _make_entity_service(graph_provider=graph_provider)
        svc._EntityEventService__create_kb_connector_app_instance = AsyncMock()

        payload = {
            "orgId": "org-1",
            "accountType": AccountType.INDIVIDUAL.value,
            "userId": "u-1",
        }
        result = await svc.process_event("orgCreated", payload)
        assert result is True

        call_args = graph_provider.batch_upsert_nodes.call_args_list[0]
        org_data = call_args[0][0][0]
        assert org_data["name"] == "Individual Account"

    @pytest.mark.asyncio
    async def test_db_error_returns_false(self):
        """Database error during org creation returns False."""
        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock(
            side_effect=Exception("DB error")
        )
        svc = _make_entity_service(graph_provider=graph_provider)

        payload = {
            "orgId": "org-1",
            "accountType": AccountType.INDIVIDUAL.value,
            "userId": "u-1",
        }
        result = await svc.process_event("orgCreated", payload)
        assert result is False


# ===================================================================
# orgUpdated
# ===================================================================

class TestOrgUpdated:
    """Tests for __handle_org_updated."""

    @pytest.mark.asyncio
    async def test_success(self):
        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock()
        svc = _make_entity_service(graph_provider=graph_provider)

        payload = {
            "orgId": "org-1",
            "registeredName": "Updated Corp",
        }
        result = await svc.process_event("orgUpdated", payload)
        assert result is True

        call_args = graph_provider.batch_upsert_nodes.call_args
        org_data = call_args[0][0][0]
        assert org_data["name"] == "Updated Corp"
        assert org_data["id"] == "org-1"

    @pytest.mark.asyncio
    async def test_failure_returns_false(self):
        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock(
            side_effect=Exception("update failed")
        )
        svc = _make_entity_service(graph_provider=graph_provider)

        payload = {
            "orgId": "org-1",
            "registeredName": "Updated Corp",
        }
        result = await svc.process_event("orgUpdated", payload)
        assert result is False


# ===================================================================
# userAdded — deeper tests
# ===================================================================

class TestUserAdded:
    """Tests for __handle_user_added."""

    @pytest.mark.asyncio
    async def test_new_user_created(self):
        """When user doesn't exist, creates new user."""
        graph_provider = AsyncMock()
        graph_provider.get_user_by_email = AsyncMock(return_value=None)
        graph_provider.get_document = AsyncMock(
            return_value={"_key": "org-1", "accountType": "enterprise"}
        )
        graph_provider.batch_upsert_nodes = AsyncMock()
        graph_provider.batch_create_edges = AsyncMock()
        graph_provider.get_nodes_by_filters = AsyncMock(return_value=[])

        svc = _make_entity_service(graph_provider=graph_provider)
        svc._EntityEventService__get_or_create_knowledge_base = AsyncMock(return_value={})
        svc._EntityEventService__create_user_kb_app_relation = AsyncMock()

        payload = {
            "orgId": "org-1",
            "userId": "u-1",
            "email": "alice@test.com",
            "fullName": "Alice Smith",
            "firstName": "Alice",
            "lastName": "Smith",
            "syncAction": "none",
        }
        result = await svc.process_event("userAdded", payload)
        assert result is True

        # Should upsert user
        graph_provider.batch_upsert_nodes.assert_awaited()
        # Should create belongs_to edge
        graph_provider.batch_create_edges.assert_awaited()

    @pytest.mark.asyncio
    async def test_existing_user_updated(self):
        """When user exists by email, updates existing user."""
        existing_user = MagicMock()
        existing_user.id = "existing-key"

        graph_provider = AsyncMock()
        graph_provider.get_user_by_email = AsyncMock(return_value=existing_user)
        graph_provider.get_document = AsyncMock(
            return_value={"_key": "org-1", "accountType": "enterprise"}
        )
        graph_provider.batch_upsert_nodes = AsyncMock()
        graph_provider.batch_create_edges = AsyncMock()
        graph_provider.get_nodes_by_filters = AsyncMock(return_value=[])

        svc = _make_entity_service(graph_provider=graph_provider)
        svc._EntityEventService__get_or_create_knowledge_base = AsyncMock(return_value={})
        svc._EntityEventService__create_user_kb_app_relation = AsyncMock()

        payload = {
            "orgId": "org-1",
            "userId": "u-1",
            "email": "alice@test.com",
            "syncAction": "none",
        }
        result = await svc.process_event("userAdded", payload)
        assert result is True

        # Should use existing key
        call_args = graph_provider.batch_upsert_nodes.call_args_list[0]
        user_data = call_args[0][0][0]
        assert user_data["id"] == "existing-key"

    @pytest.mark.asyncio
    async def test_with_sync_action_immediate(self):
        """When syncAction is 'immediate', triggers sync for org apps."""
        graph_provider = AsyncMock()
        graph_provider.get_user_by_email = AsyncMock(return_value=None)
        graph_provider.get_document = AsyncMock(
            return_value={"_key": "org-1", "accountType": "enterprise"}
        )
        graph_provider.batch_upsert_nodes = AsyncMock()
        graph_provider.batch_create_edges = AsyncMock()
        graph_provider.get_org_apps = AsyncMock(
            return_value=[{"name": "GoogleDrive"}]
        )
        graph_provider.get_nodes_by_filters = AsyncMock(return_value=[])

        svc = _make_entity_service(graph_provider=graph_provider)
        svc._EntityEventService__get_or_create_knowledge_base = AsyncMock(return_value={})
        svc._EntityEventService__create_user_kb_app_relation = AsyncMock()
        svc._EntityEventService__handle_sync_event = AsyncMock(return_value=True)

        payload = {
            "orgId": "org-1",
            "userId": "u-1",
            "email": "alice@test.com",
            "syncAction": "immediate",
        }
        result = await svc.process_event("userAdded", payload)
        assert result is True

        svc._EntityEventService__handle_sync_event.assert_awaited()

    @pytest.mark.asyncio
    async def test_calendar_app_skipped_during_sync(self):
        """Calendar apps are skipped during sync trigger."""
        graph_provider = AsyncMock()
        graph_provider.get_user_by_email = AsyncMock(return_value=None)
        graph_provider.get_document = AsyncMock(
            return_value={"_key": "org-1", "accountType": "enterprise"}
        )
        graph_provider.batch_upsert_nodes = AsyncMock()
        graph_provider.batch_create_edges = AsyncMock()
        graph_provider.get_org_apps = AsyncMock(
            return_value=[{"name": "Calendar"}, {"name": "GoogleDrive"}]
        )
        graph_provider.get_nodes_by_filters = AsyncMock(return_value=[])

        svc = _make_entity_service(graph_provider=graph_provider)
        svc._EntityEventService__get_or_create_knowledge_base = AsyncMock(return_value={})
        svc._EntityEventService__create_user_kb_app_relation = AsyncMock()
        svc._EntityEventService__handle_sync_event = AsyncMock(return_value=True)

        payload = {
            "orgId": "org-1",
            "userId": "u-1",
            "email": "alice@test.com",
            "syncAction": "immediate",
        }
        await svc.process_event("userAdded", payload)

        # Only GoogleDrive sync should be triggered, not Calendar
        assert svc._EntityEventService__handle_sync_event.await_count == 1

    @pytest.mark.asyncio
    async def test_org_not_found_returns_false(self):
        """When org doesn't exist, returns False."""
        graph_provider = AsyncMock()
        graph_provider.get_user_by_email = AsyncMock(return_value=None)
        graph_provider.get_document = AsyncMock(return_value=None)

        svc = _make_entity_service(graph_provider=graph_provider)

        payload = {
            "orgId": "org-missing",
            "userId": "u-1",
            "email": "alice@test.com",
            "syncAction": "none",
        }
        result = await svc.process_event("userAdded", payload)
        assert result is False


# ===================================================================
# userUpdated
# ===================================================================

class TestUserUpdated:
    """Tests for __handle_user_updated."""

    @pytest.mark.asyncio
    async def test_success(self):
        existing_user = {"id": "user-key-1", "_key": "user-key-1"}

        graph_provider = AsyncMock()
        graph_provider.get_user_by_user_id = AsyncMock(return_value=existing_user)
        graph_provider.batch_upsert_nodes = AsyncMock()

        svc = _make_entity_service(graph_provider=graph_provider)

        payload = {
            "userId": "u-1",
            "orgId": "org-1",
            "email": "alice@test.com",
            "fullName": "Alice Smith Updated",
            "firstName": "Alice",
            "lastName": "Smith",
        }
        result = await svc.process_event("userUpdated", payload)
        assert result is True

        call_args = graph_provider.batch_upsert_nodes.call_args
        user_data = call_args[0][0][0]
        assert user_data["fullName"] == "Alice Smith Updated"
        assert user_data["isActive"] is True

    @pytest.mark.asyncio
    async def test_user_not_found_returns_false(self):
        graph_provider = AsyncMock()
        graph_provider.get_user_by_user_id = AsyncMock(return_value=None)

        svc = _make_entity_service(graph_provider=graph_provider)

        payload = {
            "userId": "u-missing",
            "orgId": "org-1",
            "email": "unknown@test.com",
        }
        result = await svc.process_event("userUpdated", payload)
        assert result is False

    @pytest.mark.asyncio
    async def test_db_error_returns_false(self):
        graph_provider = AsyncMock()
        graph_provider.get_user_by_user_id = AsyncMock(
            side_effect=Exception("DB fail")
        )
        svc = _make_entity_service(graph_provider=graph_provider)

        payload = {
            "userId": "u-1",
            "orgId": "org-1",
            "email": "alice@test.com",
        }
        result = await svc.process_event("userUpdated", payload)
        assert result is False


# ===================================================================
# userDeleted
# ===================================================================

class TestUserDeleted:
    """Tests for __handle_user_deleted."""

    @pytest.mark.asyncio
    async def test_success(self):
        graph_provider = AsyncMock()
        graph_provider.get_entity_id_by_email = AsyncMock(
            return_value="user-key-1"
        )
        graph_provider.batch_upsert_nodes = AsyncMock()

        svc = _make_entity_service(graph_provider=graph_provider)

        payload = {
            "orgId": "org-1",
            "email": "alice@test.com",
        }
        result = await svc.process_event("userDeleted", payload)
        assert result is True

        call_args = graph_provider.batch_upsert_nodes.call_args
        user_data = call_args[0][0][0]
        assert user_data["isActive"] is False
        assert user_data["id"] == "user-key-1"

    @pytest.mark.asyncio
    async def test_user_not_found_returns_false(self):
        graph_provider = AsyncMock()
        graph_provider.get_entity_id_by_email = AsyncMock(return_value=None)

        svc = _make_entity_service(graph_provider=graph_provider)

        payload = {
            "orgId": "org-1",
            "email": "unknown@test.com",
        }
        result = await svc.process_event("userDeleted", payload)
        assert result is False

    @pytest.mark.asyncio
    async def test_db_error_returns_false(self):
        graph_provider = AsyncMock()
        graph_provider.get_entity_id_by_email = AsyncMock(
            side_effect=Exception("DB fail")
        )
        svc = _make_entity_service(graph_provider=graph_provider)

        result = await svc.process_event(
            "userDeleted", {"orgId": "org-1", "email": "a@b.com"}
        )
        assert result is False


# ===================================================================
# appEnabled — deeper tests
# ===================================================================

class TestAppEnabled:
    """Tests for __handle_app_enabled."""

    @pytest.mark.asyncio
    async def test_success_no_sync(self):
        """Apps enabled without immediate sync."""
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"_key": "org-1", "accountType": "enterprise"}
        )

        svc = _make_entity_service(graph_provider=graph_provider)

        payload = {
            "orgId": "org-1",
            "apps": ["GoogleDrive", "Slack"],
            "syncAction": "none",
        }
        result = await svc.process_event("appEnabled", payload)
        assert result is True

    @pytest.mark.asyncio
    async def test_success_with_immediate_sync(self):
        """Apps enabled with immediate sync triggers sync events."""
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"_key": "org-1", "accountType": "enterprise"}
        )

        svc = _make_entity_service(graph_provider=graph_provider)
        svc._EntityEventService__handle_sync_event = AsyncMock(return_value=True)

        payload = {
            "orgId": "org-1",
            "apps": ["GoogleDrive", "Slack"],
            "syncAction": "immediate",
            "connectorId": "conn-1",
            "scope": ConnectorScopes.PERSONAL.value,
        }
        result = await svc.process_event("appEnabled", payload)
        assert result is True

        # Should trigger sync for each app
        assert svc._EntityEventService__handle_sync_event.await_count == 2

    @pytest.mark.asyncio
    async def test_org_not_found_returns_false(self):
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value=None)

        svc = _make_entity_service(graph_provider=graph_provider)

        payload = {
            "orgId": "org-missing",
            "apps": ["GoogleDrive"],
            "syncAction": "immediate",
        }
        result = await svc.process_event("appEnabled", payload)
        assert result is False

    @pytest.mark.asyncio
    async def test_db_error_returns_false(self):
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            side_effect=Exception("DB error")
        )

        svc = _make_entity_service(graph_provider=graph_provider)

        payload = {
            "orgId": "org-1",
            "apps": ["GoogleDrive"],
        }
        result = await svc.process_event("appEnabled", payload)
        assert result is False

    @pytest.mark.asyncio
    async def test_default_sync_action_is_none(self):
        """When syncAction is not in payload, defaults to 'none'."""
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"_key": "org-1"}
        )

        svc = _make_entity_service(graph_provider=graph_provider)
        svc._EntityEventService__handle_sync_event = AsyncMock(return_value=True)

        payload = {
            "orgId": "org-1",
            "apps": ["GoogleDrive"],
            # No syncAction key
        }
        result = await svc.process_event("appEnabled", payload)
        assert result is True

        # No sync should be triggered
        svc._EntityEventService__handle_sync_event.assert_not_awaited()


# ===================================================================
# appDisabled — deeper tests
# ===================================================================

class TestAppDisabled:
    """Tests for __handle_app_disabled."""

    @pytest.mark.asyncio
    async def test_success(self):
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={
                "_key": "conn-1",
                "name": "GoogleDrive",
                "type": "connector",
                "appGroup": "google",
                "createdAtTimestamp": 123456,
            }
        )
        graph_provider.batch_upsert_nodes = AsyncMock()

        svc = _make_entity_service(graph_provider=graph_provider)

        with patch(
            "app.services.messaging.kafka.handlers.entity.sync_task_manager"
        ) as mock_stm:
            mock_stm.cancel_sync = AsyncMock()
            payload = {
                "orgId": "org-1",
                "apps": ["GoogleDrive"],
                "connectorId": "conn-1",
            }
            result = await svc.process_event("appDisabled", payload)
            assert result is True

            # App should be marked inactive
            call_args = graph_provider.batch_upsert_nodes.call_args
            app_data = call_args[0][0][0]
            assert app_data["isActive"] is False

            # Sync should be cancelled
            mock_stm.cancel_sync.assert_awaited_once_with("conn-1")

    @pytest.mark.asyncio
    async def test_missing_org_id_returns_false(self):
        svc = _make_entity_service()

        payload = {
            "orgId": "",
            "apps": ["GoogleDrive"],
        }
        result = await svc.process_event("appDisabled", payload)
        assert result is False

    @pytest.mark.asyncio
    async def test_missing_apps_returns_false(self):
        svc = _make_entity_service()

        payload = {
            "orgId": "org-1",
            "apps": [],
        }
        result = await svc.process_event("appDisabled", payload)
        assert result is False

    @pytest.mark.asyncio
    async def test_app_not_found_returns_false(self):
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value=None)

        svc = _make_entity_service(graph_provider=graph_provider)

        payload = {
            "orgId": "org-1",
            "apps": ["NonExistentApp"],
            "connectorId": "conn-missing",
        }
        result = await svc.process_event("appDisabled", payload)
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_sync_failure_does_not_fail_overall(self):
        """Even if cancel_sync fails, the overall operation should succeed."""
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={
                "_key": "conn-1",
                "name": "GoogleDrive",
                "type": "connector",
                "appGroup": "google",
                "createdAtTimestamp": 123456,
            }
        )
        graph_provider.batch_upsert_nodes = AsyncMock()

        svc = _make_entity_service(graph_provider=graph_provider)

        with patch(
            "app.services.messaging.kafka.handlers.entity.sync_task_manager"
        ) as mock_stm:
            mock_stm.cancel_sync = AsyncMock(
                side_effect=Exception("cancel failed")
            )
            payload = {
                "orgId": "org-1",
                "apps": ["GoogleDrive"],
                "connectorId": "conn-1",
            }
            result = await svc.process_event("appDisabled", payload)
            assert result is True


# ===================================================================
# _kb_name_from_user_added_payload
# ===================================================================

class TestKbNameFromPayload:
    """Tests for _kb_name_from_user_added_payload."""

    def test_with_full_name(self):
        svc = _make_entity_service()
        payload = {"fullName": "Alice Smith", "email": "alice@test.com"}
        assert svc._kb_name_from_user_added_payload(payload) == "Alice Smith's Private"

    def test_without_full_name_uses_email(self):
        svc = _make_entity_service()
        payload = {"fullName": "", "email": "alice@test.com"}
        assert svc._kb_name_from_user_added_payload(payload) == "alice@test.com's Private"

    def test_without_full_name_or_email(self):
        svc = _make_entity_service()
        payload = {"fullName": "", "email": ""}
        assert svc._kb_name_from_user_added_payload(payload) == "Private"

    def test_whitespace_only_full_name_uses_email(self):
        svc = _make_entity_service()
        payload = {"fullName": "   ", "email": "bob@test.com"}
        assert svc._kb_name_from_user_added_payload(payload) == "bob@test.com's Private"

    def test_no_keys_present(self):
        svc = _make_entity_service()
        payload = {}
        assert svc._kb_name_from_user_added_payload(payload) == "Private"


# ===================================================================
# __handle_sync_event
# ===================================================================

class TestHandleSyncEvent:
    """Tests for __handle_sync_event."""

    @pytest.mark.asyncio
    async def test_success(self):
        svc = _make_entity_service()

        result = await svc._EntityEventService__handle_sync_event(
            "googledrive.start", {"orgId": "org-1"}
        )
        assert result is True

        svc.app_container.messaging_producer.send_message.assert_awaited_once()
        call_args = svc.app_container.messaging_producer.send_message.call_args
        assert call_args[1]["topic"] == "sync-events"
        msg = call_args[1]["message"]
        assert msg["eventType"] == "googledrive.start"

    @pytest.mark.asyncio
    async def test_send_failure_returns_false(self):
        svc = _make_entity_service()
        svc.app_container.messaging_producer.send_message = AsyncMock(
            side_effect=Exception("kafka down")
        )

        result = await svc._EntityEventService__handle_sync_event(
            "googledrive.start", {}
        )
        assert result is False

# =============================================================================
# Merged from test_entity_handler_coverage.py
# =============================================================================

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

class TestHandleSyncEventCoverage:

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
        call_data = svc.graph_provider.batch_upsert_nodes.call_args[0][0][0]
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
