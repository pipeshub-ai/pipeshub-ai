"""Base and specialized sync services for Google Drive synchronization"""

# pylint: disable=E1101, W0718, W0719
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
import asyncio
import uuid
from typing import Dict, Optional
from app.config.arangodb_constants import (CollectionNames, Connectors, 
                                           RecordTypes, RecordRelations, 
                                           OriginTypes, EventTypes)

from app.connectors.utils.drive_worker import DriveWorker
from app.utils.logger import create_logger
from app.connectors.google.core.arango_service import ArangoService
from app.connectors.google.google_drive.core.drive_admin_service import DriveAdminService
from app.connectors.google.google_drive.core.drive_user_service import DriveUserService
from app.connectors.core.kafka_service import KafkaService
from app.config.configuration_service import ConfigurationService, config_node_constants
from app.utils.time_conversion import get_epoch_timestamp_in_ms, parse_timestamp

logger = create_logger("google_drive_sync_service")

class DriveSyncProgress:
    """Class to track sync progress"""

    def __init__(self):
        self.total_files = 0
        self.processed_files = 0
        self.percentage = 0
        self.status = "initializing"
        self.lastUpdatedTimestampAtSource = datetime.now(
            timezone(timedelta(hours=5, minutes=30))).isoformat()

class BaseDriveSyncService(ABC):
    """Abstract base class for sync services"""

    def __init__(
        self,
        config: ConfigurationService,
        arango_service: ArangoService,
        change_handler,
        kafka_service: KafkaService,
        celery_app
    ):
        self.config_service = config
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

        # Configuration
        self._sync_task = None
        self.batch_size = 100

    @abstractmethod
    async def connect_services(self, org_id: str) -> bool:
        """Connect to required services"""
        pass

    @abstractmethod
    async def initialize(self, org_id) -> bool:
        """Initialize sync service"""
        pass

    @abstractmethod
    async def perform_initial_sync(self, org_id, action: str = "start", resume_hierarchy: Dict = None) -> bool:
        """Perform initial sync"""
        pass

    async def setup_changes_watch(self, user_service: DriveUserService) -> Optional[Dict]:
        """Set up changes.watch after initial sync"""
        try:
            # Set up watch
            return await user_service.create_changes_watch()

        except Exception as e:
            logger.error("Failed to set up changes watch: %s", str(e))
            return None

    async def initialize_workers(self, user_service: DriveUserService):
        """Initialize workers for root and shared drives"""
        async with self._worker_lock:
            try:
                logger.info("🔄 Initializing drive workers...")

                # Clear existing workers
                self.drive_workers.clear()

                # Initialize root drive worker
                logger.info("🏠 Setting up root drive worker...")
                self.drive_workers['root'] = DriveWorker(
                    'root',
                    user_service,
                    self.arango_service
                )
                logger.info("✅ Root drive worker initialized")

                # Initialize shared drive workers
                logger.info("🌐 Fetching shared drives...")
                drives = await user_service.list_shared_drives()
                if drives:
                    logger.info(f"📦 Found {len(drives)} shared drives")
                    for drive in drives:
                        drive_id = drive['id']
                        drive_name = drive.get('name', 'Unknown')
                        logger.info(
                            "🔄 Initializing worker for drive: %s (%s)", drive_name, drive_id)

                        self.drive_workers[drive_id] = DriveWorker(
                            drive_id,
                            user_service,
                            self.arango_service
                        )
                        logger.info(
                            "✅ Worker initialized for drive: %s", drive_name)

                total_workers = len(self.drive_workers)
                logger.info("""
                🎉 Worker initialization completed:
                - Total workers: %s
                - Root drive: %s
                - Shared drives: %s
                """, total_workers, '✅' if 'root' in self.drive_workers else '❌', total_workers - 1 if 'root' in self.drive_workers else total_workers)
                return True

            except Exception as e:
                logger.error(f"❌ Failed to initialize workers: {str(e)}")
                return False

    async def start(self, org_id) -> bool:
        logger.info("🚀 Starting sync, Action: start")
        async with self._transition_lock:
            try:
                users = await self.arango_service.get_users(org_id=org_id)
                for user in users:
                    # Check current state using get_user_sync_state
                    sync_state = await self.arango_service.get_user_sync_state(user['email'], Connectors.GOOGLE_DRIVE.value)
                    current_state = sync_state.get('syncState') if sync_state else 'NOT_STARTED'

                    if current_state == 'IN_PROGRESS':
                        logger.warning("💥 Sync service is already running")
                        return False

                    if current_state == 'PAUSED':
                        logger.warning("💥 Sync is paused, use resume to continue")
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

                logger.info("✅ Sync service started")
                return True

            except Exception as e:
                logger.error("❌ Failed to start sync service: %s", str(e))
                return False

    async def pause(self, org_id) -> bool:
        logger.info("⏸️ Pausing sync service")
        async with self._transition_lock:
            try:
                users = await self.arango_service.get_users(org_id=org_id)
                for user in users:
                    # Check current state using get_user_sync_state
                    sync_state = await self.arango_service.get_user_sync_state(user['email'], Connectors.GOOGLE_DRIVE.value)
                    current_state = sync_state.get('syncState') if sync_state else 'NOT_STARTED'

                    if current_state != 'IN_PROGRESS':
                        logger.warning("💥 Sync service is not running")
                        continue

                    self._stop_requested = True

                    # Update user state
                    await self.arango_service.update_user_sync_state(
                        user['email'],
                        'PAUSED',
                        service_type=Connectors.GOOGLE_DRIVE.value
                    )

                    # Cancel current sync task
                    if self._sync_task and not self._sync_task.done():
                        self._sync_task.cancel()
                        try:
                            await self._sync_task
                        except asyncio.CancelledError:
                            pass

                logger.info("✅ Sync service paused")
                return True

            except Exception as e:
                logger.error("❌ Failed to pause sync service: %s", str(e))
                return False

    async def resume(self, org_id) -> bool:
        logger.info("🔄 Resuming sync service")
        async with self._transition_lock:
            try:
                users = await self.arango_service.get_users(org_id=org_id)
                for current_user in users:
                    # Check current state using get_user_sync_state
                    sync_state = await self.arango_service.get_user_sync_state(current_user['email'], Connectors.GOOGLE_DRIVE.value)
                    if not sync_state:
                        logger.warning("⚠️ No sync state found, starting fresh")
                        return await self.start(org_id)

                    current_state = sync_state.get('syncState')
                    if current_state == 'IN_PROGRESS':
                        logger.warning("💥 Sync service is already running")
                        return False

                    if current_state != 'PAUSED':
                        logger.warning("💥 Sync was not paused, use start instead")
                        return False

                    self._pause_event.set()
                    self._stop_requested = False

                # Start sync with resume state
                self._sync_task = asyncio.create_task(
                    self.perform_initial_sync(org_id, action="resume")
                )

                logger.info("✅ Sync service resumed")
                return True

            except Exception as e:
                logger.error("❌ Failed to resume sync service: %s", str(e))
                return False

    async def _should_stop(self, org_id) -> bool:
        """Check if operation should stop"""
        if self._stop_requested:
            users = await self.arango_service.get_users(org_id=org_id)
            for user in users:
                current_state = await self.arango_service.get_user_sync_state(user['email'], Connectors.GOOGLE_DRIVE.value)
                if current_state:
                    current_state = current_state.get('syncState')
                    if current_state == 'IN_PROGRESS':
                        await self.arango_service.update_user_sync_state(
                            user['email'],
                            'PAUSED',
                            service_type=Connectors.GOOGLE_DRIVE.value
                        )
                        logger.info("✅ Drive sync state updated before stopping")
                        return True
            return False
        return False


    async def process_drive_data(self, drive_info, user):
        """Process drive data including drive document, file record, record and permissions
        
        Args:
            drive_info (dict): Complete drive metadata from get_drive_info
            user (dict): Current user information
        
        Returns:
            bool: True if processing successful, False otherwise
        """
        try:
            logger.info("🚀 Processing drive data for drive %s", drive_info['drive']['id'])

            # Check if drive already exists
            existing_drive = self.arango_service.db.aql.execute(
                f'FOR doc IN {CollectionNames.RECORDS.value} FILTER doc.externalRecordId == @drive_id RETURN doc',
                bind_vars={'drive_id': drive_info['drive']['id']}
            )
            existing = next(existing_drive, None)

            if existing:
                logger.debug("Drive %s already exists in ArangoDB", drive_info['drive']['id'])
                drive_info['drive']['_key'] = existing['_key']
                drive_info['record']['_key'] = existing['_key']

            # Save drive in drives collection
            await self.arango_service.batch_upsert_nodes(
                [drive_info['drive']],
                collection=CollectionNames.DRIVES.value
            )

            # Save drive as record
            await self.arango_service.batch_upsert_nodes(
                [drive_info['record']],
                collection=CollectionNames.RECORDS.value
            )

            # Get user ID for relationships
            user_id = await self.arango_service.get_entity_id_by_email(user['email'])
            
            # Create user-drive relationship
            user_drive_relation = {
                '_from': f'users/{user_id}',
                '_to': f'drives/{drive_info["drive"]["_key"]}',
                'access_level': drive_info['drive']['access_level']
            }
            print("user_drive_relation: ", user_drive_relation)

            await self.arango_service.batch_create_edges(
                [user_drive_relation],
                collection=CollectionNames.USER_DRIVE_RELATION.value
            )

            # Create user-record relationship with permissions
            user_record_relation = {
                '_to': f'users/{user_id}',
                '_from': f'records/{drive_info["record"]["_key"]}',
                'role': 'WRITER' if drive_info['drive']['access_level'] == 'writer' else 'READER',
                'type': 'USER',
                'externalPermissionId': None,
                'createdAtTimestamp': get_epoch_timestamp_in_ms(),
                'updatedAtTimestamp': get_epoch_timestamp_in_ms(),
                'lastUpdatedTimestampAtSource': get_epoch_timestamp_in_ms()
            }
            
            await self.arango_service.batch_create_edges(
                [user_record_relation],
                collection=CollectionNames.PERMISSIONS.value
            )

            logger.info("✅ Successfully processed drive data for drive %s", drive_info['drive']['id'])
            return True

        except Exception as e:
            logger.error("❌ Failed to process drive data: %s", str(e))
            return False

    async def process_batch(self, metadata_list, org_id):
        """Process a single batch with atomic operations"""
        batch_start_time = datetime.now(timezone.utc)

        try:
            if await self._should_stop(org_id):
                return False

            async with self._sync_lock:

                # Prepare nodes and edges for batch processing
                files = []
                records = []
                is_of_type_records = []
                recordRelations = []
                existing_files = []

                for metadata in metadata_list:
                    if not metadata:
                        logger.warning("❌ No metadata found for file")
                        continue

                    file_id = metadata.get('id')
                    if not file_id:
                        logger.warning("❌ No file ID found for file")
                        continue

                    # Check if file already exists in ArangoDB
                    existing_file = self.arango_service.db.aql.execute(
                        f'FOR doc IN {CollectionNames.RECORDS.value} FILTER doc.externalRecordId == @file_id RETURN doc',
                        bind_vars={'file_id': file_id}
                    )
                    existing = next(existing_file, None)

                    if existing:
                        logger.debug(
                            "File %s already exists in ArangoDB", file_id)
                        existing_files.append(file_id)

                    else:
                        # Prepare File, Record and File Metadata
                        file = {
                            '_key': str(uuid.uuid4()),
                            'orgId': org_id,
                            'name': str(metadata.get('name')),
                            'isFile': metadata.get('mimeType', '') != 'application/vnd.google-apps.folder',
                            'extension': metadata.get('fileExtension', None),
                            'mimeType': metadata.get('mimeType', None),
                            'sizeInBytes': int(metadata.get('size', None)),
                            'webUrl': metadata.get('webViewLink', None),
                            'etag': metadata.get('etag', None),
                            'ctag': metadata.get('ctag', None),
                            'quickXorHash': metadata.get('quickXorHash', None),
                            'crc32Hash': metadata.get('crc32Hash', None),
                            'md5Checksum': metadata.get('md5Checksum', None),
                            'sha1Hash': metadata.get('sha1Checksum', None),
                            'sha256Hash': metadata.get('sha256Checksum', None),
                            'path': metadata.get('path', None)
                            
                        }

                        record = {
                            '_key': f'{file["_key"]}',
                            'orgId': org_id,
                            'recordName': f'{file["name"]}',
                            'recordType': RecordTypes.FILE.value,
                            'version': 0,
                            'externalRecordId': str(file_id),
                            'externalRevisionId': metadata.get('headRevisionId', None),
                            'createdAtTimestamp':  get_epoch_timestamp_in_ms(),
                            'updatedAtTimestamp':  get_epoch_timestamp_in_ms(),
                            'sourceCreatedAtTimestamp': int(parse_timestamp(metadata.get('createdTime')).timestamp()),
                            'sourceLastModifiedTimestamp': int(parse_timestamp(metadata.get('modifiedTime')).timestamp()),
                            "origin": OriginTypes.CONNECTOR.value,
                            'connectorName': Connectors.GOOGLE_DRIVE.value,
                            'isArchived': False,
                            'isDeleted': False,
                            'isLatestVersion': True,
                            'isDirty': False,
                            'lastSyncTimestamp':  get_epoch_timestamp_in_ms(),
                            'indexingStatus': 'NOT_STARTED',
                            'extractionStatus': 'NOT_STARTED',
                            'lastIndexTimestamp': None,
                            'lastExtractionTimestamp': None,
                            'isDirty': False,
                            'reason': None,
                        }
                        
                        is_of_type_record = {
                            '_from': f'records/{record["_key"]}',
                            '_to': f'files/{file["_key"]}',
                            "createdAtTimestamp" : get_epoch_timestamp_in_ms(),
                            "updatedAtTimestamp" : get_epoch_timestamp_in_ms(),
                        }

                        files.append(file)
                        records.append(record)
                        is_of_type_records.append(is_of_type_record)

                # Batch process all collected data
                if files or records or recordRelations:
                    try:
                        txn = None
                        txn = self.arango_service.db.begin_transaction(
                            read=[CollectionNames.FILES.value, CollectionNames.RECORDS.value, CollectionNames.RECORD_RELATIONS.value, CollectionNames.IS_OF_TYPE.value,
                                  CollectionNames.USERS.value, CollectionNames.GROUPS.value, CollectionNames.ORGS.value, CollectionNames.ANYONE.value, CollectionNames.PERMISSIONS.value, CollectionNames.BELONGS_TO.value],
                            write=[CollectionNames.FILES.value, CollectionNames.RECORDS.value, CollectionNames.RECORD_RELATIONS.value, CollectionNames.IS_OF_TYPE.value,
                                   CollectionNames.USERS.value, CollectionNames.GROUPS.value, CollectionNames.ORGS.value, CollectionNames.ANYONE.value, CollectionNames.PERMISSIONS.value, CollectionNames.BELONGS_TO.value]
                        )
                        # Process files with revision checking
                        if files:
                            if not await self.arango_service.batch_upsert_nodes(
                                files,
                                collection=CollectionNames.FILES.value,
                                transaction=txn
                            ):
                                raise Exception(
                                    "Failed to batch upsert files with existing revision")

                        # Process records and relations
                        if records:
                            if not await self.arango_service.batch_upsert_nodes(
                                records,
                                collection=CollectionNames.RECORDS.value,
                                transaction=txn
                            ):
                                raise Exception(
                                    "Failed to batch upsert records")
                                
                        if is_of_type_records:
                            if not await self.arango_service.batch_create_edges(
                                is_of_type_records,
                                collection=CollectionNames.IS_OF_TYPE.value,
                                transaction=txn
                            ):
                                raise Exception(
                                    "Failed to batch create is_of_type relations")

                        db = txn if txn else self.arango_service.db
                        # Prepare edge data if parent exists
                        for metadata in metadata_list:
                            if 'parents' in metadata:
                                logger.info("parents in metadata: %s",
                                            metadata['parents'])
                                for parent_id in metadata['parents']:
                                    logger.info("parent_id: %s", parent_id)
                                    parent_cursor = db.aql.execute(
                                        f'FOR doc IN {CollectionNames.RECORDS.value} FILTER doc.externalRecordId == @parent_id RETURN doc._key',
                                        bind_vars={'parent_id': parent_id}
                                    )
                                    file_cursor = db.aql.execute(
                                        f'FOR doc IN {CollectionNames.RECORDS.value} FILTER doc.externalRecordId == @file_id RETURN doc._key',
                                        bind_vars={'file_id': file_id}
                                    )
                                    parent_key = next(parent_cursor, None)
                                    file_key = next(file_cursor, None)
                                    logger.info("parent_key: %s", parent_key)
                                    logger.info("file_key: %s", file_key)

                                    if parent_key and file_key:
                                        recordRelations.append({
                                            '_from': f'{CollectionNames.RECORDS.value}/{parent_key}',
                                            '_to': f'{CollectionNames.RECORDS.value}/{file_key}',
                                            'relationType': RecordRelations.PARENT_CHILD.value
                                        })

                        if recordRelations:
                            if not await self.arango_service.batch_create_edges(
                                recordRelations,
                                collection=CollectionNames.RECORD_RELATIONS.value,
                                transaction=txn
                            ):
                                raise Exception(
                                    "Failed to batch create file relations")

                        # Process permissions
                        for metadata in metadata_list:
                            file_id = metadata.get('id')
                            if file_id in existing_files:
                                continue
                            permissions = metadata.pop('permissions', [])

                            # Get file key from file_id
                            query = f"""
                            FOR record IN {CollectionNames.RECORDS.value}
                            FILTER record.externalRecordId == @file_id
                            RETURN record._key
                            """

                            db = txn if txn else self.arango_service.db

                            cursor = db.aql.execute(
                                query, bind_vars={'file_id': file_id})
                            file_key = next(cursor, None)

                            if not file_key:
                                logger.error(
                                    "❌ File not found with ID: %s", file_id)
                                return False
                            if permissions:
                                await self.arango_service.process_file_permissions(org_id, file_key, permissions, transaction=txn)

                        txn.commit_transaction()
                        txn = None

                        logger.info(
                            "✅ Transaction for processing batch complete successfully.")

                        logger.info("""
                        ✅ Batch processed successfully:
                        - Files: %d
                        - Records: %d
                        - Relations: %d
                        - Processing Time: %s
                        """, len(files), len(records), len(recordRelations),
                            datetime.now(timezone.utc) - batch_start_time)

                        return True

                    except Exception as e:
                        if txn:
                            txn.abort_transaction()
                            txn = None
                        logger.error(
                            f"❌ Failed to process batch data: {str(e)}")
                        return False
                return True

        except Exception as e:
            logger.error(f"❌ Batch processing failed: {str(e)}")
            return False

class DriveSyncEnterpriseService(BaseDriveSyncService):
    """Sync service for enterprise setup using admin service"""

    def __init__(
        self,
        config: ConfigurationService,
        drive_admin_service: DriveAdminService,
        arango_service: ArangoService,
        change_handler,
        kafka_service: KafkaService,
        celery_app
    ):
        super().__init__(config, arango_service,
                         change_handler, kafka_service, celery_app)
        self.drive_admin_service = drive_admin_service
        self._active_user_service = None

    async def connect_services(self, org_id: str) -> bool:
        """Connect to services for enterprise setup"""
        try:
            logger.info("🚀 Connecting to enterprise services")

            # Connect to Google Drive Admin
            if not await self.drive_admin_service.connect_admin(org_id):
                raise Exception("Failed to connect to Drive Admin API")

            logger.info("✅ Enterprise services connected successfully")
            return True

        except Exception as e:
            logger.error("❌ Enterprise service connection failed: %s", str(e))
            return False

    async def initialize(self, org_id) -> bool:
        """Initialize enterprise sync service"""
        try:
            logger.info("Initializing Drive sync service")
            if not await self.connect_services(org_id):
                return False

            # List and store enterprise users
            users = await self.drive_admin_service.list_enterprise_users(org_id)
            if users:
                logger.info("🚀 Found %s users", len(users))

                for user in users:
                    # Add sync state to user info                
                    user_id = await self.arango_service.get_entity_id_by_email(user['email'])
                    if not user_id:
                        await self.arango_service.batch_upsert_nodes([user], collection=CollectionNames.USERS.value)

            # List and store groups
            groups = await self.drive_admin_service.list_groups(org_id)
            if groups:
                logger.info("🚀 Found %s groups", len(groups))
                await self.arango_service.batch_upsert_nodes(groups, collection=CollectionNames.GROUPS.value)

            # Create relationships between users and groups in belongsTo collection
            belongs_to_group_relations = []
            for group in groups:
                try:
                    group_members = await self.drive_admin_service.list_group_members(group['email'])
                    for member in group_members:
                        matching_user = next(
                            (user for user in users if user['email'] == member['email']), None)
                        if matching_user:
                            # Check if the relationship already exists
                            existing_relation = await self.arango_service.check_edge_exists(
                                f'users/{matching_user["_key"]}',
                                f'groups/{group["_key"]}',
                                CollectionNames.BELONGS_TO.value
                            )
                            if not existing_relation:
                                relation = {
                                    '_from': f'users/{matching_user["_key"]}',
                                    '_to': f'groups/{group["_key"]}',
                                    'entityType': 'GROUP',
                                    'role': member.get('role', 'member')
                                }
                                belongs_to_group_relations.append(relation)
                except Exception as e:
                    logger.error(
                        "❌ Error fetching group members for group %s: %s", group['_key'], str(e))

            if belongs_to_group_relations:
                await self.arango_service.batch_create_edges(
                    belongs_to_group_relations,
                    collection=CollectionNames.BELONGS_TO.value
                )
                logger.info("✅ Created %s user-group relationships",
                            len(belongs_to_group_relations))

            # Create relationships between users and orgs
            belongs_to_org_relations = []
            for user in users:
                # Check if the relationship already exists
                existing_relation = await self.arango_service.check_edge_exists(
                    f'users/{user["_key"]}',
                    f'organizations/{org_id}',
                    CollectionNames.BELONGS_TO.value
                )
                if not existing_relation:
                    relation = {
                        '_from': f'users/{user["_key"]}',
                        '_to': f'organizations/{org_id}',
                        'entityType': 'ORGANIZATION'
                    }
                    belongs_to_org_relations.append(relation)

            if belongs_to_org_relations:
                await self.arango_service.batch_create_edges(
                    belongs_to_org_relations,
                    collection=CollectionNames.BELONGS_TO.value
                )
                logger.info("✅ Created %s user-organization relationships",
                            len(belongs_to_org_relations))

            # Initialize Celery
            await self.celery_app.setup_app()

            # Check sync states and update if needed
            active_users = await self.arango_service.get_users(org_id, active = True)
            for user in active_users:
                sync_state = await self.arango_service.get_user_sync_state(user['email'], Connectors.GOOGLE_DRIVE.value)
                current_state = sync_state.get('syncState') if sync_state else 'NOT_STARTED'
                
                if current_state == 'IN_PROGRESS':
                    logger.warning(f"Sync is currently RUNNING for user {user['email']}. Pausing it.")
                    await self.arango_service.update_user_sync_state(
                        user['email'],
                        'PAUSED',
                        service_type=Connectors.GOOGLE_DRIVE.value
                    )

            # Phase 1: Set up changes.watch for each user
            logger.info("👀 Setting up changes watch for all users...")
            logger.debug("🚀 Users: %s", users)
            
            for user in users:
                try:
                    user_service = await self.drive_admin_service.create_user_service(user['email'])
                    if not user_service:
                        logger.warning(
                            "❌ Failed to create user service for user: %s", user['email'])
                        continue

                    channel_data = await self.setup_changes_watch(user_service)
                    logger.info(f"🚀 Channel data: {channel_data}")
                    if not channel_data:
                        logger.warning(
                            "❌ Failed to set up changes watch for user: %s", user['email'])
                        continue

                    logger.info(
                        "✅ Changes watch set up successfully for user: %s", user['email'])

                    await self.arango_service.store_page_token(
                        channel_data['channel_id'], 
                        channel_data['resource_id'], 
                        user['email'], 
                        channel_data['page_token']
                    )
                    
                    changes, new_token = await user_service.get_changes(channel_data['page_token'])
                    logger.info("🚀 Found %s changes since page token: %s", len(
                        changes), channel_data['page_token'])
                    logger.info("Changes: %s", changes)
                    logger.info("🚀 New token: %s", new_token)
                except Exception as e:
                    logger.error(
                        "❌ Error setting up changes watch for user %s: %s", user['email'], str(e))
                    return False

            logger.info("✅ Drive Sync service initialized successfully")
            return True

        except Exception as e:
            logger.error("❌ Failed to initialize enterprise sync: %s", str(e))
            return False

    async def perform_initial_sync(self, org_id, action: str = "start") -> bool:
        """First phase: Build complete drive structure using batch operations"""
        try:
            if await self._should_stop(org_id):
                logger.info("Sync stopped before starting")
                return False

            users = await self.arango_service.get_users(org_id)

            for user in users:
                # Update user sync state to RUNNING
                await self.arango_service.update_user_sync_state(
                    user['email'],
                    'IN_PROGRESS',
                    service_type=Connectors.GOOGLE_DRIVE.value
                )

                if await self._should_stop(org_id):
                    logger.info("Sync stopped during user %s processing", user['email'])
                    await self.arango_service.update_user_sync_state(
                        user['email'],
                        'PAUSED',
                        service_type=Connectors.GOOGLE_DRIVE.value
                    )
                    return False

                # Validate user access and get fresh token
                user_service = await self.drive_admin_service.create_user_service(user['email'])
                if not user_service:
                    logger.warning(
                        "❌ Failed to create user service for user: %s", user['email'])
                    continue

                # Initialize workers and get drive list
                await self.initialize_workers(user_service)

                # Process each drive
                for drive_id, worker in self.drive_workers.items():

                    # Get drive details with complete metadata
                    drive_info = await user_service.get_drive_info(drive_id, org_id)
                    if not drive_info:
                        logger.warning("❌ Failed to get drive info for drive %s", drive_id)
                        continue
                    
                    drive_key = drive_info.get('drive').get('_key')
                    
                    # Check drive state first
                    drive_state = await self.arango_service.get_drive_sync_state(drive_key)
                    if drive_state == 'COMPLETED':
                        logger.info("Drive %s is already completed, skipping", drive_key)
                        continue
                    
                    if await self._should_stop(org_id):
                        logger.info("Sync stopped during drive %s processing", drive_key)
                        await self.arango_service.update_drive_sync_state(
                            drive_key,
                            'PAUSED'
                        )
                        return False

                    try:
                        # Process drive data
                        if not await self.process_drive_data(drive_info, user):
                            logger.error("❌ Failed to process drive data for drive %s", drive_key)
                            continue

                        # Update drive state to RUNNING
                        await self.arango_service.update_drive_sync_state(
                            drive_key,
                            'IN_PROGRESS'
                        )

                        # Get file list
                        files = await user_service.list_files_in_folder(drive_id)
                        if not files:
                            continue

                        # Process files in batches
                        batch_size = 50
                        total_processed = 0

                        for i in range(0, len(files), batch_size):
                            if await self._should_stop(org_id):
                                logger.info("Sync stopped during batch processing at index %s", i)
                                await self.arango_service.update_drive_sync_state(
                                    drive_key,
                                    'PAUSED'
                                )
                                return False

                            batch = files[i:i + batch_size]
                            batch_file_ids = [f['id'] for f in batch]
                            batch_metadata = await user_service.batch_fetch_metadata_and_permissions(batch_file_ids, files=files)

                            if not await self.process_batch(batch_metadata, org_id):
                                continue

                            # Process each file in the batch
                            for file_id in batch_file_ids:
                                file_metadata = next(
                                    (meta for meta in batch_metadata if meta['id'] == file_id),
                                    None
                                )
                                if file_metadata:
                                    print("FILE METADATA: ", file_metadata)
                                    file_id = file_metadata.get('id')

                                    file_key = await self.arango_service.get_key_by_external_file_id(file_id)
                                    record = await self.arango_service.get_document(file_key, CollectionNames.RECORDS.value)
                                    file = await self.arango_service.get_document(file_key, CollectionNames.FILES.value)
                                    
                                    user_id = user['userId']
                                    
                                    connector_config = await self.config_service.get_config(config_node_constants.CONNECTORS_SERVICE.value)
                                    connector_endpoint = connector_config.get('endpoint')
            
                                    record_version = 0  # Initial version for new files
                                    extension = file.get('extension')
                                    mime_type = file.get('mimeType')
                                    index_event = {
                                        "orgId": org_id,
                                        "recordId": file_key,
                                        "recordName": record.get('recordName'),
                                        "recordVersion": record_version,
                                        "recordType": record.get('recordType'),
                                        'eventType': EventTypes.NEW_RECORD.value,
                                        "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/{user_id}/drive/record/{file_key}/signedUrl",
                                        "metadataRoute": f"/api/v1/drive/files/{file_key}/metadata",
                                        "connectorName": Connectors.GOOGLE_DRIVE.value,
                                        "origin": OriginTypes.CONNECTOR.value,
                                        "createdAtSourceTimestamp": int(parse_timestamp(file_metadata.get('createdTime')).timestamp()),
                                        "modifiedAtSourceTimestamp": int(parse_timestamp(file_metadata.get('modifiedTime')).timestamp()),
                                        "extension": extension,
                                        "mimeType": mime_type
                                    }

                                    await self.kafka_service.send_event_to_kafka(
                                        index_event)
                                    logger.info(
                                        "📨 Sent Kafka Indexing event for file %s: %s", file_id, index_event)

                        # Update drive status after completion
                        await self.arango_service.update_drive_sync_state(
                            drive_key,
                            'COMPLETED'
                        )

                    except Exception as e:
                        logger.error(f"❌ Failed to process drive {drive_key}: {str(e)}")
                        continue

                # Update user state to COMPLETED
                await self.arango_service.update_user_sync_state(
                    user['email'],
                    'COMPLETED',
                    service_type=Connectors.GOOGLE_DRIVE.value
                )

            self.is_completed = True
            return True

        except Exception as e:
            # Update user state to FAILED if we have a current user
            if 'user' in locals():
                await self.arango_service.update_user_sync_state(
                    user['email'],
                    'FAILED',
                    service_type=Connectors.GOOGLE_DRIVE.value
                )
            logger.error(f"❌ Initial sync failed: {str(e)}")
            return False

    async def sync_specific_user(self, user_email: str) -> bool:
        """Synchronize a specific user's drive content"""
        try:
            logger.info(f"🚀 Starting sync for specific user: {user_email}")

            # Verify user exists in the database
            sync_state = await self.arango_service.get_user_sync_state(user_email, Connectors.GOOGLE_DRIVE.value)
            current_state = sync_state.get('syncState') if sync_state else 'NOT_STARTED'
            if current_state == 'IN_PROGRESS':
                logger.warning("💥 Gmail sync is already running for user %s", user_email)
                return False
            
            user_id = await self.arango_service.get_entity_id_by_email(user_email)
            user = self.arango_service.get_document(user_id, CollectionNames.USERS.value)
            # Get org_id from belongsTo relation for this user
            query = f"""
            FOR edge IN belongsTo
                FILTER edge._from == 'users/{user_id}'
                AND edge.entityType == 'ORGANIZATION'
                RETURN PARSE_IDENTIFIER(edge._to).key
            """
            cursor = self.arango_service.db.aql.execute(query)
            org_id = next(cursor, None)
            
            if not org_id:
                logger.warning(f"No organization found for user {user_email}")
                return False
            
            if not user:
                logger.warning("User does not exist!")
                return False

            # Update user sync state to RUNNING
            await self.arango_service.update_user_sync_state(
                user_email,
                'IN_PROGRESS',
                service_type=Connectors.GOOGLE_DRIVE.value
            )

            # Validate user access and get fresh token
            user_service = await self.drive_admin_service.create_user_service(user_email)
            if not user_service:
                logger.error(f"❌ Failed to get drive service for user {user_email}")
                await self.arango_service.update_user_sync_state(user_email, 'FAILED', service_type=Connectors.GOOGLE_DRIVE.value)
                return False

            # Set up changes watch for the user
            channel_data = await self.setup_changes_watch(user_service)
            if not channel_data:
                logger.error(f"❌ Failed to set up changes watch for user: {user_email}")
                await self.arango_service.update_user_sync_state(user_email, 'FAILED', service_type=Connectors.GOOGLE_DRIVE.value)
                return False

            # Store the page token
            await self.arango_service.store_page_token(
                channel_data['channel_id'],
                channel_data['resource_id'],
                user_email,
                channel_data['page_token']
            )

            # Initialize workers and get drive list
            await self.initialize_workers(user_service)

            # Process each drive
            for drive_id, worker in self.drive_workers.items():
                
                # Get drive details
                drive_info = await user_service.get_drive_info(drive_id, org_id)
                if not drive_info:
                    logger.warning("❌ Failed to get drive info for drive %s", drive_id)
                    continue
                
                drive_key = drive_info.get('drive').get('_key')

                if await self._should_stop(org_id):
                    logger.info("Sync stopped during drive %s processing", drive_id)
                    await self.arango_service.update_drive_sync_state(
                        drive_key,
                        'PAUSED'
                    )
                    await self.arango_service.update_user_sync_state(user_email, 'PAUSED', service_type=Connectors.GOOGLE_DRIVE.value)
                    return False

                # Check drive state first
                drive_state = await self.arango_service.get_drive_sync_state(drive_key)
                if drive_state == 'COMPLETED':
                    logger.info("Drive %s is already completed, skipping", drive_key)
                    continue

                

                try:
                    # Process drive data
                    if not await self.process_drive_data(drive_info, user):
                        logger.error("❌ Failed to process drive data for drive %s", drive_key)
                        continue

                    # Update drive state to RUNNING
                    await self.arango_service.update_drive_sync_state(
                        drive_key,
                        'IN_PROGRESS'
                    )

                    # Get file list
                    files = await user_service.list_files_in_folder(drive_id)
                    if not files:
                        continue

                    # Process files in batches
                    batch_size = 50
                    total_processed = 0

                    for i in range(0, len(files), batch_size):
                        if await self._should_stop(org_id):
                            logger.info("Sync stopped during batch processing at index %s", i)
                            await self.arango_service.update_drive_sync_state(
                                drive_key,
                                'PAUSED'
                            )
                            await self.arango_service.update_user_sync_state(user_email, 'PAUSED', service_type=Connectors.GOOGLE_DRIVE.value)
                            return False

                        batch = files[i:i + batch_size]
                        batch_file_ids = [f['id'] for f in batch]
                        batch_metadata = await user_service.batch_fetch_metadata_and_permissions(batch_file_ids, files=files)

                        if not await self.process_batch(batch_metadata, org_id):
                            continue

                        # Process each file in the batch
                        for file_id in batch_file_ids:
                            file_metadata = next(
                                (meta for meta in batch_metadata if meta['id'] == file_id),
                                None
                            )
                            if file_metadata:
                                file_key = await self.arango_service.get_key_by_external_file_id(file_id)
                                record = await self.arango_service.get_document(file_key, CollectionNames.RECORDS.value)
                                file = await self.arango_service.get_document(file_key, CollectionNames.FILES.value)

                                user_id = user['userId']
                                
                                connector_config = await self.config_service.get_config(config_node_constants.CONNECTORS_SERVICE.value)
                                connector_endpoint = connector_config.get('endpoint')

                                # Send Kafka indexing event
                                index_event = {
                                    "orgId": org_id,
                                    "recordId": file_key,
                                    "recordName": record.get('recordName'),
                                    "recordVersion": 0,
                                    "recordType": record.get('recordType'),
                                    'eventType': EventTypes.NEW_RECORD.value,
                                    "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/{user_id}/drive/record/{file_key}/signedUrl",
                                    "metadataRoute": f"/api/v1/drive/files/{file_key}/metadata",
                                    "connectorName": Connectors.GOOGLE_DRIVE.value,
                                    "origin": OriginTypes.CONNECTOR.value,
                                    "createdAtSourceTimestamp": int(parse_timestamp(file_metadata.get('createdTime')).timestamp()),
                                    "modifiedAtSourceTimestamp": int(parse_timestamp(file_metadata.get('modifiedTime')).timestamp()),
                                    "extension": file.get('extension'),
                                    "mimeType": file.get('mimeType')
                                }
                                await self.kafka_service.send_event_to_kafka(index_event)
                                total_processed += 1

                    # Update drive status after completion
                    await self.arango_service.update_drive_sync_state(
                        drive_key,
                        'COMPLETED'
                    )

                except Exception as e:
                    logger.error(f"❌ Failed to process drive {drive_key}: {str(e)}")
                    continue

            # Update user state to COMPLETED
            await self.arango_service.update_user_sync_state(
                user_email,
                'COMPLETED',
                service_type=Connectors.GOOGLE_DRIVE.value
            )
            logger.info(f"✅ Successfully completed sync for user {user_email}")
            return True

        except Exception as e:
            await self.arango_service.update_user_sync_state(user_email, 'FAILED', service_type=Connectors.GOOGLE_DRIVE.value)
            logger.error(f"❌ Failed to sync user {user_email}: {str(e)}")
            return False


class DriveSyncIndividualService(BaseDriveSyncService):
    """Sync service for individual user setup"""

    def __init__(
        self,
        config: ConfigurationService,
        drive_user_service: DriveUserService,
        arango_service: ArangoService,
        change_handler,
        kafka_service: KafkaService,
        celery_app
    ):
        super().__init__(config, arango_service,
                         change_handler, kafka_service, celery_app)
        self.drive_user_service = drive_user_service

    async def connect_services(self, org_id: str) -> bool:
        """Connect to services for individual setup"""
        try:
            logger.info("🚀 Connecting to individual user services")
            
            user_info = await self.arango_service.get_users(org_id, active=True)
            if user_info:
                # Add sync state to user info                
                user_id = user_info[0]['userId']
                
            # Connect to Google Drive
            if not await self.drive_user_service.connect_individual_user(org_id, user_id):
                raise Exception("Failed to connect to Drive API")

            # Connect to ArangoDB and Redis
            if not await self.arango_service.connect():
                raise Exception("Failed to connect to ArangoDB")

            logger.info("✅ Individual user services connected successfully")
            return True
        except Exception as e:
            logger.error("❌ Individual service connection failed: %s", str(e))
            return False

    async def initialize(self, org_id) -> bool:
        """Initialize individual user sync service"""
        try:
            if not await self.connect_services(org_id):
                return False

            # Get and store user info with initial sync state
            user_info = await self.drive_user_service.list_individual_user(org_id)
            if user_info:
                # Add sync state to user info                
                user_id = await self.arango_service.get_entity_id_by_email(user_info[0]['email'])
                if not user_id:
                    await self.arango_service.batch_upsert_nodes(user_info, collection=CollectionNames.USERS.value)
                user_info = user_info[0]

            # Initialize Celery
            await self.celery_app.setup_app()

            # Check if sync is already running
            sync_state = await self.arango_service.get_user_sync_state(user_info['email'], Connectors.GOOGLE_DRIVE.value)
            current_state = sync_state.get('syncState') if sync_state else 'NOT_STARTED'
            
            if current_state == 'IN_PROGRESS':
                logger.warning(f"Sync is currently RUNNING for user {user_info['email']}. Pausing it.")
                await self.arango_service.update_user_sync_state(
                    user_info['email'],
                    'PAUSED',
                    service_type=Connectors.GOOGLE_DRIVE.value
                )
            # Phase 1: Set up changes.watch for each user
            logger.info("👀 Setting up changes watch for all users...")

            # Set up changes watch for each user
            if user_info:
                try:
                    user_service = self.drive_user_service
                    channel_data = await self.setup_changes_watch(user_service)
                    if not channel_data:
                        logger.warning(
                            "❌ Failed to set up changes watch for user: %s", user_info['email'])
                        return False

                    logger.info(
                        "✅ Changes watch set up successfully for user: %s", user_info['email'])

                    await self.arango_service.store_page_token(
                        channel_data['channel_id'], 
                        channel_data['resource_id'], 
                        user_info['email'], 
                        channel_data['page_token']
                    )
                    
                    changes, new_token = await user_service.get_changes(channel_data['page_token'])
                    logger.info("🚀 Found %s changes since page token: %s", len(changes), channel_data['page_token'])
                    logger.info("Changes: %s", changes)
                    logger.info("🚀 New token: %s", new_token)
                except Exception as e:
                    logger.error(
                        "❌ Error setting up changes watch for user %s: %s", user_info['email'], str(e))
                    return False

            logger.info("✅ Sync service initialized successfully")
            return True

        except Exception as e:
            logger.error("❌ Failed to initialize individual sync: %s", str(e))
            return False

    async def perform_initial_sync(self, org_id, action: str = "start") -> bool:
        """First phase: Build complete drive structure using batch operations"""
        try:
            if await self._should_stop(org_id):
                logger.info("Sync stopped before starting")
                return False

            user = await self.arango_service.get_users(org_id, active=True)
            user = user[0]

            # Update user sync state to RUNNING
            await self.arango_service.update_user_sync_state(
                user['email'],
                'IN_PROGRESS',
                service_type=Connectors.GOOGLE_DRIVE.value
            )

            if await self._should_stop(org_id):
                logger.info("Sync stopped during user %s processing", user['email'])
                await self.arango_service.update_user_sync_state(
                    user['email'],
                    'PAUSED',
                    service_type=Connectors.GOOGLE_DRIVE.value
                )
                return False

            user_service = self.drive_user_service

            # Initialize workers and get drive list
            await self.initialize_workers(user_service)

            # Process each drive
            for drive_id, worker in self.drive_workers.items():
                
                # Get drive details
                drive_info = await user_service.get_drive_info(drive_id, org_id)
                if not drive_info:
                    logger.warning("❌ Failed to get drive info for drive %s", drive_id)
                    continue
                
                drive_key = drive_info.get('drive').get('_key')

                if await self._should_stop(org_id):
                    logger.info("Sync stopped during drive %s processing", drive_key)
                    await self.arango_service.update_drive_sync_state(
                        drive_key,
                        'PAUSED'
                    )
                    return False

                # Check drive state first
                drive_state = await self.arango_service.get_drive_sync_state(drive_key)
                if drive_state == 'COMPLETED':
                    logger.info("Drive %s is already completed, skipping", drive_key)
                    continue

                try:
                    # Process drive data
                    if not await self.process_drive_data(drive_info, user):
                        logger.error("❌ Failed to process drive data for drive %s", drive_key)
                        continue

                    # Update drive state to RUNNING
                    await self.arango_service.update_drive_sync_state(
                        drive_key,
                        'IN_PROGRESS'
                    )

                    # Get file list
                    files = await user_service.list_files_in_folder(drive_id)
                    if not files:
                        continue

                    # Process files in batches
                    batch_size = 50
                    total_processed = 0

                    for i in range(0, len(files), batch_size):
                        if await self._should_stop(org_id):
                            logger.info("Sync stopped during batch processing at index %s", i)
                            await self.arango_service.update_drive_sync_state(
                                drive_key,
                                'PAUSED'
                            )
                            return False

                        batch = files[i:i + batch_size]
                        batch_file_ids = [f['id'] for f in batch]
                        batch_metadata = await user_service.batch_fetch_metadata_and_permissions(batch_file_ids, files=files)

                        if not await self.process_batch(batch_metadata, org_id):
                            continue

                        # Process each file in the batch
                        for file_id in batch_file_ids:
                            total_processed += 1
                            file_metadata = next(
                                (meta for meta in batch_metadata if meta['id'] == file_id),
                                None
                            )
                            if file_metadata:
                                file_id = file_metadata.get('id')

                                file_key = await self.arango_service.get_key_by_external_file_id(file_id)
                                record = await self.arango_service.get_document(file_key, CollectionNames.RECORDS.value)
                                file = await self.arango_service.get_document(file_key, CollectionNames.FILES.value)

                                record_version = 0  # Initial version for new files
                                extension = file.get('extension')
                                mime_type = file.get('mimeType')
                                user_id = user['userId']
                                
                                connector_config = await self.config_service.get_config(config_node_constants.CONNECTORS_SERVICE.value)
                                connector_endpoint = connector_config.get('endpoint')
                                                    
                                index_event = {
                                    "orgId": org_id,
                                    "recordId": file_key,
                                    "recordName": record.get('recordName'),
                                    "recordVersion": record_version,
                                    "recordType": record.get('recordType'),
                                    'eventType': EventTypes.NEW_RECORD.value,
                                    "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/{user_id}/drive/record/{file_key}/signedUrl",
                                    "metadataRoute": f"/api/v1/drive/files/{file_key}/metadata",
                                    "connectorName": Connectors.GOOGLE_DRIVE.value,
                                    "origin": OriginTypes.CONNECTOR.value,
                                    "createdAtSourceTimestamp": int(parse_timestamp(file_metadata.get('createdTime')).timestamp()),
                                    "modifiedAtSourceTimestamp": int(parse_timestamp(file_metadata.get('modifiedTime')).timestamp()),
                                    "extension": extension,
                                    "mimeType": mime_type
                                }

                                await self.kafka_service.send_event_to_kafka(
                                    index_event)
                                logger.info(
                                    "📨 Sent Kafka Indexing event for file %s: %s", file_id, index_event)

                    # Update drive status after completion
                    await self.arango_service.update_drive_sync_state(
                        drive_key,
                        'COMPLETED'
                    )

                except Exception as e:
                    logger.error(f"❌ Failed to process drive {drive_key}: {str(e)}")
                    continue

            # Update user state to COMPLETED
            await self.arango_service.update_user_sync_state(
                user['email'],
                'COMPLETED',
                service_type=Connectors.GOOGLE_DRIVE.value
            )

            self.is_completed = True
            return True

        except Exception as e:
            # Update user state to FAILED
            if user:
                await self.arango_service.update_user_sync_state(
                    user['email'],
                    'FAILED',
                    service_type=Connectors.GOOGLE_DRIVE.value
                )
            logger.error(f"❌ Initial sync failed: {str(e)}")
            return False
