"""
Files to Records MD5/Size Migration Service

This migration copies md5Checksum and sizeInBytes from the files collection
to the corresponding records collection. Files and records are linked via
IS_OF_TYPE edges (from RECORDS to FILES).

Migration Steps:
1. Find all files that have md5Checksum and/or sizeInBytes values
2. For each file, find the corresponding record via IS_OF_TYPE edge
3. Copy values from files to records ONLY when records are missing these values:
   - Copy md5Checksum if record doesn't have it (null/empty) but file has it
   - Copy sizeInBytes if record doesn't have it (null) but file has it
4. Process in batches to handle large datasets efficiently
5. Use transactions for atomicity
"""

import asyncio
import traceback
from typing import Dict, List

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.connectors.services.base_arango_service import BaseArangoService


class FilesToRecordsMigrationError(Exception):
    """Base exception for files to records migration errors."""
    pass


class FilesToRecordsMigrationService:
    """
    Service for migrating md5Checksum and sizeInBytes from files to records.

    This service ensures that records have md5Checksum and sizeInBytes values
    copied from their corresponding file records when the records are missing
    these values.
    """

    # Batch size for processing records
    BATCH_SIZE = 500

    # Migration version identifier for idempotency
    MIGRATION_FLAG_KEY = "/migrations/files_to_records_md5_size_v1"

    def __init__(
        self,
        arango_service: BaseArangoService,
        config_service: ConfigurationService,
        logger,
    ) -> None:
        """
        Initialize the files to records migration service.

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
                    "records_updated": result.get("records_updated", 0),
                    "md5_copied": result.get("md5_copied", 0),
                    "size_copied": result.get("size_copied", 0),
                }
            )
            self.logger.info("Migration completion flag set successfully")
        except Exception as e:
            # Non-fatal: migration itself completed successfully
            self.logger.warning(
                f"Failed to set migration completion flag: {e}. "
                "Migration completed but may run again on next startup."
            )

    async def _find_files_with_records_needing_update(self) -> List[Dict]:
        """
        Find all files that have md5Checksum and/or sizeInBytes and their
        corresponding records that need updating.

        Returns:
            List[Dict]: List of dictionaries with file, record, and update info
        """
        try:
            query = f"""
                FOR file IN {CollectionNames.FILES.value}
                    FILTER file.md5Checksum != null OR file.sizeInBytes != null

                    // Find corresponding record via IS_OF_TYPE edge
                    LET record = FIRST(
                        FOR edge IN {CollectionNames.IS_OF_TYPE.value}
                            FILTER edge._to == file._id
                            FOR record IN {CollectionNames.RECORDS.value}
                                FILTER edge._from == record._id
                                RETURN record
                    )

                    // Only include if record exists and needs updating
                    FILTER record != null AND (
                        (file.md5Checksum != null AND (record.md5Checksum == null OR record.md5Checksum == ""))
                        OR (file.sizeInBytes != null AND record.sizeInBytes == null)
                    )

                    RETURN {{
                        file: file,
                        record: record,
                        needs_md5: file.md5Checksum != null AND (record.md5Checksum == null OR record.md5Checksum == ""),
                        needs_size: file.sizeInBytes != null AND record.sizeInBytes == null
                    }}
            """

            cursor = self.arango.db.aql.execute(query)
            pairs = list(cursor)

            self.logger.info(
                f"Found {len(pairs)} file-record pair(s) that need updating"
            )
            return pairs

        except Exception as e:
            error_msg = f"Failed to find files with records needing update: {str(e)}"
            self.logger.error(error_msg)
            raise FilesToRecordsMigrationError(error_msg) from e

    async def _update_records_batch(self, pairs: List[Dict], transaction) -> Dict:
        """
        Update a batch of records with md5Checksum and sizeInBytes from files.

        Args:
            pairs: List of file-record pairs to update
            transaction: Active ArangoDB transaction

        Returns:
            Dict: Result with batch statistics
        """
        records_updated = 0
        md5_copied = 0
        size_copied = 0
        failed_updates = []

        for pair in pairs:
            try:
                file = pair.get("file")
                record = pair.get("record")
                needs_md5 = pair.get("needs_md5", False)
                needs_size = pair.get("needs_size", False)

                if not file or not record:
                    continue

                record_key = record.get("_key")
                if not record_key:
                    continue

                # Build update object with only fields that need updating
                update_data = {}
                if needs_md5 and file.get("md5Checksum"):
                    update_data["md5Checksum"] = file.get("md5Checksum")
                    md5_copied += 1

                if needs_size and file.get("sizeInBytes") is not None:
                    update_data["sizeInBytes"] = file.get("sizeInBytes")
                    size_copied += 1

                if not update_data:
                    continue

                # Update the record
                update_query = f"""
                    UPDATE {{ _key: @record_key }} WITH @update_data
                    IN {CollectionNames.RECORDS.value}
                    OPTIONS {{ keepNull: false, mergeObjects: true }}
                    RETURN NEW
                """

                cursor = transaction.aql.execute(
                    update_query,
                    bind_vars={
                        "record_key": record_key,
                        "update_data": update_data
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
            "md5_copied": md5_copied,
            "size_copied": size_copied,
            "failed_updates": failed_updates
        }

    async def migrate_all(self) -> Dict:
        """
        Execute the complete migration for all files and records.

        This method is idempotent - it will skip execution if the
        completion flag is already set.

        Returns:
            Dict: Result with success status and statistics
        """
        # Check if migration was already completed
        if await self._is_migration_already_done():
            self.logger.info(
                "Files to Records MD5/Size migration already completed - skipping"
            )
            return {
                "success": True,
                "records_updated": 0,
                "md5_copied": 0,
                "size_copied": 0,
                "skipped": True,
                "message": "Migration already completed"
            }

        try:
            self.logger.info("=" * 70)
            self.logger.info("Starting Files to Records MD5/Size Migration")
            self.logger.info("=" * 70)

            # Step 1: Find all files with records that need updating
            pairs_to_update = await self._find_files_with_records_needing_update()

            if not pairs_to_update:
                self.logger.info("✅ No records need updating")
                result = {
                    "success": True,
                    "records_updated": 0,
                    "md5_copied": 0,
                    "size_copied": 0,
                }
                # Mark as complete even if no records needed updating
                await self._mark_migration_done(result)
                return result

            self.logger.info(f"Found {len(pairs_to_update)} file-record pair(s) to update")

            # Step 2: Process pairs in batches
            total_records_updated = 0
            total_md5_copied = 0
            total_size_copied = 0
            all_failed_updates = []

            for i in range(0, len(pairs_to_update), self.BATCH_SIZE):
                batch_num = (i // self.BATCH_SIZE) + 1
                batch_pairs = pairs_to_update[i:i + self.BATCH_SIZE]

                self.logger.info(
                    f"Processing batch {batch_num} ({len(batch_pairs)} pair(s))..."
                )

                transaction = None
                try:
                    # Start transaction
                    transaction = self.arango.db.begin_transaction(
                        write=[CollectionNames.RECORDS.value]
                    )

                    # Update batch
                    batch_result = await self._update_records_batch(
                        batch_pairs, transaction
                    )

                    # Commit transaction
                    await asyncio.to_thread(lambda: transaction.commit_transaction())
                    self.logger.info(
                        f"✅ Batch {batch_num} committed: "
                        f"{batch_result['records_updated']} record(s) updated"
                    )

                    total_records_updated += batch_result["records_updated"]
                    total_md5_copied += batch_result["md5_copied"]
                    total_size_copied += batch_result["size_copied"]
                    all_failed_updates.extend(batch_result.get("failed_updates", []))

                except Exception as batch_error:
                    # Rollback transaction on error
                    if transaction:
                        try:
                            await asyncio.to_thread(lambda: transaction.abort_transaction())
                            self.logger.warning(f"Batch {batch_num} rolled back due to error")
                        except Exception as rollback_error:
                            self.logger.error(f"Batch {batch_num} rollback failed: {rollback_error}")

                    error_msg = f"Batch {batch_num} migration failed: {str(batch_error)}"
                    self.logger.error(error_msg)
                    # Continue with next batch instead of failing entire migration
                    continue

            # Log summary
            self.logger.info("=" * 70)
            self.logger.info("Files to Records MD5/Size Migration Summary")
            self.logger.info("=" * 70)
            self.logger.info(f"Total pairs found: {len(pairs_to_update)}")
            self.logger.info(f"Records updated successfully: {total_records_updated}")
            self.logger.info(f"MD5 checksums copied: {total_md5_copied}")
            self.logger.info(f"Size in bytes copied: {total_size_copied}")

            if all_failed_updates:
                self.logger.warning(f"Failed updates: {len(all_failed_updates)}")
                for failed in all_failed_updates[:10]:  # Show first 10 failures
                    self.logger.warning(
                        f"  - Record {failed['record_key']}: {failed['error']}"
                    )

            self.logger.info("=" * 70)

            result = {
                "success": True,
                "records_updated": total_records_updated,
                "md5_copied": total_md5_copied,
                "size_copied": total_size_copied,
                "failed_updates": len(all_failed_updates),
                "failed_updates_details": all_failed_updates if all_failed_updates else None,
            }

            # Mark migration as complete (even if some records failed)
            # Successfully updated records remain updated due to transactions
            await self._mark_migration_done(result)

            return result

        except Exception as e:
            error_msg = f"Files to Records MD5/Size migration failed: {str(e)}"
            self.logger.error(error_msg)
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return {
                "success": False,
                "records_updated": 0,
                "md5_copied": 0,
                "size_copied": 0,
                "error": str(e),
            }


async def run_files_to_records_migration(
    arango_service: BaseArangoService,
    config_service: ConfigurationService,
    logger,
) -> Dict:
    """
    Convenience function to execute the files to records migration.

    Args:
        arango_service: Service for ArangoDB operations
        config_service: Service for configuration management
        logger: Logger for tracking migration progress

    Returns:
        Dict: Result with success status and statistics

    Example:
        >>> result = await run_files_to_records_migration(arango_service, config_service, logger)
    """
    service = FilesToRecordsMigrationService(arango_service, config_service, logger)
    return await service.migrate_all()

