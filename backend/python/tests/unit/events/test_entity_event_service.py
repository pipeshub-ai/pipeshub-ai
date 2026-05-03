"""
Unit tests for EntityEventService (app/services/messaging/kafka/handlers/entity.py).

Covers:
- process_event dispatching for all event types
- Unknown event type returns False
- Exception in process_event returns False
- __handle_org_created: enterprise + individual account types, departments
- __handle_org_updated, __handle_org_deleted
- __handle_user_added: existing user, new user, immediate sync
- __handle_user_updated: found and not found
- __handle_user_deleted: found and not found
- __handle_app_enabled: immediate sync
- __handle_app_disabled: cancel sync, missing app
- __handle_sync_event
- _kb_name_from_user_added_payload
- __get_or_create_knowledge_base
- __create_kb_connector_app_instance
- __create_user_kb_app_relation
"""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.kafka.handlers.entity import EntityEventService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service():
    """Create an EntityEventService with mocked deps."""
    logger = MagicMock(spec=logging.Logger)
    graph_provider = AsyncMock()
    app_container = MagicMock()
    app_container.messaging_producer = AsyncMock()
    app_container.config_service.return_value = MagicMock()
    app_container.data_store = AsyncMock(return_value=MagicMock())

    service = EntityEventService(logger, graph_provider, app_container)
    return service, logger, graph_provider, app_container


# ============================================================================
# process_event dispatcher
# ============================================================================


class TestProcessEvent:
    @pytest.mark.asyncio
    async def test_unknown_event_type(self):
        svc, logger, gp, _ = _make_service()
        result = await svc.process_event("unknownEvent", {})
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        svc, logger, gp, _ = _make_service()
        # orgCreated will fail because payload is empty (KeyError)
        result = await svc.process_event("orgCreated", {})
        assert result is False

    @pytest.mark.asyncio
    async def test_dispatches_org_created(self):
        svc, logger, gp, _ = _make_service()
        gp.batch_upsert_nodes = AsyncMock()
        gp.get_nodes_by_filters = AsyncMock(return_value=[])
        gp.get_org_apps = AsyncMock(return_value=[])
        gp.batch_create_edges = AsyncMock()

        payload = {"orgId": "org-1", "accountType": "enterprise", "registeredName": "Test Corp"}
        with patch.object(svc, '_EntityEventService__create_kb_connector_app_instance', new_callable=AsyncMock, return_value=None):
            result = await svc.process_event("orgCreated", payload)
        assert result is True

    @pytest.mark.asyncio
    async def test_dispatches_org_updated(self):
        svc, logger, gp, _ = _make_service()
        gp.batch_upsert_nodes = AsyncMock()
        result = await svc.process_event("orgUpdated", {"orgId": "org-1", "registeredName": "New Name"})
        assert result is True

    @pytest.mark.asyncio
    async def test_dispatches_org_deleted(self):
        svc, logger, gp, _ = _make_service()
        gp.batch_upsert_nodes = AsyncMock()
        result = await svc.process_event("orgDeleted", {"orgId": "org-1"})
        assert result is True


# ============================================================================
# _kb_name_from_user_added_payload
# ============================================================================


class TestKbNameFromPayload:
    def test_with_fullname(self):
        svc, *_ = _make_service()
        payload = {"fullName": "Alice Smith", "email": "alice@example.com"}
        assert svc._kb_name_from_user_added_payload(payload) == "Alice Smith's Private"

    def test_with_email_only(self):
        svc, *_ = _make_service()
        payload = {"fullName": "", "email": "alice@example.com"}
        assert svc._kb_name_from_user_added_payload(payload) == "alice@example.com's Private"

    def test_with_nothing(self):
        svc, *_ = _make_service()
        payload = {"fullName": "", "email": ""}
        assert svc._kb_name_from_user_added_payload(payload) == "Private"

    def test_with_none_values(self):
        svc, *_ = _make_service()
        payload = {"fullName": None, "email": None}
        assert svc._kb_name_from_user_added_payload(payload) == "Private"

    def test_with_whitespace_fullname(self):
        svc, *_ = _make_service()
        payload = {"fullName": "   ", "email": "bob@example.com"}
        assert svc._kb_name_from_user_added_payload(payload) == "bob@example.com's Private"


# ============================================================================
# __handle_org_created
# ============================================================================


class TestHandleOrgCreated:
    @pytest.mark.asyncio
    async def test_enterprise_account_with_departments(self):
        svc, logger, gp, _ = _make_service()
        gp.batch_upsert_nodes = AsyncMock()
        gp.get_nodes_by_filters = AsyncMock(return_value=[
            {"id": "dept-1", "_key": "dept-1"},
            {"id": "dept-2"},
        ])
        gp.batch_create_edges = AsyncMock()
        gp.get_org_apps = AsyncMock(return_value=[])

        payload = {"orgId": "org-1", "accountType": "enterprise", "userId": "user-1"}
        with patch.object(svc, '_EntityEventService__create_kb_connector_app_instance', new_callable=AsyncMock, return_value=None):
            result = await svc.process_event("orgCreated", payload)
        assert result is True
        gp.batch_create_edges.assert_awaited()

    @pytest.mark.asyncio
    async def test_individual_account_no_departments(self):
        svc, logger, gp, _ = _make_service()
        gp.batch_upsert_nodes = AsyncMock()
        gp.get_nodes_by_filters = AsyncMock(return_value=[])
        gp.batch_create_edges = AsyncMock()
        gp.get_org_apps = AsyncMock(return_value=[])

        payload = {"orgId": "org-2", "accountType": "individual"}
        with patch.object(svc, '_EntityEventService__create_kb_connector_app_instance', new_callable=AsyncMock, return_value=None):
            result = await svc.process_event("orgCreated", payload)
        assert result is True

    @pytest.mark.asyncio
    async def test_business_account_treated_as_enterprise(self):
        svc, logger, gp, _ = _make_service()
        gp.batch_upsert_nodes = AsyncMock()
        gp.get_nodes_by_filters = AsyncMock(return_value=[])
        gp.batch_create_edges = AsyncMock()
        gp.get_org_apps = AsyncMock(return_value=[])

        payload = {"orgId": "org-3", "accountType": "business"}
        with patch.object(svc, '_EntityEventService__create_kb_connector_app_instance', new_callable=AsyncMock, return_value=None):
            result = await svc.process_event("orgCreated", payload)
        assert result is True

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        svc, logger, gp, _ = _make_service()
        gp.batch_upsert_nodes = AsyncMock(side_effect=Exception("db error"))
        payload = {"orgId": "org-1", "accountType": "enterprise"}
        result = await svc.process_event("orgCreated", payload)
        assert result is False


# ============================================================================
# __handle_org_updated / __handle_org_deleted
# ============================================================================


class TestHandleOrgUpdatedDeleted:
    @pytest.mark.asyncio
    async def test_org_updated_exception(self):
        svc, logger, gp, _ = _make_service()
        gp.batch_upsert_nodes = AsyncMock(side_effect=Exception("fail"))
        result = await svc.process_event("orgUpdated", {"orgId": "org-1", "registeredName": "X"})
        assert result is False

    @pytest.mark.asyncio
    async def test_org_deleted_exception(self):
        svc, logger, gp, _ = _make_service()
        gp.batch_upsert_nodes = AsyncMock(side_effect=Exception("fail"))
        result = await svc.process_event("orgDeleted", {"orgId": "org-1"})
        assert result is False


# ============================================================================
# __handle_user_added
# ============================================================================


class TestHandleUserAdded:
    @pytest.mark.asyncio
    async def test_existing_user(self):
        svc, logger, gp, _ = _make_service()
        existing_user = SimpleNamespace(id="user-key-1")
        gp.get_user_by_email = AsyncMock(return_value=existing_user)
        gp.get_document = AsyncMock(return_value={"accountType": "enterprise"})
        gp.batch_upsert_nodes = AsyncMock()
        gp.batch_create_edges = AsyncMock()
        gp.get_nodes_by_filters = AsyncMock(return_value=[])
        gp.get_org_apps = AsyncMock(return_value=[])
        gp.get_edges_from_node = AsyncMock(return_value=[])

        payload = {
            "userId": "uid-1",
            "orgId": "org-1",
            "email": "alice@example.com",
            "fullName": "Alice",
            "syncAction": "none",
        }

        with patch.object(svc, '_EntityEventService__get_or_create_knowledge_base', new_callable=AsyncMock, return_value={}):
            with patch.object(svc, '_EntityEventService__create_user_kb_app_relation', new_callable=AsyncMock, return_value=True):
                result = await svc.process_event("userAdded", payload)
        assert result is True

    @pytest.mark.asyncio
    async def test_new_user_with_immediate_sync(self):
        svc, logger, gp, container = _make_service()
        gp.get_user_by_email = AsyncMock(return_value=None)
        gp.get_document = AsyncMock(return_value={"accountType": "enterprise"})
        gp.batch_upsert_nodes = AsyncMock()
        gp.batch_create_edges = AsyncMock()
        gp.get_nodes_by_filters = AsyncMock(return_value=[])
        gp.get_org_apps = AsyncMock(return_value=[
            {"name": "Drive", "_key": "app-1"},
            {"name": "Calendar", "_key": "app-2"},  # Should be skipped
        ])
        gp.get_edges_from_node = AsyncMock(return_value=[])

        payload = {
            "userId": "uid-2",
            "orgId": "org-1",
            "email": "bob@example.com",
            "fullName": "Bob",
            "syncAction": "immediate",
        }

        with patch.object(svc, '_EntityEventService__get_or_create_knowledge_base', new_callable=AsyncMock, return_value={}):
            with patch.object(svc, '_EntityEventService__create_user_kb_app_relation', new_callable=AsyncMock, return_value=True):
                with patch.object(svc, '_EntityEventService__handle_sync_event', new_callable=AsyncMock, return_value=True) as mock_sync:
                    result = await svc.process_event("userAdded", payload)
        assert result is True
        # Drive should trigger sync, Calendar should not
        mock_sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_org_not_found_returns_false(self):
        svc, logger, gp, _ = _make_service()
        gp.get_user_by_email = AsyncMock(return_value=None)
        gp.get_document = AsyncMock(return_value=None)

        payload = {
            "userId": "uid-3",
            "orgId": "org-missing",
            "email": "charlie@example.com",
            "syncAction": "none",
        }
        result = await svc.process_event("userAdded", payload)
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        svc, logger, gp, _ = _make_service()
        gp.get_user_by_email = AsyncMock(side_effect=Exception("db error"))
        payload = {
            "userId": "uid-1",
            "orgId": "org-1",
            "email": "x@example.com",
            "syncAction": "none",
        }
        result = await svc.process_event("userAdded", payload)
        assert result is False


# ============================================================================
# __handle_user_updated
# ============================================================================


class TestHandleUserUpdated:
    @pytest.mark.asyncio
    async def test_user_not_found(self):
        svc, logger, gp, _ = _make_service()
        gp.get_user_by_user_id = AsyncMock(return_value=None)
        result = await svc.process_event("userUpdated", {
            "userId": "uid-1", "orgId": "org-1", "email": "a@b.com"
        })
        assert result is False

    @pytest.mark.asyncio
    async def test_user_found_and_updated(self):
        svc, logger, gp, _ = _make_service()
        gp.get_user_by_user_id = AsyncMock(return_value={"id": "key-1", "_key": "key-1"})
        gp.batch_upsert_nodes = AsyncMock()
        result = await svc.process_event("userUpdated", {
            "userId": "uid-1",
            "orgId": "org-1",
            "email": "a@b.com",
            "fullName": "Updated Name",
            "firstName": "First",
            "middleName": None,
            "lastName": "Last",
        })
        assert result is True

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        svc, logger, gp, _ = _make_service()
        gp.get_user_by_user_id = AsyncMock(side_effect=Exception("fail"))
        result = await svc.process_event("userUpdated", {
            "userId": "uid-1", "orgId": "org-1", "email": "a@b.com"
        })
        assert result is False


# ============================================================================
# __handle_user_deleted
# ============================================================================


class TestHandleUserDeleted:
    @pytest.mark.asyncio
    async def test_user_not_found(self):
        svc, logger, gp, _ = _make_service()
        gp.get_entity_id_by_email = AsyncMock(return_value=None)
        result = await svc.process_event("userDeleted", {
            "orgId": "org-1", "email": "ghost@example.com"
        })
        assert result is False

    @pytest.mark.asyncio
    async def test_user_found_and_soft_deleted(self):
        svc, logger, gp, _ = _make_service()
        gp.get_entity_id_by_email = AsyncMock(return_value="key-1")
        gp.batch_upsert_nodes = AsyncMock()
        result = await svc.process_event("userDeleted", {
            "orgId": "org-1", "email": "alice@example.com"
        })
        assert result is True

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        svc, logger, gp, _ = _make_service()
        gp.get_entity_id_by_email = AsyncMock(side_effect=Exception("fail"))
        result = await svc.process_event("userDeleted", {
            "orgId": "org-1", "email": "x@x.com"
        })
        assert result is False


# ============================================================================
# __handle_app_enabled
# ============================================================================


class TestHandleAppEnabled:
    @pytest.mark.asyncio
    async def test_app_enabled_immediate_sync(self):
        svc, logger, gp, container = _make_service()
        gp.get_document = AsyncMock(return_value={"accountType": "enterprise"})

        payload = {
            "orgId": "org-1",
            "apps": ["Drive"],
            "syncAction": "immediate",
            "connectorId": "conn-1",
            "scope": "team",
            "fullSync": True,
        }

        with patch.object(svc, '_EntityEventService__handle_sync_event', new_callable=AsyncMock, return_value=True) as mock_sync:
            result = await svc.process_event("appEnabled", payload)
        assert result is True
        mock_sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_app_enabled_no_sync_action(self):
        svc, logger, gp, _ = _make_service()
        gp.get_document = AsyncMock(return_value={"accountType": "enterprise"})
        payload = {
            "orgId": "org-1",
            "apps": ["Slack"],
            "syncAction": "none",
        }
        result = await svc.process_event("appEnabled", payload)
        assert result is True

    @pytest.mark.asyncio
    async def test_org_not_found(self):
        svc, logger, gp, _ = _make_service()
        gp.get_document = AsyncMock(return_value=None)
        payload = {"orgId": "org-missing", "apps": ["Drive"]}
        result = await svc.process_event("appEnabled", payload)
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        svc, logger, gp, _ = _make_service()
        gp.get_document = AsyncMock(side_effect=Exception("fail"))
        result = await svc.process_event("appEnabled", {"orgId": "org-1", "apps": ["X"]})
        assert result is False


# ============================================================================
# __handle_app_disabled
# ============================================================================


class TestHandleAppDisabled:
    @pytest.mark.asyncio
    async def test_app_disabled_success(self):
        svc, logger, gp, _ = _make_service()
        gp.get_document = AsyncMock(return_value={
            "name": "Drive", "type": "FILE", "appGroup": "Google",
            "createdAtTimestamp": 1000000,
        })
        gp.batch_upsert_nodes = AsyncMock()

        with patch("app.services.messaging.kafka.handlers.entity.sync_task_manager") as mock_stm:
            mock_stm.cancel_sync = AsyncMock()
            result = await svc.process_event("appDisabled", {
                "orgId": "org-1",
                "apps": ["Drive"],
                "connectorId": "conn-1",
            })
        assert result is True
        mock_stm.cancel_sync.assert_awaited_once_with("conn-1")

    @pytest.mark.asyncio
    async def test_missing_org_or_apps(self):
        svc, logger, gp, _ = _make_service()
        result = await svc.process_event("appDisabled", {
            "orgId": "",
            "apps": [],
            "connectorId": "conn-1",
        })
        assert result is False

    @pytest.mark.asyncio
    async def test_app_not_found(self):
        svc, logger, gp, _ = _make_service()
        gp.get_document = AsyncMock(return_value=None)
        result = await svc.process_event("appDisabled", {
            "orgId": "org-1",
            "apps": ["Missing"],
            "connectorId": "conn-1",
        })
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_sync_error_handled(self):
        svc, logger, gp, _ = _make_service()
        gp.get_document = AsyncMock(return_value={
            "name": "Slack", "type": "MESSAGING", "appGroup": "Slack",
            "createdAtTimestamp": 1000000,
        })
        gp.batch_upsert_nodes = AsyncMock()

        with patch("app.services.messaging.kafka.handlers.entity.sync_task_manager") as mock_stm:
            mock_stm.cancel_sync = AsyncMock(side_effect=Exception("cancel error"))
            result = await svc.process_event("appDisabled", {
                "orgId": "org-1",
                "apps": ["Slack"],
                "connectorId": "conn-2",
            })
        assert result is True

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        svc, logger, gp, _ = _make_service()
        gp.get_document = AsyncMock(side_effect=Exception("fail"))
        result = await svc.process_event("appDisabled", {
            "orgId": "org-1", "apps": ["X"], "connectorId": "c"
        })
        assert result is False


# ============================================================================
# __handle_sync_event
# ============================================================================


class TestHandleSyncEvent:
    @pytest.mark.asyncio
    async def test_send_sync_event_success(self):
        svc, logger, gp, container = _make_service()
        container.messaging_producer.send_message = AsyncMock()

        result = await svc._EntityEventService__handle_sync_event(
            "drive.start", {"orgId": "org-1"}
        )
        assert result is True
        container.messaging_producer.send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_sync_event_failure(self):
        svc, logger, gp, container = _make_service()
        container.messaging_producer.send_message = AsyncMock(side_effect=Exception("kafka down"))

        result = await svc._EntityEventService__handle_sync_event(
            "drive.start", {"orgId": "org-1"}
        )
        assert result is False


# ============================================================================
# __get_or_create_knowledge_base
# ============================================================================


class TestGetOrCreateKnowledgeBase:
    @pytest.mark.asyncio
    async def test_missing_user_or_org_id(self):
        svc, logger, gp, _ = _make_service()
        result = await svc._EntityEventService__get_or_create_knowledge_base(
            "key-1", "", "org-1"
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_existing_kb_returned(self):
        svc, logger, gp, _ = _make_service()
        gp.get_nodes_by_filters = AsyncMock(return_value=[
            {"id": "kb-1", "isDeleted": False, "groupName": "Private"},
        ])
        result = await svc._EntityEventService__get_or_create_knowledge_base(
            "key-1", "uid-1", "org-1"
        )
        assert result.get("id") == "kb-1"

    @pytest.mark.asyncio
    async def test_deleted_kb_is_filtered_out(self):
        """Deleted KBs should not be returned as existing, triggering creation of a new one."""
        svc, logger, gp, container = _make_service()
        gp.get_nodes_by_filters = AsyncMock(return_value=[
            {"id": "kb-1", "isDeleted": True},
        ])
        # __get_or_create_kb_app_for_org will be called next - it does a local import
        # so we mock the entire method chain
        with patch.object(svc, '_EntityEventService__get_or_create_kb_app_for_org',
                         new_callable=AsyncMock, return_value={"_key": "app-1"}):
            gp.batch_upsert_nodes = AsyncMock()
            gp.batch_create_edges = AsyncMock()
            result = await svc._EntityEventService__get_or_create_knowledge_base(
                "key-1", "uid-1", "org-1", name="Test Private"
            )
        # Should create a new KB since the existing one was deleted
        assert result.get("kb_id") is not None

    @pytest.mark.asyncio
    async def test_exception_returns_empty_dict(self):
        svc, logger, gp, _ = _make_service()
        gp.get_nodes_by_filters = AsyncMock(side_effect=Exception("db error"))
        result = await svc._EntityEventService__get_or_create_knowledge_base(
            "key-1", "uid-1", "org-1"
        )
        assert result == {}


# ============================================================================
# __create_user_kb_app_relation
# ============================================================================


class TestCreateUserKbAppRelation:
    @pytest.mark.asyncio
    async def test_no_kb_app_returns_false(self):
        svc, logger, gp, _ = _make_service()
        with patch.object(svc, '_EntityEventService__get_or_create_kb_app_for_org', new_callable=AsyncMock, return_value=None):
            result = await svc._EntityEventService__create_user_kb_app_relation("key-1", "org-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_edge_already_exists(self):
        svc, logger, gp, _ = _make_service()
        gp.get_edges_from_node = AsyncMock(return_value=[
            {"to_id": "app-1"},
        ])
        with patch.object(svc, '_EntityEventService__get_or_create_kb_app_for_org', new_callable=AsyncMock, return_value={"_key": "app-1"}):
            result = await svc._EntityEventService__create_user_kb_app_relation("key-1", "org-1")
        assert result is True

    @pytest.mark.asyncio
    async def test_creates_new_edge(self):
        svc, logger, gp, _ = _make_service()
        gp.get_edges_from_node = AsyncMock(return_value=[])
        gp.batch_create_edges = AsyncMock()
        with patch.object(svc, '_EntityEventService__get_or_create_kb_app_for_org', new_callable=AsyncMock, return_value={"_key": "app-1"}):
            result = await svc._EntityEventService__create_user_kb_app_relation("key-1", "org-1")
        assert result is True
        gp.batch_create_edges.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        svc, logger, gp, _ = _make_service()
        with patch.object(svc, '_EntityEventService__get_or_create_kb_app_for_org', new_callable=AsyncMock, side_effect=Exception("fail")):
            result = await svc._EntityEventService__create_user_kb_app_relation("key-1", "org-1")
        assert result is False


# ============================================================================
# __create_kb_connector_app_instance
# ============================================================================


class TestCreateKbConnectorAppInstance:
    @pytest.mark.asyncio
    async def test_exception_during_import_returns_none(self):
        """Exception during KB connector creation returns None gracefully."""
        svc, logger, gp, _ = _make_service()
        # Make get_org_apps fail to trigger exception path
        gp.get_org_apps = AsyncMock(side_effect=Exception("fail"))

        # The __create_kb_connector_app_instance method does a local import
        # of KnowledgeBaseConnector. Any exception (including import errors
        # or database errors) should be caught and return None.
        result = await svc._EntityEventService__create_kb_connector_app_instance("org-1")
        assert result is None
