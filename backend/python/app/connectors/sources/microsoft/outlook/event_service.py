"""Outlook Event Service for handling Outlook-specific events"""

import asyncio
import logging
from typing import Any, Dict

from dependency_injector import providers

from app.connectors.core.base.data_store.arango_data_store import ArangoDataStore
from app.connectors.core.base.event_service.event_service import BaseEventService
from app.connectors.sources.google.common.arango_service import ArangoService
from app.connectors.sources.microsoft.outlook.connector import (
    OutlookConnector,
)
from app.containers.connector import ConnectorAppContainer


class OutlookEventService(BaseEventService):
    """Outlook specific event service"""

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
            self.logger.info(f"Handling Outlook connector event: {event_type}")

            if event_type == "outlook.init":
                return await self._handle_outlook_init(payload)
            elif event_type == "outlook.start":
                return await self._handle_outlook_start_sync(payload)
            elif event_type == "outlook.resync":
                return await self._handle_outlook_start_sync(payload)
            else:
                self.logger.error(f"Unknown Outlook connector event type: {event_type}")
                return False

        except Exception as e:
            self.logger.error(f"Error handling Outlook connector event {event_type}: {e}", exc_info=True)
            return False

    async def _handle_outlook_init(self, payload: Dict[str, Any]) -> bool:
        """Initializes the Outlook connector and its dependencies."""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                self.logger.error("'orgId' is required in the payload for 'outlook.init' event.")
                return False

            self.logger.info(f"Initializing Outlook init sync service for org_id: {org_id}")
            config_service = self.app_container.config_service()
            arango_service = await self.app_container.arango_service()
            data_store_provider = ArangoDataStore(self.logger, arango_service)

            outlook_connector = await OutlookConnector.create_connector(self.logger, data_store_provider, config_service)

            init_result = await outlook_connector.init()

            if init_result:
                # Override the container's outlook_connector provider with the initialized instance
                self.logger.debug("Overriding container outlook_connector with initialized instance...")
                self.app_container.outlook_connector.override(providers.Object(outlook_connector))
                self.logger.info(f"Outlook connector successfully initialized for org_id: {org_id}")
                return True
            else:
                self.logger.error(f"Outlook connector initialization failed for org_id: {org_id}")
                return False
        except Exception as e:
            self.logger.error("Failed to initialize Outlook connector for org_id %s: %s", org_id, e, exc_info=True)
            return False

    async def _handle_outlook_start_sync(self, payload: Dict[str, Any]) -> bool:
        """Start Outlook email sync"""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                raise ValueError("orgId is required")

            self.logger.info(f"Starting Outlook sync service for org_id: {org_id}")

            try:
                outlook_connector: OutlookConnector = self.app_container.outlook_connector()
                if outlook_connector:
                    asyncio.create_task(outlook_connector.run_sync())
                    return True
                else:
                    self.logger.error("Outlook connector not initialized")
                    return False

            except Exception as e:
                self.logger.error(f"Failed to get Outlook connector: {str(e)}")
                return False

        except Exception as e:
            self.logger.error("Failed to start Outlook sync service: %s", str(e))
            return False
