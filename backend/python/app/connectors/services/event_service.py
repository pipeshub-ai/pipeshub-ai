"""Generic Event Service for handling connector-specific events"""

import asyncio
import logging
from typing import Any, Dict, Optional

from dependency_injector import providers

from app.config.constants.arangodb import Connectors
from app.connectors.core.base.connector.connector_service import BaseConnector
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

    def _get_connector(self, connector_name: str) -> Optional[BaseConnector]:
        """
        Get connector instance from app_container.
        """
        connector_key = f"{connector_name}_connector"

        if hasattr(self.app_container, connector_key):
            return getattr(self.app_container, connector_key)()
        elif hasattr(self.app_container, 'connectors_map'):
            return self.app_container.connectors_map.get(connector_name)

        return None

    async def process_event(self, event_type: str, payload: Dict[str, Any]) -> bool:
        """Handle connector-specific events - implementing abstract method"""
        try:
            if "." in event_type:
                parts = event_type.split(".")
                connector_name = parts[0].replace(" ", "").lower()
                action = parts[1].lower()
            else:
                self.logger.error(f"Invalid event type format (missing connector prefix): {event_type}")
                return False

            self.logger.info(f"Handling {connector_name} connector event: {action}")

            if action == "init":
                return await self._handle_init(connector_name, payload)
            elif action == "start":
                return await self._handle_start_sync(connector_name, payload)
            elif action == "resync":
                return await self._handle_start_sync(connector_name, payload)
            elif action == "reindex":
                return await self._handle_reindex(connector_name, payload)
            else:
                self.logger.error(f"Unknown {connector_name.capitalize()} connector event type: {action}")
                return False

        except Exception as e:
            self.logger.error(f"Error handling connector event {event_type}: {e}", exc_info=True)
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
            connector_name_normalized = connector_name.replace(" ", "").lower()

            connector = self._get_connector(connector_name_normalized)
            if not connector:
                self.logger.error(f"{connector_name.capitalize()} connector not initialized")
                return False

            asyncio.create_task(connector.run_sync())
            self.logger.info(f"Started sync for {connector_name} connector")
            return True

        except Exception as e:
            self.logger.error(f"Failed to queue {connector_name.capitalize()} sync service start: {str(e)}")
            return False

    async def _handle_reindex(self, connector_name: str, payload: Dict[str, Any]) -> bool:
        """Handle reindex event for a connector with pagination support"""
        try:
            org_id = payload.get("orgId")
            status_filters = payload.get("statusFilters", ["FAILED"])

            if not org_id:
                raise ValueError("orgId is required")

            self.logger.info(f"Starting reindex for {connector_name} connector with status filters: {status_filters}")
            connector_name_normalized = connector_name.replace(" ", "").lower()

            connector = self._get_connector(connector_name_normalized)
            if not connector:
                self.logger.error(f"{connector_name.capitalize()} connector not initialized")
                return False

            # Get connector enum value
            connector_enum = getattr(Connectors, connector_name.upper().replace(" ", ""), None)
            if not connector_enum:
                self.logger.error(f"Unknown connector name: {connector_name}")
                return False

            # Fetch and process records in batches of 100
            batch_size = 100
            offset = 0
            total_processed = 0

            while True:
                # Fetch batch of typed Record instances
                records = await self.arango_service.get_records_by_status(
                    org_id=org_id,
                    connector_name=connector_enum,
                    status_filters=status_filters,
                    limit=batch_size,
                    offset=offset
                )

                if not records:
                    break

                self.logger.info(f"Processing batch of {len(records)} records (offset: {offset})")

                # Process this batch with typed records
                await connector.reindex_records(records)

                total_processed += len(records)
                offset += batch_size

                # If we got fewer records than batch_size, we've reached the end
                if len(records) < batch_size:
                    break

            self.logger.info(f"✅ Completed reindex for {connector_name} connector. Total records processed: {total_processed}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to handle reindex for {connector_name.capitalize()}: {str(e)}", exc_info=True)
            return False
