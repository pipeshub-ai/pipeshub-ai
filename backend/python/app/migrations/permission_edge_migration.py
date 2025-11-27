"""
Unified Permission Edge Migration Service
Migrates permission edges from deprecated collections to 'permission' collection.
Supports both direction reversal and same-direction migrations.
"""

import uuid
from logging import Logger
from typing import Any, Dict, List

from app.config.constants.arangodb import CollectionNames, LegacyCollectionNames
from app.connectors.services.base_arango_service import BaseArangoService
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class UnifiedPermissionEdgeMigrationService:
    """
    Unified service to handle migration of permission edges from deprecated collections to new collection.

    Migration process:
    1. Validates all preconditions
    2. Reads edges from source collection (deprecated)
    3. Creates edges in target collection (new) with optional direction reversal
    4. Deletes edges from source collection

    Args:
        arango_service: BaseArangoService instance
        logger: Logger instance
        source_collection: Name of the deprecated source collection (e.g., "permissions", "permissionsToKB")
        target_collection: Name of the target collection (default: "permission")
        reverse_direction: If True, reverses edge direction; if False, keeps same direction
    """

    def __init__(
        self,
        arango_service: BaseArangoService,
        logger: Logger,
        source_collection: str,
        target_collection: str = CollectionNames.PERMISSION.value,
        reverse_direction: bool = False
    ) -> None:
        self.arango_service = arango_service
        self.logger = logger
        self.db = arango_service.db
        self.source_collection = source_collection
        self.target_collection = target_collection
        self.reverse_direction = reverse_direction

    async def run_migration(
        self,
        dry_run: bool = False,
        batch_size: int = 1000
    ) -> Dict[str, Any]:
        """
        Main migration method that orchestrates the entire migration process.

        Args:
            dry_run: If True, only reports what would be done without making changes
            batch_size: Number of edges to process in each batch

        Returns:
            Dict with statistics about the operation
        """
        try:
            direction_msg = "REVERSED" if self.reverse_direction else "SAME"
            self.logger.info("🚀 Starting Permission Edge Migration (dry_run=%s)", dry_run)
            self.logger.info(f"   Source: {self.source_collection} (deprecated)")
            self.logger.info(f"   Target: {self.target_collection} (new)")
            self.logger.info(f"   Direction: {direction_msg}")

            # Step 1: Validate all migration preconditions
            validation_result = await self._validate_migration_preconditions()
            if not validation_result["success"]:
                return validation_result

            # Check if migration is needed
            if not validation_result.get("migration_needed", True):
                self.logger.info("✅ No migration needed - no edges found in source collection")
                return {
                    "success": True,
                    "message": "No migration needed",
                    "total_edges": 0,
                    "migrated_edges": 0,
                    "deleted_edges": 0,
                    "errors": 0
                }

            # Step 2: Fetch all edges from source collection
            fetch_result = await self._fetch_source_edges()
            if not fetch_result["success"]:
                return fetch_result

            total_edges = fetch_result["total_edges"]
            all_edges = fetch_result["edges"]

            if dry_run:
                return await self._dry_run_analysis(all_edges, total_edges)

            # Step 3: Execute migration in batches
            migration_result = await self._execute_migration(
                all_edges,
                total_edges,
                batch_size
            )

            if not migration_result["success"]:
                return migration_result

            # Step 4: Verify migration

            self.logger.info("🎉 Permission Edge Migration completed successfully")
            return {
                "success": True,
                "message": "Migration completed successfully",
                "total_edges": total_edges,
                "migrated_edges": migration_result["migrated_edges"],
                "deleted_edges": migration_result["deleted_edges"],
                "errors": migration_result["errors"],
            }

        except Exception as e:
            self.logger.error(f"❌ Permission Edge Migration failed: {str(e)}", exc_info=True)
            return {
                "success": False,
                "message": f"Migration failed: {str(e)}",
                "total_edges": 0,
                "migrated_edges": 0,
                "deleted_edges": 0,
                "errors": 0
            }

    async def _validate_migration_preconditions(self) -> Dict[str, Any]:
        """
        Validate all preconditions for migration:
        1. Database connection
        2. Source collection exists
        3. Target collection exists (or can be created)
        4. Source collection has edges to migrate
        """
        try:
            self.logger.info("🔍 Validating migration preconditions...")

            # 1. Check database connection
            if not self.db:
                return {
                    "success": False,
                    "message": "Database not connected",
                    "migration_needed": False
                }

            # 2. Check if source collection exists
            if not self.db.has_collection(self.source_collection):
                self.logger.info(f"✅ Source collection '{self.source_collection}' does not exist - no migration needed")
                return {
                    "success": True,
                    "message": f"Source collection '{self.source_collection}' not found",
                    "migration_needed": False,
                    "edge_count": 0
                }

            # 3. Check if target collection exists (should exist, but check anyway)
            if not self.db.has_collection(self.target_collection):
                self.logger.warning(f"⚠️ Target collection '{self.target_collection}' does not exist")
                self.logger.info("   Target collection will need to be created or already exists in graph definition")

            # 4. Count edges in source collection
            count_query = f"""
            RETURN LENGTH(
                FOR edge IN {self.source_collection}
                    RETURN edge
            )
            """
            cursor = self.db.aql.execute(count_query)
            edge_count = next(cursor, 0)

            if edge_count == 0:
                self.logger.info(f"✅ No edges found in source collection '{self.source_collection}' - no migration needed")
                return {
                    "success": True,
                    "message": "No edges found in source collection",
                    "migration_needed": False,
                    "edge_count": 0
                }

            self.logger.info("✅ Preconditions validated:")
            self.logger.info(f"   Source collection '{self.source_collection}': {edge_count} edges")
            self.logger.info(f"   Target collection '{self.target_collection}': {'exists' if self.db.has_collection(self.target_collection) else 'will be used'}")

            return {
                "success": True,
                "message": "All preconditions validated",
                "migration_needed": True,
                "edge_count": edge_count,
                "source_collection": self.source_collection,
                "target_collection": self.target_collection
            }

        except Exception as e:
            self.logger.error(f"❌ Precondition validation failed: {str(e)}")
            return {
                "success": False,
                "message": f"Precondition validation failed: {str(e)}",
                "migration_needed": False
            }

    async def _fetch_source_edges(self) -> Dict[str, Any]:
        """Fetch all edges from the source (deprecated) collection"""
        try:
            self.logger.info(f"📊 Fetching edges from source collection '{self.source_collection}'...")

            fetch_query = f"""
            FOR edge IN {self.source_collection}
                RETURN {{
                    _key: edge._key,
                    _from: edge._from,
                    _to: edge._to,
                    role: edge.role,
                    type: edge.type,
                    externalPermissionId: edge.externalPermissionId,
                    createdAtTimestamp: edge.createdAtTimestamp,
                    updatedAtTimestamp: edge.updatedAtTimestamp,
                    lastUpdatedTimestampAtSource: edge.lastUpdatedTimestampAtSource
                }}
            """

            cursor = self.db.aql.execute(fetch_query)
            all_edges = list(cursor)
            total_edges = len(all_edges)

            self.logger.info(f"✅ Fetched {total_edges} edges from source collection")

            return {
                "success": True,
                "total_edges": total_edges,
                "edges": all_edges
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch source edges: {str(e)}")
            return {
                "success": False,
                "message": f"Failed to fetch source edges: {str(e)}",
                "total_edges": 0,
                "edges": []
            }

    async def _dry_run_analysis(
        self,
        all_edges: List[Dict],
        total_edges: int
    ) -> Dict[str, Any]:
        """Perform dry run analysis without making changes"""
        try:
            self.logger.info("🔍 Running dry run analysis...")

            # Show sample of edges that would be migrated
            sample_size = min(10, total_edges)
            sample_edges = all_edges[:sample_size]

            direction_label = "reversed" if self.reverse_direction else "same"
            self.logger.info(f"📋 Sample edges (first {sample_size}):")
            for i, edge in enumerate(sample_edges, 1):
                if self.reverse_direction:
                    self.logger.info(f"  {i}. Source ({self.source_collection}): {edge['_from']} → {edge['_to']}")
                    self.logger.info(f"     Target ({self.target_collection}): {edge['_to']} → {edge['_from']} ({direction_label})")
                else:
                    self.logger.info(f"  {i}. Source ({self.source_collection}): {edge['_from']} → {edge['_to']}")
                    self.logger.info(f"     Target ({self.target_collection}): {edge['_from']} → {edge['_to']} ({direction_label})")

            return {
                "success": True,
                "dry_run": True,
                "total_edges": total_edges,
                "message": f"Dry run completed. Would migrate {total_edges} edges from '{self.source_collection}' to '{self.target_collection}' with {direction_label} direction."
            }

        except Exception as e:
            self.logger.error(f"❌ Dry run analysis failed: {str(e)}")
            return {
                "success": False,
                "message": f"Dry run analysis failed: {str(e)}"
            }

    async def _execute_migration(
        self,
        all_edges: List[Dict],
        total_edges: int,
        batch_size: int
    ) -> Dict[str, Any]:
        """Execute the actual migration in batches"""
        try:
            self.logger.info(f"🔄 Processing {total_edges} edges in batches of {batch_size}...")

            migrated_count = 0
            deleted_count = 0
            error_count = 0
            total_batches = (total_edges + batch_size - 1) // batch_size

            for i in range(0, total_edges, batch_size):
                batch = all_edges[i:i + batch_size]
                batch_num = (i // batch_size) + 1

                self.logger.info(f"Processing batch {batch_num}/{total_batches} ({len(batch)} edges)...")

                # Process batch: create in target, delete from source
                batch_result = await self._process_batch(batch, batch_num)

                migrated_count += batch_result["migrated"]
                deleted_count += batch_result["deleted"]
                error_count += batch_result["errors"]

                # Progress update
                progress = (i + len(batch)) / total_edges * 100
                self.logger.info(f"📈 Progress: {progress:.1f}% ({i + len(batch)}/{total_edges} edges)")

            self.logger.info("=" * 60)
            self.logger.info("✅ Migration batch processing completed!")
            self.logger.info(f"   Total edges processed: {total_edges}")
            self.logger.info(f"   Successfully migrated: {migrated_count}")
            self.logger.info(f"   Successfully deleted: {deleted_count}")
            self.logger.info(f"   Errors: {error_count}")
            self.logger.info("=" * 60)

            return {
                "success": True,
                "migrated_edges": migrated_count,
                "deleted_edges": deleted_count,
                "errors": error_count,
                "total_processed": total_edges
            }

        except Exception as e:
            self.logger.error(f"❌ Migration execution failed: {str(e)}", exc_info=True)
            return {
                "success": False,
                "message": f"Migration execution failed: {str(e)}",
                "migrated_edges": 0,
                "deleted_edges": 0,
                "errors": 0
            }

    async def _process_batch(
        self,
        batch: List[Dict],
        batch_num: int
    ) -> Dict[str, int]:
        """
        Process a single batch of edges:
        1. Create edges in target collection (with optional direction reversal)
        2. Delete edges from source collection
        """
        migrated = 0
        deleted = 0
        errors = 0

        # Prepare edges for target collection
        new_edges = []
        edge_keys_to_delete = []

        for old_edge in batch:
            try:
                # Determine _from and _to based on reverse_direction flag
                if self.reverse_direction:
                    # Reverse the direction: swap _from and _to
                    new_from = old_edge["_to"]
                    new_to = old_edge["_from"]
                else:
                    # Keep the same direction
                    new_from = old_edge["_from"]
                    new_to = old_edge["_to"]

                new_edge = {
                    "_key": str(uuid.uuid4()),  # Generate new key for target collection
                    "_from": new_from,
                    "_to": new_to,
                    "role": old_edge.get("role", "READER"),
                    "type": old_edge.get("type", "USER"),
                    "externalPermissionId": old_edge.get("externalPermissionId"),
                    "createdAtTimestamp": old_edge.get("createdAtTimestamp", get_epoch_timestamp_in_ms()),
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                    "lastUpdatedTimestampAtSource": old_edge.get("lastUpdatedTimestampAtSource")
                }

                new_edges.append(new_edge)
                edge_keys_to_delete.append(old_edge["_key"])

            except Exception as e:
                self.logger.error(f"❌ Error preparing edge {old_edge.get('_key')}: {str(e)}")
                errors += 1
                continue

        # Step 1: Create edges in target collection using base_arango_service method
        try:
            if new_edges:
                result = await self.arango_service.batch_create_edges(
                    new_edges,
                    collection=self.target_collection
                )
                if result:
                    migrated += len(new_edges)
                    direction_msg = "reversed" if self.reverse_direction else "same"
                    self.logger.debug(f"✅ Created {len(new_edges)} edges in '{self.target_collection}' with {direction_msg} direction (batch {batch_num})")
                else:
                    self.logger.error(f"❌ Failed to create edges in target collection (batch {batch_num})")
                    errors += len(new_edges)
                    # Don't delete from source if creation failed
                    edge_keys_to_delete = []
        except Exception as e:
            self.logger.error(f"❌ Error creating edges in target collection (batch {batch_num}): {str(e)}")
            errors += len(new_edges)
            # Don't delete from source if creation failed
            edge_keys_to_delete = []

        # Step 2: Delete edges from source collection using base_arango_service method
        try:
            if edge_keys_to_delete:
                result = await self.arango_service.delete_nodes(
                    keys=edge_keys_to_delete,
                    collection=self.source_collection
                )
                if result:
                    deleted += len(edge_keys_to_delete)
                    self.logger.debug(f"✅ Deleted {len(edge_keys_to_delete)} edges from '{self.source_collection}' (batch {batch_num})")
                else:
                    self.logger.warning(f"⚠️ Some edges may not have been deleted from source (batch {batch_num})")
                    errors += len(edge_keys_to_delete)
        except Exception as e:
            self.logger.error(f"❌ Error deleting edges from source collection (batch {batch_num}): {str(e)}")
            errors += len(edge_keys_to_delete)

        return {
            "migrated": migrated,
            "deleted": deleted,
            "errors": errors
        }


# ========== Convenience Functions for Specific Migrations ==========

async def run_permissions_edge_migration(
    arango_service: BaseArangoService,
    logger: Logger,
    dry_run: bool = False,
    batch_size: int = 1000
) -> Dict[str, Any]:
    """
    Convenience function to run the 'permissions' collection migration with REVERSED direction.

    Migrates from 'permissions' to 'permission' collection:
    - Old: Record → User/Group/Org
    - New: User/Group/Org → Record

    Args:
        arango_service: BaseArangoService instance
        logger: Logger instance
        dry_run: If True, only reports what would be done
        batch_size: Number of edges to process in each batch

    Returns:
        Dict with migration results
    """
    try:
        migration_service = UnifiedPermissionEdgeMigrationService(
            arango_service=arango_service,
            logger=logger,
            source_collection=LegacyCollectionNames.PERMISSIONS.value,
            target_collection=CollectionNames.PERMISSION.value,
            reverse_direction=True  # Reverse direction for permissions collection
        )
        result = await migration_service.run_migration(dry_run=dry_run, batch_size=batch_size)

        if result.get("success"):
            if dry_run:
                logger.info(f"✅ Migration dry run completed: {result.get('message')}")
            else:
                logger.info(
                    f"✅ Migration completed: {result.get('migrated_edges', 0)} edges migrated, "
                    f"{result.get('deleted_edges', 0)} edges deleted, "
                    f"{result.get('errors', 0)} errors"
                )
        else:
            logger.error(f"❌ Migration failed: {result.get('message')}")

        return result

    except Exception as e:
        logger.error(f"❌ Migration error: {str(e)}")
        return {
            "success": False,
            "message": f"Migration error: {str(e)}",
            "migrated_edges": 0,
            "deleted_edges": 0,
            "errors": 0
        }


async def run_permissions_to_kb_migration(
    arango_service: BaseArangoService,
    logger: Logger,
    dry_run: bool = False,
    batch_size: int = 1000
) -> Dict[str, Any]:
    """
    Convenience function to run the 'permissionsToKB' collection migration with SAME direction.

    Migrates from 'permissionsToKB' to 'permission' collection:
    - Old: User/Team → RecordGroup
    - New: User/Team → RecordGroup (same direction)

    Args:
        arango_service: BaseArangoService instance
        logger: Logger instance
        dry_run: If True, only reports what would be done
        batch_size: Number of edges to process in each batch

    Returns:
        Dict with migration results
    """
    try:
        migration_service = UnifiedPermissionEdgeMigrationService(
            arango_service=arango_service,
            logger=logger,
            source_collection=LegacyCollectionNames.PERMISSIONS_TO_KB.value,
            target_collection=CollectionNames.PERMISSION.value,
            reverse_direction=False  # Keep same direction for permissionsToKB collection
        )
        result = await migration_service.run_migration(dry_run=dry_run, batch_size=batch_size)

        if result.get("success"):
            if dry_run:
                logger.info(f"✅ Migration dry run completed: {result.get('message')}")
            else:
                logger.info(
                    f"✅ Migration completed: {result.get('migrated_edges', 0)} edges migrated, "
                    f"{result.get('deleted_edges', 0)} edges deleted, "
                    f"{result.get('errors', 0)} errors"
                )
        else:
            logger.error(f"❌ Migration failed: {result.get('message')}")

        return result

    except Exception as e:
        logger.error(f"❌ Migration error: {str(e)}")
        return {
            "success": False,
            "message": f"Migration error: {str(e)}",
            "migrated_edges": 0,
            "deleted_edges": 0,
            "errors": 0
        }

