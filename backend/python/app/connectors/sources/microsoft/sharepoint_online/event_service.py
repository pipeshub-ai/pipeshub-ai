"""OneDrive Event Service for handling OneDrive-specific events"""

import asyncio
import logging
from typing import Any, Dict

from dependency_injector import providers

from app.connectors.core.base.data_store.arango_data_store import ArangoDataStore
from app.connectors.core.base.event_service.event_service import BaseEventService
from app.connectors.services.base_arango_service import BaseArangoService
from app.connectors.sources.microsoft.sharepoint_online.connector import (
    SharePointConnector,
)
from app.containers.connector import ConnectorAppContainer


class SharePointOnlineEventService(BaseEventService):
    """SharePoint Online specific event service"""

    def __init__(
        self,
        logger: logging.Logger,
        app_container: ConnectorAppContainer,
        arango_service: BaseArangoService,
    ) -> None:
        super().__init__(logger)
        self.arango_service = arango_service
        self.app_container = app_container

    async def process_event(self, event_type: str, payload: Dict[str, Any]) -> bool:
        """Handle connector-specific events - implementing abstract method"""
        try:
            self.logger.info(f"Handling SharePoint Online connector event: {event_type}")

            if event_type == "sharepointonline.init":
                return await self._handle_sharepoint_init(payload)
            elif event_type == "sharepointonline.start":
                return await self._handle_sharepoint_start_sync(payload)
            elif event_type.lower() == "sharepointonline.resync":
                return await self._handle_sharepoint_start_sync(payload)
            else:
                self.logger.error(f"Unknown sharepoint online connector event type: {event_type}")
                return False

        except Exception as e:
            self.logger.error(f"Error handling SharePoint Online connector event {event_type}: {e}", exc_info=True)
            return False

    async def _handle_sharepoint_init(self, payload: Dict[str, Any]) -> bool:
        """Initializes the SharePoint Online connector and its dependencies."""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                self.logger.error("'orgId' is required in the payload for 'sharepointonline.init' event.")
                return False

            self.logger.info(f"Initializing SharePoint Online init sync service for org_id: {org_id}")
            config_service = self.app_container.config_service()
            arango_service = await self.app_container.arango_service()
            data_store_provider = ArangoDataStore(self.logger, arango_service)
            sharepoint_connector = await SharePointConnector.create_connector(self.logger,
                                            data_store_provider, config_service)
            await sharepoint_connector.init()
            # Override the container's sharepoint_connector provider with the initialized instance
            self.app_container.sharepoint_connector.override(providers.Object(sharepoint_connector))
            # Initialize directly since we can't use BackgroundTasks in Kafka consumer
            return True
        except Exception as e:
            self.logger.error("Failed to initialize SharePoint Online connector for org_id %s: %s", org_id, e, exc_info=True)
            return False

    async def _handle_sharepoint_start_sync(self, payload: Dict[str, Any]) -> bool:
        """Queue immediate start of the sync service"""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                raise ValueError("orgId is required")

            self.logger.info(f"Starting SharePoint Online sync service for org_id: {org_id}")
            try:
                sharepoint_connector: SharePointConnector = self.app_container.sharepoint_connector()
                if sharepoint_connector:
                    asyncio.create_task(sharepoint_connector.run_sync())
                    return True
                else:
                    self.logger.error("SharePoint Online connector not initialized")
                    return False
            except Exception as e:
                self.logger.error(f"Failed to get SharePoint Online connector: {str(e)}")
                return False
        except Exception as e:
            self.logger.error("Failed to queue SharePoint Online sync service start: %s", str(e))
            return False
