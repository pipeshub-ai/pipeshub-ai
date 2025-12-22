"""
Connector Migration Service with Transaction Support

This module provides functionality to migrate legacy name-based connector instances
to UUID-based connector instances with proper relationship management and transaction safety.

Migration Steps:
1. Read all existing connector apps from the database (filter out already-migrated UUID-based apps)
2. Create new app instances with UUID keys
3. Migrate organizational relationships to new instances
4. Migrate user-app relationships to new instances
5. Backfill connectorId on associated records
6. Backfill connectorId on record groups
7. Backfill connectorId on app user groups
8. Backfill connectorId on roles
9. Migrate sync points to reference new connector IDs
10. Migrate page tokens to reference new connector IDs
11. Migrate channel history to reference new connector IDs
12. Copy etcd configurations to new instance paths
13. Clean up legacy documents and relationships

The migration is idempotent and can be safely re-run.
All operations within a single app migration are atomic via transactions.
"""

import asyncio
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames, ConnectorScopes
from app.connectors.services.base_arango_service import BaseArangoService


class ConnectorMigrationError(Exception):
    """Base exception for connector migration errors."""
    pass


class ConnectorMigrationService:
    """
    Service for migrating legacy name-addressed connectors to UUID-keyed instances.

    This service handles the complete migration process including:
    - Creating new UUID-based connector instances
    - Migrating organizational relationships (org-app edges)
    - Migrating user-app relationships (user-app edges)
    - Backfilling connector IDs on records
    - Backfilling connector IDs on record groups
    - Backfilling connector IDs on app user groups
    - Backfilling connector IDs on roles
    - Migrating sync points to reference new connector IDs
    - Migrating page tokens to reference new connector IDs
    - Migrating channel history to reference new connector IDs
    - Copying etcd configurations
    - Cleaning up legacy data

    Note: Roles (AppRole) are migrated to support connectorId for
    consistency with other entities, even though they primarily reference
    connectors by connectorName.

    All database operations for a single connector are wrapped in transactions
    for atomicity and safety.

    Attributes:
        arango (BaseArangoService): ArangoDB service instance
        config (ConfigurationService): Configuration service instance
        logger: Logger instance for migration tracking
    """

    # Migration version identifier for idempotency
    MIGRATION_FLAG_KEY = "/migrations/connectors_uuid_instance_v1"

    def __init__(
        self,
        arango_service: BaseArangoService,
        config_service: ConfigurationService,
        logger,
    ) -> None:
        """
        Initialize the connector migration service.

        Args:
            arango_service: Service for ArangoDB operations
            config_service: Service for configuration management
            logger: Logger for tracking migration progress
        """
        self.arango = arango_service
        self.config = config_service
        self.logger = logger
        self.org_type : str | None = None

    async def _is_migration_already_done(self) -> bool:
        """
        Check if migration has already been completed.

        Returns:
            bool: True if migration was previously completed, False otherwise
        """
        try:
            flag = await self.config.get_config(self.MIGRATION_FLAG_KEY)
            return bool(flag and flag.get("done") is True)
        except Exception as e:
            self.logger.debug(
                f"Unable to read migration flag (assuming not done): {e}"
            )
            return False

    async def _mark_migration_done(self) -> None:
        """
        Mark the migration as completed in the configuration store.

        This creates a persistent flag to ensure idempotency on subsequent runs.
        """
        try:
            await self.config.set_config(self.MIGRATION_FLAG_KEY, {"done": True})
            self.logger.info("Migration completion flag set successfully")
        except Exception as e:
            # Non-fatal: migration itself completed successfully
            self.logger.warning(
                f"Failed to set migration completion flag: {e}. "
                "Migration completed but may run again on next startup."
            )

    async def _get_organisation_type(self) -> str | None:
        """
        Get the organisation type from the database.
        """
        try:
            query = f"""
                FOR org IN {CollectionNames.ORGS.value}
                  RETURN org
            """
            cursor = self.arango.db.aql.execute(query)
            org = list(cursor)
            if not org:
                self.logger.error("No organisation found")
                return None
            org_type = org[0].get("accountType", "")
            if not org_type:
                self.logger.error("No organisation type found")
                raise ValueError("No organisation type found")
            return org_type
        except Exception as e:
            self.logger.error(f"Failed to get organisation type: {e}")
            return None

    async def _fetch_all_apps(self) -> List[Dict]:
        """
        Retrieve all connector app documents from the database.

        Returns:
            List[Dict]: List of all app documents

        Raises:
            ConnectorMigrationError: If unable to fetch apps from database
        """
        try:
            query = f"""
                FOR app IN {CollectionNames.APPS.value}
                  RETURN app
            """
            cursor = self.arango.db.aql.execute(query)
            apps = list(cursor)
            self.logger.info(f"Retrieved {len(apps)} connector apps from database")
            return apps
        except Exception as e:
            error_msg = f"Failed to fetch connector apps: {e}"
            self.logger.error(error_msg)
            raise ConnectorMigrationError(error_msg) from e

    def _get_connector_info(self, legacy_app: Dict) -> Tuple[str, str]:
        """
        Extract connector name and key from legacy app document.

        Args:
            legacy_app: Legacy app document

        Returns:
            tuple: (connector_name, legacy_key)
        """
        legacy_key = legacy_app.get("_key", "")
        connector_name = legacy_app.get("name", "Unknown") or legacy_app.get("type", "Unknown")
        return connector_name, legacy_key

    async def _migrate_single_app_with_transaction(
        self,
        legacy_app: Dict
    ) -> Dict:
        """
        Migrate a single app with full transaction support.

        All operations for one app are atomic - either all succeed or all rollback.

        Args:
            legacy_app: Legacy app document to migrate

        Returns:
            Dict: Result with success status and details
        """
        connector_name, legacy_key = self._get_connector_info(legacy_app)

        # Start transaction with all collections we'll modify
        transaction = None
        try:
            transaction = self.arango.db.begin_transaction(
                write=[
                    CollectionNames.APPS.value,
                    CollectionNames.ORG_APP_RELATION.value,
                    CollectionNames.USER_APP_RELATION.value,
                    CollectionNames.RECORDS.value,
                    CollectionNames.RECORD_GROUPS.value,
                    CollectionNames.GROUPS.value,
                    CollectionNames.ROLES.value,
                    CollectionNames.SYNC_POINTS.value,
                    CollectionNames.PAGE_TOKENS.value,
                    CollectionNames.CHANNEL_HISTORY.value,
                ]
            )

            self.logger.info(f"🔄 Transaction started for app: {connector_name} ({legacy_key})")

            # Step 1: Create new UUID-based instance
            new_app = await self._create_new_app_instance(legacy_app, transaction)

            # Step 2: Migrate organizational relationships
            await self._fix_org_edges(legacy_app, new_app, transaction)

            # Step 3: Migrate page tokens (only for Drive connectors, before user-app edges are migrated)
            connector_type = legacy_app.get("name", "") or legacy_app.get("type", "")
            updated_page_tokens = 0
            if connector_type.lower() == "drive":
                updated_page_tokens = await self._migrate_page_tokens(
                    legacy_app, new_app, transaction
                )

            # Step 4: Migrate channel history (only for Gmail connectors, before user-app edges are migrated)
            updated_channel_history = 0
            if connector_type.lower() == "gmail":
                updated_channel_history = await self._migrate_channel_history(
                    legacy_app, new_app, transaction
                )

            # Step 5: Migrate user-app relationships
            updated_user_edges = await self._fix_user_app_edges(
                legacy_app, new_app, transaction
            )

            # Step 6: Backfill connector IDs on records
            updated_records = await self._backfill_record_connector_ids(
                legacy_app, new_app, transaction
            )

            # Step 7: Backfill connector IDs on record groups
            updated_record_groups = await self._backfill_record_group_connector_ids(
                legacy_app, new_app, transaction
            )

            # Step 8: Backfill connector IDs on app user groups
            updated_user_groups = await self._backfill_user_group_connector_ids(
                legacy_app, new_app, transaction
            )

            # Step 9: Backfill connector IDs on roles
            updated_roles = await self._backfill_role_connector_ids(
                legacy_app, new_app, transaction
            )

            # Step 10: Migrate sync points
            updated_sync_points = await self._migrate_sync_points(
                legacy_app, new_app, transaction
            )

            # Step 11: Delete legacy app and edges
            await self._delete_legacy_app_and_edges(legacy_app, transaction)

            # Step 10: Commit transaction
            self.logger.info(f"💾 Committing transaction for {connector_name}...")
            await asyncio.to_thread(lambda: transaction.commit_transaction())
            self.logger.info(f"✅ Transaction committed for {connector_name}")

            # Step 11: Copy etcd config (outside transaction - non-critical)
            try:
                await self._copy_etcd_config(legacy_app, new_app)
            except Exception as config_error:
                self.logger.warning(
                    f"Config copy failed for {connector_name} (non-fatal): {config_error}"
                )

            return {
                "success": True,
                "legacy_key": legacy_key,
                "new_key": new_app["_key"],
                "updated_records": updated_records,
                "updated_record_groups": updated_record_groups,
                "updated_user_groups": updated_user_groups,
                "updated_roles": updated_roles,
                "updated_user_edges": updated_user_edges,
                "updated_sync_points": updated_sync_points,
                "updated_page_tokens": updated_page_tokens,
                "updated_channel_history": updated_channel_history,
                "connector_type": connector_name
            }

        except Exception as e:
            # Rollback transaction on any error
            if transaction:
                try:
                    self.logger.warning(f"🔄 Rolling back transaction for {connector_name}...")
                    await asyncio.to_thread(lambda: transaction.abort_transaction())
                    self.logger.info(f"✅ Transaction rolled back for {connector_name}")
                except Exception as rollback_error:
                    self.logger.error(
                        f"❌ Transaction rollback failed for {connector_name}: {rollback_error}"
                    )

            error_msg = f"Migration failed for {connector_name} ({legacy_key}): {str(e)}"
            self.logger.error(error_msg)
            return {
                "success": False,
                "legacy_key": legacy_key,
                "connector_type": connector_name,
                "error": str(e)
            }

    async def _create_new_app_instance(
        self,
        legacy_app: Dict,
        transaction
    ) -> Dict:
        """
        Create a new connector app instance with a UUID-based key.

        Args:
            legacy_app: Legacy app document to migrate
            transaction: Active ArangoDB transaction

        Returns:
            Dict: Newly created app document with UUID key

        Raises:
            ConnectorMigrationError: If app creation fails
        """
        new_key = str(uuid4())

        if not self.org_type:
            self.logger.error("No organisation type found")
            raise ValueError("No organisation type found")

        created_by = None

        if self.org_type.lower() == "enterprise":
            scope = ConnectorScopes.TEAM.value
        else:
            scope = ConnectorScopes.PERSONAL.value
            user = await self.arango.get_users(org_id=legacy_app.get("orgId"), active=True)
            if user:
                created_by = user[0].get("userId")

        # Preserve critical fields from legacy app
        new_app = {
            "_key": new_key,
            "name": legacy_app.get("name"),
            "type": legacy_app.get("name"),
            "appGroup": legacy_app.get("appGroup"),
            "authType": legacy_app.get("authType"),
            "isActive": legacy_app.get("isActive", False),
            "isConfigured": legacy_app.get("isConfigured", False),
            "isAgentActive": legacy_app.get("isAgentActive", False),
            "createdBy": created_by,
            "updatedBy": None,
            "isAuthenticated": legacy_app.get("isAuthenticated", False),
            "scope": scope,
            "createdAtTimestamp": legacy_app.get("createdAtTimestamp"),
            "updatedAtTimestamp": legacy_app.get("updatedAtTimestamp"),
        }

        try:
            await self.arango.batch_upsert_nodes(
                [new_app],
                collection=CollectionNames.APPS.value,
                transaction=transaction
            )
            self.logger.info(
                f"Created new app instance '{new_key}' for connector type "
                f"'{legacy_app.get('type')}' (legacy key: {legacy_app.get('_key')})"
            )
            return new_app
        except Exception as e:
            error_msg = (
                f"Failed to create new app instance for "
                f"{legacy_app.get('type')}: {e}"
            )
            self.logger.error(error_msg)
            raise ConnectorMigrationError(error_msg) from e

    async def _copy_etcd_config(self, legacy_app: Dict, new_app: Dict) -> None:
        """
        Copy configuration from legacy name-based path to new UUID-based path.

        This operation is performed OUTSIDE of the database transaction since
        it's non-critical and shouldn't cause transaction rollback.

        Args:
            legacy_app: Legacy app document
            new_app: New app document with UUID key
        """
        # Construct legacy config path
        name = legacy_app.get("name", "").replace(" ", "").lower()
        if not name:
            self.logger.warning(
                f"Legacy app {legacy_app.get('_key')} has no name, skipping config copy"
            )
            return

        legacy_key = f"/services/connectors/{name}/config"
        src_config: Optional[Dict] = None

        # Attempt to retrieve legacy configuration
        try:
            cfg = await self.config.get_config(legacy_key)
            if isinstance(cfg, dict) and cfg:
                src_config = cfg
                self.logger.debug(f"Found legacy config at {legacy_key}")
        except Exception as e:
            self.logger.debug(f"No legacy config found at {legacy_key}: {e}")

        if not src_config:
            self.logger.info(
                f"No legacy etcd config found for '{legacy_app.get('name')}', "
                "skipping config copy"
            )
            return

        # Copy configuration to new UUID-based path
        dest_key = f"/services/connectors/{new_app['_key']}/config"
        try:
            await self.config.set_config(dest_key, src_config)
            self.logger.info(f"Copied etcd config from {legacy_key} to {dest_key}")
        except Exception as e:
            self.logger.error(f"Failed to copy config to {dest_key}: {e}")
            raise ConnectorMigrationError(
                f"Config copy failed for {legacy_app.get('name')}"
            ) from e

        # Attempt to delete legacy config (best-effort)
        try:
            if hasattr(self.config, 'delete_config'):
                await self.config.delete_config(legacy_key)
                self.logger.info(f"Deleted legacy etcd config at {legacy_key}")
        except Exception as e:
            # Non-fatal: config was copied successfully
            self.logger.warning(
                f"Unable to delete legacy config at {legacy_key}: {e}. "
                "Manual cleanup may be required."
            )

    async def _migrate_app_edges(
        self,
        edge_collection: str,
        legacy_app: Dict,
        new_app: Dict,
        transaction,
        edge_type: str,
        is_non_fatal: bool = False
    ) -> int:
        """
        Generic helper to migrate edges from legacy app to new app instance.

        Creates new edges pointing to the new app and immediately deletes old edges
        to prevent duplicates. Preserves all edge attributes.

        Args:
            edge_collection: Name of the edge collection
            legacy_app: Legacy app document
            new_app: New app document with UUID key
            transaction: Active ArangoDB transaction
            edge_type: Human-readable edge type for logging
            is_non_fatal: If True, return 0 on errors instead of raising

        Returns:
            int: Number of edges migrated
        """
        old_id = f"{CollectionNames.APPS.value}/{legacy_app['_key']}"
        new_id = f"{CollectionNames.APPS.value}/{new_app['_key']}"

        # Find all edges pointing to the legacy app
        query = f"""
            FOR edge IN {edge_collection}
              FILTER edge._to == @old_id
              RETURN edge
        """

        try:
            edges = list(
                transaction.aql.execute(query, bind_vars={"old_id": old_id})
            )
        except Exception as e:
            if is_non_fatal:
                self.logger.debug(
                    f"Could not query {edge_type} edges for {legacy_app.get('_key')}: {e}"
                )
                return 0
            else:
                self.logger.error(
                    f"Failed to query {edge_type} edges for {legacy_app.get('_key')}: {e}"
                )
                raise ConnectorMigrationError(f"Failed to query {edge_type} edges") from e

        if not edges:
            self.logger.debug(f"No {edge_type} edges found for {legacy_app.get('_key')}")
            return 0

        # Create new edges pointing to the new app instance
        new_edges = []
        for edge in edges:
            # Preserve all edge attributes except system fields
            edge_data = {
                k: v
                for k, v in edge.items()
                if k not in ["_key", "_id", "_rev", "_from", "_to"]
            }
            new_edges.append({
                "_from": edge["_from"],
                "_to": new_id,
                **edge_data,
            })

        try:
            # Create new edges
            await self.arango.batch_create_edges(
                new_edges,
                collection=edge_collection,
                transaction=transaction
            )
            self.logger.info(
                f"Created {len(new_edges)} {edge_type} edges to new app instance {new_app['_key']}"
            )

            # Immediately delete old edges to prevent duplicates
            delete_query = f"""
                FOR edge IN {edge_collection}
                  FILTER edge._to == @old_id
                  REMOVE edge IN {edge_collection}
            """
            transaction.aql.execute(delete_query, bind_vars={"old_id": old_id})
            self.logger.info(f"Deleted {len(edges)} old {edge_type} edges pointing to legacy app")

            return len(new_edges)
        except Exception as e:
            error_msg = f"Failed to migrate {edge_type} edges for {new_app['_key']}: {e}"
            if is_non_fatal:
                self.logger.warning(error_msg)
                return 0
            else:
                self.logger.error(error_msg)
                raise ConnectorMigrationError(error_msg) from e

    async def _fix_org_edges(
        self,
        legacy_app: Dict,
        new_app: Dict,
        transaction
    ) -> None:
        """Migrate organizational relationships from legacy app to new app instance."""
        await self._migrate_app_edges(
            CollectionNames.ORG_APP_RELATION.value,
            legacy_app,
            new_app,
            transaction,
            "org-app",
            is_non_fatal=False
        )

    async def _fix_user_app_edges(
        self,
        legacy_app: Dict,
        new_app: Dict,
        transaction
    ) -> int:
        """Migrate user-app relationships from legacy app to new app instance."""
        return await self._migrate_app_edges(
            CollectionNames.USER_APP_RELATION.value,
            legacy_app,
            new_app,
            transaction,
            "user-app",
            is_non_fatal=True  # Collection might not exist in some deployments
        )

    async def _backfill_connector_ids(
        self,
        collection_name: str,
        connector_name: str,
        legacy_key: str,
        new_connector_id: str,
        transaction,
        entity_type: str,
        is_non_fatal: bool = False
    ) -> int:
        """
        Generic helper to backfill connectorId on entities in a collection.

        Only updates entities where connectorId is null, empty, or equals legacy_key.
        This prevents overwriting valid UUID-based connectorIds from other instances.

        Args:
            collection_name: Name of the collection to update
            connector_name: Connector name/type to filter by
            legacy_key: Legacy app key to identify entities to migrate
            new_connector_id: New UUID-based connector instance ID
            transaction: Active ArangoDB transaction
            entity_type: Human-readable entity type for logging
            is_non_fatal: If True, log warnings instead of raising errors

        Returns:
            int: Number of entities updated
        """
        update_query = f"""
            LET updated = (
              FOR doc IN {collection_name}
                FILTER LOWER(SUBSTITUTE(doc.connectorName, " ", "")) == LOWER(SUBSTITUTE(@connector_name, " ", ""))
                  AND (
                    doc.connectorId == null
                    OR doc.connectorId == ''
                    OR doc.connectorId == @legacy_key
                  )
                UPDATE doc WITH {{ connectorId: @connector_id }}
                  IN {collection_name}
                  OPTIONS {{ keepNull: false, mergeObjects: true }}
                RETURN NEW
            )
            RETURN LENGTH(updated)
        """

        try:
            cursor = transaction.aql.execute(
                update_query,
                bind_vars={
                    "connector_name": connector_name,
                    "connector_id": new_connector_id,
                    "legacy_key": legacy_key,
                },
            )
            updated_count = next(cursor, 0) if cursor is not None else 0

            if updated_count > 0:
                self.logger.info(
                    f"Backfilled connectorId on {updated_count} {entity_type} for {connector_name}"
                )

            return int(updated_count or 0)
        except Exception as e:
            error_msg = (
                f"Failed to backfill connectorId for {entity_type} of connector "
                f"{connector_name}: {e}"
            )
            if is_non_fatal:
                self.logger.warning(f"{error_msg} (non-fatal)")
                return 0
            else:
                self.logger.error(error_msg)
                raise ConnectorMigrationError(error_msg) from e

    async def _backfill_record_connector_ids(
        self,
        legacy_app: Dict,
        new_app: Dict,
        transaction
    ) -> int:
        """Update records to reference the new connector instance ID."""
        connector_name, legacy_key = self._get_connector_info(legacy_app)
        if not connector_name or connector_name == "Unknown":
            self.logger.warning(
                f"Legacy app {legacy_key} has no type or name, skipping record backfill"
            )
            return 0

        return await self._backfill_connector_ids(
            CollectionNames.RECORDS.value,
            connector_name,
            legacy_key,
            new_app["_key"],
            transaction,
            "records"
        )

    async def _backfill_record_group_connector_ids(
        self,
        legacy_app: Dict,
        new_app: Dict,
        transaction
    ) -> int:
        """Update record groups to reference the new connector instance ID."""
        connector_name, legacy_key = self._get_connector_info(legacy_app)
        if not connector_name or connector_name == "Unknown":
            self.logger.warning(
                f"Legacy app {legacy_key} has no type or name, skipping record group backfill"
            )
            return 0

        return await self._backfill_connector_ids(
            CollectionNames.RECORD_GROUPS.value,
            connector_name,
            legacy_key,
            new_app["_key"],
            transaction,
            "record groups"
        )

    async def _backfill_user_group_connector_ids(
        self,
        legacy_app: Dict,
        new_app: Dict,
        transaction
    ) -> int:
        """Update app user groups to reference the new connector instance ID."""
        connector_name, legacy_key = self._get_connector_info(legacy_app)
        if not connector_name or connector_name == "Unknown":
            self.logger.warning(
                f"Legacy app {legacy_key} has no type or name, skipping user group backfill"
            )
            return 0

        return await self._backfill_connector_ids(
            CollectionNames.GROUPS.value,
            connector_name,
            legacy_key,
            new_app["_key"],
            transaction,
            "user groups"
        )

    async def _backfill_role_connector_ids(
        self,
        legacy_app: Dict,
        new_app: Dict,
        transaction
    ) -> int:
        """
        Update roles to reference the new connector instance ID.

        Note: Roles may not have connectorId in the schema yet, but we backfill
        it for future compatibility and consistency with other entities.
        """
        connector_name, legacy_key = self._get_connector_info(legacy_app)
        if not connector_name or connector_name == "Unknown":
            self.logger.warning(
                f"Legacy app {legacy_key} has no type or name, skipping role backfill"
            )
            return 0

        return await self._backfill_connector_ids(
            CollectionNames.ROLES.value,
            connector_name,
            legacy_key,
            new_app["_key"],
            transaction,
            "roles",
            is_non_fatal=True  # Non-fatal if schema doesn't support it yet
        )

    async def _migrate_sync_points(
        self,
        legacy_app: Dict,
        new_app: Dict,
        transaction
    ) -> int:
        """
        Migrate sync points from legacy connector ID to new UUID-based connector ID.

        Sync points have the format: {org_id}/{connector_id}/{sync_data_point_type}/{sync_point_key}
        The sync_point_key itself may also contain connector_id if generated with
        generate_record_sync_point_key.

        Args:
            legacy_app: Legacy app document
            new_app: New app document with UUID key
            transaction: Active ArangoDB transaction

        Returns:
            int: Number of sync points migrated
        """
        SPLIT_KEY_PARTS_COUNT = 3
        legacy_key = legacy_app.get("_key", "")
        connector_name = legacy_app.get("name", "")
        new_connector_id = new_app["_key"]

        # Find all sync points that reference the legacy connector ID
        query = f"""
            FOR sync_point IN {CollectionNames.SYNC_POINTS.value}
              FILTER LOWER(SUBSTITUTE(sync_point.connectorName, " ", "")) == LOWER(SUBSTITUTE(@connector_name, " ", ""))
              RETURN sync_point
        """

        try:
            sync_points = list(
                transaction.aql.execute(query, bind_vars={"connector_name": connector_name})
            )
        except Exception as e:
            self.logger.warning(
                f"Could not query sync points for {connector_name}: {e} (non-fatal)"
            )
            return 0

        if not sync_points:
            self.logger.debug(f"No sync points found for connector {connector_name}")
            return 0

        updated_count = 0
        for sync_point in sync_points:
            try:
                old_sync_point_key : str = sync_point.get("syncPointKey", "")
                org_id = sync_point.get("orgId", "")
                sync_data_point_type = sync_point.get("syncDataPointType", "")

                if not old_sync_point_key:
                    self.logger.warning(
                        f"Sync point missing syncPointKey, skipping: {sync_point.get('_key', 'unknown')}"
                    )
                    continue

                # Parse the sync point key format: {org_id}/{connector_id}/{sync_data_point_type}/{sync_point_key}
                # We need to replace the connector_id part
                key_parts = old_sync_point_key.split("/", SPLIT_KEY_PARTS_COUNT)

                # Validate we have at least the minimum parts
                if len(key_parts) < SPLIT_KEY_PARTS_COUNT:
                    self.logger.warning(
                        f"Sync point key format unexpected (too few parts) for {old_sync_point_key}, skipping"
                    )
                    continue

                # Verify the connector_id in the key matches the legacy_key
                if key_parts[1].replace(" ", "").lower() != connector_name.replace(" ", "").lower():
                    # This shouldn't happen since we filtered by connectorId, but log it
                    self.logger.warning(
                        f"Sync point key connector_id mismatch: expected {connector_name}, "
                        f"found {key_parts[1]} in {old_sync_point_key}, skipping"
                    )
                    continue

                # Reconstruct the key with new connector_id
                new_sync_point_key = f"{org_id}/{new_connector_id}/{sync_data_point_type}"

                if len(key_parts) > SPLIT_KEY_PARTS_COUNT:
                    # The sync_point_key part might also contain connector_id
                    sync_point_key_part = key_parts[SPLIT_KEY_PARTS_COUNT]
                    new_sync_point_key = f"{new_sync_point_key}/{sync_point_key_part}"

                # Update the sync point document using its _key
                sync_point_key = sync_point.get("_key")
                if not sync_point_key:
                    self.logger.warning(
                        f"Sync point missing _key, skipping: {old_sync_point_key}"
                    )
                    continue

                update_query = f"""
                    UPDATE {{ _key: @sync_point_key }} WITH {{
                        connectorId: @new_connector_id,
                        syncPointKey: @new_sync_point_key
                    }} IN {CollectionNames.SYNC_POINTS.value}
                    RETURN NEW
                """

                transaction.aql.execute(
                    update_query,
                    bind_vars={
                        "sync_point_key": sync_point_key,
                        "new_connector_id": new_connector_id,
                        "new_sync_point_key": new_sync_point_key,
                    },
                )

                updated_count += 1
                self.logger.debug(
                    f"Migrated sync point: {old_sync_point_key} -> {new_sync_point_key}"
                )

            except Exception as e:
                self.logger.warning(
                    f"Failed to migrate sync point {sync_point.get('syncPointKey', 'unknown')}: {e}"
                )
                continue

        if updated_count > 0:
            self.logger.info(
                f"Migrated {updated_count} sync points for connector {legacy_key} -> {new_connector_id}"
            )

        return updated_count

    async def _migrate_page_tokens(
        self,
        legacy_app: Dict,
        new_app: Dict,
        transaction
    ) -> int:
        """
        Migrate page tokens from legacy connector to new UUID-based connector ID.

        IMPORTANT: This method should ONLY be called for Drive connectors.
        Page tokens are Drive-specific and should not be migrated for other connector types.

        Page tokens are associated with users via userEmail. We need to find all
        page tokens for users that have user-app edges to the legacy connector,
        and update them to include the new connector ID.

        Args:
            legacy_app: Legacy app document (should be a Drive connector)
            new_app: New app document with UUID key
            transaction: Active ArangoDB transaction

        Returns:
            int: Number of page tokens migrated
        """
        legacy_key = legacy_app.get("_key", "")
        new_connector_id = new_app["_key"]
        legacy_app_id = f"{CollectionNames.APPS.value}/{legacy_key}"

        # Find all users that have user-app edges to the legacy connector
        # Then find their page tokens and update them
        query = f"""
            FOR edge IN {CollectionNames.USER_APP_RELATION.value}
              FILTER edge._to == @legacy_app_id
              FOR user IN users
                FILTER user._id == edge._from
              FOR token IN {CollectionNames.PAGE_TOKENS.value}
                FILTER token.userEmail == user.email
                FILTER !HAS(token, "connectorId") OR token.connectorId == null OR token.connectorId == ""
              RETURN {{
                token: token,
                userEmail: user.email
              }}
        """

        try:
            results = list(
                transaction.aql.execute(
                    query,
                    bind_vars={"legacy_app_id": legacy_app_id}
                )
            )
        except Exception as e:
            self.logger.warning(
                f"Could not query page tokens for {legacy_key}: {e} (non-fatal)"
            )
            return 0

        if not results:
            self.logger.debug(f"No page tokens found for connector {legacy_key}")
            return 0

        updated_count = 0
        for result in results:
            try:
                token = result["token"]
                token_key = token.get("_key")
                if not token_key:
                    self.logger.warning(
                        f"Page token missing _key, skipping: {result.get('userEmail', 'unknown')}"
                    )
                    continue

                # Update the page token with the new connector ID
                update_query = f"""
                    UPDATE {{ _key: @token_key }} WITH {{
                        connectorId: @new_connector_id
                    }} IN {CollectionNames.PAGE_TOKENS.value}
                    RETURN NEW
                """

                transaction.aql.execute(
                    update_query,
                    bind_vars={
                        "token_key": token_key,
                        "new_connector_id": new_connector_id,
                    },
                )

                updated_count += 1
                self.logger.debug(
                    f"Migrated page token for user {result['userEmail']} "
                    f"to connector {new_connector_id}"
                )

            except Exception as e:
                self.logger.warning(
                    f"Failed to migrate page token for {result.get('userEmail', 'unknown')}: {e}"
                )
                continue

        if updated_count > 0:
            self.logger.info(
                f"Migrated {updated_count} page tokens for connector "
                f"{legacy_key} -> {new_connector_id}"
            )

        return updated_count

    async def _migrate_channel_history(
        self,
        legacy_app: Dict,
        new_app: Dict,
        transaction
    ) -> int:
        """
        Migrate channel history from legacy connector to new UUID-based connector ID.

        IMPORTANT: This method should ONLY be called for Gmail connectors.
        Channel history is Gmail-specific and should not be migrated for other connector types.

        Channel history is associated with users via userEmail. We need to find all
        channel history for users that have user-app edges to the legacy connector,
        and update them to include the new connector ID.

        Args:
            legacy_app: Legacy app document (should be a Gmail connector)
            new_app: New app document with UUID key
            transaction: Active ArangoDB transaction

        Returns:
            int: Number of channel history records migrated
        """
        legacy_key = legacy_app.get("_key", "")
        new_connector_id = new_app["_key"]
        legacy_app_id = f"{CollectionNames.APPS.value}/{legacy_key}"

        # Find all users that have user-app edges to the legacy connector
        # Then find their channel history and update them
        query = f"""
            FOR edge IN {CollectionNames.USER_APP_RELATION.value}
              FILTER edge._to == @legacy_app_id
              FOR user IN users
                FILTER user._id == edge._from
              FOR history IN {CollectionNames.CHANNEL_HISTORY.value}
                FILTER history.userEmail == user.email
                FILTER !HAS(history, "connectorId") OR history.connectorId == null OR history.connectorId == ""
              RETURN {{
                history: history,
                userEmail: user.email
              }}
        """

        try:
            results = list(
                transaction.aql.execute(
                    query,
                    bind_vars={"legacy_app_id": legacy_app_id}
                )
            )
        except Exception as e:
            self.logger.warning(
                f"Could not query channel history for {legacy_key}: {e} (non-fatal)"
            )
            return 0

        if not results:
            self.logger.debug(f"No channel history found for connector {legacy_key}")
            return 0

        updated_count = 0
        for result in results:
            try:
                history = result["history"]
                history_key = history.get("_key")
                if not history_key:
                    self.logger.warning(
                        f"Channel history missing _key, skipping: {result.get('userEmail', 'unknown')}"
                    )
                    continue

                # Update the channel history with the new connector ID
                update_query = f"""
                    UPDATE {{ _key: @history_key }} WITH {{
                        connectorId: @new_connector_id
                    }} IN {CollectionNames.CHANNEL_HISTORY.value}
                    RETURN NEW
                """

                transaction.aql.execute(
                    update_query,
                    bind_vars={
                        "history_key": history_key,
                        "new_connector_id": new_connector_id,
                    },
                )

                updated_count += 1
                self.logger.debug(
                    f"Migrated channel history for user {result['userEmail']} "
                    f"to connector {new_connector_id}"
                )

            except Exception as e:
                self.logger.warning(
                    f"Failed to migrate channel history for {result.get('userEmail', 'unknown')}: {e}"
                )
                continue

        if updated_count > 0:
            self.logger.info(
                f"Migrated {updated_count} channel history records for connector "
                f"{legacy_key} -> {new_connector_id}"
            )

        return updated_count

    async def _delete_legacy_app_and_edges(
        self,
        legacy_app: Dict,
        transaction
    ) -> None:
        """
        Remove legacy app document and any remaining associated edges.

        Note: Edges are already deleted during migration steps, but this serves
        as a safety cleanup. The edge deletion here is idempotent.

        Args:
            legacy_app: Legacy app document to delete
            transaction: Active ArangoDB transaction
        """
        old_id = f"{CollectionNames.APPS.value}/{legacy_app['_key']}"

        # Safety cleanup: Delete any remaining edges (idempotent operation)
        # Edges should already be deleted, but this ensures complete cleanup
        edge_collections = [
            (CollectionNames.ORG_APP_RELATION.value, "org-app"),
            (CollectionNames.USER_APP_RELATION.value, "user-app"),
        ]

        for collection, edge_type in edge_collections:
            try:
                delete_query = f"""
                    FOR edge IN {collection}
                      FILTER edge._to == @old_id OR edge._from == @old_id
                      REMOVE edge IN {collection}
                """
                transaction.aql.execute(delete_query, bind_vars={"old_id": old_id})
                self.logger.debug(f"Cleaned up any remaining {edge_type} edges for {legacy_app['_key']}")
            except Exception as e:
                # Non-fatal: edges may already be deleted or collection might not exist
                self.logger.debug(f"Could not clean up {edge_type} edges for {legacy_app['_key']}: {e}")

        # Delete the legacy app document
        try:
            delete_app_query = f"""
                REMOVE {{ _key: @key }} IN {CollectionNames.APPS.value}
            """
            transaction.aql.execute(
                delete_app_query, bind_vars={"key": legacy_app["_key"]}
            )
            self.logger.info(
                f"Deleted legacy app '{legacy_app['_key']}' "
                f"({legacy_app.get('type') or legacy_app.get('name', 'Unknown')})"
            )
        except Exception as e:
            error_msg = f"Failed to delete legacy app {legacy_app['_key']}: {e}"
            self.logger.error(error_msg)
            raise ConnectorMigrationError(error_msg) from e

    async def migrate_all(self) -> None:
        """
        Execute the complete migration process for all connector apps.

        This is the main entry point for the migration. It processes all
        legacy connector apps, creating new UUID-based instances and
        migrating all associated data and relationships.
        The migration is idempotent - it will skip execution if the
        completion flag is already set.

        Each app migration is atomic via transactions.
        """
        # Check if migration was already completed
        if await self._is_migration_already_done():
            self.logger.info(
                "Connector UUID migration already completed - skipping"
            )
            return

        self.logger.info("=" * 70)
        self.logger.info("Starting Connector UUID Migration")
        self.logger.info("=" * 70)

        # Fetch the organisation type

        try:
            org_type = await self._get_organisation_type()
            if not org_type:
                self.logger.error("No organisation type found")
                return
            self.org_type = org_type
        except Exception as e:
            self.logger.error(f"Failed to get organisation type: {e}")
            return

        # Fetch all apps in the system
        try:
            apps = await self._fetch_all_apps()
        except ConnectorMigrationError:
            self.logger.error("Migration aborted: unable to fetch apps")
            return

        if not apps or len(apps) == 0:
            # Fresh setup: no connector instances exist yet. Treat migration as
            # completed so it does not run unnecessarily on subsequent startups.
            self.logger.info(
                "No connector apps found in database - treating as fresh setup. "
                "Marking connector migration as completed."
            )
            await self._mark_migration_done()
            return

        self.logger.info(f"Found {len(apps)} connector apps to migrate")

        # Track migration statistics
        success_count = 0
        error_count = 0
        errors = []

        # Process each legacy app with its own transaction
        for idx, legacy_app in enumerate(apps, 1):
            connector_name, legacy_key = self._get_connector_info(legacy_app)

            self.logger.info(
                f"\n[{idx}/{len(apps)}] Migrating: {connector_name} ({legacy_key})"
            )

            # Migrate single app with transaction
            result = await self._migrate_single_app_with_transaction(legacy_app)

            if result["success"]:
                success_count += 1
                # Log summary of updates (only if there were any)
                updates = []
                if result.get("updated_records", 0) > 0:
                    updates.append(f"{result['updated_records']} records")
                if result.get("updated_record_groups", 0) > 0:
                    updates.append(f"{result['updated_record_groups']} record groups")
                if result.get("updated_user_groups", 0) > 0:
                    updates.append(f"{result['updated_user_groups']} user groups")
                if result.get("updated_roles", 0) > 0:
                    updates.append(f"{result['updated_roles']} roles")
                if result.get("updated_user_edges", 0) > 0:
                    updates.append(f"{result['updated_user_edges']} user-app edges")
                if result.get("updated_sync_points", 0) > 0:
                    updates.append(f"{result['updated_sync_points']} sync points")
                if result.get("updated_page_tokens", 0) > 0:
                    updates.append(f"{result['updated_page_tokens']} page tokens")
                if result.get("updated_channel_history", 0) > 0:
                    updates.append(f"{result['updated_channel_history']} channel history")

                if updates:
                    self.logger.info(
                        f"  ✓ Successfully migrated {connector_name}: "
                        f"updated {', '.join(updates)}"
                    )
                else:
                    self.logger.info(f"  ✓ Successfully migrated {connector_name} (no entities to update)")
            else:
                error_count += 1
                error_msg = result.get("error", "Unknown error")
                self.logger.error(f"  ✗ {error_msg}")
                errors.append({
                    "app": connector_name,
                    "key": legacy_key,
                    "error": error_msg
                })

        # Log migration summary
        self.logger.info("\n" + "=" * 70)
        self.logger.info("Migration Summary")
        self.logger.info("=" * 70)
        self.logger.info(f"Total apps processed: {len(apps)}")
        self.logger.info(f"Successfully migrated: {success_count}")
        self.logger.info(f"Failed migrations: {error_count}")

        if errors:
            self.logger.warning("\nFailed migrations:")
            for error in errors:
                self.logger.warning(
                    f"  - {error['app']} ({error['key']}): {error['error']}"
                )

        # Mark migration as complete even if some apps failed
        # Successfully migrated apps remain migrated due to transactions
        await self._mark_migration_done()

        self.logger.info("=" * 70)
        self.logger.info("Migration Complete")
        self.logger.info("=" * 70)


async def run_migration(
    arango_service: BaseArangoService,
    config_service: ConfigurationService,
    logger,
) -> None:
    """
    Convenience function to execute the connector migration.

    Args:
        arango_service: Service for ArangoDB operations
        config_service: Service for configuration management
        logger: Logger for tracking migration progress

    Example:
        >>> await run_migration(arango_service, config_service, logger)
    """
    service = ConnectorMigrationService(arango_service, config_service, logger)
    await service.migrate_all()
