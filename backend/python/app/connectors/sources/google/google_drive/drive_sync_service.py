"""Base and specialized sync services for Google Drive synchronization"""

# pylint: disable=E1101, W0718, W0719
import asyncio
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Dict, List, Optional

from arango.database import TransactionDatabase

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    EventTypes,
    OriginTypes,
    ProgressStatus,
    RecordRelations,
)
from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.connectors.services.base_arango_service import (
    BaseArangoService as ArangoService,
)
from app.connectors.services.kafka_service import KafkaService
from app.connectors.sources.google.admin.google_admin_service import GoogleAdminService
from app.connectors.sources.google.google_drive.drive_user_service import (
    DriveUserService,
)
from app.connectors.sources.google.google_drive.file_processor import process_drive_file
from app.connectors.utils.drive_worker import DriveWorker
from app.utils.time_conversion import get_epoch_timestamp_in_ms, parse_timestamp


class DriveSyncProgress:
    """Class to track sync progress"""

    def __init__(self) -> None:
        self.total_files = 0
        self.processed_files = 0
        self.percentage = 0
        self.status = "initializing"
        self.lastUpdatedTimestampAtSource = get_epoch_timestamp_in_ms()


class BaseDriveSyncService(ABC):
    """Abstract base class for sync services"""

    def __init__(
        self,
        logger,
        config_service: ConfigurationService,
        arango_service: ArangoService,
        change_handler,
        kafka_service: KafkaService,
        celery_app,
    ) -> None:
        self.logger = logger
        self.config_service = config_service
        self.arango_service = arango_service
        self.change_handler = change_handler
        self.kafka_service = kafka_service
        self.celery_app = celery_app

        # Common state
        self.drive_workers = {}
        self._current_batch = None
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._stop_requested = False

        # Locks
        self._sync_lock = asyncio.Lock()
        self._transition_lock = asyncio.Lock()
        self._worker_lock = asyncio.Lock()
        # Cross-thread batch lock to serialize executor runs
        self._batch_lock = threading.Lock()

        # Configuration
        self._sync_task = None
        self.batch_size = 100
        self.CHUNK_SIZE = 1000

    @abstractmethod
    async def connect_services(self, org_id: str) -> bool:
        """Connect to required services"""
        pass

    @abstractmethod
    async def initialize(self, org_id) -> bool:
        """Initialize sync service"""
        pass

    @abstractmethod
    async def perform_initial_sync(
        self, org_id, action: str = "start", resume_hierarchy: Dict = None
    ) -> bool:
        """Perform initial sync"""
        pass

    async def initialize_workers(self, user_service: DriveUserService) -> bool | None:
        """Initialize workers for root and shared drives"""
        async with self._worker_lock:
            try:
                self.logger.info("🔄 Initializing drive workers...")

                # Clear existing workers
                self.drive_workers.clear()

                # Initialize root drive worker
                self.logger.info("🏠 Setting up root drive worker...")
                self.drive_workers["root"] = DriveWorker(
                    "root", user_service, self.arango_service
                )
                self.logger.info("✅ Root drive worker initialized")

                # Initialize shared drive workers
                self.logger.info("🌐 Fetching shared drives...")
                drives = await user_service.list_shared_drives()
                if drives:
                    self.logger.info(f"📦 Found {len(drives)} shared drives")
                    for drive in drives:
                        drive_id = drive["id"]
                        drive_name = drive.get("name", "Unknown")
                        self.logger.info(
                            "🔄 Initializing worker for drive: %s (%s)",
                            drive_name,
                            drive_id,
                        )

                        self.drive_workers[drive_id] = DriveWorker(
                            drive_id, user_service, self.arango_service
                        )
                        self.logger.info(
                            "✅ Worker initialized for drive: %s", drive_name
                        )

                total_workers = len(self.drive_workers)
                self.logger.info(
                    """
                🎉 Worker initialization completed:
                - Total workers: %s
                - Root drive: %s
                - Shared drives: %s
                """,
                    total_workers,
                    "✅" if "root" in self.drive_workers else "❌",
                    (
                        total_workers - 1
                        if "root" in self.drive_workers
                        else total_workers
                    ),
                )
                return True

            except Exception as e:
                self.logger.error(f"❌ Failed to initialize workers: {str(e)}")
                return False

    async def start(self, org_id) -> bool:
        self.logger.info("🚀 Starting sync, Action: start")
        async with self._transition_lock:
            try:
                users = await self.arango_service.get_users(org_id=org_id)
                for user in users:
                    # Check current state using get_user_sync_state
                    sync_state = await self.arango_service.get_user_sync_state(
                        user["email"], Connectors.GOOGLE_DRIVE.value.lower()
                    )
                    current_state = (
                        sync_state.get("syncState") if sync_state else ProgressStatus.NOT_STARTED.value
                    )

                    if current_state == ProgressStatus.IN_PROGRESS.value:
                        self.logger.warning("💥 Sync service is already running")
                        return False

                    if current_state == ProgressStatus.PAUSED.value:
                        self.logger.warning("💥 Sync is paused, use resume to continue")
                        return False

                    # Cancel any existing task
                    if self._sync_task and not self._sync_task.done():
                        self._sync_task.cancel()
                        try:
                            await self._sync_task
                        except asyncio.CancelledError:
                            pass

                # Start fresh sync
                self._sync_task = asyncio.create_task(
                    self.perform_initial_sync(org_id, action="start")
                )

                self.logger.info("✅ Sync service started")
                return True

            except Exception as e:
                self.logger.error("❌ Failed to start sync service: %s", str(e))
                return False

    async def pause(self, org_id) -> bool:
        self.logger.info("⏸️ Pausing sync service")
        async with self._transition_lock:
            try:
                users = await self.arango_service.get_users(org_id=org_id)
                for user in users:
                    # Check current state using get_user_sync_state
                    sync_state = await self.arango_service.get_user_sync_state(
                        user["email"], Connectors.GOOGLE_DRIVE.value.lower()
                    )
                    current_state = (
                        sync_state.get("syncState") if sync_state else ProgressStatus.NOT_STARTED.value
                    )

                    if current_state != ProgressStatus.IN_PROGRESS.value:
                        self.logger.warning("💥 Sync service is not running")
                        continue

                    self._stop_requested = True

                    # Update user state
                    await self.arango_service.update_user_sync_state(
                        user["email"],
                        ProgressStatus.PAUSED.value,
                        service_type=Connectors.GOOGLE_DRIVE.value.lower(),
                    )

                    # Cancel current sync task
                    if self._sync_task and not self._sync_task.done():
                        self._sync_task.cancel()
                        try:
                            await self._sync_task
                        except asyncio.CancelledError:
                            pass

                self.logger.info("✅ Sync service paused")
                return True

            except Exception as e:
                self.logger.error("❌ Failed to pause sync service: %s", str(e))
                return False

    async def resume(self, org_id) -> bool:
        self.logger.info("🔄 Resuming sync service")
        async with self._transition_lock:
            try:
                users = await self.arango_service.get_users(org_id=org_id)
                for current_user in users:
                    # Check current state using get_user_sync_state
                    sync_state = await self.arango_service.get_user_sync_state(
                        current_user["email"], Connectors.GOOGLE_DRIVE.value.lower()
                    )
                    if not sync_state:
                        self.logger.warning("⚠️ No sync state found, starting fresh")
                        return await self.start(org_id)

                    current_state = sync_state.get("syncState")
                    if current_state == ProgressStatus.IN_PROGRESS.value:
                        self.logger.warning("💥 Sync service is already running")
                        return False

                    if current_state != ProgressStatus.PAUSED.value:
                        self.logger.warning("💥 Sync was not paused, use start instead")
                        return False

                    self._pause_event.set()
                    self._stop_requested = False

                # Start sync with resume state
                self._sync_task = asyncio.create_task(
                    self.perform_initial_sync(org_id, action="resume")
                )

                self.logger.info("✅ Sync service resumed")
                return True

            except Exception as e:
                self.logger.error("❌ Failed to resume sync service: %s", str(e))
                return False

    async def _should_stop(self, org_id) -> bool:
        """Check if operation should stop"""
        if self._stop_requested:
            users = await self.arango_service.get_users(org_id=org_id)
            for user in users:
                current_state = await self.arango_service.get_user_sync_state(
                    user["email"], Connectors.GOOGLE_DRIVE.value.lower()
                )
                if current_state:
                    current_state = current_state.get("syncState")
                    if current_state == ProgressStatus.IN_PROGRESS.value:
                        await self.arango_service.update_user_sync_state(
                            user["email"],
                            ProgressStatus.PAUSED.value,
                            service_type=Connectors.GOOGLE_DRIVE.value.lower(),
                        )
                        self.logger.info("✅ Drive sync state updated before stopping")
                        return True
            return False
        return False

    @abstractmethod
    async def resync_drive(self, org_id, user) -> bool:
        """Resync a user's Google Drive"""
        pass

    async def process_drive_data(self, drive_info, user) -> bool | None:
        """Process drive data including drive document, file record, record and permissions

        Args:
            drive_info (dict): Complete drive metadata from get_drive_info
            user (dict): Current user information

        Returns:
            bool: True if processing successful, False otherwise
        """
        try:
            self.logger.info(
                "🚀 Processing drive data for drive %s", drive_info["drive"]["id"]
            )

            # Check if drive already exists
            existing_drive = self.arango_service.db.aql.execute(
                f"FOR doc IN {CollectionNames.RECORDS.value} FILTER doc.externalRecordId == @drive_id RETURN doc",
                bind_vars={"drive_id": drive_info["drive"]["id"]},
            )
            existing = next(existing_drive, None)

            if existing:
                self.logger.debug(
                    "Drive %s already exists in ArangoDB", drive_info["drive"]["id"]
                )
                drive_info["drive"]["_key"] = existing["_key"]
                drive_info["record"]["_key"] = existing["_key"]

            # Save drive in drives collection
            await self.arango_service.batch_upsert_nodes(
                [drive_info["drive"]], collection=CollectionNames.DRIVES.value
            )

            # Save drive as record
            await self.arango_service.batch_upsert_nodes(
                [drive_info["record"]], collection=CollectionNames.RECORDS.value
            )

            await self.arango_service.batch_create_edges(
                [
                    {
                        "_from": f"{CollectionNames.RECORDS.value}/{drive_info['record']['_key']}",
                        "_to": f"{CollectionNames.DRIVES.value}/{drive_info['drive']['_key']}",
                    }
                ],
                collection=CollectionNames.IS_OF_TYPE.value,
            )

            # Get user ID for relationships
            user_id = await self.arango_service.get_entity_id_by_email(user["email"])
            self.logger.info("user_id: %s", user_id)

            # Create user-drive relationship
            user_drive_relation = {
                "_from": f"{CollectionNames.USERS.value}/{user_id}",
                "_to": f"{CollectionNames.DRIVES.value}/{drive_info['drive']['_key']}",
                "access_level": drive_info["drive"]["access_level"],
            }
            self.logger.info("user_drive_relation: %s", user_drive_relation)

            await self.arango_service.batch_create_edges(
                [user_drive_relation],
                collection=CollectionNames.USER_DRIVE_RELATION.value,
            )

            # Create user-record relationship with permissions
            user_record_relation = {
                "_to": f"{CollectionNames.USERS.value}/{user_id}",
                "_from": f"{CollectionNames.RECORDS.value}/{drive_info['record']['_key']}",
                "role": (
                    "WRITER"
                    if drive_info["drive"]["access_level"] == "writer"
                    else "READER"
                ),
                "type": "USER",
                "externalPermissionId": None,
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                "lastUpdatedTimestampAtSource": get_epoch_timestamp_in_ms(),
            }

            await self.arango_service.batch_create_edges(
                [user_record_relation], collection=CollectionNames.PERMISSIONS.value
            )

            self.logger.info(
                "✅ Successfully processed drive data for drive %s",
                drive_info["drive"]["id"],
            )
            return True

        except Exception as e:
            self.logger.error("❌ Failed to process drive data: %s", str(e))
            return False



    async def process_batch(self, file_metadata_list, org_id) -> bool | None:
        """Process a single batch with atomic operations in a separate thread"""
        # Respect stop signal before scheduling work
        if await self._should_stop(org_id):
            return False
        self.logger.info(
            "🚀 Starting batch processing with %d items", len(file_metadata_list)
        )

        # Run the entire batch processing in a separate thread under a cross-thread lock
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._run_batch_in_thread(file_metadata_list, org_id),
        )

    def _run_batch_in_thread(self, file_metadata_list, org_id) -> bool | None:
        # Serialize batch execution across threads
        with self._batch_lock:
            return asyncio.run(self._process_batch_sync(file_metadata_list, org_id))

    async def _process_batch_sync(self, file_metadata_list, org_id) -> bool | None:
        """
        Optimized batch processing with chunking for large datasets.
        Can handle 50,000+ files without memory issues.
        """
        batch_start_time = datetime.now(timezone.utc)
        total_files = len(file_metadata_list)

        try:
            self.logger.info(
                f"📦 Processing {total_files} files in chunks of {self.CHUNK_SIZE}"
            )

            # Process in chunks to prevent memory issues
            total_chunks = (total_files + self.CHUNK_SIZE - 1) // self.CHUNK_SIZE

            for chunk_idx in range(0, total_files, self.CHUNK_SIZE):
                chunk_end = min(chunk_idx + self.CHUNK_SIZE, total_files)
                chunk = file_metadata_list[chunk_idx:chunk_end]
                chunk_num = (chunk_idx // self.CHUNK_SIZE) + 1

                self.logger.info(
                    f"📊 Processing chunk {chunk_num}/{total_chunks} "
                    f"({len(chunk)} files)"
                )

                success = await self._process_chunk(chunk, org_id)
                if not success:
                    self.logger.error(
                        f"❌ Failed to process chunk {chunk_num}/{total_chunks}"
                    )
                    return False

            processing_time = datetime.now(timezone.utc) - batch_start_time
            self.logger.info(
                f"✅ Successfully processed {total_files} files in {processing_time}"
            )
            return True

        except Exception as e:
            self.logger.error(f"❌ Batch processing failed: {str(e)}")
            return False

    async def _process_chunk(self, file_metadata_list: List[dict], org_id: str) -> bool:
        """Process a single chunk of files with optimized queries"""

        try:
            # Step 1: Extract valid file IDs
            file_ids = [
                metadata.get("id")
                for metadata in file_metadata_list
                if metadata and metadata.get("id")
            ]

            if not file_ids:
                self.logger.warning("⚠️  No valid file IDs in chunk")
                return True

            # Step 2: Single bulk query to check ALL existing files at once
            existing_files_map = await self._get_existing_files_bulk(file_ids)
            self.logger.debug(
                f"Found {len(existing_files_map)} existing files out of {len(file_ids)}"
            )

            # Step 3: Prepare data structures for new files only
            files = []
            records = []
            is_of_type_records = []
            file_id_to_key = {}  # In-memory mapping to avoid DB queries
            new_file_metadata = []  # Track metadata for new files

            for metadata in file_metadata_list:
                if not metadata:
                    self.logger.warning("⚠️  No metadata found for file")
                    continue

                file_id = metadata.get("id")
                if not file_id:
                    self.logger.warning("⚠️  No file ID found")
                    continue

                # Skip existing files
                if file_id in existing_files_map:
                    self.logger.debug(f"⏭️  File {file_id} already exists, skipping")
                    continue

                # Process new file
                self.logger.debug(f"Processing metadata for new file: {file_id}")
                file_record, record, is_of_type_record = await process_drive_file(
                    metadata, org_id
                )

                files.append(file_record.to_dict())
                records.append(record.to_dict())
                is_of_type_records.append(is_of_type_record)

                # Store mapping for later use (eliminates DB queries)
                file_id_to_key[file_id] = record.key
                new_file_metadata.append(metadata)

                self.logger.debug(f"✓ Prepared file_record: {file_id}")

            # Early return if no new files
            if not records:
                self.logger.info("ℹ️  No new files to process in this chunk")
                return True

            # Step 4: Execute single transaction for all operations
            return await self._execute_chunk_transaction(
                files=files,
                records=records,
                is_of_type_records=is_of_type_records,
                file_id_to_key=file_id_to_key,
                new_file_metadata=new_file_metadata,
                existing_files_map=existing_files_map,
                org_id=org_id
            )

        except Exception as e:
            self.logger.error(f"❌ Chunk processing failed: {str(e)}")
            return False

    async def _get_existing_files_bulk(self, file_ids: List[str]) -> Dict[str, dict]:
        """
        Single bulk query to check existence of multiple files.
        Returns mapping of externalRecordId -> {_key, ...}
        """
        if not file_ids:
            return {}

        # Single optimized query for all files
        query = f"""
        FOR doc IN {CollectionNames.RECORDS.value}
            FILTER doc.externalRecordId IN @file_ids
            RETURN {{
                externalRecordId: doc.externalRecordId,
                _key: doc._key
            }}
        """

        cursor = self.arango_service.db.aql.execute(
            query,
            bind_vars={"file_ids": file_ids}
        )

        # Convert to dictionary for O(1) lookups
        return {doc["externalRecordId"]: doc for doc in cursor}

    async def _execute_chunk_transaction(
        self,
        files: List[dict],
        records: List[dict],
        is_of_type_records: List[dict],
        file_id_to_key: Dict[str, str],
        new_file_metadata: List[dict],
        existing_files_map: Dict[str, dict],
        org_id: str
    ) -> bool:
        """Execute all database operations in a single transaction"""

        txn = None
        try:
            txn = self.arango_service.db.begin_transaction(
                read=[
                    CollectionNames.FILES.value,
                    CollectionNames.RECORDS.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.IS_OF_TYPE.value,
                    CollectionNames.USERS.value,
                    CollectionNames.GROUPS.value,
                    CollectionNames.ORGS.value,
                    CollectionNames.ANYONE.value,
                    CollectionNames.PERMISSIONS.value,
                    CollectionNames.BELONGS_TO.value,
                ],
                write=[
                    CollectionNames.FILES.value,
                    CollectionNames.RECORDS.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.IS_OF_TYPE.value,
                    CollectionNames.USERS.value,
                    CollectionNames.GROUPS.value,
                    CollectionNames.ORGS.value,
                    CollectionNames.ANYONE.value,
                    CollectionNames.PERMISSIONS.value,
                    CollectionNames.BELONGS_TO.value,
                ],
            )

            # Batch insert files
            if files and not await self.arango_service.batch_upsert_nodes(
                files,
                collection=CollectionNames.FILES.value,
                transaction=txn,
            ):
                raise Exception("Failed to batch upsert files")

            # Batch insert records
            if records and not await self.arango_service.batch_upsert_nodes(
                records,
                collection=CollectionNames.RECORDS.value,
                transaction=txn,
            ):
                raise Exception("Failed to batch upsert records")

            # Batch insert is_of_type edges
            if is_of_type_records and not await self.arango_service.batch_create_edges(
                is_of_type_records,
                collection=CollectionNames.IS_OF_TYPE.value,
                transaction=txn,
            ):
                raise Exception("Failed to batch create is_of_type relations")

            # Prepare parent-child relationships with bulk query
            record_relations = await self._prepare_parent_relations_bulk(
                new_file_metadata,
                file_id_to_key,
                existing_files_map,
                txn
            )

            # Batch insert parent-child relations
            if record_relations and not await self.arango_service.batch_create_edges(
                record_relations,
                collection=CollectionNames.RECORD_RELATIONS.value,
                transaction=txn,
            ):
                raise Exception("Failed to batch create record relations")

            # Process permissions for new files only
            await self._process_permissions_batch(
                new_file_metadata,
                file_id_to_key,
                org_id,
                txn
            )

            txn.commit_transaction()
            txn = None

            self.logger.info(
                f"✅ Chunk transaction complete: "
                f"Files: {len(files)}, Records: {len(records)}, "
                f"Relations: {len(record_relations)}"
            )

            return True

        except Exception as e:
            if txn:
                txn.abort_transaction()
                txn = None
            self.logger.error(f"❌ Transaction failed: {str(e)}")
            return False

    async def _prepare_parent_relations_bulk(
        self,
        file_metadata_list: List[dict],
        file_id_to_key: Dict[str, str],
        existing_files_map: Dict[str, dict],
        txn
    ) -> List[dict]:
        """
        Prepare parent-child relationships using a SINGLE bulk query.
        No individual queries per file - all done in one go.
        """

        # Step 1: Collect all parent-child pairs and unique IDs
        parent_child_pairs = []
        parent_ids_needed = set()
        child_ids_needed = set()

        for metadata in file_metadata_list:
            file_id = metadata.get("id")
            if not file_id:
                continue

            parents = metadata.get("parents", [])
            if not parents:
                continue

            for parent_id in parents:
                parent_child_pairs.append((parent_id, file_id))

                # Only query if not in our existing maps
                if parent_id not in file_id_to_key:
                    parent_ids_needed.add(parent_id)
                if file_id not in file_id_to_key:
                    child_ids_needed.add(file_id)

        if not parent_child_pairs:
            return []

        # Step 2: Single bulk query for ALL parent and child keys we don't have
        all_ids_to_query = list(parent_ids_needed.union(child_ids_needed))
        id_to_key_map = {}

        if all_ids_to_query:
            query = f"""
            FOR doc IN {CollectionNames.RECORDS.value}
                FILTER doc.externalRecordId IN @file_ids
                RETURN {{
                    externalRecordId: doc.externalRecordId,
                    _key: doc._key
                }}
            """

            db = txn if txn else self.arango_service.db
            cursor = db.aql.execute(query, bind_vars={"file_ids": all_ids_to_query})

            id_to_key_map = {doc["externalRecordId"]: doc["_key"] for doc in cursor}

        # Step 3: Merge with in-memory mappings (prioritize in-memory)
        id_to_key_map.update(file_id_to_key)

        # Also include existing files
        for ext_id, data in existing_files_map.items():
            if ext_id not in id_to_key_map:
                id_to_key_map[ext_id] = data["_key"]

        # Step 4: Build all relations using the complete mapping
        record_relations = []
        missing_parents = set()
        missing_children = set()

        for parent_id, child_id in parent_child_pairs:
            parent_key = id_to_key_map.get(parent_id)
            child_key = id_to_key_map.get(child_id)

            if parent_key and child_key:
                record_relations.append({
                    "_from": f"{CollectionNames.RECORDS.value}/{parent_key}",
                    "_to": f"{CollectionNames.RECORDS.value}/{child_key}",
                    "relationType": RecordRelations.PARENT_CHILD.value,
                })
            else:
                if not parent_key:
                    missing_parents.add(parent_id)
                if not child_key:
                    missing_children.add(child_id)

        # Log missing keys only once per chunk
        if missing_parents:
            self.logger.warning(
                f"⚠️  Missing parent keys for {len(missing_parents)} parents"
            )
        if missing_children:
            self.logger.warning(
                f"⚠️  Missing child keys for {len(missing_children)} children"
            )

        self.logger.info(
            f"Prepared {len(record_relations)} parent-child relations from "
            f"{len(parent_child_pairs)} pairs"
        )

        return record_relations

    async def _process_permissions_batch(
        self,
        file_metadata_list: List[dict],
        file_id_to_key: Dict[str, str],
        org_id: str,
        txn
    ) -> None:
        """
        Process permissions for new files using in-memory mappings.
        No database queries needed - we already have all the keys.
        """

        for metadata in file_metadata_list:
            file_id = metadata.get("id")
            if not file_id:
                continue

            # Get file key from in-memory mapping (no DB query!)
            file_key = file_id_to_key.get(file_id)
            if not file_key:
                self.logger.error(
                    f"❌ File key not found in mapping for ID: {file_id}"
                )
                continue

            # Extract permissions (use pop to remove from metadata if needed)
            permissions = metadata.get("permissions", [])

            if permissions:
                await self.arango_service.process_file_permissions(
                    org_id, file_key, permissions, transaction=txn
                )

    # Async wrapper methods for blocking database operations
    async def _execute_aql_query_async(self, query: str, bind_vars: dict = None) -> list:
        """Async wrapper for AQL query execution"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: list(self.arango_service.db.aql.execute(query, bind_vars=bind_vars or {}))
        )

    async def _check_collection_has_document_async(self, collection_name: str, document_id: str) -> bool:
        """Async wrapper for checking if document exists in collection"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.arango_service.check_collection_has_document(collection_name, document_id)
        )

    async def _get_document_async(self, document_id: str, collection_name: str) -> dict:
        """Async wrapper for getting document from collection"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.arango_service.get_document(document_id, collection_name)
        )

    async def _batch_upsert_nodes_async(self, nodes: list, collection: str, transaction=None) -> bool:
        """Async wrapper for batch upserting nodes"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.arango_service.batch_upsert_nodes(nodes, collection, transaction)
        )

    async def _batch_create_edges_async(self, edges: list, collection: str, transaction=None) -> bool:
        """Async wrapper for batch creating edges"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.arango_service.batch_create_edges(edges, collection, transaction)
        )

    async def _process_file_permissions_async(self, org_id: str, file_key: str, permissions: list, transaction=None) -> bool:
        """Async wrapper for processing file permissions"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.arango_service.process_file_permissions(org_id, file_key, permissions, transaction)
        )

    async def _begin_transaction_async(self, read: list, write: list) -> TransactionDatabase:
        """Async wrapper for beginning transaction"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.arango_service.db.begin_transaction(read=read, write=write)
        )

    async def _commit_transaction_async(self, transaction) -> None:
        """Async wrapper for committing transaction"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: transaction.commit_transaction()
        )

    async def _abort_transaction_async(self, transaction) -> None:
        """Async wrapper for aborting transaction"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: transaction.abort_transaction()
        )


class DriveSyncEnterpriseService(BaseDriveSyncService):
    """Sync service for enterprise setup using admin service"""

    def __init__(
        self,
        logger,
        config_service: ConfigurationService,
        drive_admin_service: GoogleAdminService,
        arango_service: ArangoService,
        change_handler,
        kafka_service: KafkaService,
        celery_app,
    ) -> None:
        super().__init__(
            logger, config_service, arango_service, change_handler, kafka_service, celery_app
        )
        self.drive_admin_service = drive_admin_service
        self._active_user_service = None

    async def connect_services(self, org_id: str) -> bool:
        """Connect to services for enterprise setup"""
        try:
            self.logger.info("🚀 Connecting to enterprise services")

            # Connect to Google Drive Admin
            if not await self.drive_admin_service.connect_admin(org_id, "drive"):
                raise Exception("Failed to connect to Drive Admin API")

            self.logger.info("✅ Enterprise services connected successfully")
            return True

        except Exception as e:
            self.logger.error("❌ Enterprise service connection failed: %s", str(e))
            return False

    async def setup_changes_watch(self, user_email: str) -> Optional[Dict]:
        """Set up changes.watch after initial sync"""
        try:
            # Set up watch
            user_service = await self.drive_admin_service.create_drive_user_service(
                user_email
            )
            self.logger.info("👀 Setting up Drive changes watch for user %s", user_email)
            page_token = await self.arango_service.get_page_token_db(
                user_email=user_email
            )
            if not page_token:
                self.logger.warning("⚠️ No page token found for user %s", user_email)
                watch = await user_service.create_changes_watch()
                if not watch:
                    self.logger.error("❌ Failed to create changes watch")
                    return None
                return watch
            # Check if the page token is expired
            current_time = get_epoch_timestamp_in_ms()
            expiration = page_token.get("expiration", 0)
            self.logger.info("Current time: %s", current_time)
            self.logger.info("Page token expiration: %s", expiration)
            if expiration is None or expiration == 0 or expiration < current_time:
                self.logger.warning("⚠️ Page token expired for user %s", user_email)

                if page_token["channelId"] and page_token["resourceId"]:
                    await user_service.stop_watch(
                        page_token["channelId"], page_token["resourceId"]
                    )

                watch = await user_service.create_changes_watch(page_token)
                if not watch:
                    self.logger.warning(
                        "Changes watch not created for user: %s", user_email
                    )
                    return None
                return watch

            self.logger.info(
                "✅ Page token is not expired for user %s. Using existing webhooks",
                user_email,
            )
            return page_token
        except Exception as e:
            self.logger.error("Failed to set up changes watch: %s", str(e))
            return None

    async def initialize(self, org_id) -> bool:
        """Initialize enterprise sync service"""
        try:
            self.logger.info("Initializing Drive sync service")
            if not await self.connect_services(org_id):
                return False

            # List and store enterprise users
            enterprise_users = await self.drive_admin_service.list_enterprise_users(org_id)
            if enterprise_users:
                self.logger.info("🚀 Found %s users", len(enterprise_users))

                for user in enterprise_users:
                    # Add sync state to user info
                    user_id = await self.arango_service.get_entity_id_by_email(
                        user["email"]
                    )
                    if not user_id:
                        await self.arango_service.batch_upsert_nodes(
                            [user], collection=CollectionNames.USERS.value
                        )

            # List and store groups
            groups = await self.drive_admin_service.list_groups(org_id)
            if groups:
                self.logger.info("🚀 Found %s groups", len(groups))
                for group in groups:
                    group_id = await self.arango_service.get_entity_id_by_email(group["email"])
                    if not group_id:
                        await self.arango_service.batch_upsert_nodes(
                            [group], collection=CollectionNames.GROUPS.value
                        )

            # Create relationships between users and groups in belongsTo collection
            belongs_to_group_relations = []
            for group in groups:
                try:
                    group_members = await self.drive_admin_service.list_group_members(
                        group["email"]
                    )
                    for member in group_members:
                        matching_user = next(
                            (
                                user
                                for user in enterprise_users
                                if user["email"] == member["email"]
                            ),
                            None,
                        )
                        if matching_user:
                            # Check if the relationship already exists
                            existing_relation = (
                                await self.arango_service.check_edge_exists(
                                    f"{CollectionNames.USERS.value}/{matching_user['_key']}",
                                    f"{CollectionNames.GROUPS.value}/{group['_key']}",
                                    CollectionNames.BELONGS_TO.value,
                                )
                            )
                            if not existing_relation:
                                relation = {
                                    "_from": f"{CollectionNames.USERS.value}/{matching_user['_key']}",
                                    "_to": f"{CollectionNames.GROUPS.value}/{group['_key']}",
                                    "entityType": "GROUP",
                                    "role": member.get("role", "member"),
                                }
                                belongs_to_group_relations.append(relation)
                except Exception as e:
                    self.logger.error(
                        "❌ Error fetching group members for group %s: %s",
                        group["_key"],
                        str(e),
                    )

            if belongs_to_group_relations:
                await self.arango_service.batch_create_edges(
                    belongs_to_group_relations,
                    collection=CollectionNames.BELONGS_TO.value,
                )
                self.logger.info(
                    "✅ Created %s user-group relationships",
                    len(belongs_to_group_relations),
                )

            # Create relationships between users and orgs
            belongs_to_org_relations = []
            for user in enterprise_users:
                # Check if the relationship already exists
                existing_relation = await self.arango_service.check_edge_exists(
                    f"{CollectionNames.USERS.value}/{user['_key']}",
                    f"{CollectionNames.ORGS.value}/{org_id}",
                    CollectionNames.BELONGS_TO.value,
                )
                if not existing_relation:
                    relation = {
                        "_from": f"{CollectionNames.USERS.value}/{user['_key']}",
                        "_to": f"{CollectionNames.ORGS.value}/{org_id}",
                        "entityType": "ORGANIZATION",
                    }
                    belongs_to_org_relations.append(relation)

            if belongs_to_org_relations:
                await self.arango_service.batch_create_edges(
                    belongs_to_org_relations,
                    collection=CollectionNames.BELONGS_TO.value,
                )
                self.logger.info(
                    "✅ Created %s user-organization relationships",
                    len(belongs_to_org_relations),
                )

            # Initialize Celery
            await self.celery_app.setup_app()

            # Check sync states and update if needed
            active_users = await self.arango_service.get_users(org_id, active=True)
            for user in active_users:
                # Check if user exists in enterprise users
                is_enterprise_user = False
                for enterprise_user in enterprise_users:
                    if enterprise_user["email"] == user["email"]:
                        is_enterprise_user = True
                        break

                if not is_enterprise_user:
                    self.logger.warning(f"User {user['email']} not found in enterprise users")
                    continue

                self.logger.info(f"Found enterprise user {user['email']}, continuing with sync")

                sync_state = await self.arango_service.get_user_sync_state(
                    user["email"], Connectors.GOOGLE_DRIVE.value.lower()
                )
                current_state = (
                    sync_state.get("syncState") if sync_state else ProgressStatus.NOT_STARTED.value
                )

                if current_state == ProgressStatus.IN_PROGRESS.value:
                    self.logger.warning(
                        f"Sync is currently RUNNING for user {user['email']}. Pausing it."
                    )
                    await self.arango_service.update_user_sync_state(
                        user["email"],
                        ProgressStatus.PAUSED.value,
                        service_type=Connectors.GOOGLE_DRIVE.value.lower(),
                    )

                try:
                    self.logger.info(
                        "🚀 Setting up changes watch for user %s", user["email"]
                    )
                    channel_data = await self.setup_changes_watch(user["email"])
                    if not channel_data:
                        self.logger.error(
                            "Token not created for user: %s", user["email"]
                        )
                        continue
                    else:
                        await self.arango_service.store_page_token(
                            channel_data["channelId"],
                            channel_data["resourceId"],
                            user["email"],
                            channel_data["token"],
                            channel_data["expiration"],
                        )

                    self.logger.info(
                        "✅ Changes watch set up successfully for user: %s",
                        user["email"],
                    )

                except Exception as e:
                    self.logger.error(
                        "❌ Error setting up changes watch for user %s: %s",
                        user["email"],
                        str(e),
                    )
                    return False

            self.logger.info("✅ Drive Sync service initialized successfully")
            return True

        except Exception as e:
            self.logger.error("❌ Failed to initialize enterprise sync: %s", str(e))
            return False

    async def perform_initial_sync(self, org_id, action: str = "start") -> bool:
        """First phase: Build complete drive structure using batch operations"""
        try:
            if await self._should_stop(org_id):
                self.logger.info("Sync stopped before starting")
                return False

            users = await self.arango_service.get_users(org_id)

            for user in users:
                enterprise_users = await self.drive_admin_service.list_enterprise_users(org_id)

                # Check if user exists in enterprise users
                is_enterprise_user = False
                for enterprise_user in enterprise_users:
                    if enterprise_user["email"] == user["email"]:
                        is_enterprise_user = True
                        break

                if not is_enterprise_user:
                    self.logger.warning(f"User {user['email']} not found in enterprise users")
                    continue

                self.logger.info(f"Found enterprise user {user['email']}, continuing with sync")

                sync_state = await self.arango_service.get_user_sync_state(
                    user["email"], Connectors.GOOGLE_DRIVE.value.lower()
                )
                if sync_state is None:
                    apps = await self.arango_service.get_org_apps(org_id)
                    app_key = next(
                        (
                            a.get("_key")
                            for a in apps
                            if (a.get("name", "") or "").lower() == Connectors.GOOGLE_DRIVE.value.lower()
                        ),
                        None,
                    )
                    if not app_key:
                        # Fallback: fetch app doc by name (DB may have different casing)
                        try:
                            app_doc = await self.arango_service.get_app_by_name(Connectors.GOOGLE_DRIVE.value)
                            if isinstance(app_doc, dict):
                                app_key = app_doc.get("_key")
                        except Exception:
                            app_key = None

                    if not app_key:
                        self.logger.warning(
                            "⚠️ Drive app not found for org %s; skipping relation creation for user %s",
                            org_id,
                            user.get("email"),
                        )
                        continue

                    # Create edge between user and app
                    app_edge_data = {
                        "_from": f"{CollectionNames.USERS.value}/{user['_key']}",
                        "_to": f"{CollectionNames.APPS.value}/{app_key}",
                        "syncState": ProgressStatus.NOT_STARTED.value,
                        "lastSyncUpdate": get_epoch_timestamp_in_ms(),
                    }
                    await self.arango_service.batch_create_edges(
                        [app_edge_data],
                        CollectionNames.USER_APP_RELATION.value,
                    )
                    sync_state = app_edge_data

                current_state = sync_state.get("syncState")
                if current_state == ProgressStatus.COMPLETED.value:
                    self.logger.info(
                        "💥 Drive sync is already completed for user %s", user["email"]
                    )

                    try:
                        if not await self.resync_drive(org_id, user):
                            self.logger.error(
                                f"Failed to resync drive for user {user['email']}"
                            )
                            continue
                    except Exception as e:
                        self.logger.error(
                            f"Error processing user {user['email']}: {str(e)}"
                        )
                        continue

                    continue

                # Update user sync state to RUNNING
                await self.arango_service.update_user_sync_state(
                    user["email"],
                    ProgressStatus.IN_PROGRESS.value,
                    service_type=Connectors.GOOGLE_DRIVE.value.lower(),
                )

                if await self._should_stop(org_id):
                    self.logger.info(
                        "Sync stopped during user %s processing", user["email"]
                    )
                    await self.arango_service.update_user_sync_state(
                        user["email"],
                        ProgressStatus.PAUSED.value,
                        service_type=Connectors.GOOGLE_DRIVE.value.lower(),
                    )
                    return False

                # Validate user access and get fresh token
                user_service = await self.drive_admin_service.create_drive_user_service(
                    user["email"]
                )
                if not user_service:
                    self.logger.warning(
                        "❌ Failed to create user service for user: %s", user["email"]
                    )
                    continue

                # Initialize workers and get drive list
                await self.initialize_workers(user_service)

                # Process each drive
                for drive_id, worker in self.drive_workers.items():

                    # Get drive details with complete metadata (using async wrapper)
                    drive_info = await user_service.get_drive_info_async(drive_id, org_id)
                    if not drive_info:
                        self.logger.warning(
                            "❌ Failed to get drive info for drive %s", drive_id
                        )
                        continue

                    drive_id = drive_info.get("drive").get("id")

                    # Check drive state first
                    drive_state = await self.arango_service.get_drive_sync_state(
                        drive_id
                    )
                    if drive_state == ProgressStatus.COMPLETED.value:
                        self.logger.info(
                            "Drive %s is already completed, skipping", drive_id
                        )
                        continue

                    if await self._should_stop(org_id):
                        self.logger.info(
                            "Sync stopped during drive %s processing", drive_id
                        )
                        await self.arango_service.update_drive_sync_state(
                            drive_id, ProgressStatus.PAUSED.value
                        )
                        return False

                    try:
                        # Process drive data
                        if not await self.process_drive_data(drive_info, user):
                            self.logger.error(
                                "❌ Failed to process drive data for drive %s",
                                drive_id,
                            )
                            continue

                        # Update drive state to RUNNING
                        await self.arango_service.update_drive_sync_state(
                            drive_id, ProgressStatus.IN_PROGRESS.value
                        )

                        # Get file list (using async wrapper)
                        files = await user_service.list_files_in_folder_async(drive_id)
                        if not files:
                            continue

                        # Get shared files and add them to processing queue (using async wrapper)
                        shared_files = await user_service.get_shared_with_me_files_async(user["email"])
                        if shared_files:
                            self.logger.info("Found %d shared files to process", len(shared_files))
                            files.extend(shared_files)

                        # Process files in batches
                        batch_size = 50

                        for i in range(0, len(files), batch_size):
                            if await self._should_stop(org_id):
                                self.logger.info(
                                    "Sync stopped during batch processing at index %s",
                                    i,
                                )
                                await self.arango_service.update_drive_sync_state(
                                    drive_id, ProgressStatus.PAUSED.value
                                )
                                return False

                            batch = files[i : i + batch_size]

                            # Separate shared and regular files
                            shared_batch_metadata = [
                                f for f in batch if f.get("isSharedWithMe", False)
                            ]
                            regular_file_ids = [
                                f["id"] for f in batch if not f.get("isSharedWithMe", False)
                            ]

                            # Get metadata for regular files
                            regular_batch_metadata = []
                            if regular_file_ids:
                                regular_batch_metadata = await user_service.batch_fetch_metadata_and_permissions_async(
                                    regular_file_ids, files=[f for f in batch if not f.get("isSharedWithMe", False)]
                                )

                            # Combine metadata from both shared and regular files
                            batch_metadata = shared_batch_metadata + regular_batch_metadata

                            if not await self.process_batch(batch_metadata, org_id):
                                continue

                            # Process each file in the batch - ONLY FOR REGULAR FILES
                            for file_id in regular_file_ids:  # Changed from batch_file_ids to regular_file_ids
                                file_metadata = next(
                                    (
                                        meta
                                        for meta in regular_batch_metadata  # Changed from batch_metadata
                                        if meta["id"] == file_id
                                    ),
                                    None,
                                )
                                if file_metadata:
                                    file_id = file_metadata.get("id")

                                    file_key = await self.arango_service.get_key_by_external_file_id(
                                        file_id
                                    )
                                    record = await self.arango_service.get_document(
                                        file_key, CollectionNames.RECORDS.value
                                    )
                                    file = await self.arango_service.get_document(
                                        file_key, CollectionNames.FILES.value
                                    )

                                    user_id = user["userId"]

                                    endpoints = await self.config_service.get_config(
                                        config_node_constants.ENDPOINTS.value
                                    )
                                    connector_endpoint = endpoints.get(
                                        "connectors"
                                    ).get("endpoint", DefaultEndpoints.CONNECTOR_ENDPOINT.value)

                                    record_version = 0  # Initial version for new files
                                    extension = file.get("extension")
                                    mime_type = file.get("mimeType")
                                    index_event = {
                                        "orgId": org_id,
                                        "recordId": file_key,
                                        "recordName": record.get("recordName"),
                                        "recordVersion": record_version,
                                        "recordType": record.get("recordType"),
                                        "eventType": EventTypes.NEW_RECORD.value,
                                        "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/{user_id}/drive/record/{file_key}/signedUrl",
                                        "connectorName": Connectors.GOOGLE_DRIVE.value,
                                        "origin": OriginTypes.CONNECTOR.value,
                                        "createdAtSourceTimestamp": int(
                                            parse_timestamp(
                                                file_metadata.get("createdTime")
                                            )
                                        ),
                                        "modifiedAtSourceTimestamp": int(
                                            parse_timestamp(
                                                file_metadata.get("modifiedTime")
                                            )
                                        ),
                                        "extension": extension,
                                        "mimeType": mime_type,
                                    }

                                    await self.kafka_service.send_event_to_kafka(
                                        index_event
                                    )
                                    self.logger.info(
                                        "📨 Sent Kafka Indexing event for file %s: %s",
                                        file_id,
                                        index_event,
                                    )

                        # Update drive status after completion
                        await self.arango_service.update_drive_sync_state(
                            drive_id, "COMPLETED"
                        )

                    except Exception as e:
                        self.logger.error(
                            f"❌ Failed to process drive {drive_id}: {str(e)}"
                        )
                        continue

                # Update user state to COMPLETED
                await self.arango_service.update_user_sync_state(
                    user["email"],
                    ProgressStatus.COMPLETED.value,
                    service_type=Connectors.GOOGLE_DRIVE.value.lower(),
                )

            self.is_completed = True
            return True

        except Exception as e:
            # Update user state to FAILED if we have a current user
            if "user" in locals():
                await self.arango_service.update_user_sync_state(
                    user["email"], ProgressStatus.FAILED.value, service_type=Connectors.GOOGLE_DRIVE.value.lower()
                )
            self.logger.error(f"❌ Initial sync failed: {str(e)}")
            return False

    async def sync_specific_user(self, user_email: str) -> bool:
        """Synchronize a specific user's drive content"""
        try:
            self.logger.info(f"🚀 Starting sync for specific user: {user_email}")

            # Verify user exists in the database
            sync_state = await self.arango_service.get_user_sync_state(
                user_email, Connectors.GOOGLE_DRIVE.value.lower()
            )
            current_state = sync_state.get("syncState") if sync_state else ProgressStatus.NOT_STARTED.value
            if current_state == ProgressStatus.IN_PROGRESS.value:
                self.logger.warning(
                    "💥 Drive sync is already running for user %s", user_email
                )
                return False

            user_id = await self.arango_service.get_entity_id_by_email(user_email)
            user = await self.arango_service.get_document(
                user_id, CollectionNames.USERS.value
            )
            org_id = user["orgId"]

            if not org_id:
                self.logger.warning(f"No organization found for user {user_email}")
                return False

            if not user:
                self.logger.warning("User does not exist!")
                return False

            enterprise_users = await self.drive_admin_service.list_enterprise_users(org_id)

            # Check if user exists in enterprise users
            is_enterprise_user = False
            for enterprise_user in enterprise_users:
                if enterprise_user["email"] == user_email:
                    is_enterprise_user = True
                    break

            if not is_enterprise_user:
                self.logger.warning(f"User {user_email} not found in enterprise users")
                return False

            self.logger.info(f"Found enterprise user {user_email}, continuing with sync")

            # Update user sync state to RUNNING
            await self.arango_service.update_user_sync_state(
                user_email, ProgressStatus.IN_PROGRESS.value, service_type=Connectors.GOOGLE_DRIVE.value.lower()
            )

            # Validate user access and get fresh token
            user_service = await self.drive_admin_service.create_drive_user_service(
                user_email
            )
            if not user_service:
                self.logger.error(
                    f"❌ Failed to get drive service for user {user_email}"
                )
                await self.arango_service.update_user_sync_state(
                    user_email, ProgressStatus.FAILED.value, service_type=Connectors.GOOGLE_DRIVE.value.lower()
                )
                return False

            # Set up changes watch for the user
            channel_data = await self.setup_changes_watch(user_email)
            if not channel_data:
                self.logger.error(f"Token not created for user: {user_email}")
            else:
                # Store the page token
                await self.arango_service.store_page_token(
                    channel_data["channelId"],
                    channel_data["resourceId"],
                    user_email,
                    channel_data["token"],
                    channel_data["expiration"],
                )

            # Initialize workers and get drive list
            await self.initialize_workers(user_service)

            # Process each drive
            for drive_id, worker in self.drive_workers.items():

                # Get drive details (using async wrapper)
                drive_info = await user_service.get_drive_info_async(drive_id, org_id)
                if not drive_info:
                    self.logger.warning(
                        "❌ Failed to get drive info for drive %s", drive_id
                    )
                    continue

                drive_id = drive_info.get("drive").get("id")

                if await self._should_stop(org_id):
                    self.logger.info(
                        "Sync stopped during drive %s processing", drive_id
                    )
                    await self.arango_service.update_drive_sync_state(
                        drive_id, ProgressStatus.PAUSED.value
                    )
                    await self.arango_service.update_user_sync_state(
                        user_email, ProgressStatus.PAUSED.value, service_type=Connectors.GOOGLE_DRIVE.value.lower()
                    )
                    return False

                # Check drive state first
                drive_state = await self.arango_service.get_drive_sync_state(drive_id)
                if drive_state == ProgressStatus.COMPLETED.value:
                    self.logger.info(
                        "Drive %s is already completed, skipping", drive_id
                    )
                    continue

                try:
                    # Process drive data
                    if not await self.process_drive_data(drive_info, user):
                        self.logger.error(
                            "❌ Failed to process drive data for drive %s", drive_id
                        )
                        continue

                    # Update drive state to RUNNING
                    await self.arango_service.update_drive_sync_state(
                        drive_id, ProgressStatus.IN_PROGRESS.value
                    )

                    # Get file list (using async wrapper)
                    files = await user_service.list_files_in_folder_async(drive_id)
                    if not files:
                        continue

                    # Get shared files and add them to processing queue
                    shared_files = await user_service.get_shared_with_me_files(user["email"])
                    if shared_files:
                        self.logger.info("Found %d shared files to process", len(shared_files))
                        files.extend(shared_files)

                    # Process files in batches
                    batch_size = 50

                    for i in range(0, len(files), batch_size):
                        if await self._should_stop(org_id):
                            self.logger.info(
                                "Sync stopped during batch processing at index %s", i
                            )
                            await self.arango_service.update_drive_sync_state(
                                drive_id, ProgressStatus.PAUSED.value
                            )
                            await self.arango_service.update_user_sync_state(
                                user_email,
                                ProgressStatus.PAUSED.value,
                                service_type=Connectors.GOOGLE_DRIVE.value.lower(),
                            )
                            return False

                        batch = files[i : i + batch_size]

                        # Separate shared and regular files
                        shared_batch_metadata = [
                            f for f in batch if f.get("isSharedWithMe", False)
                        ]
                        regular_file_ids = [
                            f["id"] for f in batch if not f.get("isSharedWithMe", False)
                        ]

                        # Get metadata for regular files
                        regular_batch_metadata = []
                        if regular_file_ids:
                            regular_batch_metadata = await user_service.batch_fetch_metadata_and_permissions_async(
                                regular_file_ids, files=[f for f in batch if not f.get("isSharedWithMe", False)]
                            )

                        # Combine metadata from both shared and regular files
                        batch_metadata = shared_batch_metadata + regular_batch_metadata

                        if not await self.process_batch(batch_metadata, org_id):
                            continue

                        # Process each file in the batch - ONLY FOR REGULAR FILES
                        for file_id in regular_file_ids:
                            file_metadata = next(
                                (
                                    meta
                                    for meta in regular_batch_metadata
                                    if meta["id"] == file_id
                                ),
                                None,
                            )
                            if file_metadata:
                                file_id = file_metadata.get("id")

                                file_key = await self.arango_service.get_key_by_external_file_id(
                                    file_id
                                )
                                record = await self.arango_service.get_document(
                                    file_key, CollectionNames.RECORDS.value
                                )
                                file = await self.arango_service.get_document(
                                    file_key, CollectionNames.FILES.value
                                )

                                record_version = 0  # Initial version for new files
                                extension = file.get("extension")
                                mime_type = file.get("mimeType")
                                user_id = user["userId"]

                                endpoints = await self.config_service.get_config(
                                    config_node_constants.ENDPOINTS.value
                                )
                                connector_endpoint = endpoints.get("connectors").get(
                                    "endpoint", DefaultEndpoints.CONNECTOR_ENDPOINT.value
                                )

                                index_event = {
                                    "orgId": org_id,
                                    "recordId": file_key,
                                    "recordName": record.get("recordName"),
                                    "recordVersion": record_version,
                                    "recordType": record.get("recordType"),
                                    "eventType": EventTypes.NEW_RECORD.value,
                                    "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/{user_id}/drive/record/{file_key}/signedUrl",
                                    "connectorName": Connectors.GOOGLE_DRIVE.value,
                                    "origin": OriginTypes.CONNECTOR.value,
                                    "createdAtSourceTimestamp": int(
                                        parse_timestamp(
                                            file_metadata.get("createdTime")
                                        )
                                    ),
                                    "modifiedAtSourceTimestamp": int(
                                        parse_timestamp(
                                            file_metadata.get("modifiedTime")
                                        )
                                    ),
                                    "extension": extension,
                                    "mimeType": mime_type,
                                }

                                await self.kafka_service.send_event_to_kafka(
                                    index_event
                                )
                                self.logger.info(
                                    "📨 Sent Kafka Indexing event for file %s: %s",
                                    file_id,
                                    index_event,
                                )

                    # Update drive status after completion
                    await self.arango_service.update_drive_sync_state(
                        drive_id, "COMPLETED"
                    )

                except Exception as e:
                    self.logger.error(
                        f"❌ Failed to process drive {drive_id}: {str(e)}"
                    )
                    continue

            # Update user state to COMPLETED
            await self.arango_service.update_user_sync_state(
                user_email, ProgressStatus.COMPLETED.value, service_type=Connectors.GOOGLE_DRIVE.value.lower()
            )
            self.logger.info(f"✅ Successfully completed sync for user {user_email}")
            return True

        except Exception as e:
            await self.arango_service.update_user_sync_state(
                user_email, ProgressStatus.FAILED.value, service_type=Connectors.GOOGLE_DRIVE.value.lower()
            )
            self.logger.error(f"❌ Failed to sync user {user_email}: {str(e)}")
            return False

    async def resync_drive(self, org_id, user) -> bool | None:
        try:
            self.logger.info(f"Resyncing drive for user {user['email']}")
            enterprise_users = await self.drive_admin_service.list_enterprise_users(org_id)

            # Check if user exists in enterprise users
            is_enterprise_user = False
            for enterprise_user in enterprise_users:
                if enterprise_user["email"] == user["email"]:
                    is_enterprise_user = True
                    break

            if not is_enterprise_user:
                self.logger.warning(f"User {user['email']} not found in enterprise users")
                return True

            self.logger.info(f"Found enterprise user {user['email']}, continuing with sync")

            user_service = await self.drive_admin_service.create_drive_user_service(
                user["email"]
            )
            self.logger.info(f"Resyncing drive for user {user['email']}")
            page_token = await self.arango_service.get_page_token_db(
                user_email=user["email"]
            )

            if not page_token:
                self.logger.warning(f"No page token found for user {user['email']}")
                return True

            changes, new_token = await user_service.get_changes(
                page_token=page_token["token"]
            )
            user_id = user["userId"]

            if changes:
                self.logger.warning(f"Changes found for user {user['email']}")
                for change in changes:
                    try:
                        await self.change_handler.process_change(
                            change, user_service, org_id, user_id
                        )
                    except Exception as e:
                        self.logger.error(f"Error processing change: {str(e)}")
                        continue
            else:
                self.logger.info("ℹ️ No changes found for user %s", user["email"])

            if new_token and new_token != page_token["token"]:
                await self.arango_service.store_page_token(
                    channel_id=page_token["channelId"],
                    resource_id=page_token["resourceId"],
                    user_email=user["email"],
                    token=new_token,
                    expiration=page_token["expiration"],
                )
                self.logger.info(f"🚀 Updated token for user {user['email']}")

            return True

        except Exception as e:
            self.logger.error(
                f"Error resyncing drive for user {user['email']}: {str(e)}"
            )
            return False

    async def reindex_failed_records(self, org_id) -> bool | None:
        """Reindex failed records"""
        try:
            self.logger.info("🔄 Starting reindexing of failed records")

            # Query to get all failed records and their active users with permissions
            failed_records_with_users = self.arango_service.db.aql.execute(
                """
                FOR doc IN records
                    FILTER doc.orgId == @org_id
                    AND doc.indexingStatus == "FAILED"
                    AND doc.connectorName == @connector_name

                    LET active_users = (
                        FOR perm IN permissions
                            FILTER perm._from == doc._id
                            FOR user IN users
                                FILTER perm._to == user._id
                                AND user.isActive == true
                            RETURN DISTINCT user
                    )

                    FILTER LENGTH(active_users) > 0

                    RETURN {
                        record: doc,
                        users: active_users
                    }
                """,
                bind_vars={
                    "org_id": org_id,
                    "connector_name": Connectors.GOOGLE_DRIVE.value
                }
            )

            endpoints = await self.config_service.get_config(
                config_node_constants.ENDPOINTS.value
            )
            connector_endpoint = endpoints.get("connectors").get("endpoint", DefaultEndpoints.CONNECTOR_ENDPOINT.value)

            count = 0

            failed_records_with_users = list(failed_records_with_users)
            if len(failed_records_with_users) == 0:
                self.logger.info("⚠️ NO FAILED RECORDS")

            for item in failed_records_with_users:
                try:
                    record = item["record"]
                    users = item["users"]

                    # Get associated file document for mime type and extension
                    file = await self.arango_service.get_document(
                        record["_key"],
                        CollectionNames.FILES.value
                    )
                    if not file:
                        self.logger.warning(f"⚠️ No file found for record {record['_key']}")
                        continue

                    # Create base event
                    base_event = {
                        "orgId": org_id,
                        "recordId": record["_key"],
                        "recordName": record["recordName"],
                        "recordVersion": record["version"],
                        "recordType": record["recordType"],
                        "eventType": EventTypes.REINDEX_RECORD.value,
                        "connectorName": Connectors.GOOGLE_DRIVE.value,
                        "origin": OriginTypes.CONNECTOR.value,
                        "createdAtSourceTimestamp": record.get("sourceCreatedAtTimestamp"),
                        "modifiedAtSourceTimestamp": record.get("sourceLastModifiedTimestamp"),
                        "extension": file.get("extension"),
                        "mimeType": file.get("mimeType")
                    }

                    # Send event for each active user with permissions
                    for user in users:
                        try:
                            event = base_event.copy()
                            event["signedUrlRoute"] = f"{connector_endpoint}/api/v1/{org_id}/{user['userId']}/drive/record/{record['_key']}/signedUrl"

                            await self.kafka_service.send_event_to_kafka(event)
                            count += 1
                            self.logger.debug(f"✅ Sent reindex event for record {record['_key']} with user {user['email']}")

                        except Exception as e:
                            self.logger.error(f"❌ Error sending event for user {user['email']}: {str(e)}")
                            continue

                except Exception as e:
                    self.logger.error(f"❌ Error processing record {record['_key']}: {str(e)}")
                    continue

            self.logger.info(f"✅ Successfully sent reindex events for {count} records")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error reindexing failed records: {str(e)}")
            return False


class DriveSyncIndividualService(BaseDriveSyncService):
    """Sync service for individual user setup"""

    def __init__(
        self,
        logger,
        config_service: ConfigurationService,
        drive_user_service: DriveUserService,
        arango_service: ArangoService,
        change_handler,
        kafka_service: KafkaService,
        celery_app,
    ) -> None:
        super().__init__(
            logger, config_service, arango_service, change_handler, kafka_service, celery_app
        )
        self.drive_user_service = drive_user_service

    async def connect_services(self, org_id: str) -> bool:
        """Connect to services for individual setup"""
        try:
            self.logger.info("🚀 Connecting to individual user services")

            user_id = None
            user_info = await self.arango_service.get_users(org_id, active=True)
            if user_info and user_info[0].get("userId"):
                user_id = user_info[0]["userId"]
            else:
                # Fallback: fetch individual user directly from Drive API to get a valid user_id
                self.logger.warning("⚠️ No active users found in DB; fetching individual user from Drive API")
                fetched_users = await self.drive_user_service.list_individual_user(org_id)
                if fetched_users and len(fetched_users) > 0 and fetched_users[0].get("userId"):
                    user_id = fetched_users[0]["userId"]
                else:
                    self.logger.error("❌ Unable to determine user_id for individual Drive connection")
                    return False

            # Connect to Google Drive
            if not await self.drive_user_service.connect_individual_user(
                org_id, user_id
            ):
                raise Exception("Failed to connect to Drive API")

            # Connect to ArangoDB and Redis
            if not await self.arango_service.connect():
                raise Exception("Failed to connect to ArangoDB")

            self.logger.info("✅ Individual user services connected successfully")
            return True
        except Exception as e:
            self.logger.error("❌ Individual service connection failed: %s", str(e))
            return False

    async def setup_changes_watch(self, user_email: str) -> Optional[Dict]:
        """Set up changes.watch after initial sync"""
        try:
            # Set up watch
            user_service = self.drive_user_service
            page_token = await self.arango_service.get_page_token_db(
                user_email=user_email
            )
            if not page_token:
                self.logger.warning("⚠️ No page token found for user %s", user_email)
                watch = await user_service.create_changes_watch()
                if not watch:
                    self.logger.error("❌ Failed to create changes watch")
                    return None
                return watch

            # Check if the page token is expired
            current_time = get_epoch_timestamp_in_ms()
            expiration = page_token.get("expiration", 0)
            self.logger.info("Current time: %s", current_time)
            self.logger.info("Page token expiration: %s", expiration)
            if expiration is None or expiration == 0 or expiration < current_time:
                self.logger.warning("⚠️ Page token expired for user %s", user_email)

                if page_token["channelId"] and page_token["resourceId"]:
                    await user_service.stop_watch(
                        page_token["channelId"], page_token["resourceId"]
                    )

                watch = await user_service.create_changes_watch(page_token)
                if not watch:
                    self.logger.error("❌ Failed to create changes watch")
                    return None
                return watch

            self.logger.info(
                "✅ Page token is not expired for user %s. Using existing webhooks",
                user_email,
            )
            return page_token
        except Exception as e:
            self.logger.error("Failed to set up changes watch: %s", str(e))
            return None

    async def initialize(self, org_id) -> bool:
        """Initialize individual user sync service"""
        try:
            if not await self.connect_services(org_id):
                return False

            # Get and store user info with initial sync state
            user_info = await self.drive_user_service.list_individual_user(org_id)
            if user_info:
                # Add sync state to user info
                user_id = await self.arango_service.get_entity_id_by_email(
                    user_info[0]["email"]
                )
                if not user_id:
                    await self.arango_service.batch_upsert_nodes(
                        user_info, collection=CollectionNames.USERS.value
                    )
                user_info = user_info[0]

            # Check if sync is already running
            sync_state = await self.arango_service.get_user_sync_state(
                user_info["email"], Connectors.GOOGLE_DRIVE.value.lower()
            )
            current_state = sync_state.get("syncState") if sync_state else ProgressStatus.NOT_STARTED.value

            if current_state == ProgressStatus.IN_PROGRESS.value:
                self.logger.warning(
                    f"Sync is currently RUNNING for user {user_info['email']}. Pausing it."
                )
                await self.arango_service.update_user_sync_state(
                    user_info["email"],
                    ProgressStatus.PAUSED.value,
                    service_type=Connectors.GOOGLE_DRIVE.value.lower(),
                )

            self.logger.info("👀 Setting up changes watch for all users...")

            # Set up changes watch for each user
            if user_info:
                try:
                    channel_data = await self.setup_changes_watch(user_info["email"])
                    if not channel_data:
                        self.logger.error(
                            "Token not created for user: %s", user_info["email"]
                        )
                    else:
                        await self.arango_service.store_page_token(
                            channel_data["channelId"],
                            channel_data["resourceId"],
                            user_info["email"],
                            channel_data["token"],
                            channel_data["expiration"],
                        )

                        self.logger.info(
                            "✅ Changes watch set up successfully for user: %s",
                            user_info["email"],
                        )

                except Exception as e:
                    self.logger.error(
                        "❌ Error setting up changes watch for user %s: %s",
                        user_info["email"],
                        str(e),
                    )
                    return False

            await self.celery_app.setup_app()

            self.logger.info("✅ Sync service initialized successfully")
            return True

        except Exception as e:
            self.logger.error("❌ Failed to initialize individual sync: %s", str(e))
            return False

    async def perform_initial_sync(self, org_id, action: str = "start") -> bool:
        """First phase: Build complete drive structure using batch operations"""
        try:
            if await self._should_stop(org_id):
                self.logger.info("Sync stopped before starting")
                return False

            user = await self.arango_service.get_users(org_id, active=True)
            user = user[0]

            sync_state = await self.arango_service.get_user_sync_state(
                user["email"], Connectors.GOOGLE_DRIVE.value.lower()
            )

            if sync_state is None:
                apps = await self.arango_service.get_org_apps(org_id)
                app_key = next(
                    (
                        a.get("_key")
                        for a in apps
                        if (a.get("name", "") or "").lower() == Connectors.GOOGLE_DRIVE.value.lower()
                    ),
                    None,
                )
                if not app_key:
                    # Fallback: fetch app doc by name (DB may have different casing)
                    try:
                        app_doc = await self.arango_service.get_app_by_name(Connectors.GOOGLE_DRIVE.value)
                        if isinstance(app_doc, dict):
                            app_key = app_doc.get("_key")
                    except Exception:
                        app_key = None

                if not app_key:
                    self.logger.warning(
                        "⚠️ Drive app not found for org %s; skipping relation creation for user %s",
                        org_id,
                        user.get("email"),
                    )
                    return False

                # Create edge between user and app
                app_edge_data = {
                    "_from": f"{CollectionNames.USERS.value}/{user['_key']}",
                    "_to": f"{CollectionNames.APPS.value}/{app_key}",
                    "syncState": ProgressStatus.NOT_STARTED.value,
                    "lastSyncUpdate": get_epoch_timestamp_in_ms(),
                }
                await self.arango_service.batch_create_edges(
                    [app_edge_data],
                    CollectionNames.USER_APP_RELATION.value,
                )
                sync_state = app_edge_data

            current_state = sync_state.get("syncState")
            if current_state == ProgressStatus.COMPLETED.value:
                self.logger.info(
                    "💥 Drive sync is already completed for user %s", user["email"]
                )

                try:
                    if not await self.resync_drive(org_id, user):
                        self.logger.error(
                            f"Failed to resync drive for user {user['email']}"
                        )
                        return False

                except Exception as e:
                    self.logger.error(
                        f"Error getting changes for user {user['email']}: {str(e)}"
                    )
                    return False

                return True

            # Update user sync state to RUNNING
            await self.arango_service.update_user_sync_state(
                user["email"], ProgressStatus.IN_PROGRESS.value, service_type=Connectors.GOOGLE_DRIVE.value.lower()
            )

            if await self._should_stop(org_id):
                self.logger.info(
                    "Sync stopped during user %s processing", user["email"]
                )
                await self.arango_service.update_user_sync_state(
                    user["email"], ProgressStatus.PAUSED.value, service_type=Connectors.GOOGLE_DRIVE.value.lower()
                )
                return False

            user_service = self.drive_user_service

            # Initialize workers and get drive list
            await self.initialize_workers(user_service)

            # Process each drive
            for drive_id, worker in self.drive_workers.items():

                # Get drive details (using async wrapper)
                drive_info = await user_service.get_drive_info_async(drive_id, org_id)
                if not drive_info:
                    self.logger.warning(
                        "❌ Failed to get drive info for drive %s", drive_id
                    )
                    continue

                drive_id = drive_info.get("drive").get("id")

                if await self._should_stop(org_id):
                    self.logger.info(
                        "Sync stopped during drive %s processing", drive_id
                    )
                    await self.arango_service.update_drive_sync_state(
                        drive_id, ProgressStatus.PAUSED.value
                    )
                    return False

                # Check drive state first
                drive_state = await self.arango_service.get_drive_sync_state(drive_id)
                if drive_state == ProgressStatus.COMPLETED.value:
                    self.logger.info(
                        "Drive %s is already completed, skipping", drive_id
                    )
                    continue

                try:
                    # Process drive data
                    if not await self.process_drive_data(drive_info, user):
                        self.logger.error(
                            "❌ Failed to process drive data for drive %s", drive_id
                        )
                        continue

                    # Update drive state to RUNNING
                    await self.arango_service.update_drive_sync_state(
                        drive_id, ProgressStatus.IN_PROGRESS.value
                    )

                    # Get file list (using async wrapper)
                    files = await user_service.list_files_in_folder_async(drive_id)
                    if not files:
                        continue

                    # Get shared files and add them to processing queue
                    shared_files = await user_service.get_shared_with_me_files(user["email"])
                    if shared_files:
                        self.logger.info("Found %d shared files to process", len(shared_files))
                        files.extend(shared_files)

                    # Process files in batches
                    batch_size = 50

                    for i in range(0, len(files), batch_size):
                        if await self._should_stop(org_id):
                            self.logger.info(
                                "Sync stopped during batch processing at index %s", i
                            )
                            await self.arango_service.update_drive_sync_state(
                                drive_id, ProgressStatus.PAUSED.value
                            )
                            return False

                        batch = files[i : i + batch_size]

                        # Separate shared and regular files
                        shared_batch_metadata = [
                            f for f in batch if f.get("isSharedWithMe", False)
                        ]
                        regular_file_ids = [
                            f["id"] for f in batch if not f.get("isSharedWithMe", False)
                        ]

                        # Get metadata for regular files
                        regular_batch_metadata = []
                        if regular_file_ids:
                            regular_batch_metadata = await user_service.batch_fetch_metadata_and_permissions_async(
                                regular_file_ids, files=[f for f in batch if not f.get("isSharedWithMe", False)]
                            )

                        # Combine metadata from both shared and regular files
                        batch_metadata = shared_batch_metadata + regular_batch_metadata

                        if not await self.process_batch(batch_metadata, org_id):
                            continue

                        # Process each file in the batch - ONLY FOR REGULAR FILES
                        for file_id in regular_file_ids:
                            file_metadata = next(
                                (
                                    meta
                                    for meta in regular_batch_metadata
                                    if meta["id"] == file_id
                                ),
                                None,
                            )
                            if file_metadata:
                                file_id = file_metadata.get("id")

                                file_key = await self.arango_service.get_key_by_external_file_id(
                                    file_id
                                )
                                record = await self.arango_service.get_document(
                                    file_key, CollectionNames.RECORDS.value
                                )
                                file = await self.arango_service.get_document(
                                    file_key, CollectionNames.FILES.value
                                )

                                record_version = 0  # Initial version for new files
                                extension = file.get("extension")
                                mime_type = file.get("mimeType")
                                user_id = user["userId"]

                                endpoints = await self.config_service.get_config(
                                    config_node_constants.ENDPOINTS.value
                                )
                                connector_endpoint = endpoints.get("connectors").get(
                                    "endpoint", DefaultEndpoints.CONNECTOR_ENDPOINT.value
                                )

                                index_event = {
                                    "orgId": org_id,
                                    "recordId": file_key,
                                    "recordName": record.get("recordName"),
                                    "recordVersion": record_version,
                                    "recordType": record.get("recordType"),
                                    "eventType": EventTypes.NEW_RECORD.value,
                                    "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/{user_id}/drive/record/{file_key}/signedUrl",
                                    "connectorName": Connectors.GOOGLE_DRIVE.value,
                                    "origin": OriginTypes.CONNECTOR.value,
                                    "createdAtSourceTimestamp": int(
                                        parse_timestamp(
                                            file_metadata.get("createdTime")
                                        )
                                    ),
                                    "modifiedAtSourceTimestamp": int(
                                        parse_timestamp(
                                            file_metadata.get("modifiedTime")
                                        )
                                    ),
                                    "extension": extension,
                                    "mimeType": mime_type,
                                }

                                await self.kafka_service.send_event_to_kafka(
                                    index_event
                                )
                                self.logger.info(
                                    "📨 Sent Kafka Indexing event for file %s: %s",
                                    file_id,
                                    index_event,
                                )

                    # Update drive status after completion
                    await self.arango_service.update_drive_sync_state(
                        drive_id, "COMPLETED"
                    )

                except Exception as e:
                    self.logger.error(
                        f"❌ Failed to process drive {drive_id}: {str(e)}"
                    )
                    continue

            # Update user state to COMPLETED
            await self.arango_service.update_user_sync_state(
                user["email"], ProgressStatus.COMPLETED.value, service_type=Connectors.GOOGLE_DRIVE.value.lower()
            )

            self.is_completed = True
            return True

        except Exception as e:
            # Update user state to FAILED
            if user:
                await self.arango_service.update_user_sync_state(
                    user["email"], ProgressStatus.FAILED.value, service_type=Connectors.GOOGLE_DRIVE.value.lower()
                )
            self.logger.error(f"❌ Initial sync failed: {str(e)}")
            return False

    async def resync_drive(self, org_id, user) -> bool | None:
        try:
            user_service = self.drive_user_service
            self.logger.info(f"Resyncing drive for user {user['email']}")
            page_token = await self.arango_service.get_page_token_db(
                user_email=user["email"]
            )

            if not page_token:
                self.logger.warning(f"No page token found for user {user['email']}")
                return True

            changes, new_token = await user_service.get_changes(
                page_token=page_token["token"]
            )
            user_id = user["userId"]

            if changes:
                self.logger.warning(f"Changes found for user {user['email']}")
                for change in changes:
                    try:
                        await self.change_handler.process_change(
                            change, user_service, org_id, user_id
                        )
                    except Exception as e:
                        self.logger.error(f"Error processing change: {str(e)}")
                        continue
            else:
                self.logger.info("ℹ️ No changes found for user %s", user["email"])

            if new_token and new_token != page_token["token"]:
                await self.arango_service.store_page_token(
                    channel_id=page_token["channelId"],
                    resource_id=page_token["resourceId"],
                    user_email=user["email"],
                    token=new_token,
                    expiration=page_token["expiration"],
                )
                self.logger.info(f"🚀 Updated token for user {user['email']}")

            return True

        except Exception as e:
            self.logger.error(
                f"Error resyncing drive for user {user['email']}: {str(e)}"
            )
            return False

    async def reindex_failed_records(self, org_id) -> bool | None:
        """Reindex failed records"""
        try:
            self.logger.info("🔄 Starting reindexing of failed records")

            # Query to get all failed records for Drive connector
            failed_records = self.arango_service.db.aql.execute(
                """
                FOR doc IN records
                    FILTER doc.orgId == @org_id
                    AND doc.indexingStatus == "FAILED"
                    AND doc.connectorName == @connector_name
                    RETURN doc
                """,
                bind_vars={
                    "org_id": org_id,
                    "connector_name": Connectors.GOOGLE_DRIVE.value
                }
            )

            endpoints = await self.config_service.get_config(
                config_node_constants.ENDPOINTS.value
            )
            connector_endpoint = endpoints.get("connectors").get("endpoint", DefaultEndpoints.CONNECTOR_ENDPOINT.value)

            # Get user info for constructing routes
            user = await self.arango_service.get_users(org_id)
            if not user:
                self.logger.warning("⚠️ No user found!")
                return False

            user_id = user[0]["userId"]

            count = 0
            failed_records = list(failed_records)
            if len(failed_records) == 0:
                self.logger.info("⚠️ NO FAILED RECORDS")

            for record in failed_records:
                try:
                    # Get associated file document for mime type and extension
                    file = await self.arango_service.get_document(
                        record["_key"],
                        CollectionNames.FILES.value
                    )
                    if not file:
                        self.logger.warning(f"⚠️ No file found for record {record['_key']}")
                        continue

                    # Prepare reindex event
                    event = {
                        "orgId": org_id,
                        "recordId": record["_key"],
                        "recordName": record["recordName"],
                        "recordVersion": record["version"],
                        "recordType": record["recordType"],
                        "eventType": EventTypes.REINDEX_RECORD.value,
                        "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/{user_id}/drive/record/{record['_key']}/signedUrl",
                        "connectorName": Connectors.GOOGLE_DRIVE.value,
                        "origin": OriginTypes.CONNECTOR.value,
                        "createdAtSourceTimestamp": record.get("sourceCreatedAtTimestamp"),
                        "modifiedAtSourceTimestamp": record.get("sourceLastModifiedTimestamp"),
                        "extension": file.get("extension"),
                        "mimeType": file.get("mimeType")
                    }

                    # Send event to Kafka
                    await self.kafka_service.send_event_to_kafka(event)
                    count += 1
                    self.logger.debug(f"✅ Sent reindex event for record {record['_key']}")

                except Exception as e:
                    self.logger.error(f"❌ Error processing record {record['_key']}: {str(e)}")
                    continue

            self.logger.info(f"✅ Successfully sent reindex events for {count} failed records")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error reindexing failed records: {str(e)}")
            return False
