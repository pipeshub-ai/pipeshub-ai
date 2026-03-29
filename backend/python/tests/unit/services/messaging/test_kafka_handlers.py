"""Unit tests for Kafka event handlers: AiConfigEventService, EntityEventService.

Sources:
  app.services.messaging.kafka.handlers.ai_config
  app.services.messaging.kafka.handlers.entity
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.kafka.handlers.ai_config import AiConfigEventService
from app.services.messaging.kafka.handlers.entity import EntityEventService

log = logging.getLogger("test")
log.setLevel(logging.CRITICAL)


# ============================================================================
# AiConfigEventService
# ============================================================================

class TestAiConfigEventService:

    def _make_service(self):
        retrieval_service = AsyncMock()
        return AiConfigEventService(logger=log, retrieval_service=retrieval_service)

    # -- llmConfigured --

    @pytest.mark.asyncio
    async def test_llm_configured_success(self):
        svc = self._make_service()
        svc.retrieval_service.get_llm_instance = AsyncMock(return_value=MagicMock())

        result = await svc.process_event("llmConfigured", {"provider": "openai"})

        assert result is True
        svc.retrieval_service.get_llm_instance.assert_awaited_once_with(use_cache=False)

    @pytest.mark.asyncio
    async def test_llm_configured_failure(self):
        svc = self._make_service()
        svc.retrieval_service.get_llm_instance = AsyncMock(
            side_effect=RuntimeError("config error")
        )

        result = await svc.process_event("llmConfigured", {})

        assert result is False

    # -- embeddingModelConfigured --

    @pytest.mark.asyncio
    async def test_embedding_configured_success(self):
        svc = self._make_service()
        svc.retrieval_service.get_embedding_model_instance = AsyncMock(
            return_value=MagicMock()
        )

        result = await svc.process_event("embeddingModelConfigured", {"model": "ada"})

        assert result is True
        svc.retrieval_service.get_embedding_model_instance.assert_awaited_once_with(
            use_cache=False
        )

    @pytest.mark.asyncio
    async def test_embedding_configured_failure(self):
        svc = self._make_service()
        svc.retrieval_service.get_embedding_model_instance = AsyncMock(
            side_effect=RuntimeError("embed fail")
        )

        result = await svc.process_event("embeddingModelConfigured", {})

        assert result is False

    # -- unknown event --

    @pytest.mark.asyncio
    async def test_unknown_event_returns_false(self):
        svc = self._make_service()

        result = await svc.process_event("unknownEvent", {})

        assert result is False

    # -- top-level exception --

    @pytest.mark.asyncio
    async def test_top_level_exception_returns_false(self):
        """If process_event itself throws unexpectedly, it returns False."""
        svc = self._make_service()
        # Monkey-patch the private handler to explode
        svc._AiConfigEventService__handle_llm_configured = AsyncMock(
            side_effect=Exception("unexpected")
        )

        result = await svc.process_event("llmConfigured", {})

        assert result is False


# ============================================================================
# EntityEventService
# ============================================================================

class TestEntityEventService:

    def _make_service(self):
        graph_provider = AsyncMock()
        app_container = MagicMock()
        app_container.messaging_producer = AsyncMock()
        app_container.messaging_producer.send_message = AsyncMock()
        return EntityEventService(
            logger=log,
            graph_provider=graph_provider,
            app_container=app_container,
        )

    # -- orgCreated --

    @pytest.mark.asyncio
    async def test_org_created_success(self):
        svc = self._make_service()
        svc.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
        svc.graph_provider.get_nodes_by_filters = AsyncMock(return_value=[])
        svc.graph_provider.batch_create_edges = AsyncMock(return_value=True)

        # Stub the KB connector creation
        svc._EntityEventService__create_kb_connector_app_instance = AsyncMock(return_value=None)

        payload = {
            "orgId": "org1",
            "registeredName": "Test Org",
            "accountType": "ENTERPRISE",
            "userId": "user1",
        }

        result = await svc.process_event("orgCreated", payload)

        assert result is True
        svc.graph_provider.batch_upsert_nodes.assert_awaited()

    @pytest.mark.asyncio
    async def test_org_created_individual_account(self):
        svc = self._make_service()
        svc.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
        svc.graph_provider.get_nodes_by_filters = AsyncMock(return_value=[])
        svc.graph_provider.batch_create_edges = AsyncMock(return_value=True)
        svc._EntityEventService__create_kb_connector_app_instance = AsyncMock(return_value=None)

        payload = {
            "orgId": "org1",
            "registeredName": "Personal",
            "accountType": "INDIVIDUAL",
        }

        result = await svc.process_event("orgCreated", payload)
        assert result is True

    @pytest.mark.asyncio
    async def test_org_created_with_departments(self):
        svc = self._make_service()
        svc.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
        svc.graph_provider.get_nodes_by_filters = AsyncMock(return_value=[
            {"id": "dept1", "orgId": None},
            {"id": "dept2", "orgId": None},
        ])
        svc.graph_provider.batch_create_edges = AsyncMock(return_value=True)
        svc._EntityEventService__create_kb_connector_app_instance = AsyncMock(return_value=None)

        payload = {
            "orgId": "org1",
            "registeredName": "Corp",
            "accountType": "BUSINESS",
        }

        result = await svc.process_event("orgCreated", payload)
        assert result is True
        # batch_create_edges should be called for department relations
        svc.graph_provider.batch_create_edges.assert_awaited()

    @pytest.mark.asyncio
    async def test_org_created_exception_returns_false(self):
        svc = self._make_service()
        svc.graph_provider.batch_upsert_nodes = AsyncMock(
            side_effect=RuntimeError("db error")
        )

        payload = {
            "orgId": "org1",
            "registeredName": "Fail",
            "accountType": "ENTERPRISE",
        }

        result = await svc.process_event("orgCreated", payload)
        assert result is False

    # -- orgUpdated --

    @pytest.mark.asyncio
    async def test_org_updated_success(self):
        svc = self._make_service()
        svc.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)

        payload = {"orgId": "org1", "registeredName": "Updated Name"}
        result = await svc.process_event("orgUpdated", payload)

        assert result is True

    @pytest.mark.asyncio
    async def test_org_updated_failure(self):
        svc = self._make_service()
        svc.graph_provider.batch_upsert_nodes = AsyncMock(
            side_effect=RuntimeError("fail")
        )

        payload = {"orgId": "org1", "registeredName": "X"}
        result = await svc.process_event("orgUpdated", payload)

        assert result is False

    # -- orgDeleted --

    @pytest.mark.asyncio
    async def test_org_deleted_success(self):
        svc = self._make_service()
        svc.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)

        payload = {"orgId": "org1"}
        result = await svc.process_event("orgDeleted", payload)

        assert result is True

    @pytest.mark.asyncio
    async def test_org_deleted_failure(self):
        svc = self._make_service()
        svc.graph_provider.batch_upsert_nodes = AsyncMock(
            side_effect=RuntimeError("fail")
        )

        payload = {"orgId": "org1"}
        result = await svc.process_event("orgDeleted", payload)

        assert result is False

    # -- userAdded --

    @pytest.mark.asyncio
    async def test_user_added_new_user(self):
        svc = self._make_service()
        svc.graph_provider.get_user_by_email = AsyncMock(return_value=None)
        svc.graph_provider.get_document = AsyncMock(return_value={"accountType": "ENTERPRISE"})
        svc.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
        svc.graph_provider.batch_create_edges = AsyncMock(return_value=True)
        svc.graph_provider.get_org_apps = AsyncMock(return_value=[])

        svc._EntityEventService__get_or_create_knowledge_base = AsyncMock(return_value={})
        svc._EntityEventService__create_user_kb_app_relation = AsyncMock(return_value=True)

        payload = {
            "userId": "u1",
            "orgId": "org1",
            "email": "test@example.com",
            "fullName": "Test User",
            "syncAction": "none",
        }

        result = await svc.process_event("userAdded", payload)
        assert result is True

    @pytest.mark.asyncio
    async def test_user_added_existing_user(self):
        svc = self._make_service()
        existing = MagicMock()
        existing.id = "existing_key"
        svc.graph_provider.get_user_by_email = AsyncMock(return_value=existing)
        svc.graph_provider.get_document = AsyncMock(return_value={"accountType": "ENTERPRISE"})
        svc.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
        svc.graph_provider.batch_create_edges = AsyncMock(return_value=True)
        svc.graph_provider.get_org_apps = AsyncMock(return_value=[])

        svc._EntityEventService__get_or_create_knowledge_base = AsyncMock(return_value={})
        svc._EntityEventService__create_user_kb_app_relation = AsyncMock(return_value=True)

        payload = {
            "userId": "u1",
            "orgId": "org1",
            "email": "test@example.com",
            "syncAction": "none",
        }

        result = await svc.process_event("userAdded", payload)
        assert result is True

    @pytest.mark.asyncio
    async def test_user_added_org_not_found(self):
        svc = self._make_service()
        svc.graph_provider.get_user_by_email = AsyncMock(return_value=None)
        svc.graph_provider.get_document = AsyncMock(return_value=None)  # org not found

        payload = {
            "userId": "u1",
            "orgId": "org1",
            "email": "test@example.com",
            "syncAction": "none",
        }

        result = await svc.process_event("userAdded", payload)
        assert result is False

    @pytest.mark.asyncio
    async def test_user_added_immediate_sync(self):
        svc = self._make_service()
        svc.graph_provider.get_user_by_email = AsyncMock(return_value=None)
        svc.graph_provider.get_document = AsyncMock(return_value={"accountType": "ENTERPRISE"})
        svc.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
        svc.graph_provider.batch_create_edges = AsyncMock(return_value=True)
        svc.graph_provider.get_org_apps = AsyncMock(return_value=[
            {"name": "Gmail"},
        ])

        svc._EntityEventService__get_or_create_knowledge_base = AsyncMock(return_value={})
        svc._EntityEventService__create_user_kb_app_relation = AsyncMock(return_value=True)
        svc._EntityEventService__handle_sync_event = AsyncMock(return_value=True)

        payload = {
            "userId": "u1",
            "orgId": "org1",
            "email": "test@example.com",
            "syncAction": "immediate",
        }

        result = await svc.process_event("userAdded", payload)
        assert result is True
        svc._EntityEventService__handle_sync_event.assert_awaited()

    # -- userUpdated --

    @pytest.mark.asyncio
    async def test_user_updated_success(self):
        svc = self._make_service()
        existing = {"id": "key1", "_key": "key1"}
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=existing)
        svc.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)

        payload = {
            "userId": "u1",
            "orgId": "org1",
            "email": "test@example.com",
        }

        result = await svc.process_event("userUpdated", payload)
        assert result is True

    @pytest.mark.asyncio
    async def test_user_updated_not_found(self):
        svc = self._make_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)

        payload = {"userId": "u1", "orgId": "org1", "email": "x@x.com"}
        result = await svc.process_event("userUpdated", payload)
        assert result is False

    # -- userDeleted --

    @pytest.mark.asyncio
    async def test_user_deleted_success(self):
        svc = self._make_service()
        svc.graph_provider.get_entity_id_by_email = AsyncMock(return_value="key1")
        svc.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)

        payload = {"email": "test@example.com", "orgId": "org1"}
        result = await svc.process_event("userDeleted", payload)
        assert result is True

    @pytest.mark.asyncio
    async def test_user_deleted_not_found(self):
        svc = self._make_service()
        svc.graph_provider.get_entity_id_by_email = AsyncMock(return_value=None)

        payload = {"email": "missing@x.com", "orgId": "org1"}
        result = await svc.process_event("userDeleted", payload)
        assert result is False

    # -- appEnabled --

    @pytest.mark.asyncio
    async def test_app_enabled_success(self):
        svc = self._make_service()
        svc.graph_provider.get_document = AsyncMock(return_value={"accountType": "ENTERPRISE"})
        svc._EntityEventService__handle_sync_event = AsyncMock(return_value=True)

        payload = {
            "orgId": "org1",
            "apps": ["Gmail"],
            "syncAction": "immediate",
            "connectorId": "c1",
        }

        result = await svc.process_event("appEnabled", payload)
        assert result is True
        svc._EntityEventService__handle_sync_event.assert_awaited()

    @pytest.mark.asyncio
    async def test_app_enabled_no_sync(self):
        svc = self._make_service()
        svc.graph_provider.get_document = AsyncMock(return_value={"accountType": "ENTERPRISE"})
        svc._EntityEventService__handle_sync_event = AsyncMock(return_value=True)

        payload = {
            "orgId": "org1",
            "apps": ["Gmail"],
            "syncAction": "none",
        }

        result = await svc.process_event("appEnabled", payload)
        assert result is True
        # sync event not called when syncAction != immediate
        svc._EntityEventService__handle_sync_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_app_enabled_org_not_found(self):
        svc = self._make_service()
        svc.graph_provider.get_document = AsyncMock(return_value=None)

        payload = {"orgId": "org1", "apps": ["Gmail"], "syncAction": "immediate"}
        result = await svc.process_event("appEnabled", payload)
        assert result is False

    # -- appDisabled --

    @pytest.mark.asyncio
    async def test_app_disabled_success(self):
        svc = self._make_service()
        svc.graph_provider.get_document = AsyncMock(return_value={
            "name": "Gmail", "type": "gmail", "appGroup": "Google",
            "createdAtTimestamp": 1000,
        })
        svc.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)

        with patch("app.services.messaging.kafka.handlers.entity.sync_task_manager") as mock_stm:
            mock_stm.cancel_sync = AsyncMock()

            payload = {
                "orgId": "org1",
                "apps": ["Gmail"],
                "connectorId": "c1",
            }
            result = await svc.process_event("appDisabled", payload)

        assert result is True

    @pytest.mark.asyncio
    async def test_app_disabled_missing_org_or_apps(self):
        svc = self._make_service()

        payload = {"orgId": "", "apps": []}
        result = await svc.process_event("appDisabled", payload)
        assert result is False

    @pytest.mark.asyncio
    async def test_app_disabled_app_not_found(self):
        svc = self._make_service()
        svc.graph_provider.get_document = AsyncMock(return_value=None)

        payload = {"orgId": "org1", "apps": ["Missing"], "connectorId": "c1"}
        result = await svc.process_event("appDisabled", payload)
        assert result is False

    # -- unknown event --

    @pytest.mark.asyncio
    async def test_unknown_event_returns_false(self):
        svc = self._make_service()

        result = await svc.process_event("someRandomEvent", {})
        assert result is False

    # -- top-level exception --

    @pytest.mark.asyncio
    async def test_process_event_exception_returns_false(self):
        svc = self._make_service()
        svc._EntityEventService__handle_org_created = AsyncMock(
            side_effect=Exception("boom")
        )

        result = await svc.process_event("orgCreated", {
            "orgId": "o1", "registeredName": "X", "accountType": "ENTERPRISE"
        })
        assert result is False

    # -- _kb_name_from_user_added_payload --

    def test_kb_name_with_full_name(self):
        svc = self._make_service()
        name = svc._kb_name_from_user_added_payload(
            {"fullName": "Alice Smith", "email": "alice@x.com"}
        )
        assert name == "Alice Smith's Private"

    def test_kb_name_with_email_only(self):
        svc = self._make_service()
        name = svc._kb_name_from_user_added_payload(
            {"fullName": "", "email": "alice@x.com"}
        )
        assert name == "alice@x.com's Private"

    def test_kb_name_fallback(self):
        svc = self._make_service()
        name = svc._kb_name_from_user_added_payload({})
        assert name == "Private"
