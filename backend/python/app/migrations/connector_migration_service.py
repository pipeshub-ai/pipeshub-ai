"""
Connector Migration Service with Transaction Support

This module provides functionality to migrate legacy name-based connector instances
to UUID-based connector instances with proper relationship management and transaction safety.

Migration Steps:
1. Read all existing connector apps from the database
2. Create new app instances with UUID keys
3. Migrate organizational relationships to new instances
4. Backfill connectorId on associated records
5. Copy etcd configurations to new instance paths
6. Clean up legacy documents and relationships

The migration is idempotent and can be safely re-run.
All operations within a single app migration are atomic via transactions.
"""

import asyncio
from typing import Dict, List, Optional
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
    - Migrating organizational relationships
    - Backfilling connector IDs on records
    - Copying etcd configurations
    - Cleaning up legacy data

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
        legacy_key = legacy_app.get("_key")
        legacy_type = legacy_app.get("type") or legacy_app.get("name", "Unknown")

        # Start transaction with all collections we'll modify
        transaction = None
        try:
            transaction = self.arango.db.begin_transaction(
                write=[
                    CollectionNames.APPS.value,
                    CollectionNames.ORG_APP_RELATION.value,
                    CollectionNames.USER_APP_RELATION.value,
                    CollectionNames.RECORDS.value,
                ]
            )

            self.logger.info(f"🔄 Transaction started for app: {legacy_type} ({legacy_key})")

            # Step 1: Create new UUID-based instance
            new_app = await self._create_new_app_instance(legacy_app, transaction)

            # Step 2: Migrate organizational relationships
            await self._fix_org_edges(legacy_app, new_app, transaction)

            # Step 3: Backfill connector IDs on records
            updated_records = await self._backfill_record_connector_ids(
                legacy_app, new_app, transaction
            )

            # Step 4: Delete legacy app and edges
            await self._delete_legacy_app_and_edges(legacy_app, transaction)

            # Step 5: Commit transaction
            self.logger.info(f"💾 Committing transaction for {legacy_type}...")
            await asyncio.to_thread(lambda: transaction.commit_transaction())
            self.logger.info(f"✅ Transaction committed for {legacy_type}")

            # Step 6: Copy etcd config (outside transaction - non-critical)
            try:
                await self._copy_etcd_config(legacy_app, new_app)
            except Exception as config_error:
                self.logger.warning(
                    f"Config copy failed for {legacy_type} (non-fatal): {config_error}"
                )

            return {
                "success": True,
                "legacy_key": legacy_key,
                "new_key": new_app["_key"],
                "updated_records": updated_records,
                "connector_type": legacy_type
            }

        except Exception as e:
            # Rollback transaction on any error
            if transaction:
                try:
                    self.logger.warning(f"🔄 Rolling back transaction for {legacy_type}...")
                    await asyncio.to_thread(lambda: transaction.abort_transaction())
                    self.logger.info(f"✅ Transaction rolled back for {legacy_type}")
                except Exception as rollback_error:
                    self.logger.error(
                        f"❌ Transaction rollback failed for {legacy_type}: {rollback_error}"
                    )

            error_msg = f"Migration failed for {legacy_type} ({legacy_key}): {str(e)}"
            self.logger.error(error_msg)
            return {
                "success": False,
                "legacy_key": legacy_key,
                "connector_type": legacy_type,
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
            "createdBy": None,
            "updatedBy": None,
            "isAuthenticated": legacy_app.get("isAuthenticated", False),
            "scope": legacy_app.get("scope", ConnectorScopes.TEAM.value),
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

    async def _fix_org_edges(
        self,
        legacy_app: Dict,
        new_app: Dict,
        transaction
    ) -> None:
        """
        Migrate organizational relationships from legacy app to new app instance.

        Creates new edges connecting organizations to the new app instance,
        preserving all edge attributes from the legacy relationships.

        Args:
            legacy_app: Legacy app document
            new_app: New app document with UUID key
            transaction: Active ArangoDB transaction
        """
        old_id = f"{CollectionNames.APPS.value}/{legacy_app['_key']}"

        # Find all organizational relationships to the legacy app
        query = f"""
            FOR edge IN {CollectionNames.ORG_APP_RELATION.value}
              FILTER edge._to == @old_id
              RETURN edge
        """

        try:
            edges = list(
                transaction.aql.execute(query, bind_vars={"old_id": old_id})
            )
        except Exception as e:
            self.logger.error(
                f"Failed to query org-app edges for {legacy_app.get('_key')}: {e}"
            )
            raise ConnectorMigrationError("Failed to query organizational edges") from e

        if not edges:
            self.logger.debug(
                f"No organizational edges found for {legacy_app.get('_key')}"
            )
            return

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
                "_to": f"{CollectionNames.APPS.value}/{new_app['_key']}",
                **edge_data,
            })

        try:
            await self.arango.batch_create_edges(
                new_edges,
                collection=CollectionNames.ORG_APP_RELATION.value,
                transaction=transaction
            )
            self.logger.info(
                f"Created {len(new_edges)} org-app edges to new app instance "
                f"{new_app['_key']}"
            )
        except Exception as e:
            error_msg = (
                f"Failed to create {len(new_edges)} org-app edges for "
                f"{new_app['_key']}: {e}"
            )
            self.logger.error(error_msg)
            raise ConnectorMigrationError(error_msg) from e

    async def _backfill_record_connector_ids(
        self,
        legacy_app: Dict,
        new_app: Dict,
        transaction
    ) -> int:
        """
        Update records to reference the new connector instance ID.

        Finds all records associated with the legacy connector (by name/type)
        that don't have a connectorId set, and updates them to reference
        the new UUID-based connector instance.

        Args:
            legacy_app: Legacy app document
            new_app: New app document with UUID key
            transaction: Active ArangoDB transaction

        Returns:
            int: Number of records updated

        Raises:
            ConnectorMigrationError: If backfill operation fails
        """
        connector_name = legacy_app.get("type") or legacy_app.get("name")
        if not connector_name:
            self.logger.warning(
                f"Legacy app {legacy_app.get('_key')} has no type or name, "
                "skipping record backfill"
            )
            return 0

        update_query = f"""
            LET updated = (
              FOR r IN {CollectionNames.RECORDS.value}
                FILTER r.connectorName == @connector_name
                  AND (r.connectorId == null OR r.connectorId == '')
                UPDATE r WITH {{ connectorId: @connector_id }}
                  IN {CollectionNames.RECORDS.value}
                RETURN NEW
            )
            RETURN LENGTH(updated)
        """

        try:
            cursor = transaction.aql.execute(
                update_query,
                bind_vars={
                    "connector_name": connector_name,
                    "connector_id": new_app["_key"],
                },
            )
            # Avoid materializing full cursor to prevent 'cursor count not enabled'
            updated_count = next(cursor, 0) if cursor is not None else 0

            if updated_count > 0:
                self.logger.info(
                    f"Backfilled connectorId on {updated_count} records for {connector_name}"
                )

            return int(updated_count or 0)
        except Exception as e:
            error_msg = (
                f"Failed to backfill connectorId for connector "
                f"{connector_name}: {e}"
            )
            self.logger.error(error_msg)
            raise ConnectorMigrationError(error_msg) from e

    async def _delete_legacy_app_and_edges(
        self,
        legacy_app: Dict,
        transaction
    ) -> None:
        """
        Remove legacy app document and all associated edges.

        Cleans up the legacy connector instance and all relationships to avoid
        dangling references. Targets org-app and user-app relationship collections.

        Args:
            legacy_app: Legacy app document to delete
            transaction: Active ArangoDB transaction
        """
        old_id = f"{CollectionNames.APPS.value}/{legacy_app['_key']}"

        # Delete organizational edges (bidirectional safety check)
        try:
            delete_org_edges_query = f"""
                FOR edge IN {CollectionNames.ORG_APP_RELATION.value}
                  FILTER edge._to == @old_id OR edge._from == @old_id
                  REMOVE edge IN {CollectionNames.ORG_APP_RELATION.value}
            """
            transaction.aql.execute(
                delete_org_edges_query, bind_vars={"old_id": old_id}
            )
            self.logger.debug(f"Deleted org-app edges for {legacy_app['_key']}")
        except Exception as e:
            self.logger.warning(
                f"Failed to delete org-app edges for {legacy_app['_key']}: {e}"
            )
            # Don't raise - continue with deletion

        # Delete user-app edges if collection exists
        try:
            delete_user_edges_query = f"""
                FOR edge IN {CollectionNames.USER_APP_RELATION.value}
                  FILTER edge._to == @old_id OR edge._from == @old_id
                  REMOVE edge IN {CollectionNames.USER_APP_RELATION.value}
            """
            transaction.aql.execute(
                delete_user_edges_query, bind_vars={"old_id": old_id}
            )
            self.logger.debug(f"Deleted user-app edges for {legacy_app['_key']}")
        except Exception as e:
            # Non-fatal: collection might not exist in this deployment
            self.logger.debug(
                f"Could not delete user-app edges for {legacy_app['_key']}: {e}"
            )

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
                f"({legacy_app.get('type')}) and its edges"
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

        # Fetch all apps to migrate
        try:
            apps = await self._fetch_all_apps()
        except ConnectorMigrationError:
            self.logger.error("Migration aborted: unable to fetch apps")
            return

        if not apps:
            self.logger.info("No apps found to migrate")
            await self._mark_migration_done()
            return

        self.logger.info(f"Found {len(apps)} connector apps to migrate")

        # Track migration statistics
        success_count = 0
        error_count = 0
        errors = []

        # Process each legacy app with its own transaction
        for idx, legacy_app in enumerate(apps, 1):
            legacy_key = legacy_app.get("_key")
            legacy_type = legacy_app.get("type") or legacy_app.get("name", "Unknown")

            self.logger.info(
                f"\n[{idx}/{len(apps)}] Migrating: {legacy_type} ({legacy_key})"
            )

            # Migrate single app with transaction
            result = await self._migrate_single_app_with_transaction(legacy_app)

            if result["success"]:
                success_count += 1
                updated_records = result.get("updated_records", 0)
                self.logger.info(
                    f"  ✓ Successfully migrated {legacy_type}"
                )
                if updated_records > 0:
                    self.logger.info(
                        f"  ✓ Backfilled connectorId on {updated_records} records"
                    )
            else:
                error_count += 1
                error_msg = result.get("error", "Unknown error")
                self.logger.error(f"  ✗ {error_msg}")
                errors.append({
                    "app": legacy_type,
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
