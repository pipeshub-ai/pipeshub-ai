"""OneDrive Event Service for handling OneDrive-specific events"""

import asyncio
import logging
from typing import Any, Dict

from dependency_injector import providers

from app.connectors.core.base.data_store.arango_data_store import ArangoDataStore
from app.connectors.core.base.event_service.event_service import BaseEventService
from app.connectors.sources.google.common.arango_service import ArangoService
from app.connectors.sources.microsoft.onedrive.connector import (
    OneDriveConnector,
)
from app.containers.connector import ConnectorAppContainer


class OneDriveEventService(BaseEventService):
    """OneDrive specific event service"""

    def __init__(
        self,
        logger: logging.Logger,
        app_container: ConnectorAppContainer,
        arango_service: ArangoService,
    ) -> None:
        super().__init__(logger)
        self.arango_service = arango_service
        self.app_container = app_container

    async def process_event(self, event_type: str, payload: Dict[str, Any]) -> bool:
        """Handle connector-specific events - implementing abstract method"""
        try:
            self.logger.info(f"Handling OneDrive connector event: {event_type}")

            if event_type == "onedrive.init":
                return await self._handle_onedrive_init(payload)
            elif event_type == "onedrive.start":
                return await self._handle_onedrive_start_sync(payload)
            elif event_type == "onedrive.resync":
                return await self._handle_onedrive_start_sync(payload)
            else:
                self.logger.error(f"Unknown OneDrive connector event type: {event_type}")
                return False

        except Exception as e:
            self.logger.error(f"Error handling OneDrive connector event {event_type}: {e}", exc_info=True)
            return False

    async def _handle_onedrive_init(self, payload: Dict[str, Any]) -> bool:
        """Initializes the OneDrive connector and its dependencies."""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                self.logger.error("'orgId' is required in the payload for 'onedrive.init' event.")
                return False

            self.logger.info(f"Initializing OneDrive init sync service for org_id: {org_id}")
            config_service = self.app_container.config_service()
            arango_service = await self.app_container.arango_service()
            data_store_provider = ArangoDataStore(self.logger, arango_service)
            onedrive_connector = await OneDriveConnector.create_connector(self.logger, data_store_provider, config_service)
            await onedrive_connector.init()
            # Override the container's onedrive_connector provider with the initialized instance
            self.app_container.onedrive_connector.override(providers.Object(onedrive_connector))
            # Initialize directly since we can't use BackgroundTasks in Kafka consumer
            return True
        except Exception as e:
            self.logger.error("Failed to initialize OneDrive connector for org_id %s: %s", org_id, e, exc_info=True)
            return False

    async def _handle_onedrive_start_sync(self, payload: Dict[str, Any]) -> bool:
        """Queue immediate start of the sync service"""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                raise ValueError("orgId is required")

            self.logger.info(f"Starting OneDrive sync service for org_id: {org_id}")
            try:
                onedrive_connector: OneDriveConnector = self.app_container.onedrive_connector()
                if onedrive_connector:
                    asyncio.create_task(onedrive_connector.run_sync())
                    return True
                else:
                    self.logger.error("OneDrive connector not initialized")
                    return False
            except Exception as e:
                self.logger.error(f"Failed to get OneDrive connector: {str(e)}")
                return False
        except Exception as e:
            self.logger.error("Failed to queue OneDrive sync service start: %s", str(e))
            return False
