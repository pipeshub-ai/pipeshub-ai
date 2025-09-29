"""Generic Event Service for handling connector-specific events"""

import asyncio
import logging
from typing import Any, Dict

from dependency_injector import providers

from app.connectors.core.base.data_store.arango_data_store import ArangoDataStore
from app.connectors.core.factory.connector_factory import ConnectorFactory
from app.connectors.services.base_arango_service import BaseArangoService
from app.containers.connector import ConnectorAppContainer


class EventService:
    """Event service for handling connector-specific events"""

    def __init__(
        self,
        logger: logging.Logger,
        app_container: ConnectorAppContainer,
        arango_service: BaseArangoService,
    ) -> None:
        self.logger = logger
        self.arango_service = arango_service
        self.app_container = app_container

    async def process_event(self, event_type: str, payload: Dict[str, Any]) -> bool:
        """Handle connector-specific events - implementing abstract method"""
        try:
            connector_name = event_type.split(".")[0]
            connector_name = connector_name.replace(" ", "").lower()
            self.logger.info(f"Handling {connector_name} connector event: {event_type}")
            event_type = event_type.split(".")[1]
            if event_type == "init":
                return await self._handle_init(connector_name, payload)
            elif event_type == "start":
                return await self._handle_start_sync(connector_name, payload)
            elif event_type.lower() == "resync":
                return await self._handle_start_sync(connector_name, payload)
            else:
                self.logger.error(f"Unknown {connector_name.capitalize()} connector event type: {event_type}")
                return False

        except Exception as e:
            self.logger.error(f"Error handling {connector_name.capitalize()} connector event {event_type}: {e}", exc_info=True)
            return False

    async def _handle_init(self, connector_name: str, payload: Dict[str, Any]) -> bool:
        """Initializes the event service connector and its dependencies."""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                self.logger.error(f"'orgId' is required in the payload for '{connector_name}.init' event.")
                return False

            self.logger.info(f"Initializing {connector_name} init sync service for org_id: {org_id}")
            config_service = self.app_container.config_service()
            arango_service = await self.app_container.arango_service()
            data_store_provider = ArangoDataStore(self.logger, arango_service)
            # Use generic connector factory
            connector = await ConnectorFactory.create_connector(
                name=connector_name,
                logger=self.logger,
                data_store_provider=data_store_provider,
                config_service=config_service
            )

            if not connector:
                self.logger.error(f"❌ Failed to create {connector_name} connector")
                return False

            await connector.init()

            # Store connector in container using generic approach
            connector_key = f"{connector_name}_connector"
            if hasattr(self.app_container, connector_key):
                getattr(self.app_container, connector_key).override(providers.Object(connector))
            else:
                # Store in connectors_map if specific connector attribute doesn't exist
                if not hasattr(self.app_container, 'connectors_map'):
                    self.app_container.connectors_map = {}
                self.app_container.connectors_map[connector_name] = connector
            # Initialize directly since we can't use BackgroundTasks in Kafka consumer
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize event service connector {connector_name} for org_id %s: %s", org_id, e, exc_info=True)
            return False

    async def _handle_start_sync(self, connector_name: str, payload: Dict[str, Any]) -> bool:
        """Queue immediate start of the sync service"""
        try:
            org_id = payload.get("orgId")
            if not org_id:
                raise ValueError("orgId is required")

            self.logger.info(f"Starting {connector_name} sync service for org_id: {org_id}")
            connector_name = connector_name.replace(" ", "").lower()

            try:
                # Try to get connector from specific container attribute first
                connector_key = f"{connector_name}_connector"
                connector = None

                if hasattr(self.app_container, connector_key):
                    connector = getattr(self.app_container, connector_key)()
                elif hasattr(self.app_container, 'connectors_map'):
                    connector = self.app_container.connectors_map.get(connector_name)

                if connector:
                    asyncio.create_task(connector.run_sync())
                    self.logger.info(f"Started sync for {connector_name} connector")
                    return True
                else:
                    self.logger.error(f"{connector_name.capitalize()} connector not initialized")
                    return False
            except Exception as e:
                self.logger.error(f"Failed to get {connector_name.capitalize()} connector: {str(e)}")
                return False
        except Exception as e:
            self.logger.error(f"Failed to queue {connector_name.capitalize()} sync service start: {str(e)}")
            return False
