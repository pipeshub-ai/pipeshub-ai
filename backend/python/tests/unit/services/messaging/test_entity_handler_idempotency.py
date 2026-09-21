"""Unit tests for idempotency of app.services.messaging.kafka.handlers.entity.EntityEventService."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.config.constants.arangodb import CollectionNames
from app.services.messaging.kafka.handlers.entity import EntityEventService

def _make_service():
    """Create an EntityEventService with mock dependencies."""
    logger = MagicMock()
    graph_provider = AsyncMock()
    app_container = MagicMock()
    app_container.messaging_producer = AsyncMock()
    app_container.messaging_producer.send_message = AsyncMock()
    app_container.config_service.return_value = AsyncMock()
    app_container.data_store = AsyncMock()
    return EntityEventService(logger, graph_provider, app_container)

class TestIdempotency:
    
    @pytest.mark.asyncio
    async def test_create_all_team_for_org_is_idempotent(self):
        svc = _make_service()
        svc.graph_provider.batch_upsert_nodes = AsyncMock()

        # Call once
        await svc._create_all_team_for_org("org-1", "user-1")
        # Call twice
        await svc._create_all_team_for_org("org-1", "user-1")

        assert svc.graph_provider.batch_upsert_nodes.await_count == 2
        
        args1 = svc.graph_provider.batch_upsert_nodes.call_args_list[0][0]
        args2 = svc.graph_provider.batch_upsert_nodes.call_args_list[1][0]
        
        # Verify the same exact ID is used, meaning upsert will just overwrite/ignore
        assert args1[0][0]["id"] == "all_org-1"
        assert args2[0][0]["id"] == "all_org-1"
        
        assert args1[1] == CollectionNames.TEAMS.value
        assert args2[1] == CollectionNames.TEAMS.value

    @pytest.mark.asyncio
    async def test_handle_app_enabled_is_idempotent(self):
        svc = _make_service()
        svc.graph_provider.get_document = AsyncMock(
            return_value={"_key": "org-1", "accountType": "enterprise"}
        )
        svc._handle_sync_event = AsyncMock(return_value=True)
        
        payload = {
            "orgId": "org-1",
            "apps": ["GoogleDrive"],
            "syncAction": "immediate",
            "connectorId": "conn-1",
            "scope": "personal",
        }
        class MockMsg:
            def __init__(self, event_type, payload, event_id):
                self.eventType = event_type
                self.payload = payload
                self.eventId = event_id
                
        msg = MockMsg("appEnabled", payload, "event-id-123")
        
        # Create the handler
        from app.services.messaging.kafka.utils.utils import create_entity_message_handler
        handler = await create_entity_message_handler(svc.app_container, svc.graph_provider)
        
        # We need to spy on process_event
        svc.process_event = AsyncMock(return_value=True)
        
        # Event delivered first time
        result1 = await handler(msg)
        
        # Event redelivered
        result2 = await handler(msg)
        
        assert result1 is True
        assert result2 is True
        
        # Should only process once
        assert svc.process_event.await_count == 1
        
        # Should have sent sync events twice, but sync manager handles deduplication
        assert svc._handle_sync_event.await_count == 2

    @pytest.mark.asyncio
    async def test_handle_app_disabled_is_idempotent(self):
        svc = _make_service()
        svc.graph_provider.get_document = AsyncMock(
            return_value={
                "_key": "conn-1",
                "name": "GoogleDrive",
                "type": "connector",
                "appGroup": "google",
                "createdAtTimestamp": 1000,
            }
        )
        svc.graph_provider.batch_upsert_nodes = AsyncMock()
        svc.graph_provider.reset_indexing_status_for_connector = AsyncMock()

        # Mock sync_task_manager
        import app.services.messaging.kafka.handlers.entity as entity_module
        with pytest.MonkeyPatch().context() as m:
            mock_stm = AsyncMock()
            m.setattr(entity_module, "sync_task_manager", mock_stm)
            mock_rtm = AsyncMock()
            m.setattr(entity_module, "reindex_task_manager", mock_rtm)
            
            payload = {
                "orgId": "org-1",
                "apps": ["GoogleDrive"],
                "connectorId": "conn-1",
            }
            
            result1 = await svc.process_event("appDisabled", payload)
            result2 = await svc.process_event("appDisabled", payload)
            
            assert result1 is True
            assert result2 is True
            
            assert svc.graph_provider.batch_upsert_nodes.await_count == 2
            
            args1 = svc.graph_provider.batch_upsert_nodes.call_args_list[0][0]
            args2 = svc.graph_provider.batch_upsert_nodes.call_args_list[1][0]
            
            assert args1[0][0]["isActive"] is False
            assert args2[0][0]["isActive"] is False
