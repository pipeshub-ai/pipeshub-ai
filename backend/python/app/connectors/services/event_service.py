"""Generic Event Service for handling connector-specific events"""

import logging
from typing import Any, Dict, Optional

from dependency_injector import providers

from app.config.constants.arangodb import Connectors
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.factory.connector_factory import ConnectorFactory
from app.connectors.core.sync.task_manager import sync_task_manager
from app.containers.connector import ConnectorAppContainer
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


class EventService:
    """Event service for handling connector-specific events"""

    def __init__(
        self,
        logger: logging.Logger,
        app_container: ConnectorAppContainer,
        graph_provider: IGraphDBProvider,
    ) -> None:
        self.logger = logger
        self.graph_provider = graph_provider
        self.app_container = app_container

    def _get_connector(self, connector_id: str) -> Optional[BaseConnector]:
        """
        Get connector instance from app_container.
        """
        connector_key = f"{connector_id}_connector"

        if hasattr(self.app_container, connector_key):
            return getattr(self.app_container, connector_key)()
        elif hasattr(self.app_container, 'connectors_map'):
            return self.app_container.connectors_map.get(connector_id)

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
            connector_id = payload.get("connectorId")
            if not org_id:
                self.logger.error(f"'orgId' is required in the payload for '{connector_name}.init' event.")
                return False

            self.logger.info(f"Initializing {connector_name} init sync service for org_id: {org_id} and connector_id: {connector_id}")
            config_service = self.app_container.config_service()
            # Create data_store manually using already-resolved graph_provider (arango_service) to avoid coroutine reuse
            from app.connectors.core.base.data_store.graph_data_store import (
                GraphDataStore,
            )
            data_store_provider = GraphDataStore(self.logger, self.graph_provider)
            # Use generic connector factory
            connector = await ConnectorFactory.create_connector(
                name=connector_name,
                logger=self.logger,
                data_store_provider=data_store_provider,
                config_service=config_service,
                connector_id=connector_id
            )

            if not connector:
                self.logger.error(f"❌ Failed to create {connector_name} connector")
                return False

            is_initialized = await connector.init()

            if not is_initialized:
                self.logger.error(f"❌ Failed to initialize {connector_name} connector (init returned False). Not storing in container.")
                return False

            self.logger.info(f"✅ Successfully initialized {connector_name} connector")

            # Store connector in container using generic approach
            connector_key = f"{connector_id}_connector"
            if hasattr(self.app_container, connector_key):
                getattr(self.app_container, connector_key).override(providers.Object(connector))
            else:
                # Store in connectors_map if specific connector attribute doesn't exist
                if not hasattr(self.app_container, 'connectors_map'):
                    self.app_container.connectors_map = {}
                self.app_container.connectors_map[connector_id] = connector
            # Initialize directly since we can't use BackgroundTasks in Kafka consumer
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize event service connector {connector_name} for org_id %s: %s", org_id, e, exc_info=True)
            return False

    async def _handle_start_sync(self, connector_name: str, payload: Dict[str, Any]) -> bool:
        """Queue immediate start of the sync service"""
        try:
            org_id = payload.get("orgId")
            connector_id = payload.get("connectorId")
            full_sync = payload.get("fullSync", False)
            if not org_id:
                raise ValueError("orgId is required")

            self.logger.info(f"Starting {connector_name} sync service for org_id: {org_id}, full_sync: {full_sync}")

            connector = self._get_connector(connector_id)
            if not connector:
                self.logger.error(f"{connector_name.capitalize()} {connector_id} connector not initialized")
                return False

            # If fullSync flag is set, delete all sync points for this connector
            if full_sync:
                self.logger.info(f"Full sync requested - deleting sync points for connector {connector_id}")
                try:
                    deleted_count, success = await self.graph_provider.delete_sync_points_by_connector_id(
                        connector_id=connector_id
                    )
                    if success:
                        self.logger.info(f"✅ Successfully deleted {deleted_count} sync points for connector {connector_id}")
                    else:
                        self.logger.warning(f"⚠️ Failed to delete sync points for connector {connector_id}, continuing with sync")
                except Exception as sync_point_error:
                    self.logger.error(f"❌ Error deleting sync points for connector {connector_id}: {str(sync_point_error)}")
                    # Continue with sync even if sync point deletion fails
                    self.logger.warning("Continuing with sync despite sync point deletion failure")

                try:
                    deleted_edges, success = await self.graph_provider.delete_connector_sync_edges(
                        connector_id=connector_id
                    )
                    if success:
                        self.logger.info(f"Successfully deleted {deleted_edges} sync edges for connector {connector_id}")
                    else:
                        self.logger.warning(f"Failed to delete some sync edges for connector {connector_id}, continuing with sync")
                except Exception as edge_error:
                    self.logger.error(f"Error deleting connector sync edges for {connector_id}: {str(edge_error)}")

            # Run the sync — at most one task per connector at a time
            await sync_task_manager.start_sync(connector_id, connector.run_sync())
            self.logger.info(f"Started sync for {connector_name} {connector_id} connector")
            return True

        except Exception as e:
            self.logger.error(f"Failed to queue {connector_name.capitalize()} {connector_id} sync service start: {str(e)}")
            return False

    async def _handle_reindex(self, connector_name: str, payload: Dict[str, Any]) -> bool:
        """Handle reindex event for a connector with pagination support.

        Supports three modes:
        1. Record with depth: recordId + depth - reindex a folder and its children
        2. Record group with depth: recordGroupId + depth - reindex all records in a record group
        3. Status-based: statusFilters - reindex records by indexing status (e.g., FAILED)
        """
        try:

            org_id = payload.get("orgId")
            record_id = payload.get("recordId")
            record_group_id = payload.get("recordGroupId")
            depth = payload.get("depth", 0)
            status_filters = payload.get("statusFilters", ["FAILED"])
            connector_id = payload.get("connectorId")
            user_key = payload.get("userKey")

            if not org_id:
                raise ValueError("orgId is required")

            if not connector_id:
                self.logger.error("connectorId is required in payload for reindex event")
                return False

            connector = self._get_connector(connector_id)
            if not connector:
                self.logger.error(f"{connector_name.capitalize()} {connector_id} connector not initialized")
                return False

            connector_app_name = connector.app.get_app_name()
            # Get connector enum value
            enum_key = connector_app_name.name.upper().replace(" ", "_")
            connector_enum = getattr(Connectors, enum_key, None)
            if not connector_enum:
                self.logger.error(f"Unknown connector name: {connector_name}")
                return False

            # Log which mode we're using
            if record_id is not None:
                self.logger.info(f"Starting reindex for {connector_name}, {connector_id} connector record {record_id} with depth {depth}")
            elif record_group_id is not None:
                self.logger.info(f"Starting reindex for {connector_name}, {connector_id} connector record group {record_group_id} with depth {depth}")
            else:
                self.logger.info(f"Starting reindex for {connector_name}, {connector_id} connector with status filters: {status_filters}")

            # Fetch and process records in batches of 100
            batch_size = 100
            offset = 0
            total_processed = 0

            while True:
                # Fetch batch of typed Record instances based on mode
                if record_id is not None:
                    # Mode 1: Reindex a folder and its children
                    records = await self.graph_provider.get_records_by_parent_record(
                        parent_record_id=record_id,
                        connector_id=connector_id,
                        org_id=org_id,
                        depth=depth,
                        user_key=user_key,
                        limit=batch_size,
                        offset=offset
                    )
                elif record_group_id is not None:
                    # Mode 2: Reindex records in a record group
                    records = await self.graph_provider.get_records_by_record_group(
                        record_group_id=record_group_id,
                        connector_id=connector_id,
                        org_id=org_id,
                        depth=depth,
                        user_key=user_key,
                        limit=batch_size,
                        offset=offset
                    )
                else:
                    # Mode 3: Reindex by status
                    records = await self.graph_provider.get_records_by_status(
                        org_id=org_id,
                        connector_id=connector_id,
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

            self.logger.info(f"✅ Completed reindex for {connector_name} {connector_id} connector. Total records processed: {total_processed}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to handle reindex for {connector_name.capitalize()} {connector_id}: {str(e)}", exc_info=True)
            return False
