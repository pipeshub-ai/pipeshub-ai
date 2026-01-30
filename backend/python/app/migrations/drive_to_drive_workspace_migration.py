"""
Drive to Drive Workspace Migration Service

This migration handles connector instances with type "Drive" and scope "team"
differently based on organization account type:

For individual organizations:
- Only updates scope from "team" to "personal"
- No connector type change
- No record updates

For non-individual organizations (enterprise/business):
- Updates connector type field to "DRIVE WORKSPACE"
- Finds all records with connectorName="DRIVE"
- Updates records' connectorName field to "DRIVE WORKSPACE"

Migration Steps:
1. Find all connectors with type="Drive" and scope="team"
2. Separate connectors by organization accountType
3. For individual orgs: Update scope to "personal" only
4. For non-individual orgs: Update type to "DRIVE WORKSPACE" and update records
5. Process in batches to handle large datasets efficiently
6. Use transactions for atomicity
"""

import asyncio
import traceback
from typing import Dict, List, Tuple

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames, Connectors, ConnectorScopes
from app.connectors.services.base_arango_service import BaseArangoService


class DriveToDriveWorkspaceMigrationError(Exception):
    """Base exception for drive to drive workspace migration errors."""
    pass


class DriveToDriveWorkspaceMigrationService:
    """
    Service for migrating Drive connectors to Drive Workspace.

    This service ensures that:
    - Connector instances with type "Drive" and scope "team" are updated to type "DRIVE WORKSPACE"
    - All records with connectorName "DRIVE" are updated to "DRIVE WORKSPACE"
    """

    # Batch size for processing connectors and records
    BATCH_SIZE = 500

    # Migration version identifier for idempotency
    MIGRATION_FLAG_KEY = "/migrations/drive_to_drive_workspace_v1"

    def __init__(
        self,
        arango_service: BaseArangoService,
        config_service: ConfigurationService,
        logger,
    ) -> None:
        """
        Initialize the drive to drive workspace migration service.

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

    async def _mark_migration_done(self, result: Dict) -> None:
        """
        Mark the migration as completed in the configuration store.

        This creates a persistent flag to ensure idempotency on subsequent runs.

        Args:
            result: Migration result dictionary with statistics
        """
        try:
            await self.config.set_config(
                self.MIGRATION_FLAG_KEY,
                {
                    "done": True,
                    "connectors_updated": result.get("connectors_updated", 0),
                    "records_updated": result.get("records_updated", 0),
                }
            )
            self.logger.info("Migration completion flag set successfully")
        except Exception as e:
            # Non-fatal: migration itself completed successfully
            self.logger.warning(
                f"Failed to set migration completion flag: {e}. "
                "Migration completed but may run again on next startup."
            )

    async def _find_connectors_to_migrate(self) -> Tuple[List[Dict], List[Dict]]:
        """
        Find all connectors that need to be migrated, separated by organization account type.

        Returns:
            tuple[List[Dict], List[Dict]]:
                - First list: connectors from individual orgs (only scope update needed)
                - Second list: connectors from non-individual orgs (full migration needed)
        """
        try:
            query = f"""
                FOR app IN {CollectionNames.APPS.value}
                    FILTER app.type == @old_type AND app.scope == @team_scope
                    LET org_edge = FIRST(
                        FOR edge IN {CollectionNames.ORG_APP_RELATION.value}
                            FILTER edge._to == app._id
                            RETURN edge
                    )
                    LET org = org_edge != null ? DOCUMENT(org_edge._from) : null
                    RETURN {{
                        app: app,
                        accountType: org != null ? org.accountType : null
                    }}
            """

            cursor = self.arango.db.aql.execute(
                query,
                bind_vars={
                    "old_type": "Drive",
                    "team_scope": ConnectorScopes.TEAM.value
                }
            )
            results = list(cursor)

            individual_connectors = []
            non_individual_connectors = []

            for result in results:
                app = result.get("app", {})
                account_type = result.get("accountType", "")

                if account_type == "individual":
                    individual_connectors.append(app)
                else:
                    non_individual_connectors.append(app)

            self.logger.info(
                f"Found {len(individual_connectors)} connector(s) from individual orgs (scope update only)"
            )
            self.logger.info(
                f"Found {len(non_individual_connectors)} connector(s) from non-individual orgs (full migration)"
            )
            return individual_connectors, non_individual_connectors

        except Exception as e:
            error_msg = f"Failed to find connectors to migrate: {str(e)}"
            self.logger.error(error_msg)
            raise DriveToDriveWorkspaceMigrationError(error_msg) from e

    async def _update_connectors_scope_batch(self, connectors: List[Dict], transaction) -> Dict:
        """
        Update a batch of connectors with the new scope (for individual orgs).

        Args:
            connectors: List of connector documents to update
            transaction: Active ArangoDB transaction

        Returns:
            Dict: Result with batch statistics
        """
        connectors_updated = 0
        failed_updates = []

        for connector in connectors:
            try:
                connector_key = connector.get("_key")
                if not connector_key:
                    continue

                # Update the connector scope to "personal"
                update_query = f"""
                    UPDATE {{ _key: @connector_key }} WITH {{ scope: @new_scope }}
                    IN {CollectionNames.APPS.value}
                    OPTIONS {{ keepNull: false, mergeObjects: true }}
                    RETURN NEW
                """

                cursor = transaction.aql.execute(
                    update_query,
                    bind_vars={
                        "connector_key": connector_key,
                        "new_scope": ConnectorScopes.PERSONAL.value
                    }
                )
                updated = list(cursor)

                if updated:
                    connectors_updated += 1

            except Exception as update_error:
                self.logger.error(
                    f"Failed to update connector scope {connector.get('_key', 'unknown')}: {str(update_error)}"
                )
                failed_updates.append({
                    "connector_key": connector.get("_key", "unknown"),
                    "error": str(update_error)
                })
                continue

        return {
            "connectors_updated": connectors_updated,
            "failed_updates": failed_updates
        }

    async def _update_connectors_batch(self, connectors: List[Dict], transaction) -> Dict:
        """
        Update a batch of connectors with the new type (for non-individual orgs).

        Args:
            connectors: List of connector documents to update
            transaction: Active ArangoDB transaction

        Returns:
            Dict: Result with batch statistics
        """
        connectors_updated = 0
        failed_updates = []

        for connector in connectors:
            try:
                connector_key = connector.get("_key")
                if not connector_key:
                    continue

                # Update the connector type to "Drive Workspace" (the registered connector name)
                # This matches the @ConnectorBuilder("Drive Workspace") decorator name
                update_query = f"""
                    UPDATE {{ _key: @connector_key }} WITH {{ type: @new_type }}
                    IN {CollectionNames.APPS.value}
                    OPTIONS {{ keepNull: false, mergeObjects: true }}
                    RETURN NEW
                """

                cursor = transaction.aql.execute(
                    update_query,
                    bind_vars={
                        "connector_key": connector_key,
                        "new_type": "Drive Workspace"  # Use registered name, not enum value
                    }
                )
                updated = list(cursor)

                if updated:
                    connectors_updated += 1

            except Exception as update_error:
                self.logger.error(
                    f"Failed to update connector {connector.get('_key', 'unknown')}: {str(update_error)}"
                )
                failed_updates.append({
                    "connector_key": connector.get("_key", "unknown"),
                    "error": str(update_error)
                })
                continue

        return {
            "connectors_updated": connectors_updated,
            "failed_updates": failed_updates
        }

    async def _find_records_to_migrate(self, connector_keys: List[str]) -> List[Dict]:
        """
        Find all records that need to be migrated for the given connectors.

        Args:
            connector_keys: List of connector _key values to find records for

        Returns:
            List[Dict]: List of record documents with connectorId matching the connector keys
                       and connectorName="DRIVE"
        """
        if not connector_keys:
            return []

        try:
            query = f"""
                FOR record IN {CollectionNames.RECORDS.value}
                    FILTER record.connectorId IN @connector_keys
                        AND record.connectorName == @old_connector_name
                    RETURN record
            """

            cursor = self.arango.db.aql.execute(
                query,
                bind_vars={
                    "connector_keys": connector_keys,
                    "old_connector_name": Connectors.GOOGLE_DRIVE.value
                }
            )
            records = list(cursor)

            self.logger.info(
                f"Found {len(records)} record(s) to migrate for {len(connector_keys)} connector(s)"
            )
            return records

        except Exception as e:
            error_msg = f"Failed to find records to migrate: {str(e)}"
            self.logger.error(error_msg)
            raise DriveToDriveWorkspaceMigrationError(error_msg) from e

    async def _update_records_batch(self, records: List[Dict], transaction) -> Dict:
        """
        Update a batch of records with the new connectorName.

        Args:
            records: List of record documents to update
            transaction: Active ArangoDB transaction

        Returns:
            Dict: Result with batch statistics
        """
        records_updated = 0
        failed_updates = []

        for record in records:
            try:
                record_key = record.get("_key")
                if not record_key:
                    continue

                # Update the record connectorName
                update_query = f"""
                    UPDATE {{ _key: @record_key }} WITH {{ connectorName: @new_connector_name }}
                    IN {CollectionNames.RECORDS.value}
                    OPTIONS {{ keepNull: false, mergeObjects: true }}
                    RETURN NEW
                """

                cursor = transaction.aql.execute(
                    update_query,
                    bind_vars={
                        "record_key": record_key,
                        "new_connector_name": Connectors.GOOGLE_DRIVE_WORKSPACE.value
                    }
                )
                updated = list(cursor)

                if updated:
                    records_updated += 1

            except Exception as update_error:
                self.logger.error(
                    f"Failed to update record {record.get('_key', 'unknown')}: {str(update_error)}"
                )
                failed_updates.append({
                    "record_key": record.get("_key", "unknown"),
                    "error": str(update_error)
                })
                continue

        return {
            "records_updated": records_updated,
            "failed_updates": failed_updates
        }

    async def migrate_all(self) -> Dict:
        """
        Execute the complete migration for all connectors and records.

        This method is idempotent - it will skip execution if the
        completion flag is already set.

        Returns:
            Dict: Result with success status and statistics
        """
        # Check if migration was already completed
        if await self._is_migration_already_done():
            self.logger.info(
                "Drive to Drive Workspace migration already completed - skipping"
            )
            return {
                "success": True,
                "connectors_updated": 0,
                "records_updated": 0,
                "skipped": True,
                "message": "Migration already completed"
            }

        try:
            self.logger.info("=" * 70)
            self.logger.info("Starting Drive to Drive Workspace Migration")
            self.logger.info("=" * 70)

            # Step 1: Find all connectors to migrate, separated by org account type
            individual_connectors, non_individual_connectors = await self._find_connectors_to_migrate()

            total_connectors_updated = 0
            total_records_updated = 0
            all_failed_connector_updates = []
            all_failed_record_updates = []

            # Step 2a: Update individual org connectors (scope only, no type change, no records)
            if individual_connectors:
                self.logger.info(f"Processing {len(individual_connectors)} connector(s) from individual orgs (scope update only)")

                for i in range(0, len(individual_connectors), self.BATCH_SIZE):
                    batch_num = (i // self.BATCH_SIZE) + 1
                    batch_connectors = individual_connectors[i:i + self.BATCH_SIZE]

                    self.logger.info(
                        f"Processing individual org connector batch {batch_num} ({len(batch_connectors)} connector(s))..."
                    )

                    transaction = None
                    try:
                        # Start transaction
                        transaction = self.arango.db.begin_transaction(
                            write=[CollectionNames.APPS.value]
                        )

                        # Update batch (scope only)
                        batch_result = await self._update_connectors_scope_batch(
                            batch_connectors, transaction
                        )

                        # Commit transaction
                        await asyncio.to_thread(lambda: transaction.commit_transaction())
                        self.logger.info(
                            f"✅ Individual org connector batch {batch_num} committed: "
                            f"{batch_result['connectors_updated']} connector(s) updated (scope only)"
                        )

                        total_connectors_updated += batch_result["connectors_updated"]
                        all_failed_connector_updates.extend(batch_result.get("failed_updates", []))

                    except Exception as batch_error:
                        # Rollback transaction on error
                        if transaction:
                            try:
                                await asyncio.to_thread(lambda: transaction.abort_transaction())
                                self.logger.warning(f"Individual org connector batch {batch_num} rolled back due to error")
                            except Exception as rollback_error:
                                self.logger.error(f"Individual org connector batch {batch_num} rollback failed: {rollback_error}")

                        error_msg = f"Individual org connector batch {batch_num} migration failed: {str(batch_error)}"
                        self.logger.error(error_msg)
                        # Continue with next batch instead of failing entire migration
                        continue
            else:
                self.logger.info("✅ No individual org connectors need updating")

            # Step 2b: Update non-individual org connectors (type change + records)
            if non_individual_connectors:
                self.logger.info(f"Processing {len(non_individual_connectors)} connector(s) from non-individual orgs (full migration)")

                for i in range(0, len(non_individual_connectors), self.BATCH_SIZE):
                    batch_num = (i // self.BATCH_SIZE) + 1
                    batch_connectors = non_individual_connectors[i:i + self.BATCH_SIZE]

                    self.logger.info(
                        f"Processing non-individual org connector batch {batch_num} ({len(batch_connectors)} connector(s))..."
                    )

                    transaction = None
                    try:
                        # Start transaction
                        transaction = self.arango.db.begin_transaction(
                            write=[CollectionNames.APPS.value]
                        )

                        # Update batch (type change)
                        batch_result = await self._update_connectors_batch(
                            batch_connectors, transaction
                        )

                        # Commit transaction
                        await asyncio.to_thread(lambda: transaction.commit_transaction())
                        self.logger.info(
                            f"✅ Non-individual org connector batch {batch_num} committed: "
                            f"{batch_result['connectors_updated']} connector(s) updated (type change)"
                        )

                        total_connectors_updated += batch_result["connectors_updated"]
                        all_failed_connector_updates.extend(batch_result.get("failed_updates", []))

                    except Exception as batch_error:
                        # Rollback transaction on error
                        if transaction:
                            try:
                                await asyncio.to_thread(lambda: transaction.abort_transaction())
                                self.logger.warning(f"Non-individual org connector batch {batch_num} rolled back due to error")
                            except Exception as rollback_error:
                                self.logger.error(f"Non-individual org connector batch {batch_num} rollback failed: {rollback_error}")

                        error_msg = f"Non-individual org connector batch {batch_num} migration failed: {str(batch_error)}"
                        self.logger.error(error_msg)
                        # Continue with next batch instead of failing entire migration
                        continue
            else:
                self.logger.info("✅ No non-individual org connectors need updating")

            # Step 3: Update records if any non-individual connectors were found
            if non_individual_connectors:
                # Extract connector keys to find associated records
                connector_keys = [
                    connector.get("_key")
                    for connector in non_individual_connectors
                    if connector.get("_key")
                ]

                records_to_update = await self._find_records_to_migrate(connector_keys)

                if records_to_update:
                    self.logger.info(f"Found {len(records_to_update)} record(s) to update")

                    for i in range(0, len(records_to_update), self.BATCH_SIZE):
                        batch_num = (i // self.BATCH_SIZE) + 1
                        batch_records = records_to_update[i:i + self.BATCH_SIZE]

                        self.logger.info(
                            f"Processing record batch {batch_num} ({len(batch_records)} record(s))..."
                        )

                        transaction = None
                        try:
                            # Start transaction
                            transaction = self.arango.db.begin_transaction(
                                write=[CollectionNames.RECORDS.value]
                            )

                            # Update batch
                            batch_result = await self._update_records_batch(
                                batch_records, transaction
                            )

                            # Commit transaction
                            await asyncio.to_thread(lambda: transaction.commit_transaction())
                            self.logger.info(
                                f"✅ Record batch {batch_num} committed: "
                                f"{batch_result['records_updated']} record(s) updated"
                            )

                            total_records_updated += batch_result["records_updated"]
                            all_failed_record_updates.extend(batch_result.get("failed_updates", []))

                        except Exception as batch_error:
                            # Rollback transaction on error
                            if transaction:
                                try:
                                    await asyncio.to_thread(lambda: transaction.abort_transaction())
                                    self.logger.warning(f"Record batch {batch_num} rolled back due to error")
                                except Exception as rollback_error:
                                    self.logger.error(f"Record batch {batch_num} rollback failed: {rollback_error}")

                            error_msg = f"Record batch {batch_num} migration failed: {str(batch_error)}"
                            self.logger.error(error_msg)
                            # Continue with next batch instead of failing entire migration
                            continue
                else:
                    self.logger.info("✅ No records need updating")

            # Log summary
            self.logger.info("=" * 70)
            self.logger.info("Drive to Drive Workspace Migration Summary")
            self.logger.info("=" * 70)
            self.logger.info(f"Individual org connectors found: {len(individual_connectors)}")
            self.logger.info(f"Non-individual org connectors found: {len(non_individual_connectors)}")
            self.logger.info(f"Total connectors updated successfully: {total_connectors_updated}")
            self.logger.info(f"Records updated successfully: {total_records_updated}")

            if all_failed_connector_updates:
                self.logger.warning(f"Failed connector updates: {len(all_failed_connector_updates)}")
                for failed in all_failed_connector_updates[:10]:  # Show first 10 failures
                    self.logger.warning(
                        f"  - Connector {failed['connector_key']}: {failed['error']}"
                    )

            if all_failed_record_updates:
                self.logger.warning(f"Failed record updates: {len(all_failed_record_updates)}")
                for failed in all_failed_record_updates[:10]:  # Show first 10 failures
                    self.logger.warning(
                        f"  - Record {failed['record_key']}: {failed['error']}"
                    )

            self.logger.info("=" * 70)

            result = {
                "success": True,
                "connectors_updated": total_connectors_updated,
                "records_updated": total_records_updated,
                "failed_connector_updates": len(all_failed_connector_updates),
                "failed_record_updates": len(all_failed_record_updates),
                "failed_connector_updates_details": all_failed_connector_updates if all_failed_connector_updates else None,
                "failed_record_updates_details": all_failed_record_updates if all_failed_record_updates else None,
            }

            # Mark migration as complete (even if some updates failed)
            # Successfully updated items remain updated due to transactions
            await self._mark_migration_done(result)

            return result

        except Exception as e:
            error_msg = f"Drive to Drive Workspace migration failed: {str(e)}"
            self.logger.error(error_msg)
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return {
                "success": False,
                "connectors_updated": 0,
                "records_updated": 0,
                "error": str(e),
            }


async def run_drive_to_drive_workspace_migration(
    arango_service: BaseArangoService,
    config_service: ConfigurationService,
    logger,
) -> Dict:
    """
    Convenience function to execute the drive to drive workspace migration.

    Args:
        arango_service: Service for ArangoDB operations
        config_service: Service for configuration management
        logger: Logger for tracking migration progress

    Returns:
        Dict: Result with success status and statistics

    Example:
        >>> result = await run_drive_to_drive_workspace_migration(arango_service, config_service, logger)
    """
    service = DriveToDriveWorkspaceMigrationService(arango_service, config_service, logger)
    return await service.migrate_all()
