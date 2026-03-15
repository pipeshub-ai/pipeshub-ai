"""Generic Event Service for handling connector-specific events"""

import logging
from typing import Any, Dict, Optional

from dependency_injector import providers

from app.config.constants.arangodb import CollectionNames, Connectors, EventTypes
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.factory.connector_factory import ConnectorFactory
from app.connectors.core.sync.task_manager import sync_task_manager
from app.containers.connector import ConnectorAppContainer
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms


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

    def _store_connector(self, connector_id: str, connector: BaseConnector) -> None:
        """Store a connector instance in the app_container."""
        connector_key = f"{connector_id}_connector"
        if hasattr(self.app_container, connector_key):
            getattr(self.app_container, connector_key).override(providers.Object(connector))
        else:
            if not hasattr(self.app_container, 'connectors_map'):
                self.app_container.connectors_map = {}
            self.app_container.connectors_map[connector_id] = connector

    async def _ensure_connector(self, connector_name: str, connector_id: str) -> Optional[BaseConnector]:
        """
        Get connector from memory, or auto-initialize it if missing.
        Handles the case where the init event was missed or the service restarted.
        Checks that the connector is active in the database before initializing.
        """
        connector = self._get_connector(connector_id)
        if connector:
            return connector

        self.logger.warning(
            f"{connector_name} connector {connector_id} not in memory — attempting auto-initialization"
        )

        try:
            connector_doc = await self.graph_provider.get_document(
                document_key=connector_id,
                collection=CollectionNames.APPS.value,
            )
            if not connector_doc:
                self.logger.error(
                    f"Connector {connector_id} not found in database — skipping initialization"
                )
                return None
            if not connector_doc.get("isActive", False):
                self.logger.warning(
                    f"Connector {connector_id} is not active in database — skipping initialization"
                )
                return None
            config_service = self.app_container.config_service()
            from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
            data_store_provider = GraphDataStore(self.logger, self.graph_provider)

            connector = await ConnectorFactory.initialize_connector(
                name=connector_name,
                logger=self.logger,
                data_store_provider=data_store_provider,
                config_service=config_service,
                connector_id=connector_id,
            )

            if not connector:
                self.logger.error(
                    f"Auto-initialization failed for {connector_name} connector {connector_id}"
                )
                return None

            self._store_connector(connector_id, connector)
            self.logger.info(
                f"Auto-initialized {connector_name} connector {connector_id} successfully"
            )
            return connector
        except Exception as e:
            self.logger.error(
                f"Auto-initialization error for {connector_name} connector {connector_id}: {e}",
                exc_info=True,
            )
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
            elif action == "delete":
                return await self._handle_delete(connector_name, payload)
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

            self._store_connector(connector_id, connector)
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

            connector = await self._ensure_connector(connector_name, connector_id)
            if not connector:
                self.logger.error(f"{connector_name.capitalize()} {connector_id} connector could not be initialized")
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

            connector = await self._ensure_connector(connector_name, connector_id)
            if not connector:
                self.logger.error(f"{connector_name.capitalize()} {connector_id} connector could not be initialized")
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

    async def _handle_delete(self, connector_name: str, payload: Dict[str, Any]) -> bool:
        """
        Handle the async connector deletion event.

        Flow:
        1. Call delete_connector_instance on the graph DB
        2. On success: publish bulkDeleteRecords for Qdrant cleanup, delete etcd config
        3. On failure: revert status to null so the connector is not stuck
        """
        org_id = payload.get("orgId")
        connector_id = payload.get("connectorId")
        previous_is_active = payload.get("previousIsActive", False)

        if not org_id or not connector_id:
            self.logger.error("'orgId' and 'connectorId' are required in the delete payload")
            return False

        self.logger.info(f"🗑️ Processing async deletion for {connector_name} connector {connector_id}")

        try:
            # Cancel any running sync task for this connector before deleting
            await sync_task_manager.cancel_sync(connector_id)

            # Delete from graph DB
            result = await self.graph_provider.delete_connector_instance(
                connector_id=connector_id,
                org_id=org_id
            )

            if not result.get("success"):
                raise Exception(result.get("error", "Unknown deletion failure from graph DB"))

            self.logger.info(
                f"✅ Graph DB deletion complete for connector {connector_id}. "
                f"Records: {result.get('deleted_records_count', 0)}"
            )

            # Publish bulkDeleteRecords so the indexing service cleans up Qdrant embeddings
            virtual_record_ids = result.get("virtual_record_ids", [])
            if virtual_record_ids:
                try:
                    await self.app_container.messaging_producer.send_message(
                        topic="record-events",
                        message={
                            "eventType": EventTypes.BULK_DELETE_RECORDS.value,
                            "payload": {
                                "orgId": org_id,
                                "connectorId": connector_id,
                                "virtualRecordIds": virtual_record_ids,
                                "totalRecords": len(virtual_record_ids),
                            },
                            "timestamp": get_epoch_timestamp_in_ms(),
                        },
                    )
                    self.logger.info(f"✅ Published bulkDeleteRecords for {len(virtual_record_ids)} records")
                except Exception as kafka_err:
                    self.logger.error(
                        f"❌ Failed to publish bulkDeleteRecords for connector {connector_id}: {kafka_err}. "
                        f"Embeddings may persist in Qdrant — manual cleanup may be required."
                    )

            # Delete connector credentials from etcd/config store
            try:
                config_service = self.app_container.config_service()
                config_path = f"/services/connectors/{connector_id}/config"
                await config_service.delete_config(config_path)
                self.logger.info(f"✅ Deleted etcd config for connector {connector_id}")
            except Exception as config_err:
                self.logger.error(
                    f"❌ Failed to delete etcd config for connector {connector_id}: {config_err}. "
                    f"Orphaned configuration may remain."
                )

            self.logger.info(f"✅ Async deletion complete for connector {connector_id}")
            return True

        except Exception as e:
            self.logger.error(
                f"❌ Async deletion failed for connector {connector_id}: {e}",
                exc_info=True
            )
            try:
                await self.graph_provider.batch_upsert_nodes(
                    [{
                        "id": connector_id,
                        "status": None,
                        "isActive": previous_is_active,
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                    }],
                    CollectionNames.APPS.value
                )
                self.logger.info(f"↩️ Reverted status for connector {connector_id}")
            except Exception as revert_err:
                self.logger.error(
                    f"❌ Failed to revert status for connector {connector_id}: {revert_err}. "
                    f"Connector may be stuck in DELETING state."
                )
            return False
