"""Base and specialized sync services for Gmail and Calendar synchronization"""

# pylint: disable=E1101, W0718, W0719
import asyncio
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Dict, Optional

from app.config.configuration_service import ConfigurationService, config_node_constants
from app.config.utils.named_constants.arangodb_constants import (
    CollectionNames,
    Connectors,
    EventTypes,
    OriginTypes,
    RecordRelations,
    RecordTypes,
)
from app.connectors.core.kafka_service import KafkaService
from app.connectors.google.admin.google_admin_service import GoogleAdminService
from app.connectors.google.core.arango_service import ArangoService
from app.connectors.google.gmail.core.gmail_user_service import GmailUserService
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class GmailSyncProgress:
    """Class to track sync progress"""

    def __init__(self):
        self.total_files = 0
        self.processed_files = 0
        self.percentage = 0
        self.status = "initializing"
        self.lastUpdatedTimestampAtSource = get_epoch_timestamp_in_ms()


class BaseGmailSyncService(ABC):
    """Abstract base class for sync services"""

    def __init__(
        self,
        logger,
        config: ConfigurationService,
        arango_service: ArangoService,
        change_handler,
        kafka_service: KafkaService,
        celery_app,
    ):
        self.logger = logger
        self.config_service = config
        self.arango_service = arango_service
        self.kafka_service = kafka_service
        self.celery_app = celery_app
        self.change_handler = change_handler
        # Common state
        self.drive_workers = {}
        self.progress = GmailSyncProgress()
        self._current_batch = None
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._stop_requested = False

        # Locks
        self._sync_lock = asyncio.Lock()
        self._transition_lock = asyncio.Lock()
        self._worker_lock = asyncio.Lock()
        self._progress_lock = asyncio.Lock()

        # Configuration
        self._hierarchy_version = 0
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
    async def perform_initial_sync(
        self, org_id, action: str = "start", resume_hierarchy: Dict = None
    ) -> bool:
        """Perform initial sync"""
        pass

    @abstractmethod
    async def resync_gmail(self, org_id, user):
        """Resync a user's Google Gmail"""
        pass

    async def start(self, org_id) -> bool:
        self.logger.info("🚀 Starting Gmail sync, Action: start")
        async with self._transition_lock:
            try:
                # Get current user
                users = await self.arango_service.get_users(org_id=org_id)

                for user in users:
                    # Check current state using get_user_sync_state
                    sync_state = await self.arango_service.get_user_sync_state(
                        user["email"], Connectors.GOOGLE_MAIL.value
                    )
                    current_state = (
                        sync_state.get("syncState") if sync_state else "NOT_STARTED"
                    )

                    if current_state == "IN_PROGRESS":
                        self.logger.warning("💥 Gmail sync service is already running")
                        return False

                    if current_state == "PAUSED":
                        self.logger.warning(
                            "💥 Gmail sync is paused, use resume to continue"
                        )
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

                self.logger.info("✅ Gmail sync service started")
                return True

            except Exception as e:
                self.logger.error(f"❌ Failed to start Gmail sync service: {str(e)}")
                return False

    async def pause(self, org_id) -> bool:
        self.logger.info("⏸️ Pausing Gmail sync service")
        async with self._transition_lock:
            try:
                users = await self.arango_service.get_users(org_id=org_id)
                for user in users:

                    # Check current state using get_user_sync_state
                    sync_state = await self.arango_service.get_user_sync_state(
                        user["email"], Connectors.GOOGLE_MAIL.value
                    )
                    current_state = (
                        sync_state.get("syncState") if sync_state else "NOT_STARTED"
                    )

                    if current_state != "IN_PROGRESS":
                        self.logger.warning("💥 Gmail sync service is not running")
                        return False

                    self._stop_requested = True

                    # Update state in Arango
                    await self.arango_service.update_user_sync_state(
                        user["email"], "PAUSED", Connectors.GOOGLE_MAIL.value
                    )

                    # Cancel current sync task
                    if self._sync_task and not self._sync_task.done():
                        self._sync_task.cancel()
                        try:
                            await self._sync_task
                        except asyncio.CancelledError:
                            pass

                self.logger.info("✅ Gmail sync service paused")
                return True

            except Exception as e:
                self.logger.error(f"❌ Failed to pause Gmail sync service: {str(e)}")
                return False

    async def resume(self, org_id) -> bool:
        self.logger.info("🔄 Resuming Gmail sync service")
        async with self._transition_lock:
            try:
                users = await self.arango_service.get_users(org_id=org_id)
                for user in users:

                    # Check current state using get_user_sync_state
                    sync_state = await self.arango_service.get_user_sync_state(
                        user["email"], Connectors.GOOGLE_MAIL.value
                    )
                    if not sync_state:
                        self.logger.warning("⚠️ No user found, starting fresh")
                        return await self.start(org_id)

                    current_state = sync_state.get("syncState")
                    if current_state == "IN_PROGRESS":
                        self.logger.warning("💥 Gmail sync service is already running")
                        return False

                    if current_state != "PAUSED":
                        self.logger.warning(
                            "💥 Gmail sync was not paused, use start instead"
                        )
                        return False

                    self._pause_event.set()
                    self._stop_requested = False

                # Start sync with resume state
                self._sync_task = asyncio.create_task(
                    self.perform_initial_sync(org_id, action="resume")
                )

                self.logger.info("✅ Gmail sync service resumed")
                return True

            except Exception as e:
                self.logger.error(f"❌ Failed to resume Gmail sync service: {str(e)}")
                return False

    async def _should_stop(self, org_id) -> bool:
        """Check if operation should stop"""
        if self._stop_requested:
            # Get current user
            users = await self.arango_service.get_users(org_id=org_id)
            for user in users:
                current_state = await self.arango_service.get_user_sync_state(
                    user["email"], Connectors.GOOGLE_MAIL.value
                )
                if current_state:
                    current_state = current_state.get("syncState")
                    if current_state == "IN_PROGRESS":
                        await self.arango_service.update_user_sync_state(
                            user["email"], "PAUSED", Connectors.GOOGLE_MAIL.value
                        )
                        self.logger.info("✅ Gmail sync state updated before stopping")
                        return True
            return False
        return False

    async def process_batch(self, metadata_list, org_id):
        """Process a single batch with atomic operations"""
        self.logger.info(
            "🚀 Starting batch processing with %d items", len(metadata_list)
        )
        batch_start_time = datetime.now(timezone.utc)

        try:
            if await self._should_stop(org_id):
                self.logger.info("⏹️ Stop requested, halting batch processing")
                return False

            async with self._sync_lock:
                self.logger.debug("🔒 Acquired sync lock for batch processing")
                # Prepare nodes and edges for batch processing
                messages = []
                attachments = []
                is_of_type = []
                records = []
                permissions = []
                recordRelations = []
                existing_messages = []
                existing_attachments = []

                self.logger.debug(
                    "📊 Processing metadata list of size: %d", len(metadata_list)
                )
                for metadata in metadata_list:
                    # self.logger.debug(
                    #     "📝 Starting metadata processing: %s", metadata)
                    thread_metadata = metadata["thread"]
                    messages_metadata = metadata["messages"]
                    attachments_metadata = metadata["attachments"]
                    permissions_metadata = metadata["permissions"]

                    self.logger.debug(
                        "📨 Messages in current metadata: %d", len(messages_metadata)
                    )
                    self.logger.debug(
                        "📎 Attachments in current metadata: %d",
                        len(attachments_metadata),
                    )

                    if not thread_metadata:
                        self.logger.warning("❌ No metadata found for thread, skipping")
                        continue

                    thread_id = thread_metadata["id"]
                    self.logger.debug("🧵 Processing thread ID: %s", thread_id)
                    if not thread_id:
                        self.logger.warning(
                            "❌ No thread ID found for thread, skipping"
                        )
                        continue

                    # Process messages
                    self.logger.debug(
                        "📨 Processing %d messages for thread %s",
                        len(messages_metadata),
                        thread_id,
                    )

                    # Sort messages by internalDate to identify the first message in thread
                    sorted_messages = sorted(
                        messages_metadata,
                        key=lambda x: int(x["message"].get("internalDate", 0)),
                    )

                    previous_message_key = (
                        None  # Track previous message to create chain
                    )

                    for i, message_data in enumerate(sorted_messages):
                        message = message_data["message"]
                        message_id = message["id"]
                        self.logger.debug("📝 Processing message: %s", message_id)
                        headers = message.get("headers", {})
                        self.logger.debug("📝 Processing headers: %s", headers)

                        subject = headers.get("Subject", "No Subject")
                        date = headers.get("Date", None)
                        from_email = headers.get("From", [""])[0]
                        to_email = headers.get("To", [])
                        cc_email = headers.get("Cc", [])
                        bcc_email = headers.get("Bcc", [])
                        message_id_header = headers.get("Message-ID", None)

                        # Check if message exists
                        self.logger.debug(
                            "🔍 Checking if message %s exists in ArangoDB", message_id
                        )
                        existing_message = self.arango_service.db.aql.execute(
                            f"FOR doc IN {CollectionNames.RECORDS.value} FILTER doc.externalRecordId == @message_id RETURN doc",
                            bind_vars={"message_id": message_id},
                        )
                        existing_message = next(existing_message, None)

                        if existing_message:
                            self.logger.debug(
                                "♻️ Message %s already exists in ArangoDB", message_id
                            )
                            existing_messages.append(message_id)
                            # Keep track of previous message key for chain
                            previous_message_key = existing_message["_key"]
                        else:
                            self.logger.debug(
                                "➕ Creating new message record for %s", message_id
                            )
                            message_record = {
                                "_key": str(uuid.uuid4()),
                                "threadId": thread_id,
                                "isParent": i
                                == 0,  # First message in sorted list is parent
                                "internalDate": message.get("internalDate"),
                                "subject": subject,
                                "date": date,
                                "from": from_email,
                                "to": to_email,
                                "cc": cc_email,
                                "bcc": bcc_email,
                                "messageIdHeader": message_id_header,
                                # Move thread history to message
                                "historyId": thread_metadata.get("historyId"),
                                "webUrl": f"https://mail.google.com/mail?authuser={{user.email}}#all/{message_id}",
                                "labelIds": message.get("labelIds", []),
                            }
                            self.logger.debug("📝 Message record: %s", message_record)

                            record = {
                                "_key": message_record["_key"],
                                "orgId": org_id,
                                "recordName": subject,
                                "externalRecordId": message_id,
                                "externalRevisionId": None,
                                "recordType": RecordTypes.MAIL.value,
                                "version": 0,
                                "origin": OriginTypes.CONNECTOR.value,
                                "connectorName": Connectors.GOOGLE_MAIL.value,
                                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                                "lastSyncTimestamp": get_epoch_timestamp_in_ms(),
                                "sourceCreatedAtTimestamp": (
                                    int(message.get("internalDate"))
                                    if message.get("internalDate")
                                    else None
                                ),
                                "sourceLastModifiedTimestamp": (
                                    int(message.get("internalDate"))
                                    if message.get("internalDate")
                                    else None
                                ),
                                "isDeleted": False,
                                "isArchived": False,
                                "lastIndexTimestamp": None,
                                "lastExtractionTimestamp": None,
                                "indexingStatus": "NOT_STARTED",
                                "extractionStatus": "NOT_STARTED",
                                "isLatestVersion": True,
                                "isDirty": False,
                                "reason": None,
                            }

                            # Create is_of_type edge
                            is_of_type_record = {
                                "_from": f'records/{message_record["_key"]}',
                                "_to": f'message/{message_record["_key"]}',
                                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                            }

                            messages.append(message_record)
                            records.append(record)
                            is_of_type.append(is_of_type_record)
                            self.logger.debug(
                                "✅ Message record created: %s", message_record
                            )

                            # Create PARENT_CHILD relationship in thread if not first message
                            if previous_message_key:
                                self.logger.debug(
                                    "🔗 Creating PARENT_CHILD relation between messages in thread"
                                )
                                recordRelations.append(
                                    {
                                        "_from": f"records/{previous_message_key}",
                                        "_to": f'records/{message_record["_key"]}',
                                        "relationType": RecordRelations.SIBLING.value,
                                    }
                                )

                            # Update previous message key for next iteration
                            previous_message_key = message_record["_key"]

                    # Process attachments
                    self.logger.debug(
                        "📎 Processing %d attachments", len(attachments_metadata)
                    )
                    for attachment in attachments_metadata:
                        attachment_id = attachment["attachment_id"]
                        message_id = attachment.get("message_id")
                        self.logger.debug(
                            "📎 Processing attachment %s for message %s",
                            attachment_id,
                            message_id,
                        )

                        # Check if attachment exists
                        self.logger.debug(
                            "🔍 Checking if attachment %s exists in ArangoDB",
                            attachment_id,
                        )
                        existing_attachment = self.arango_service.db.aql.execute(
                            "FOR doc IN records FILTER doc.externalRecordId == @attachment_id RETURN doc",
                            bind_vars={"attachment_id": attachment_id},
                        )
                        existing_attachment = next(existing_attachment, None)

                        if existing_attachment:
                            self.logger.debug(
                                "♻️ Attachment %s already exists in ArangoDB",
                                attachment_id,
                            )
                            existing_attachments.append(attachment_id)
                        else:
                            self.logger.debug(
                                "➕ Creating new attachment record for %s",
                                attachment_id,
                            )
                            attachment_record = {
                                "_key": str(uuid.uuid4()),
                                "orgId": org_id,
                                "name": attachment.get("filename"),
                                "isFile": True,
                                "messageId": message_id,
                                "mimeType": attachment.get("mimeType"),
                                "extension": attachment.get("extension"),
                                "sizeInBytes": int(attachment.get("size", 0)),
                                "webUrl": f"https://mail.google.com/mail?authuser={{user.email}}#all/{message_id}",
                            }
                            record = {
                                "_key": attachment_record["_key"],
                                "orgId": org_id,
                                "recordName": attachment.get("filename"),
                                "recordType": RecordTypes.FILE.value,
                                "version": 0,
                                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                                "sourceCreatedAtTimestamp": (
                                    int(attachment.get("internalDate"))
                                    if attachment.get("internalDate")
                                    else None
                                ),
                                "sourceLastModifiedTimestamp": (
                                    int(attachment.get("internalDate"))
                                    if attachment.get("internalDate")
                                    else None
                                ),
                                "externalRecordId": attachment_id,
                                "externalRevisionId": None,
                                "origin": OriginTypes.CONNECTOR.value,
                                "connectorName": Connectors.GOOGLE_MAIL.value,
                                "lastSyncTimestamp": get_epoch_timestamp_in_ms(),
                                "isDeleted": False,
                                "isArchived": False,
                                "indexingStatus": "NOT_STARTED",
                                "extractionStatus": "NOT_STARTED",
                                "lastIndexTimestamp": None,
                                "lastExtractionTimestamp": None,
                                "isLatestVersion": True,
                                "isDirty": False,
                                "reason": None,
                            }

                            # Create is_of_type edge
                            is_of_type_record = {
                                "_from": f'records/{attachment_record["_key"]}',
                                "_to": f'files/{attachment_record["_key"]}',
                                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                            }

                            attachments.append(attachment_record)
                            records.append(record)
                            is_of_type.append(is_of_type_record)
                            self.logger.debug(
                                "✅ Attachment record created: %s", attachment_record
                            )

                            # Create record relation
                            message_key = next(
                                (
                                    m["_key"]
                                    for m in records
                                    if m["externalRecordId"] == message_id
                                ),
                                None,
                            )
                            if message_key:
                                self.logger.debug(
                                    "🔗 Creating relation between message %s and attachment %s",
                                    message_id,
                                    attachment_id,
                                )
                                recordRelations.append(
                                    {
                                        "_from": f"records/{message_key}",
                                        "_to": f'records/{attachment_record["_key"]}',
                                        "relationType": RecordRelations.ATTACHMENT.value,
                                    }
                                )
                            else:
                                self.logger.warning(
                                    "⚠️ Could not find message key for attachment relation: %s -> %s",
                                    message_id,
                                    attachment_id,
                                )

                    self.logger.debug("🔒 Processing permissions")
                    for permission in permissions_metadata:
                        message_id = permission.get("messageId")
                        attachment_ids = permission.get("attachmentIds", [])
                        emails = permission.get("users", [])
                        role = permission.get("role").upper()
                        self.logger.debug(
                            "Processing permission for message %s, users/groups %s",
                            message_id,
                            emails,
                        )

                        # Get the correct message_key from messages based on messageId
                        message_key = next(
                            (
                                m["_key"]
                                for m in records
                                if m["externalRecordId"] == message_id
                            ),
                            None,
                        )
                        if message_key:
                            self.logger.debug(
                                "🔗 Creating relation between users/groups and message %s",
                                message_id,
                            )
                            for email in emails:
                                entity_id = (
                                    await self.arango_service.get_entity_id_by_email(
                                        email
                                    )
                                )
                                if entity_id:
                                    # Check if entity exists in users or groups
                                    if self.arango_service.db.collection(
                                        CollectionNames.USERS.value
                                    ).has(entity_id):
                                        entityType = CollectionNames.USERS.value
                                        permType = "USER"
                                    elif self.arango_service.db.collection(
                                        CollectionNames.GROUPS.value
                                    ).has(entity_id):
                                        entityType = CollectionNames.GROUPS.value
                                        permType = "GROUP"
                                else:
                                    # Save entity in people collection
                                    entityType = CollectionNames.PEOPLE.value
                                    entity_id = str(uuid.uuid4())
                                    permType = "USER"
                                    await self.arango_service.save_to_people_collection(
                                        entity_id, email
                                    )

                                permissions.append(
                                    {
                                        "_from": f"{entityType}/{entity_id}",
                                        "_to": f"records/{message_key}",
                                        "role": role,
                                        "externalPermissionId": None,
                                        "type": permType,
                                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                                        "lastUpdatedTimestampAtSource": get_epoch_timestamp_in_ms(),
                                    }
                                )
                        else:
                            self.logger.warning(
                                "⚠️ Could not find message key for permission relation: message %s",
                                message_id,
                            )

                        # Process permissions for attachments
                        for attachment_id in attachment_ids:
                            self.logger.debug(
                                "🔗 Processing permission for attachment %s",
                                attachment_id,
                            )
                            attachment_key = next(
                                (
                                    a["_key"]
                                    for a in records
                                    if a["externalRecordId"] == attachment_id
                                ),
                                None,
                            )
                            if attachment_key:
                                self.logger.debug(
                                    "🔗 Creating relation between users/groups and attachment %s",
                                    attachment_id,
                                )
                                for email in emails:
                                    entity_id = await self.arango_service.get_entity_id_by_email(
                                        email
                                    )
                                    if entity_id:
                                        # Check if entity exists in users or groups
                                        if self.arango_service.db.collection(
                                            CollectionNames.USERS.value
                                        ).has(entity_id):
                                            entityType = CollectionNames.USERS.value
                                            permType = "USER"
                                        elif self.arango_service.db.collection(
                                            CollectionNames.GROUPS.value
                                        ).has(entity_id):
                                            entityType = CollectionNames.GROUPS.value
                                            permType = "GROUP"
                                        else:
                                            permType = "USER"
                                            continue

                                        permissions.append(
                                            {
                                                "_from": f"{entityType}/{entity_id}",
                                                "_to": f"records/{attachment_key}",
                                                "role": role,
                                                "externalPermissionId": None,
                                                "type": permType,
                                                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                                                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                                                "lastUpdatedTimestampAtSource": get_epoch_timestamp_in_ms(),
                                            }
                                        )
                            else:
                                self.logger.warning(
                                    "⚠️ Could not find attachment key for permission relation: attachment %s",
                                    attachment_id,
                                )

                # Batch process all collected data
                self.logger.info("📊 Batch summary before processing:")
                self.logger.info("- New messages to create: %d", len(messages))
                self.logger.info("- New attachments to create: %d", len(attachments))
                self.logger.info("- New relations to create: %d", len(recordRelations))
                self.logger.info(
                    "- Existing messages skipped: %d", len(existing_messages)
                )
                self.logger.info(
                    "- Existing attachments skipped: %d", len(existing_attachments)
                )

                if messages or attachments:
                    try:
                        self.logger.debug("🔄 Starting database transaction")
                        txn = None
                        txn = self.arango_service.db.begin_transaction(
                            read=[
                                CollectionNames.MAILS.value,
                                CollectionNames.RECORDS.value,
                                CollectionNames.FILES.value,
                                CollectionNames.RECORD_RELATIONS.value,
                                CollectionNames.PERMISSIONS.value,
                                CollectionNames.IS_OF_TYPE.value,
                            ],
                            write=[
                                CollectionNames.MAILS.value,
                                CollectionNames.RECORDS.value,
                                CollectionNames.FILES.value,
                                CollectionNames.RECORD_RELATIONS.value,
                                CollectionNames.PERMISSIONS.value,
                                CollectionNames.IS_OF_TYPE.value,
                            ],
                        )

                        if messages:
                            self.logger.debug("📥 Upserting %d messages", len(messages))
                            if not await self.arango_service.batch_upsert_nodes(
                                messages,
                                collection=CollectionNames.MAILS.value,
                                transaction=txn,
                            ):
                                raise Exception("Failed to batch upsert messages")
                            self.logger.debug("✅ Messages upserted successfully")

                        if attachments:
                            # Create a copy of attachments without messageId
                            attachment_docs = []
                            for attachment in attachments:
                                attachment_doc = attachment.copy()
                                attachment_doc.pop(
                                    "messageId", None
                                )  # Remove messageId if it exists
                                attachment_docs.append(attachment_doc)

                            self.logger.debug(
                                "📥 Upserting %d attachments", len(attachment_docs)
                            )
                            if not await self.arango_service.batch_upsert_nodes(
                                attachment_docs,
                                collection=CollectionNames.FILES.value,
                                transaction=txn,
                            ):
                                raise Exception("Failed to batch upsert attachments")
                            self.logger.debug("✅ Attachments upserted successfully")

                        if records:
                            self.logger.debug("📥 Upserting %d records", len(records))
                            if not await self.arango_service.batch_upsert_nodes(
                                records,
                                collection=CollectionNames.RECORDS.value,
                                transaction=txn,
                            ):
                                raise Exception("Failed to batch upsert records")
                            self.logger.debug("✅ Records upserted successfully")

                        if recordRelations:
                            self.logger.debug(
                                "🔗 Creating %d record relations", len(recordRelations)
                            )
                            if not await self.arango_service.batch_create_edges(
                                recordRelations,
                                collection=CollectionNames.RECORD_RELATIONS.value,
                                transaction=txn,
                            ):
                                raise Exception("Failed to batch create relations")
                            self.logger.debug(
                                "✅ Record relations created successfully"
                            )

                        if is_of_type:
                            self.logger.debug(
                                "🔗 Creating %d is_of_type relations", len(is_of_type)
                            )
                            if not await self.arango_service.batch_create_edges(
                                is_of_type,
                                collection=CollectionNames.IS_OF_TYPE.value,
                                transaction=txn,
                            ):
                                raise Exception(
                                    "Failed to batch create is_of_type relations"
                                )
                            self.logger.debug(
                                "✅ is_of_type relations created successfully"
                            )

                        if permissions:
                            self.logger.debug(
                                "🔗 Creating %d permissions", len(permissions)
                            )

                            if not await self.arango_service.batch_create_edges(
                                permissions,
                                collection=CollectionNames.PERMISSIONS.value,
                                transaction=txn,
                            ):
                                raise Exception("Failed to batch create permissions")
                            self.logger.debug("✅ Permissions created successfully")

                        self.logger.debug("✅ Committing transaction")
                        txn.commit_transaction()

                        txn = None

                        processing_time = datetime.now(timezone.utc) - batch_start_time
                        self.logger.info(
                            """
                        ✅ Batch processed successfully:
                        - Messages: %d
                        - Attachments: %d
                        - Relations: %d
                        - Processing Time: %s
                        """,
                            len(messages),
                            len(attachments),
                            len(recordRelations),
                            processing_time,
                        )

                        return True

                    except Exception as e:
                        if txn:
                            self.logger.error(
                                "❌ Transaction failed, rolling back: %s", str(e)
                            )
                            txn.abort_transaction()
                        self.logger.error("❌ Failed to process batch data: %s", str(e))
                        return False

                self.logger.info(
                    "✅ Batch processing completed with no new data to process"
                )
                return True

        except Exception as e:
            self.logger.error("❌ Batch processing failed with error: %s", str(e))
            return False


class GmailSyncEnterpriseService(BaseGmailSyncService):
    """Sync service for enterprise setup using admin service"""

    def __init__(
        self,
        logger,
        config: ConfigurationService,
        gmail_admin_service: GoogleAdminService,
        arango_service: ArangoService,
        change_handler,
        kafka_service: KafkaService,
        celery_app,
    ):
        super().__init__(
            logger, config, arango_service, change_handler, kafka_service, celery_app
        )
        self.gmail_admin_service = gmail_admin_service

    async def connect_services(self, org_id: str) -> bool:
        """Connect to services for enterprise setup"""
        try:
            self.logger.info("🚀 Connecting to enterprise services")

            # Connect to Google Drive Admin
            if not await self.gmail_admin_service.connect_admin(org_id):
                raise Exception("Failed to connect to Drive Admin API")

            self.logger.info("✅ Enterprise services connected successfully")
            return True

        except Exception as e:
            self.logger.error("❌ Enterprise service connection failed: %s", str(e))
            return False

    async def setup_changes_watch(self, org_id: str, user_email: str) -> Optional[Dict]:
        """Set up changes.watch after initial sync"""
        try:
            # Set up watch
            user_service = await self.gmail_admin_service.create_gmail_user_service(
                user_email
            )
            self.logger.info("👀 Setting up changes watch for user %s", user_email)
            channel_history = await self.arango_service.get_channel_history_id(
                user_email
            )
            if not channel_history:
                self.logger.info(
                    "🚀 Creating new changes watch for user %s", user_email
                )
                watch = await user_service.create_gmail_user_watch()
                if not watch:
                    self.logger.warning(
                        "Changes watch not created for user: %s", user_email
                    )
                    return None
                return watch

            current_timestamp = get_epoch_timestamp_in_ms()
            expiration_timestamp = channel_history.get("expiration", 0)
            self.logger.info("Current time: %s", current_timestamp)
            self.logger.info("Page token expiration: %s", expiration_timestamp)
            if expiration_timestamp < current_timestamp:
                self.logger.info("⚠️ Page token expired for user %s", user_email)
                await user_service.stop_gmail_user_watch()

                watch = await user_service.create_gmail_user_watch()
                if not watch:
                    self.logger.warning(
                        "Changes watch not created for user: %s", user_email
                    )
                    return None
                return watch
            self.logger.info(
                "✅ Changes watch set up successfully for user: %s", user_email
            )
            return channel_history

        except Exception as e:
            self.logger.error("Failed to set up changes watch: %s", str(e))
            return None

    async def stop_changes_watch(self, user_email: str) -> bool:
        """Stop changes watch"""
        try:
            user_service = await self.gmail_admin_service.create_gmail_user_service(
                user_email
            )
            stopped = await user_service.stop_gmail_user_watch(user_email)
            return stopped
        except Exception as e:
            self.logger.error("Failed to stop changes watch: %s", str(e))
            return False

    async def initialize(self, org_id) -> bool:
        """Initialize enterprise sync service"""
        try:
            self.logger.info("🚀 Initializing")
            if not await self.connect_services(org_id):
                return False

            # List and store enterprise users
            users = await self.gmail_admin_service.list_enterprise_users(org_id)
            if users:
                self.logger.info("🚀 Found %s users", len(users))

                for user in users:
                    # Add sync state to user info
                    user_id = await self.arango_service.get_entity_id_by_email(
                        user["email"]
                    )
                    if not user_id:
                        await self.arango_service.batch_upsert_nodes(
                            [user], collection=CollectionNames.USERS.value
                        )

            # List and store groups
            groups = await self.gmail_admin_service.list_groups(org_id)
            if groups:
                self.logger.info("🚀 Found %s groups", len(groups))

                for group in groups:
                    group_id = await self.arango_service.get_entity_id_by_email(
                        group["email"]
                    )
                    if not group_id:
                        self.logger.info("New group Found!")
                        await self.arango_service.batch_upsert_nodes(
                            [group], collection=CollectionNames.GROUPS.value
                        )

            # Create relationships between users and groups in belongsTo collection
            belongs_to_group_relations = []
            for group in groups:
                try:
                    # Get group members for each group
                    group_members = await self.gmail_admin_service.list_group_members(
                        group["email"]
                    )

                    for member in group_members:
                        # Find the matching user
                        matching_user = next(
                            (
                                user
                                for user in users
                                if user["email"] == member["email"]
                            ),
                            None,
                        )

                        if matching_user:
                            # Check if the relationship already exists
                            existing_relation = (
                                await self.arango_service.check_edge_exists(
                                    f'users/{matching_user["_key"]}',
                                    f'groups/{group["_key"]}',
                                    CollectionNames.BELONGS_TO.value,
                                )
                            )
                            if not existing_relation:
                                relation = {
                                    "_from": f'users/{matching_user["_key"]}',
                                    "_to": f'groups/{group["_key"]}',
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

            # Batch insert belongsTo group relations
            if belongs_to_group_relations:
                await self.arango_service.batch_create_edges(
                    belongs_to_group_relations,
                    collection=CollectionNames.BELONGS_TO.value,
                )
                self.logger.info(
                    "✅ Created %s user-group relationships",
                    len(belongs_to_group_relations),
                )

            # Create relationships between users and orgs in belongsTo collection
            belongs_to_org_relations = []
            for user in users:
                # Check if the relationship already exists
                existing_relation = await self.arango_service.check_edge_exists(
                    f'users/{user["_key"]}',
                    f"organizations/{org_id}",
                    CollectionNames.BELONGS_TO.value,
                )
                if not existing_relation:
                    relation = {
                        "_from": f'users/{user["_key"]}',
                        "_to": f"organizations/{org_id}",
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

            await self.celery_app.setup_app()

            # Set up changes watch for each user
            active_users = await self.arango_service.get_users(org_id, active=True)
            for user in active_users:
                sync_state = await self.arango_service.get_user_sync_state(
                    user["email"], Connectors.GOOGLE_MAIL.value
                )
                current_state = (
                    sync_state.get("syncState") if sync_state else "NOT_STARTED"
                )
                if current_state == "IN_PROGRESS":
                    self.logger.warning(
                        f"Sync is currently RUNNING for user {user['email']}. Pausing it."
                    )
                    await self.arango_service.update_user_sync_state(
                        user["email"],
                        "PAUSED",
                        service_type=Connectors.GOOGLE_MAIL.value,
                    )

                self.logger.info(
                    "🚀 Setting up changes watch for user %s", user["email"]
                )

                try:
                    channel_data = await self.setup_changes_watch(org_id, user["email"])
                    if not channel_data:
                        self.logger.warning(
                            "Changes watch not created for user: %s", user["email"]
                        )
                        continue
                    else:
                        await self.arango_service.store_channel_history_id(
                            channel_data["historyId"],
                            channel_data["expiration"],
                            user["email"],
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
                    continue

            self.logger.info("✅ Gmail Sync service initialized successfully")
            return True

        except Exception as e:
            self.logger.error("❌ Failed to initialize enterprise sync: %s", str(e))
            return False

    async def perform_initial_sync(self, org_id, action: str = "start") -> bool:
        """First phase: Build complete gmail structure"""
        try:
            # Add global stop check at the start
            if await self._should_stop(org_id):
                self.logger.info("Sync stopped before starting")
                return False

            # Get users and account type
            users = await self.arango_service.get_users(org_id=org_id)
            account_type = await self.arango_service.get_account_type(org_id=org_id)

            for user in users:
                sync_state = await self.arango_service.get_user_sync_state(
                    user["email"], Connectors.GOOGLE_MAIL.value
                )
                current_state = sync_state.get("syncState")
                if current_state == "COMPLETED":
                    self.logger.warning(
                        "💥 Gmail sync is already completed for user %s", user["email"]
                    )
                    try:
                        if not await self.resync_gmail(org_id, user):
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

                await self.arango_service.update_user_sync_state(
                    user["email"],
                    "IN_PROGRESS",
                    service_type=Connectors.GOOGLE_MAIL.value,
                )

                # Stop checks
                if await self._should_stop(org_id):
                    self.logger.info(
                        "Sync stopped during user %s processing", user["email"]
                    )
                    await self.arango_service.update_user_sync_state(
                        user["email"],
                        "PAUSED",
                        service_type=Connectors.GOOGLE_MAIL.value,
                    )
                    return False

                # Initialize user service
                user_service = await self.gmail_admin_service.create_gmail_user_service(
                    user["email"]
                )
                if not user_service:
                    self.logger.warning(
                        "❌ Failed to create user service for user: %s", user["email"]
                    )
                    continue

                # List all threads for the user
                threads = await user_service.list_threads()
                for thread in threads:
                    if thread.get("historyId"):
                        self.logger.info("🚀 Thread historyId: %s", thread["historyId"])
                        await self.arango_service.store_channel_history_id(
                            history_id=thread["historyId"],
                            expiration=None,
                            user_email=user["email"],
                        )
                        break

                messages_list = await user_service.list_messages()
                messages_full = []
                attachments = []
                permissions = []
                for message in messages_list:
                    message_data = await user_service.get_message(message["id"])
                    messages_full.append(message_data)

                for message in messages_full:
                    attachments_for_message = await user_service.list_attachments(
                        message, org_id, user, account_type
                    )
                    attachments.extend(attachments_for_message)
                    attachment_ids = [
                        attachment["attachment_id"]
                        for attachment in attachments_for_message
                    ]
                    headers = message.get("headers", {})
                    permission = {
                        "messageId": message["id"],
                        "attachmentIds": attachment_ids,
                        "role": "reader",
                        "users": [
                            *(
                                headers.get("To", [])
                                if isinstance(headers.get("To"), list)
                                else [headers.get("To")] if headers.get("To") else []
                            ),
                            *(
                                headers.get("From", [])
                                if isinstance(headers.get("From"), list)
                                else (
                                    [headers.get("From")] if headers.get("From") else []
                                )
                            ),
                            *(
                                headers.get("Cc", [])
                                if isinstance(headers.get("Cc"), list)
                                else [headers.get("Cc")] if headers.get("Cc") else []
                            ),
                            *(
                                headers.get("Bcc", [])
                                if isinstance(headers.get("Bcc"), list)
                                else [headers.get("Bcc")] if headers.get("Bcc") else []
                            ),
                        ],
                    }
                    permissions.append(permission)

                if not threads:
                    self.logger.info(f"No threads found for user {user['email']}")
                    continue

                self.progress.total_files = len(threads) + len(messages_full)
                self.logger.info("🚀 Total threads: %s", len(threads))
                # self.logger.debug(f"Threads: {threads}")
                self.logger.info("🚀 Total messages: %s", len(messages_full))
                # self.logger.debug(f"Messages: {messages_full}")
                self.logger.info("🚀 Total permissions: %s", len(permissions))
                self.logger.debug(f"Permissions: {permissions}")

                # Process threads in batches
                batch_size = 50
                start_batch = 0

                for i in range(start_batch, len(threads), batch_size):
                    # Stop check before each batch
                    if await self._should_stop(org_id):
                        self.logger.info(
                            f"Sync stopped during batch processing at index {i}"
                        )
                        # Save current state before stopping
                        return False

                    batch = threads[i : i + batch_size]
                    self.logger.info(
                        "🚀 Processing batch of %s threads starting at index %s",
                        len(batch),
                        i,
                    )
                    batch_metadata = []

                    # Process each thread in batch
                    for thread in batch:
                        thread_messages = []
                        thread_attachments = []

                        current_thread_messages = [
                            m
                            for m in messages_full
                            if m.get("threadId") == thread["id"]
                        ]

                        if not current_thread_messages:
                            self.logger.warning(
                                "❌ 1. No messages found for thread %s", thread["id"]
                            )
                            continue

                        self.logger.info(
                            "📨 Found %s messages in thread %s",
                            len(current_thread_messages),
                            thread["id"],
                        )

                        # Process each message
                        for message in current_thread_messages:
                            message_attachments = await user_service.list_attachments(
                                message, org_id, user, account_type
                            )
                            if message_attachments:
                                self.logger.debug(
                                    "📎 Found %s attachments in message %s",
                                    len(message_attachments),
                                    message["id"],
                                )
                                thread_attachments.extend(message_attachments)

                            # Add message with its attachments
                            thread_messages.append(
                                {"message": message, "attachments": message_attachments}
                            )

                        # Prepare complete thread metadata
                        metadata = {
                            "thread": thread,
                            "thread_id": thread["id"],
                            "messages": thread_messages,
                            "attachments": thread_attachments,
                            "permissions": permissions,
                        }

                        self.logger.info(
                            "✅ Completed thread %s processing: %s messages, %s attachments",
                            thread["id"],
                            len(thread_messages),
                            len(thread_attachments),
                        )
                        batch_metadata.append(metadata)

                    self.logger.info(
                        "✅ Completed batch processing: %s threads", len(batch_metadata)
                    )

                    # Process the batch metadata
                    if not await self.process_batch(batch_metadata, org_id):
                        self.logger.warning(
                            "Failed to process batch starting at index %s", i
                        )
                        continue

                    endpoints = await self.config_service.get_config(
                        config_node_constants.ENDPOINTS.value
                    )
                    connector_endpoint = endpoints.get("connectors").get("endpoint")

                    # Send events to Kafka for the batch
                    for metadata in batch_metadata:
                        for message_data in metadata["messages"]:
                            message = message_data["message"]
                            message_key = await self.arango_service.get_key_by_external_message_id(
                                message["id"]
                            )
                            user_id = user["userId"]

                            headers = message.get("headers", {})
                            message_event = {
                                "orgId": org_id,
                                "recordId": message_key,
                                "recordName": headers.get("Subject", "No Subject"),
                                "recordType": RecordTypes.MAIL.value,
                                "recordVersion": 0,
                                "eventType": EventTypes.NEW_RECORD.value,
                                "body": message.get("body", ""),
                                "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/{user_id}/gmail/record/{message_key}/signedUrl",
                                "metadataRoute": f"/api/v1/gmail/record/{message_key}/metadata",
                                "connectorName": Connectors.GOOGLE_MAIL.value,
                                "origin": OriginTypes.CONNECTOR.value,
                                "mimeType": "text/gmail_content",
                                "createdAtSourceTimestamp": int(
                                    message.get(
                                        "internalDate",
                                        datetime.now(timezone.utc).timestamp(),
                                    )
                                ),
                                "modifiedAtSourceTimestamp": int(
                                    message.get(
                                        "internalDate",
                                        datetime.now(timezone.utc).timestamp(),
                                    )
                                ),
                            }
                            await self.kafka_service.send_event_to_kafka(message_event)
                            self.logger.info(
                                "📨 Sent Kafka Indexing event for message %s",
                                message_key,
                            )

                        # Attachment events
                        for attachment in metadata["attachments"]:
                            attachment_key = (
                                await self.arango_service.get_key_by_attachment_id(
                                    attachment["attachment_id"]
                                )
                            )
                            attachment_event = {
                                "orgId": org_id,
                                "recordId": attachment_key,
                                "recordName": attachment.get(
                                    "filename", "Unnamed Attachment"
                                ),
                                "recordType": RecordTypes.ATTACHMENT.value,
                                "recordVersion": 0,
                                "eventType": EventTypes.NEW_RECORD.value,
                                "metadataRoute": f"/api/v1/{org_id}/{user_id}/gmail/attachments/{attachment_key}/metadata",
                                "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/{user_id}/gmail/record/{attachment_key}/signedUrl",
                                "connectorName": Connectors.GOOGLE_MAIL.value,
                                "origin": OriginTypes.CONNECTOR.value,
                                "mimeType": attachment.get(
                                    "mimeType", "application/octet-stream"
                                ),
                                "size": attachment.get("size", 0),
                                "createdAtSourceTimestamp": get_epoch_timestamp_in_ms(),
                                "modifiedAtSourceTimestamp": get_epoch_timestamp_in_ms(),
                            }
                            await self.kafka_service.send_event_to_kafka(
                                attachment_event
                            )
                            self.logger.info(
                                "📨 Sent Kafka Indexing event for attachment %s",
                                attachment_key,
                            )

                await self.arango_service.update_user_sync_state(
                    user["email"],
                    "COMPLETED",
                    service_type=Connectors.GOOGLE_MAIL.value,
                )

            # Add completion handling
            self.is_completed = True
            return True

        except Exception as e:
            if "user" in locals():
                await self.arango_service.update_user_sync_state(
                    user["email"], "FAILED", service_type=Connectors.GOOGLE_MAIL.value
                )
            self.logger.error(f"❌ Initial sync failed: {str(e)}")
            return False

    async def sync_specific_user(self, user_email: str) -> bool:
        """Synchronize a specific user's Gmail content"""
        try:
            self.logger.info("🚀 Starting sync for specific user: %s", user_email)

            # Verify user exists in the database
            sync_state = await self.arango_service.get_user_sync_state(
                user_email, Connectors.GOOGLE_MAIL.value
            )
            current_state = sync_state.get("syncState") if sync_state else "NOT_STARTED"
            if current_state == "IN_PROGRESS":
                self.logger.warning(
                    "💥 Gmail sync is already running for user %s", user_email
                )
                return False

            # Update user sync state to RUNNING
            await self.arango_service.update_user_sync_state(
                user_email, "IN_PROGRESS", service_type=Connectors.GOOGLE_MAIL.value
            )

            user_id = await self.arango_service.get_entity_id_by_email(user_email)
            user = await self.arango_service.get_document(
                user_id, CollectionNames.USERS.value
            )
            org_id = user["orgId"]

            account_type = await self.arango_service.get_account_type(org_id)

            if not org_id:
                self.logger.warning(f"No organization found for user {user_email}")
                return False

            # Create user service instance
            user_service = await self.gmail_admin_service.create_gmail_user_service(
                user_email
            )
            if not user_service:
                self.logger.error(
                    "❌ Failed to create Gmail service for user %s", user_email
                )
                await self.arango_service.update_user_sync_state(
                    user_email, "FAILED", Connectors.GOOGLE_MAIL.value
                )
                return False

            # Set up changes watch for the user
            try:
                channel_data = await self.setup_changes_watch(org_id, user["email"])
                if not channel_data:
                    self.logger.warning(
                        "Changes watch not created for user: %s", user["email"]
                    )
                else:
                    await self.arango_service.store_channel_history_id(
                        channel_data["historyId"],
                        channel_data["expiration"],
                        user["email"],
                    )

                self.logger.info(
                    "✅ Changes watch set up successfully for user: %s", user["email"]
                )

            except Exception as e:
                self.logger.error(
                    "❌ Error setting up changes watch for user %s: %s",
                    user["email"],
                    str(e),
                )
                return False

            # List all threads and messages
            threads = await user_service.list_threads()
            for thread in threads:
                if thread.get("historyId"):
                    self.logger.info("🚀 Thread historyId: %s", thread["historyId"])
                    await self.arango_service.store_channel_history_id(
                        history_id=thread["historyId"],
                        expiration=None,
                        user_email=user["email"],
                    )
                    break

            messages_list = await user_service.list_messages()

            if not threads:
                self.logger.info("No threads found for user %s", user_email)
                await self.arango_service.update_user_sync_state(
                    user_email, "COMPLETED", Connectors.GOOGLE_MAIL.value
                )
                return True

            # Process messages and build full metadata
            messages_full = []
            attachments = []
            permissions = []

            for message in messages_list:
                message_data = await user_service.get_message(message["id"])
                messages_full.append(message_data)

                # Get attachments for message
                attachments_for_message = await user_service.list_attachments(
                    message_data, org_id, user, account_type
                )
                attachments.extend(attachments_for_message)
                attachment_ids = [
                    attachment["attachment_id"]
                    for attachment in attachments_for_message
                ]

                # Build permissions from message headers
                headers = message_data.get("headers", {})
                permissions.append(
                    {
                        "messageId": message["id"],
                        "attachmentIds": attachment_ids,
                        "role": "reader",
                        "users": [
                            *(
                                headers.get("To", [])
                                if isinstance(headers.get("To"), list)
                                else [headers.get("To")] if headers.get("To") else []
                            ),
                            *(
                                headers.get("From", [])
                                if isinstance(headers.get("From"), list)
                                else (
                                    [headers.get("From")] if headers.get("From") else []
                                )
                            ),
                            *(
                                headers.get("Cc", [])
                                if isinstance(headers.get("Cc"), list)
                                else [headers.get("Cc")] if headers.get("Cc") else []
                            ),
                            *(
                                headers.get("Bcc", [])
                                if isinstance(headers.get("Bcc"), list)
                                else [headers.get("Bcc")] if headers.get("Bcc") else []
                            ),
                        ],
                    }
                )

            # Process threads in batches
            batch_size = 50
            for i in range(0, len(threads), batch_size):
                if await self._should_stop(org_id):
                    self.logger.info(
                        "Sync stopped during batch processing at index %s", i
                    )
                    await self.arango_service.update_user_sync_state(
                        user_email, "PAUSED", Connectors.GOOGLE_MAIL.value
                    )
                    return False

                batch = threads[i : i + batch_size]
                batch_metadata = []

                # Process each thread in batch
                for thread in batch:
                    thread_messages = []
                    thread_attachments = []

                    # Get messages for this thread
                    current_thread_messages = [
                        m for m in messages_full if m.get("threadId") == thread["id"]
                    ]

                    if not current_thread_messages:
                        self.logger.warning(
                            "❌ No messages found for thread %s", thread["id"]
                        )
                        continue

                    # Process messages in thread
                    for message in current_thread_messages:
                        message_attachments = await user_service.list_attachments(
                            message, org_id, user, account_type
                        )
                        if message_attachments:
                            thread_attachments.extend(message_attachments)
                        thread_messages.append({"message": message})

                    # Add thread metadata
                    metadata = {
                        "thread": thread,
                        "threadId": thread["id"],
                        "messages": thread_messages,
                        "attachments": thread_attachments,
                        "permissions": permissions,
                    }
                    batch_metadata.append(metadata)

                # Process batch
                if not await self.process_batch(batch_metadata, org_id):
                    self.logger.warning(
                        "Failed to process batch starting at index %s", i
                    )
                    continue

                endpoints = await self.config_service.get_config(
                    config_node_constants.ENDPOINTS.value
                )
                connector_endpoint = endpoints.get("connectors").get("endpoint")

                # Send events to Kafka
                for metadata in batch_metadata:
                    # Message events
                    for message_data in metadata["messages"]:
                        message = message_data["message"]
                        message_key = (
                            await self.arango_service.get_key_by_external_message_id(
                                message["id"]
                            )
                        )
                        user_id = user["userId"]
                        headers = message.get("headers", {})
                        message_event = {
                            "orgId": org_id,
                            "recordId": message_key,
                            "recordName": headers.get("Subject", "No Subject"),
                            "recordType": RecordTypes.MAIL.value,
                            "recordVersion": 0,
                            "eventType": EventTypes.NEW_RECORD.value,
                            "body": message.get("body", ""),
                            "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/{user_id}/gmail/record/{message_key}/signedUrl",
                            "metadataRoute": f"/api/v1/gmail/record/{message_key}/metadata",
                            "connectorName": Connectors.GOOGLE_MAIL.value,
                            "origin": OriginTypes.CONNECTOR.value,
                            "mimeType": "text/gmail_content",
                            "threadId": metadata["thread"]["id"],
                            "createdAtSourceTimestamp": int(
                                message.get(
                                    "internalDate",
                                    datetime.now(timezone.utc).timestamp(),
                                )
                            ),
                            "modifiedAtSourceTimestamp": int(
                                message.get(
                                    "internalDate",
                                    datetime.now(timezone.utc).timestamp(),
                                )
                            ),
                        }
                        await self.kafka_service.send_event_to_kafka(message_event)
                        self.logger.info(
                            "📨 Sent Kafka Indexing event for message %s", message_key
                        )

                    # Attachment events
                    for attachment in metadata["attachments"]:
                        attachment_key = (
                            await self.arango_service.get_key_by_attachment_id(
                                attachment["attachment_id"]
                            )
                        )
                        attachment_event = {
                            "orgId": org_id,
                            "recordId": attachment_key,
                            "recordName": attachment.get(
                                "filename", "Unnamed Attachment"
                            ),
                            "recordType": RecordTypes.ATTACHMENT.value,
                            "recordVersion": 0,
                            "eventType": EventTypes.NEW_RECORD.value,
                            "metadataRoute": f"/api/v1/{org_id}/{user_id}/gmail/attachments/{attachment_key}/metadata",
                            "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/{user_id}/gmail/record/{attachment_key}/signedUrl",
                            "connectorName": Connectors.GOOGLE_MAIL.value,
                            "origin": OriginTypes.CONNECTOR.value,
                            "mimeType": attachment.get(
                                "mimeType", "application/octet-stream"
                            ),
                            "createdAtSourceTimestamp": get_epoch_timestamp_in_ms(),
                            "modifiedAtSourceTimestamp": get_epoch_timestamp_in_ms(),
                        }
                        await self.kafka_service.send_event_to_kafka(attachment_event)
                        self.logger.info(
                            "📨 Sent Kafka Indexing event for attachment %s",
                            attachment_key,
                        )

            # Update user state to COMPLETED
            await self.arango_service.update_user_sync_state(
                user_email, "COMPLETED", Connectors.GOOGLE_MAIL.value
            )
            self.logger.info("✅ Successfully completed sync for user %s", user_email)
            return True

        except Exception as e:
            await self.arango_service.update_user_sync_state(
                user_email, "FAILED", Connectors.GOOGLE_MAIL.value
            )
            self.logger.error("❌ Failed to sync user %s: %s", user_email, str(e))
            return False

    async def resync_gmail(self, org_id, user):
        try:
            user_service = await self.gmail_admin_service.create_gmail_user_service(
                user["email"]
            )

            channel_history = await self.arango_service.get_channel_history_id(
                user["email"]
            )
            if not channel_history:
                self.logger.warning(
                    f"""⚠️ No historyId found for {
                        user['email']}"""
                )
                return

            changes = await user_service.fetch_gmail_changes(
                user["email"], channel_history["historyId"]
            )

            if changes:
                self.logger.info(
                    "Processing %s changes for user %s", len(changes), user["email"]
                )
                await self.arango_service.store_channel_history_id(
                    changes["historyId"], channel_history["expiration"], user["email"]
                )

                try:
                    await self.change_handler.process_changes(
                        user_service, changes, org_id, user
                    )
                except Exception as e:
                    self.logger.error("Error processing changes: %s", str(e))
        except Exception as e:
            self.logger.error(f"Error processing user {user['email']}: {str(e)}")
            return False


class GmailSyncIndividualService(BaseGmailSyncService):
    """Sync service for individual user setup"""

    def __init__(
        self,
        logger,
        config: ConfigurationService,
        gmail_user_service: GmailUserService,
        arango_service: ArangoService,
        change_handler,
        kafka_service: KafkaService,
        celery_app,
    ):
        super().__init__(
            logger, config, arango_service, change_handler, kafka_service, celery_app
        )
        self.gmail_user_service = gmail_user_service

    async def connect_services(self, org_id: str) -> bool:
        """Connect to services for individual setup"""
        try:
            self.logger.info("🚀 Connecting to individual user services")
            user_info = await self.arango_service.get_users(org_id, active=True)
            if user_info:
                # Add sync state to user info
                user_id = user_info[0]["userId"]

            # Connect to Google Drive
            if not await self.gmail_user_service.connect_individual_user(
                org_id, user_id
            ):
                raise Exception("Failed to connect to Gmail API")

            # Connect to ArangoDB
            if not await self.arango_service.connect():
                raise Exception("Failed to connect to ArangoDB")

            self.logger.info("✅ Individual user services connected successfully")
            return True
        except Exception as e:
            self.logger.error("❌ Individual service connection failed: %s", str(e))
            return False

    async def setup_changes_watch(self, org_id: str, user_email: str) -> Optional[Dict]:
        """Set up changes.watch after initial sync"""
        try:
            # Set up watch
            user_service = self.gmail_user_service
            self.logger.info("👀 Setting up changes watch for user %s", user_email)
            channel_history = await self.arango_service.get_channel_history_id(
                user_email
            )
            if not channel_history:
                self.logger.info(
                    "🚀 Creating new changes watch for user %s", user_email
                )
                watch = await user_service.create_gmail_user_watch()
                if not watch:
                    self.logger.warning(
                        "Changes watch not created for user: %s", user_email
                    )
                    return None
                return watch

            current_timestamp = get_epoch_timestamp_in_ms()
            expiration_timestamp = channel_history.get("expiration", 0)
            self.logger.info("Current time: %s", current_timestamp)
            self.logger.info("Page token expiration: %s", expiration_timestamp)
            if expiration_timestamp < current_timestamp:
                self.logger.info("⚠️ Page token expired for user %s", user_email)
                await user_service.stop_gmail_user_watch()
                watch = await user_service.create_gmail_user_watch()
                if not watch:
                    self.logger.warning(
                        "Changes watch not created for user: %s", user_email
                    )
                    return None
                return watch
            self.logger.info(
                "✅ Changes watch set up successfully for user: %s", user_email
            )
            return channel_history

        except Exception as e:
            self.logger.error("Failed to set up changes watch: %s", str(e))
            return None

    async def stop_changes_watch(self, user_email: str) -> bool:
        """Stop changes watch"""
        try:
            user_service = self.gmail_user_service
            stopped = await user_service.stop_gmail_user_watch(user_email)
            return stopped
        except Exception as e:
            self.logger.error("Failed to stop changes watch: %s", str(e))
            return False

    async def initialize(self, org_id) -> bool:
        """Initialize individual user sync service"""
        try:
            if not await self.connect_services(org_id):
                return False

            # Get and store user info with initial sync state
            user_info = await self.gmail_user_service.list_individual_user(org_id)
            self.logger.info("🚀 User Info: %s", user_info)
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
                user_info["email"], Connectors.GOOGLE_MAIL.value
            )
            current_state = sync_state.get("syncState") if sync_state else "NOT_STARTED"
            if current_state == "IN_PROGRESS":
                self.logger.warning(
                    f"Gmail sync is currently RUNNING for user {user_info['email']}. Pausing it."
                )
                await self.arango_service.update_user_sync_state(
                    user_info["email"], "PAUSED", Connectors.GOOGLE_MAIL.value
                )

            try:
                self.logger.info(
                    "🚀 Setting up changes watch for user %s", user_info["email"]
                )
                channel_data = await self.setup_changes_watch(
                    org_id, user_info["email"]
                )
                if not channel_data:
                    self.logger.warning(
                        "Changes watch not created for user: %s", user_info["email"]
                    )

                else:
                    await self.arango_service.store_channel_history_id(
                        channel_data["historyId"],
                        channel_data["expiration"],
                        user_info["email"],
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
            # Initialize Celery
            await self.celery_app.setup_app()

            self.logger.info("✅ Gmail sync service initialized successfully")
            return True

        except Exception as e:
            self.logger.error(
                "❌ Failed to initialize individual Gmail sync: %s", str(e)
            )
            return False

    async def perform_initial_sync(self, org_id, action: str = "start") -> bool:
        """First phase: Build complete gmail structure"""
        try:
            if await self._should_stop(org_id):
                self.logger.info("Sync stopped before starting")
                return False

            user = await self.arango_service.get_users(org_id, active=True)
            user = user[0]

            sync_state = await self.arango_service.get_user_sync_state(
                user["email"], Connectors.GOOGLE_MAIL.value
            )
            current_state = sync_state.get("syncState")
            if current_state == "COMPLETED":
                self.logger.warning(
                    "💥 Gmail sync is already completed for user %s", user["email"]
                )

                try:
                    if not await self.resync_gmail(org_id, user):
                        self.logger.error(f"Error resyncing user {user['email']}")
                        return False
                except Exception as e:
                    self.logger.error(
                        f"Error processing user {user['email']}: {str(e)}"
                    )
                    return False

                return True

            account_type = await self.arango_service.get_account_type(org_id)
            # Update user sync state to RUNNING
            await self.arango_service.update_user_sync_state(
                user["email"], "IN_PROGRESS", Connectors.GOOGLE_MAIL.value
            )

            if await self._should_stop(org_id):
                self.logger.info(
                    "Sync stopped during user %s processing", user["email"]
                )
                await self.arango_service.update_user_sync_state(
                    user["email"], "PAUSED", Connectors.GOOGLE_MAIL.value
                )
                return False

            # Initialize user service
            user_service = self.gmail_user_service

            # List all threads and messages for the user
            threads = await user_service.list_threads()
            for thread in threads:
                if thread.get("historyId"):
                    self.logger.info("🚀 Thread historyId: %s", thread["historyId"])
                    await self.arango_service.store_channel_history_id(
                        history_id=thread["historyId"],
                        expiration=None,
                        user_email=user["email"],
                    )
                    break

            messages_list = await user_service.list_messages()
            messages_full = []
            attachments = []
            permissions = []

            if not threads:
                self.logger.info(f"No threads found for user {user['email']}")
                await self.arango_service.update_user_sync_state(
                    user["email"], "COMPLETED", Connectors.GOOGLE_MAIL.value
                )
                return False

            # Process messages
            for message in messages_list:
                message_data = await user_service.get_message(message["id"])
                messages_full.append(message_data)

            for message in messages_full:
                attachments_for_message = await user_service.list_attachments(
                    message, org_id, user, account_type
                )
                attachments.extend(attachments_for_message)
                attachment_ids = [
                    attachment["attachment_id"]
                    for attachment in attachments_for_message
                ]
                headers = message.get("headers", {})
                permission = {
                    "messageId": message["id"],
                    "attachmentIds": attachment_ids,
                    "role": "reader",
                    "users": [
                        *(
                            headers.get("To", [])
                            if isinstance(headers.get("To"), list)
                            else [headers.get("To")] if headers.get("To") else []
                        ),
                        *(
                            headers.get("From", [])
                            if isinstance(headers.get("From"), list)
                            else [headers.get("From")] if headers.get("From") else []
                        ),
                        *(
                            headers.get("Cc", [])
                            if isinstance(headers.get("Cc"), list)
                            else [headers.get("Cc")] if headers.get("Cc") else []
                        ),
                        *(
                            headers.get("Bcc", [])
                            if isinstance(headers.get("Bcc"), list)
                            else [headers.get("Bcc")] if headers.get("Bcc") else []
                        ),
                    ],
                }
                self.logger.info(
                    "🚀 Mail Permission for message %s: %s", message["id"], permission
                )

                permissions.append(permission)

            # Process threads in batches
            batch_size = 50
            start_batch = 0

            for i in range(start_batch, len(threads), batch_size):
                if await self._should_stop(org_id):
                    self.logger.info(
                        f"Sync stopped during batch processing at index {i}"
                    )
                    await self.arango_service.update_user_sync_state(
                        user["email"], "PAUSED", Connectors.GOOGLE_MAIL.value
                    )
                    return False

                batch = threads[i : i + batch_size]
                batch_metadata = []

                # Process each thread in batch
                for thread in batch:
                    thread_messages = []
                    thread_attachments = []

                    # Get messages for this thread
                    current_thread_messages = [
                        m for m in messages_full if m.get("threadId") == thread["id"]
                    ]

                    if not current_thread_messages:
                        self.logger.warning(
                            "❌ No messages found for thread %s", thread["id"]
                        )
                        continue

                    # Process messages in thread
                    for message in current_thread_messages:
                        message_attachments = await user_service.list_attachments(
                            message, org_id, user, account_type
                        )
                        if message_attachments:
                            thread_attachments.extend(message_attachments)
                        thread_messages.append({"message": message})

                    # Add thread metadata
                    metadata = {
                        "thread": thread,
                        "threadId": thread["id"],
                        "messages": thread_messages,
                        "attachments": thread_attachments,
                        "permissions": permissions,
                    }
                    batch_metadata.append(metadata)

                # Process batch
                if not await self.process_batch(batch_metadata, org_id):
                    self.logger.warning(
                        f"Failed to process batch starting at index {i}"
                    )
                    continue

                # Send events to Kafka for threads, messages and attachments
                self.logger.info("🚀 Preparing events for Kafka for batch %s", i)

                endpoints = await self.config_service.get_config(
                    config_node_constants.ENDPOINTS.value
                )
                connector_endpoint = endpoints.get("connectors").get("endpoint")

                for metadata in batch_metadata:
                    # Message events
                    for message_data in metadata["messages"]:
                        message = message_data["message"]
                        message_key = (
                            await self.arango_service.get_key_by_external_message_id(
                                message["id"]
                            )
                        )
                        # message = await self.arango_service.get_document(message_key, "messages")

                        user_id = user["userId"]
                        headers = message.get("headers", {})
                        message_event = {
                            "orgId": org_id,
                            "recordId": message_key,
                            "recordName": headers.get("Subject", "No Subject"),
                            "recordType": RecordTypes.MAIL.value,
                            "recordVersion": 0,
                            "eventType": EventTypes.NEW_RECORD.value,
                            "body": message.get("body", ""),
                            "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/{user_id}/gmail/record/{message_key}/signedUrl",
                            "metadataRoute": f"/api/v1/gmail/record/{message_key}/metadata",
                            "connectorName": Connectors.GOOGLE_MAIL.value,
                            "origin": OriginTypes.CONNECTOR.value,
                            "mimeType": "text/gmail_content",
                            "createdAtSourceTimestamp": int(
                                message.get(
                                    "internalDate",
                                    datetime.now(timezone.utc).timestamp(),
                                )
                            ),
                            "modifiedAtSourceTimestamp": int(
                                message.get(
                                    "internalDate",
                                    datetime.now(timezone.utc).timestamp(),
                                )
                            ),
                        }
                        await self.kafka_service.send_event_to_kafka(message_event)
                        self.logger.info(
                            "📨 Sent Kafka Indexing event for message %s", message_key
                        )

                    # Attachment events
                    for attachment in metadata["attachments"]:
                        attachment_key = (
                            await self.arango_service.get_key_by_attachment_id(
                                attachment["attachment_id"]
                            )
                        )
                        attachment_event = {
                            "orgId": org_id,
                            "recordId": attachment_key,
                            "recordName": attachment.get(
                                "filename", "Unnamed Attachment"
                            ),
                            "recordType": RecordTypes.ATTACHMENT.value,
                            "recordVersion": 0,
                            "eventType": EventTypes.NEW_RECORD.value,
                            "metadataRoute": f"/api/v1/{org_id}/{user_id}/gmail/attachments/{attachment_key}/metadata",
                            "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/{user_id}/gmail/record/{attachment_key}/signedUrl",
                            "connectorName": Connectors.GOOGLE_MAIL.value,
                            "origin": OriginTypes.CONNECTOR.value,
                            "mimeType": attachment.get(
                                "mimeType", "application/octet-stream"
                            ),
                            "createdAtSourceTimestamp": get_epoch_timestamp_in_ms(),
                            "modifiedAtSourceTimestamp": get_epoch_timestamp_in_ms(),
                        }
                        await self.kafka_service.send_event_to_kafka(attachment_event)
                        self.logger.info(
                            "📨 Sent Kafka Indexing event for attachment %s",
                            attachment_key,
                        )

            # Update user state to COMPLETED
            await self.arango_service.update_user_sync_state(
                user["email"], "COMPLETED", Connectors.GOOGLE_MAIL.value
            )

            self.is_completed = True
            return True

        except Exception as e:
            if "user" in locals():
                await self.arango_service.update_user_sync_state(
                    user["email"], "FAILED", Connectors.GOOGLE_MAIL.value
                )
            self.logger.error(f"❌ Initial sync failed: {str(e)}")
            return False

    async def resync_gmail(self, org_id, user):
        try:
            user_service = self.gmail_user_service

            channel_history = await self.arango_service.get_channel_history_id(
                user["email"]
            )
            if not channel_history:
                self.logger.warning(
                    f"""⚠️ No historyId found for {
                        user['email']}"""
                )
                return

            changes = await user_service.fetch_gmail_changes(
                user["email"], channel_history["historyId"]
            )

            if changes:
                self.logger.info(
                    "Processing %s changes for user %s", len(changes), user["email"]
                )
                await self.arango_service.store_channel_history_id(
                    changes["historyId"], channel_history["expiration"], user["email"]
                )

                try:
                    await self.change_handler.process_changes(
                        user_service, changes, org_id, user
                    )
                except Exception as e:
                    self.logger.error("Error processing changes: %s", str(e))
        except Exception as e:
            self.logger.error(f"Error processing user {user['email']}: {str(e)}")
            return False
