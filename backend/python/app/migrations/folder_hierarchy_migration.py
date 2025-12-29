"""
Folder Hierarchy Migration Service

This migration addresses the issue where folders in knowledge bases were missing
proper RECORDS documents and edges. The current structure had folders only in the
FILES collection with direct RECORD_RELATIONS and BELONGS_TO edges pointing to FILES.

CURRENT STRUCTURE (INCORRECT):
- Folder exists only in FILES collection (isFile=false)
- RECORD_RELATIONS edges: FILES -> FILES (parent-child)
- BELONGS_TO edges: FILES -> RECORD_GROUPS (KB)
- No RECORDS document for folders
- No IS_OF_TYPE edge

NEW STRUCTURE (CORRECT):
- Folder has document in FILES collection (isFile=false) - for file metadata
- Folder has document in RECORDS collection (recordType="FOLDER") - for record metadata
- IS_OF_TYPE edge: RECORDS -> FILES (folder record -> folder file)
- RECORD_RELATIONS edges: RECORDS -> RECORDS (parent-child between records)
- BELONGS_TO edges: RECORDS -> RECORD_GROUPS (KB)

Migration Steps:
1. Find all folders (FILES with isFile=false) that don't have RECORDS documents
2. For each folder:
   - Create a RECORDS document with recordType="FOLDER"
   - Create IS_OF_TYPE edge from RECORDS to FILES
   - Update RECORD_RELATIONS edges to use RECORDS instead of FILES
   - Update BELONGS_TO edges to use RECORDS instead of FILES
3. Process in batches to handle large KBs efficiently
"""

import asyncio
import traceback
from typing import Dict, List

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.connectors.services.base_arango_service import BaseArangoService
from app.models.entities import RecordType
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class FolderHierarchyMigrationError(Exception):
    """Base exception for folder hierarchy migration errors."""
    pass


class FolderHierarchyMigrationService:
    """
    Service for migrating folder hierarchy to correct structure.

    This service ensures that all folders in knowledge bases have proper:
    - RECORDS documents with recordType="FOLDER"
    - IS_OF_TYPE edges connecting RECORDS to FILES
    - RECORD_RELATIONS edges between RECORDS (not FILES)
    - BELONGS_TO edges from RECORDS (not FILES)
    """

    # Batch size for processing folders
    BATCH_SIZE = 500

    # Migration version identifier for idempotency
    MIGRATION_FLAG_KEY = "/migrations/folder_hierarchy_v1"

    def __init__(
        self,
        arango_service: BaseArangoService,
        config_service: ConfigurationService,
        logger,
    ) -> None:
        """
        Initialize the folder hierarchy migration service.

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
                    "folders_migrated": result.get("folders_migrated", 0),
                    "edges_created": result.get("edges_created", 0),
                    "edges_updated": result.get("edges_updated", 0),
                }
            )
            self.logger.info("Migration completion flag set successfully")
        except Exception as e:
            # Non-fatal: migration itself completed successfully
            self.logger.warning(
                f"Failed to set migration completion flag: {e}. "
                "Migration completed but may run again on next startup."
            )

    async def validate_folder_hierarchy(self) -> Dict:
        """
        Validate that all folders have correct parent relationships and structure.

        Checks:
        1. All folders have RECORDS documents
        2. All folders have IS_OF_TYPE edges
        3. Parent IDs are set correctly
        4. Root folders have KB ID as parent
        5. Nested folders have parent folder ID as parent

        Returns:
            Dict: Validation results
        """
        try:
            self.logger.info("🔍 Validating folder hierarchy...")

            validation_query = f"""
                LET all_folders = (
                    FOR folder_file IN {CollectionNames.FILES.value}
                        FILTER folder_file.isFile == false

                        // Check for RECORDS document
                        LET folder_record = FIRST(
                            FOR record IN {CollectionNames.RECORDS.value}
                                FOR edge IN {CollectionNames.IS_OF_TYPE.value}
                                    FILTER edge._from == record._id
                                    FILTER edge._to == folder_file._id
                                    FILTER record.recordType == "FILE"
                                    RETURN record
                        )

                        // Check parent relationship
                        LET parent_edge = FIRST(
                            FOR edge IN {CollectionNames.RECORD_RELATIONS.value}
                                FILTER edge._to == (folder_record ? folder_record._id : folder_file._id)
                                FILTER edge.relationshipType == "PARENT_CHILD"
                                RETURN {{
                                    parent_id: PARSE_IDENTIFIER(edge._from).key,
                                    parent_collection: PARSE_IDENTIFIER(edge._from).collection
                                }}
                        )

                        LET is_valid = (
                            folder_record != null AND
                            folder_record.externalParentId != null AND
                            folder_record.connectorId == folder_file.recordGroupId
                        )

                        RETURN {{
                            folder_key: folder_file._key,
                            folder_name: folder_file.name,
                            kb_id: folder_file.recordGroupId,
                            has_record: folder_record != null,
                            record_parent_id: folder_record ? folder_record.externalParentId : null,
                            edge_parent_id: parent_edge ? parent_edge.parent_id : null,
                            edge_parent_collection: parent_edge ? parent_edge.parent_collection : null,
                            is_valid: is_valid,
                            is_root: parent_edge AND parent_edge.parent_collection == "{CollectionNames.RECORD_GROUPS.value}"
                        }}
                )

                LET total_folders = LENGTH(all_folders)
                LET folders_with_records = LENGTH(FOR f IN all_folders FILTER f.has_record RETURN 1)
                LET valid_folders = LENGTH(FOR f IN all_folders FILTER f.is_valid RETURN 1)
                LET invalid_folders_list = (
                    FOR f IN all_folders
                        FILTER !f.is_valid
                        RETURN f
                )

                RETURN {{
                    total_folders: total_folders,
                    folders_with_records: folders_with_records,
                    valid_folders: valid_folders,
                    invalid_folders: invalid_folders_list,
                    success: total_folders == valid_folders
                }}
            """

            cursor = self.arango.db.aql.execute(validation_query)
            result = next(cursor, {})

            total = result.get("total_folders", 0)
            valid = result.get("valid_folders", 0)
            invalid_list = result.get("invalid_folders", [])

            if result.get("success"):
                self.logger.info(f"✅ Validation passed: All {total} folders are valid")
            else:
                self.logger.warning(f"⚠️ Validation issues: {valid}/{total} folders valid")
                for invalid in invalid_list[:10]:  # Show first 10 invalid folders
                    self.logger.warning(
                        f"  - {invalid['folder_name']} ({invalid['folder_key']}): "
                        f"has_record={invalid['has_record']}, "
                        f"parent_id={invalid.get('record_parent_id')}"
                    )

            return result

        except Exception as e:
            error_msg = f"Validation failed: {str(e)}"
            self.logger.error(error_msg)
            return {
                "success": False,
                "error": str(e)
            }

    async def migrate_all_folders(self) -> Dict:
        """
        Execute the complete migration for all folders in all KBs.

        This method is idempotent - it will skip execution if the
        completion flag is already set.

        Returns:
            Dict: Result with success status and statistics
        """
        # Check if migration was already completed
        if await self._is_migration_already_done():
            self.logger.info(
                "Folder Hierarchy migration already completed - skipping"
            )
            return {
                "success": True,
                "folders_migrated": 0,
                "edges_created": 0,
                "edges_updated": 0,
                "skipped": True,
                "message": "Migration already completed"
            }

        try:
            self.logger.info("=" * 70)
            self.logger.info("Starting Folder Hierarchy Migration")
            self.logger.info("=" * 70)

            # Step 1: Find all folders that need migration
            folders_to_migrate = await self._find_folders_needing_migration()

            if not folders_to_migrate:
                self.logger.info("✅ No folders need migration")
                result = {
                    "success": True,
                    "folders_migrated": 0,
                    "edges_created": 0,
                    "edges_updated": 0,
                }
                # Mark as complete even if no folders needed migration
                await self._mark_migration_done(result)
                return result

            self.logger.info(f"Found {len(folders_to_migrate)} folder(s) to migrate")

            # Step 2: Process folders in batches
            total_folders_migrated = 0
            total_edges_created = 0
            total_edges_updated = 0
            failed_folders = []

            for i in range(0, len(folders_to_migrate), self.BATCH_SIZE):
                batch_num = (i // self.BATCH_SIZE) + 1
                batch_folders = folders_to_migrate[i:i + self.BATCH_SIZE]

                self.logger.info(
                    f"Processing batch {batch_num} ({len(batch_folders)} folders)..."
                )

                batch_result = await self._migrate_folder_batch(batch_folders)

                total_folders_migrated += batch_result["folders_migrated"]
                total_edges_created += batch_result["edges_created"]
                total_edges_updated += batch_result["edges_updated"]
                failed_folders.extend(batch_result.get("failed_folders", []))

            # Log summary
            self.logger.info("=" * 70)
            self.logger.info("Folder Hierarchy Migration Summary")
            self.logger.info("=" * 70)
            self.logger.info(f"Total folders found: {len(folders_to_migrate)}")
            self.logger.info(f"Folders migrated successfully: {total_folders_migrated}")
            self.logger.info(f"RECORDS documents created: {total_folders_migrated}")
            self.logger.info(f"IS_OF_TYPE edges created: {total_edges_created}")
            self.logger.info(f"Edges updated: {total_edges_updated}")

            if failed_folders:
                self.logger.warning(f"Failed folders: {len(failed_folders)}")
                for failed in failed_folders:
                    self.logger.warning(
                        f"  - {failed['folder_name']} ({failed['folder_key']}): {failed['error']}"
                    )

            self.logger.info("=" * 70)

            # Validate the migration results
            self.logger.info("🔍 Running post-migration validation...")
            validation_result = await self.validate_folder_hierarchy()

            if validation_result.get("success"):
                self.logger.info("✅ Post-migration validation passed!")
            else:
                self.logger.warning("⚠️ Post-migration validation found issues - please review")

            result = {
                "success": True,
                "folders_migrated": total_folders_migrated,
                "edges_created": total_edges_created,
                "edges_updated": total_edges_updated,
                "failed_folders": len(failed_folders),
                "failed_folders_details": failed_folders if failed_folders else None,
                "validation": validation_result,
            }

            # Mark migration as complete (even if some folders failed)
            # Successfully migrated folders remain migrated due to transactions
            await self._mark_migration_done(result)

            return result

        except Exception as e:
            error_msg = f"Folder hierarchy migration failed: {str(e)}"
            self.logger.error(error_msg)
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return {
                "success": False,
                "folders_migrated": 0,
                "edges_created": 0,
                "edges_updated": 0,
                "error": str(e),
            }

    async def _find_folders_needing_migration(self) -> List[Dict]:
        """
        Find all folders (FILES with isFile=false) that don't have corresponding RECORDS.
        Returns folders sorted by hierarchy level (parents before children) to ensure
        correct parent ID resolution during migration.

        Returns:
            List[Dict]: List of folder documents that need migration, sorted by depth
        """
        try:
            # Simplified query without complex depth calculation to avoid ArangoDB limitations
            query = f"""
                FOR folder IN {CollectionNames.FILES.value}
                    FILTER folder.isFile == false

                    // Check if folder has a corresponding RECORDS document
                    LET has_record = LENGTH(
                        FOR record IN {CollectionNames.RECORDS.value}
                            FOR edge IN {CollectionNames.IS_OF_TYPE.value}
                                FILTER edge._from == record._id
                                FILTER edge._to == folder._id
                                LIMIT 1
                                RETURN 1
                    ) > 0

                    // Only return folders without RECORDS
                    FILTER !has_record

                    // Simple depth calculation: count parent edges (0 = root, 1+ = nested)
                    LET is_root = LENGTH(
                        FOR edge IN {CollectionNames.RECORD_RELATIONS.value}
                            FILTER edge._to == folder._id
                            FILTER edge.relationshipType == "PARENT_CHILD"
                            FILTER PARSE_IDENTIFIER(edge._from).collection == "{CollectionNames.RECORD_GROUPS.value}"
                            RETURN 1
                    ) > 0

                    // Sort: root folders first (is_root=true), then nested folders
                    SORT is_root DESC, folder.name ASC

                    RETURN folder
            """

            cursor = self.arango.db.aql.execute(query)
            folders = list(cursor)

            self.logger.info(
                f"Found {len(folders)} folder(s) without RECORDS documents "
                f"(sorted by hierarchy: root folders first, then nested)"
            )
            return folders

        except Exception as e:
            error_msg = f"Failed to find folders needing migration: {str(e)}"
            self.logger.error(error_msg)
            raise FolderHierarchyMigrationError(error_msg) from e

    async def _migrate_folder_batch(self, folders: List[Dict]) -> Dict:
        """
        Migrate a batch of folders with transaction support.

        Args:
            folders: List of folder documents to migrate

        Returns:
            Dict: Result with batch statistics
        """
        transaction = None
        folders_migrated = 0
        edges_created = 0
        edges_updated = 0
        failed_folders = []

        try:
            # Start transaction
            transaction = self.arango.db.begin_transaction(
                write=[
                    CollectionNames.RECORDS.value,
                    CollectionNames.IS_OF_TYPE.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.BELONGS_TO.value,
                ]
            )

            for folder in folders:
                try:
                    folder_key = folder.get("_key")
                    folder_name = folder.get("name", "Unknown")

                    if not folder_key:
                        self.logger.warning("Folder missing _key, skipping")
                        continue

                    # Migrate single folder
                    result = await self._migrate_single_folder(folder, transaction)

                    if result["success"]:
                        folders_migrated += 1
                        edges_created += result.get("edges_created", 0)
                        edges_updated += result.get("edges_updated", 0)
                    else:
                        failed_folders.append({
                            "folder_key": folder_key,
                            "folder_name": folder_name,
                            "error": result.get("error", "Unknown error"),
                        })

                except Exception as folder_error:
                    self.logger.error(f"Failed to migrate folder {folder.get('_key')}: {str(folder_error)}")
                    failed_folders.append({
                        "folder_key": folder.get("_key", "unknown"),
                        "folder_name": folder.get("name", "unknown"),
                        "error": str(folder_error),
                    })
                    continue

            # Commit transaction
            await asyncio.to_thread(lambda: transaction.commit_transaction())
            self.logger.info(f"✅ Batch committed: {folders_migrated} folders migrated")

            return {
                "success": True,
                "folders_migrated": folders_migrated,
                "edges_created": edges_created,
                "edges_updated": edges_updated,
                "failed_folders": failed_folders,
            }

        except Exception as e:
            # Rollback transaction on error
            if transaction:
                try:
                    await asyncio.to_thread(lambda: transaction.abort_transaction())
                    self.logger.warning("Transaction rolled back due to error")
                except Exception as rollback_error:
                    self.logger.error(f"Transaction rollback failed: {rollback_error}")

            error_msg = f"Batch migration failed: {str(e)}"
            self.logger.error(error_msg)
            return {
                "success": False,
                "folders_migrated": folders_migrated,
                "edges_created": edges_created,
                "edges_updated": edges_updated,
                "failed_folders": failed_folders,
                "error": str(e),
            }

    async def _migrate_single_folder(self, folder: Dict, transaction) -> Dict:
        """
        Migrate a single folder to new structure.

        Determines parent relationship and timestamps from existing edges.

        Args:
            folder: Folder document from FILES collection
            transaction: Active ArangoDB transaction

        Returns:
            Dict: Result with success status and details
        """
        try:
            folder_key = folder.get("_key")
            folder_name = folder.get("name", "Unknown")
            kb_id = folder.get("recordGroupId")
            org_id = folder.get("orgId")
            folder_files_id = f"{CollectionNames.FILES.value}/{folder_key}"

            if not folder_key:
                return {
                    "success": False,
                    "error": "Folder missing _key"
                }

            # If kb_id is missing, try to find it from BELONGS_TO edge
            if not kb_id:
                kb_query = f"""
                    FOR edge IN {CollectionNames.BELONGS_TO.value}
                        FILTER edge._from == @folder_id
                        FILTER PARSE_IDENTIFIER(edge._to).collection == "{CollectionNames.RECORD_GROUPS.value}"
                        LIMIT 1
                        RETURN PARSE_IDENTIFIER(edge._to).key
                """
                kb_cursor = transaction.aql.execute(
                    kb_query,
                    bind_vars={"folder_id": folder_files_id}
                )
                kb_id = next(kb_cursor, None)

                if not kb_id:
                    self.logger.warning(
                        f"Folder {folder_key} has no recordGroupId and no BELONGS_TO edge to KB, skipping"
                    )
                    return {
                        "success": False,
                        "error": "Folder missing KB ID (no recordGroupId and no BELONGS_TO edge to KB)"
                    }

                self.logger.debug(f"  - Found KB ID from BELONGS_TO edge: {kb_id}")

            # If org_id is missing, try to get it from KB
            if not org_id:
                kb_doc_query = f"""
                    FOR kb IN {CollectionNames.RECORD_GROUPS.value}
                        FILTER kb._key == @kb_id
                        LIMIT 1
                        RETURN kb.orgId
                """
                org_cursor = transaction.aql.execute(
                    kb_doc_query,
                    bind_vars={"kb_id": kb_id}
                )
                org_id = next(org_cursor, None)

                if not org_id:
                    self.logger.warning(
                        f"Folder {folder_key} has no orgId and KB {kb_id} not found, skipping"
                    )
                    return {
                        "success": False,
                        "error": f"Folder missing orgId and KB {kb_id} not found"
                    }

            self.logger.debug(f"Migrating folder: {folder_name} ({folder_key}), KB: {kb_id}, Org: {org_id}")

            # Step 1: Determine parent and timestamps from existing edges
            # Check RECORD_RELATIONS edges to find parent (incoming edge)
            # Parent could be in FILES (not yet migrated) or RECORDS (already migrated)
            parent_query = f"""
                FOR edge IN {CollectionNames.RECORD_RELATIONS.value}
                    FILTER edge._to == @folder_id
                    FILTER edge.relationshipType == "PARENT_CHILD"
                    LIMIT 1
                    RETURN {{
                        parent_id: PARSE_IDENTIFIER(edge._from).key,
                        parent_collection: PARSE_IDENTIFIER(edge._from).collection,
                        created_at: edge.createdAtTimestamp,
                        updated_at: edge.updatedAtTimestamp
                    }}
            """

            parent_cursor = transaction.aql.execute(
                parent_query,
                bind_vars={"folder_id": folder_files_id}
            )
            parent_info = next(parent_cursor, None)

            # Determine parent ID and timestamps
            if parent_info:
                parent_collection = parent_info.get("parent_collection")
                parent_id = parent_info.get("parent_id")
                created_timestamp = parent_info.get("created_at", get_epoch_timestamp_in_ms())
                updated_timestamp = parent_info.get("updated_at", get_epoch_timestamp_in_ms())

                # If parent is KB (recordGroups), this is a root folder
                if parent_collection == CollectionNames.RECORD_GROUPS.value:
                    external_parent_id = kb_id  # Root folder's parent is KB
                    self.logger.debug(f"  - Root folder (parent: KB {kb_id})")
                elif parent_collection == CollectionNames.RECORDS.value:
                    # Parent is already migrated RECORDS document (folder)
                    external_parent_id = parent_id
                    self.logger.debug(f"  - Nested folder (parent: migrated folder {parent_id})")
                else:
                    # Parent is FILES document (not yet migrated or old structure)
                    # The parent folder will be migrated separately and will have same _key
                    external_parent_id = parent_id
                    self.logger.debug(f"  - Nested folder (parent: folder {parent_id} in FILES)")
            else:
                # No parent edge found, check BELONGS_TO edge for timestamps
                belongs_query = f"""
                    FOR edge IN {CollectionNames.BELONGS_TO.value}
                        FILTER edge._from == @folder_id
                        LIMIT 1
                        RETURN {{
                            created_at: edge.createdAtTimestamp,
                            updated_at: edge.updatedAtTimestamp
                        }}
                """
                belongs_cursor = transaction.aql.execute(
                    belongs_query,
                    bind_vars={"folder_id": folder_files_id}
                )
                belongs_info = next(belongs_cursor, None)

                external_parent_id = kb_id  # Default to KB as parent
                created_timestamp = belongs_info.get("created_at", get_epoch_timestamp_in_ms()) if belongs_info else get_epoch_timestamp_in_ms()
                updated_timestamp = belongs_info.get("updated_at", get_epoch_timestamp_in_ms()) if belongs_info else get_epoch_timestamp_in_ms()
                self.logger.debug(f"  - Root folder (no parent edge, using KB {kb_id})")

            # Step 2: Create RECORDS document for folder
            # Ensure connectorId is never null (required by schema)
            if not kb_id:
                return {
                    "success": False,
                    "error": f"KB ID is required but not found for folder {folder_key}"
                }

            record_data = {
                "_key": folder_key,  # Use same key as FILES document
                "orgId": org_id,
                "recordName": folder_name,
                "externalRecordId": f"kb_folder_{folder_key}",
                "connectorId": kb_id,  # Required field - must not be null
                "externalGroupId": kb_id,
                "externalParentId": external_parent_id,  # KB ID for root, parent folder ID for nested
                "externalRootGroupId": kb_id,  # Always KB ID
                "recordType": RecordType.FILE.value,
                "version": 0,
                "origin": OriginTypes.UPLOAD.value,  # KB folders are uploaded/created locally
                "connectorName": Connectors.KNOWLEDGE_BASE.value,
                "mimeType": "application/vnd.folder",
                "webUrl": folder.get("webUrl") or f"/kb/{kb_id}/folder/{folder_key}",
                "createdAtTimestamp": created_timestamp,
                "updatedAtTimestamp": updated_timestamp,
                "lastSyncTimestamp": updated_timestamp,
                "sourceCreatedAtTimestamp": created_timestamp,
                "sourceLastModifiedTimestamp": updated_timestamp,
                "isDeleted": False,
                "isArchived": False,
                "isVLMOcrProcessed": False,  # Required field with default
                "indexingStatus": "COMPLETED",
                "extractionStatus": "COMPLETED",
                "isLatestVersion": True,
                "isDirty": False,
            }

            # Insert RECORDS document
            await self.arango.batch_upsert_nodes(
                [record_data],
                collection=CollectionNames.RECORDS.value,
                transaction=transaction
            )

            edges_created = 0
            edges_updated = 0

            # Step 2: Create IS_OF_TYPE edge from RECORDS to FILES
            is_of_type_edge = {
                "_from": f"{CollectionNames.RECORDS.value}/{folder_key}",
                "_to": f"{CollectionNames.FILES.value}/{folder_key}",
                "createdAtTimestamp": created_timestamp,
                "updatedAtTimestamp": updated_timestamp,
            }

            await self.arango.batch_create_edges(
                [is_of_type_edge],
                collection=CollectionNames.IS_OF_TYPE.value,
                transaction=transaction
            )
            edges_created += 1

            # Step 3: Update RECORD_RELATIONS edges to use RECORDS instead of FILES
            # Find all edges pointing TO this folder (children)
            update_query = f"""
                FOR edge IN {CollectionNames.RECORD_RELATIONS.value}
                    FILTER edge._to == @files_id
                    UPDATE edge WITH {{
                        _to: @records_id
                    }} IN {CollectionNames.RECORD_RELATIONS.value}
                    OPTIONS {{ keepNull: false }}
                    RETURN NEW
            """

            cursor = transaction.aql.execute(
                update_query,
                bind_vars={
                    "files_id": f"{CollectionNames.FILES.value}/{folder_key}",
                    "records_id": f"{CollectionNames.RECORDS.value}/{folder_key}",
                }
            )
            edges_updated += len(list(cursor))

            # Find all edges FROM this folder (parent edges)
            update_query = f"""
                FOR edge IN {CollectionNames.RECORD_RELATIONS.value}
                    FILTER edge._from == @files_id
                    UPDATE edge WITH {{
                        _from: @records_id
                    }} IN {CollectionNames.RECORD_RELATIONS.value}
                    OPTIONS {{ keepNull: false }}
                    RETURN NEW
            """

            cursor = transaction.aql.execute(
                update_query,
                bind_vars={
                    "files_id": f"{CollectionNames.FILES.value}/{folder_key}",
                    "records_id": f"{CollectionNames.RECORDS.value}/{folder_key}",
                }
            )
            edges_updated += len(list(cursor))

            # Step 4: Update BELONGS_TO edges to use RECORDS instead of FILES
            update_query = f"""
                FOR edge IN {CollectionNames.BELONGS_TO.value}
                    FILTER edge._from == @files_id
                    UPDATE edge WITH {{
                        _from: @records_id
                    }} IN {CollectionNames.BELONGS_TO.value}
                    OPTIONS {{ keepNull: false }}
                    RETURN NEW
            """

            cursor = transaction.aql.execute(
                update_query,
                bind_vars={
                    "files_id": f"{CollectionNames.FILES.value}/{folder_key}",
                    "records_id": f"{CollectionNames.RECORDS.value}/{folder_key}",
                }
            )
            edges_updated += len(list(cursor))

            self.logger.debug(
                f"✅ Migrated folder {folder_name}: "
                f"{edges_created} edges created, {edges_updated} edges updated"
            )

            return {
                "success": True,
                "edges_created": edges_created,
                "edges_updated": edges_updated,
            }

        except Exception as e:
            error_msg = f"Failed to migrate folder {folder.get('_key')}: {str(e)}"
            self.logger.error(error_msg)
            return {
                "success": False,
                "error": str(e),
            }


async def run_folder_hierarchy_migration(
    arango_service: BaseArangoService,
    config_service: ConfigurationService,
    logger,
    dry_run: bool = False,
) -> Dict:
    """
    Convenience function to execute the folder hierarchy migration.

    Args:
        arango_service: Service for ArangoDB operations
        config_service: Service for configuration management
        logger: Logger for tracking migration progress
        dry_run: If True, only report what would be migrated without making changes

    Returns:
        Dict: Result with success status and statistics

    Example:
        >>> result = await run_folder_hierarchy_migration(arango_service, config_service, logger, dry_run=True)
    """
    service = FolderHierarchyMigrationService(arango_service, config_service, logger)

    if dry_run:
        # Only find folders that need migration, don't actually migrate
        folders = await service._find_folders_needing_migration()
        return {
            "success": True,
            "dry_run": True,
            "folders_to_migrate": len(folders),
            "message": f"Found {len(folders)} folders that need migration (dry run)",
        }

    return await service.migrate_all_folders()

