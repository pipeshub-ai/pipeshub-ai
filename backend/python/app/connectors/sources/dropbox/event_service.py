"""Dropbox Event Service for handling Dropbox-specific events"""

import asyncio
import logging
from typing import Any, Dict

from dependency_injector import providers

from app.connectors.core.base.data_store.arango_data_store import ArangoDataStore
from app.connectors.core.base.event_service.event_service import BaseEventService
from app.connectors.sources.google.common.arango_service import ArangoService
from app.connectors.sources.dropbox.connector2 import (
    DropboxConnector,
)
from app.containers.connector import ConnectorAppContainer


class DropboxEventService(BaseEventService):
    """Dropbox specific event service"""

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
            self.logger.info(f"Handling Dropbox connector event: {event_type}")

            if event_type == "dropbox.init":
                return await self._handle_dropbox_init(payload)
            elif event_type == "dropbox.start":
                return await self._handle_dropbox_start_sync(payload)
            elif event_type == "dropbox.resync":
                return await self._handle_dropbox_start_sync(payload)
            else:
                self.logger.error(f"Unknown Dropbox connector event type: {event_type}")
                return False

        except Exception as e:
            self.logger.error(f"Error handling Dropbox connector event {event_type}: {e}", exc_info=True)
            return False

    async def _handle_dropbox_init(self, payload: Dict[str, Any]) -> bool:
        """Initializes the Dropbox connector and its dependencies."""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                self.logger.error("'orgId' is required in the payload for 'dropbox.init' event.")
                return False

            self.logger.info(f"Initializing Dropbox init sync service for org_id: {org_id}")
            config_service = self.app_container.config_service()
            arango_service = await self.app_container.arango_service()
            data_store_provider = ArangoDataStore(self.logger, arango_service)
            dropbox_connector = await DropboxConnector.create_connector(self.logger, data_store_provider, config_service)
            await dropbox_connector.init()
            # Override the container's dropbox_connector provider with the initialized instance
            self.app_container.dropbox_connector.override(providers.Object(dropbox_connector))
            # Initialize directly since we can't use BackgroundTasks in Kafka consumer
            return True
        except Exception as e:
            self.logger.error("Failed to initialize Dropbox connector for org_id %s: %s", org_id, e, exc_info=True)
            return False

    async def _handle_dropbox_start_sync(self, payload: Dict[str, Any]) -> bool:
        """Queue immediate start of the sync service"""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                raise ValueError("orgId is required")

            self.logger.info(f"Starting Dropbox sync service for org_id: {org_id}")
            try:
                dropbox_connector: DropboxConnector = self.app_container.dropbox_connector()
                if dropbox_connector:
                    asyncio.create_task(dropbox_connector.run_sync())
                    return True
                else:
                    self.logger.error("Dropbox connector not initialized")
                    return False
            except Exception as e:
                self.logger.error(f"Failed to get Dropbox connector: {str(e)}")
                return False
        except Exception as e:
            self.logger.error("Failed to queue Dropbox sync service start: %s", str(e))
            return False