import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from app.config.configuration_service import ConfigurationService, config_node_constants
from app.config.utils.named_constants.arangodb_constants import (
    CollectionNames,
    Connectors,
    EntityType,
    EventTypes,
    GroupType,
    MessageType,
    OriginTypes,
    ProgressStatus,
    RecordRelations,
    RecordTypes,
)
from app.connectors.services.kafka_service import KafkaService
from app.utils.time_conversion import get_epoch_timestamp_in_ms

logger = logging.getLogger(__name__)


@dataclass
class UserMessageWindow:
    """Container for user messages within time window"""
    user_id: str
    messages: List[Dict[str, Any]] = field(default_factory=list)
    files: List[Dict[str, Any]] = field(default_factory=list)
    window_start: Optional[float] = None
    window_end: Optional[float] = None


@dataclass
class EventProcessingResult:
    """Container for event processing results"""
    processed_records: List[Dict[str, Any]] = field(default_factory=list)
    kafka_events: List[Dict[str, Any]] = field(default_factory=list)
    success: bool = True
    error_message: Optional[str] = None


class SlackChangeHandler:
    """Handler for processing Slack events and changes with generator pattern and Kafka integration"""

    def __init__(
        self,
        logger,
        config_service: ConfigurationService,
        arango_service,
        kafka_service: KafkaService
    ) -> None:
        """Initialize the Slack change handler.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            arango_service: ArangoDB service instance
            kafka_service: Kafka service instance for event publishing
        """
        self.logger = logger
        self.config_service = config_service
        self.arango_service = arango_service
        self.kafka_service = kafka_service

        # Configuration for user message windows (same as SlackDataHandler)
        self.user_message_window_seconds = 60  # 1 minute window for user message grouping
        self._user_message_windows = defaultdict(lambda: UserMessageWindow(user_id=""))

    async def handle_event(self, event: Dict[str, Any], org_id: str = None) -> bool:
        """Handle different types of Slack events using generator pattern.
        Args:
            event: The Slack event to process
            org_id: Optional organization ID
        Returns:
            bool: True if event was handled successfully, False otherwise
        """
        try:
            if not org_id:
                self.logger.error("No org_id provided for event handling")
                return False

            event_type = event.get('type')
            self.logger.info(f"Handling Slack event type: {event_type} for org_id: {org_id}")

            # Handle URL verification
            if event_type == 'url_verification':
                self.logger.info("Processing URL verification challenge")
                return {"challenge": event.get("challenge")}

            # Process event using generator pattern and get results
            async for processing_result in self._process_event_generator(event, org_id):
                if not processing_result.success:
                    self.logger.error(f"Failed to process event: {processing_result.error_message}")
                    return False

                # Send Kafka events if any were generated
                if processing_result.kafka_events:
                    async for kafka_stats in self._send_kafka_events_generator(processing_result.kafka_events, org_id):
                        self.logger.info(f"Sent {kafka_stats['sent']} Kafka events for {event_type}")
                        if kafka_stats['errors']:
                            self.logger.warning(f"Kafka errors: {kafka_stats['errors']}")

            return True

        except Exception as e:
            self.logger.error(f"Error handling Slack event for org_id {org_id}: {str(e)}")
            return False

    async def _process_event_generator(self, event: Dict[str, Any], org_id: str) -> AsyncIterator[EventProcessingResult]:
        """Generator that processes events and yields results"""
        try:
            event_type = event.get('type')

            # Handle message events and their subtypes
            if event_type == 'message':
                self.logger.info(f"Processing message event for org_id: {org_id}")
                async for result in self._handle_message_event_generator(event, org_id):
                    yield result

            # Handle channel-related events
            elif event_type in [
                'channel_created', 'channel_rename', 'channel_archive',
                'channel_unarchive', 'channel_deleted', 'channel_shared',
                'channel_unshared'
            ]:
                self.logger.info(f"Processing channel event: {event_type} for org_id: {org_id}")
                async for result in self._handle_channel_event_generator(event, org_id):
                    yield result

            # Handle membership events
            elif event_type in [
                'member_joined_channel', 'member_left_channel',
                'group_joined', 'group_left'
            ]:
                self.logger.info(f"Processing membership event: {event_type} for org_id: {org_id}")
                async for result in self._handle_channel_membership_event_generator(event, org_id):
                    yield result

            # Handle user events
            elif event_type in [
                'user_change', 'team_join', 'user_profile_changed',
                'user_status_changed'
            ]:
                self.logger.info(f"Processing user event: {event_type} for org_id: {org_id}")
                async for result in self._handle_user_event_generator(event, org_id):
                    yield result

            # Skip file_shared events - they will be handled later with more complete data
            elif event_type == 'file_shared':
                self.logger.info("Skipping file_shared event - will be handled later with complete data")
                yield EventProcessingResult(success=True)

            else:
                self.logger.warning(f"Unhandled event type: {event_type} for org_id: {org_id}")
                yield EventProcessingResult(success=True)

        except Exception as e:
            self.logger.error(f"Error in event generator: {str(e)}")
            yield EventProcessingResult(success=False, error_message=str(e))

    async def _handle_message_event_generator(self, event: Dict[str, Any], org_id: str) -> AsyncIterator[EventProcessingResult]:
        """Handle message events including thread messages using generator pattern"""
        try:
            # Skip message_changed and message_deleted events for now
            if event.get('subtype') in ['message_changed', 'message_deleted']:
                yield EventProcessingResult(success=True)
                return

            # Check for duplicate message by timestamp - prevent processing retries
            message_ts = event.get('ts')
            if message_ts:
                existing_message = await self.arango_service.get_message_by_slack_ts(message_ts, org_id)
                if existing_message:
                    self.logger.info(f"Message with timestamp {message_ts} already exists, skipping duplicate")
                    yield EventProcessingResult(success=True)
                    return

            # Get message text and check if it's empty
            message_text = event.get('text', '').strip()
            has_files = bool(event.get('files'))

            # If message is empty/null and no files, skip processing
            if not message_text and not has_files:
                self.logger.info("Skipping empty message with no files")
                yield EventProcessingResult(success=True)
                return

            # If message is empty but has files, process the files but don't create a message record
            if not message_text and has_files:
                self.logger.info("Processing files from message event (no text content)")
                # Process only the files, no message record
                async for result in self._handle_file_only_message_generator(event, org_id):
                    yield result
                return

            # Begin transaction
            txn = self.arango_service.db.begin_transaction(
                write=[
                    CollectionNames.RECORDS.value,
                    CollectionNames.SLACK_MESSAGE_METADATA.value,
                    CollectionNames.BELONGS_TO_SLACK_CHANNEL.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.SLACK_FILE_TO_ATTACHMENT_METADATA.value,
                    CollectionNames.SLACK_ATTACHMENT_METADATA.value
                ]
            )

            try:
                timestamp = get_epoch_timestamp_in_ms()
                message_key = str(uuid.uuid4())
                user_id = event.get('user', '')
                message_ts = float(event.get('ts', 0))

                # Get channel info
                channel_id = event.get('channel')
                channel = await self.arango_service.get_channel_by_slack_id(channel_id, org_id, txn)
                if not channel:
                    # Channel doesn't exist, create it
                    workspace = await self.arango_service.get_workspace_by_org_id(org_id)
                    if not workspace:
                        self.logger.error(f"Workspace not found for org_id: {org_id}")
                        yield EventProcessingResult(success=False, error_message="Workspace not found")
                        return

                    channel_key = str(uuid.uuid4())
                    channel_record = {
                        "_key": channel_key,
                        "orgId": org_id,
                        "groupName": "New Channel",  # Will be updated when we get channel details
                        "externalGroupId": channel_id,
                        "groupType": GroupType.SLACK_CHANNEL.value,
                        "connectorName": Connectors.SLACK.value,
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp,
                        "isDeletedAtSource": False,
                        "sourceCreatedAtTimestamp": timestamp,
                        "sourceLastModifiedTimestamp": timestamp,
                        "syncState": ProgressStatus.NOT_STARTED.value,
                    }

                    await self.arango_service.batch_upsert_nodes(
                        [channel_record],
                        CollectionNames.RECORD_GROUPS.value,
                        transaction=txn
                    )

                    # Create workspace relationship
                    workspace_edge = {
                        "_from": f"{CollectionNames.RECORD_GROUPS.value}/{channel_key}",
                        "_to": f"{CollectionNames.SLACK_WORKSPACES.value}/{workspace['_key']}",
                        "entityType": "WORKSPACE",
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp
                    }
                    await self.arango_service.batch_create_edges(
                        [workspace_edge],
                        CollectionNames.BELONGS_TO_SLACK_WORKSPACE.value,
                        transaction=txn
                    )
                else:
                    channel_key = channel['_key']

                # Create message record
                message_record = {
                    "_key": message_key,
                    "orgId": org_id,
                    "recordName": f"Slack_Message_{event.get('ts', '')}",
                    "externalRecordId": event.get('ts'),
                    "recordType": RecordTypes.MESSAGE.value,
                    "version": 0,
                    "origin": OriginTypes.CONNECTOR.value,
                    "connectorName": Connectors.SLACK.value,
                    "virtualRecordId": None,
                    "isLatestVersion": True,
                    "isDirty": False,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                    "lastSyncTimestamp": timestamp,
                    "sourceCreatedAtTimestamp": int(message_ts * 1000),
                    "sourceLastModifiedTimestamp": int(float(event.get('edited', {}).get('ts', 0)) * 1000) if event.get('edited') else int(message_ts * 1000),
                    "isDeleted": False,
                    "isArchived": False,
                    "reason": None,
                    "lastIndexTimestamp": None,
                    "lastExtractionTimestamp": None,
                    "indexingStatus": ProgressStatus.NOT_STARTED.value,
                    "extractionStatus": ProgressStatus.NOT_STARTED.value,
                }

                # Create message metadata
                message_metadata = {
                    "_key": message_key,
                    "orgId": org_id,
                    "slackTs": event.get('ts'),
                    "threadTs": event.get('thread_ts'),
                    "channelId": channel_id,
                    "userId": user_id,
                    "messageType": MessageType.THREAD_MESSAGE.value if event.get('thread_ts') else MessageType.ROOT_MESSAGE.value,
                    "text": message_text,
                    "replyCount": event.get('reply_count', 0),
                    "replyUsersCount": event.get('reply_users_count', 0),
                    "replyUsers": event.get('reply_users', []),
                    "hasFiles": bool(event.get('files')),
                    "fileCount": len(event.get('files', [])),
                    "botId": event.get('bot_id'),
                    "mentionedUsers": self._extract_mentions(event),
                    "links": self._extract_links(event)
                }

                # Store message record and metadata
                await self.arango_service.batch_upsert_nodes(
                    [message_record],
                    CollectionNames.RECORDS.value,
                    transaction=txn
                )
                await self.arango_service.batch_upsert_nodes(
                    [message_metadata],
                    CollectionNames.SLACK_MESSAGE_METADATA.value,
                    transaction=txn
                )

                # Create edges list for batch creation
                edges = []

                # Message to channel edge
                edges.append({
                    "_from": f"{CollectionNames.RECORDS.value}/{message_key}",
                    "_to": f"{CollectionNames.RECORD_GROUPS.value}/{channel_key}",
                    "entityType": EntityType.SLACK_CHANNEL.value,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp
                })

                # Message to metadata edge (THIS WAS MISSING!)
                edges.append({
                    "_from": f"{CollectionNames.RECORDS.value}/{message_key}",
                    "_to": f"{CollectionNames.SLACK_MESSAGE_METADATA.value}/{message_key}",
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp
                })

                # Handle thread relationships
                thread_ts = event.get('thread_ts')
                if thread_ts:
                    # This is a thread reply, find parent message
                    parent_key = await self._find_or_fetch_parent_message(
                        thread_ts,
                        channel_id,
                        org_id,
                        txn
                    )
                    if parent_key:
                        # Create parent-child relationship
                        edges.append({
                            "_from": f"{CollectionNames.RECORDS.value}/{parent_key}",
                            "_to": f"{CollectionNames.RECORDS.value}/{message_key}",
                            "relationshipType": RecordRelations.PARENT_CHILD.value,
                            "createdAtTimestamp": timestamp,
                            "updatedAtTimestamp": timestamp
                        })

                # Create all edges
                if edges:
                    await self.arango_service.batch_create_edges(
                        edges,
                        CollectionNames.RECORD_RELATIONS.value,
                        transaction=txn
                    )

                # Process files if any - use deduplication to avoid conflicts with file_shared events
                processed_files = []
                file_kafka_events = []
                if event.get('files'):
                    processed_files, file_kafka_events = await self._process_message_files_deduplicated(
                        event.get('files'),
                        message_key,
                        channel_key,
                        org_id,
                        txn
                    )

                # Commit transaction
                await asyncio.to_thread(lambda: txn.commit_transaction())

                # Generate Kafka events - Create individual message event
                kafka_events = []

                # Create Kafka event for the message
                message_kafka_event = await self._create_message_kafka_event(
                    message_record,
                    message_metadata,
                    org_id
                )
                if message_kafka_event:
                    kafka_events.append(message_kafka_event)

                # Add file Kafka events
                kafka_events.extend(file_kafka_events)

                yield EventProcessingResult(
                    processed_records=[message_record] + processed_files,
                    kafka_events=kafka_events,
                    success=True
                )

            except Exception as e:
                if hasattr(txn, 'abort_transaction'):
                    await asyncio.to_thread(lambda: txn.abort_transaction())
                raise e

        except Exception as e:
            self.logger.error(f"Error handling message event: {str(e)}")
            yield EventProcessingResult(success=False, error_message=str(e))

    async def _handle_file_only_message_generator(self, event: Dict[str, Any], org_id: str) -> AsyncIterator[EventProcessingResult]:
        """Handle message events that only contain files (no text)"""
        try:
            # Begin transaction
            txn = self.arango_service.db.begin_transaction(
                write=[
                    CollectionNames.RECORDS.value,
                    CollectionNames.SLACK_ATTACHMENT_METADATA.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.SLACK_FILE_TO_ATTACHMENT_METADATA.value
                ]
            )

            try:
                timestamp = get_epoch_timestamp_in_ms()
                event.get('user', '')

                # Get channel info
                channel_id = event.get('channel')
                channel = await self.arango_service.get_channel_by_slack_id(channel_id, org_id, txn)
                if not channel:
                    # Channel doesn't exist, create it
                    workspace = await self.arango_service.get_workspace_by_org_id(org_id)
                    if not workspace:
                        self.logger.error(f"Workspace not found for org_id: {org_id}")
                        yield EventProcessingResult(success=False, error_message="Workspace not found")
                        return

                    channel_key = str(uuid.uuid4())
                    channel_record = {
                        "_key": channel_key,
                        "orgId": org_id,
                        "groupName": "New Channel",
                        "externalGroupId": channel_id,
                        "groupType": GroupType.SLACK_CHANNEL.value,
                        "connectorName": Connectors.SLACK.value,
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp,
                        "isDeletedAtSource": False,
                        "sourceCreatedAtTimestamp": timestamp,
                        "sourceLastModifiedTimestamp": timestamp,
                        "syncState": ProgressStatus.NOT_STARTED.value,
                    }

                    await self.arango_service.batch_upsert_nodes(
                        [channel_record],
                        CollectionNames.RECORD_GROUPS.value,
                        transaction=txn
                    )

                    # Create workspace relationship
                    workspace_edge = {
                        "_from": f"{CollectionNames.RECORD_GROUPS.value}/{channel_key}",
                        "_to": f"{CollectionNames.SLACK_WORKSPACES.value}/{workspace['_key']}",
                        "entityType": "WORKSPACE",
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp
                    }
                    await self.arango_service.batch_create_edges(
                        [workspace_edge],
                        CollectionNames.BELONGS_TO_SLACK_WORKSPACE.value,
                        transaction=txn
                    )
                else:
                    channel_key = channel['_key']

                # Process files directly without creating a message record
                processed_files = []
                kafka_events = []

                if event.get('files'):
                    for file in event.get('files'):
                        if not file:
                            continue

                        file_id = file.get('id')
                        if not file_id:
                            continue

                        # Check if file already exists
                        existing_file_key = await self.arango_service.get_key_by_external_file_id(file_id, txn)

                        if existing_file_key:
                            self.logger.info(f"📎 File {file_id} already exists, skipping")
                            continue

                        # Create file record
                        file_key = str(uuid.uuid4())
                        file_name = file.get('name', '').strip()
                        if not file_name:
                            file_name = f"Slack_File_{file_id}"

                        file_record = {
                            "_key": file_key,
                            "orgId": org_id,
                            "recordName": file_name,
                            "externalRecordId": file_id,
                            "recordType": RecordTypes.FILE.value,
                            "version": 0,
                            "origin": OriginTypes.CONNECTOR.value,
                            "connectorName": Connectors.SLACK.value,
                            "virtualRecordId": None,
                            "isLatestVersion": True,
                            "isDirty": False,
                            "createdAtTimestamp": timestamp,
                            "updatedAtTimestamp": timestamp,
                            "lastSyncTimestamp": timestamp,
                            "sourceCreatedAtTimestamp": int(float(file.get('created', 0)) * 1000),
                            "sourceLastModifiedTimestamp": int(float(file.get('updated', 0)) * 1000),
                            "isDeleted": False,
                            "isArchived": False,
                            "reason": None,
                            "lastIndexTimestamp": None,
                            "lastExtractionTimestamp": None,
                            "indexingStatus": ProgressStatus.NOT_STARTED.value,
                            "extractionStatus": ProgressStatus.NOT_STARTED.value,
                        }

                        # Create file metadata
                        file_metadata = {
                            "_key": file_key,
                            "orgId": org_id,
                            "slackFileId": file_id,
                            "name": file_name,
                            "extension": file.get('filetype') or None,
                            "mimeType": file.get('mimetype') or None,
                            "sizeInBytes": file.get('size', 0),
                            "channelId": channel_key,
                            "uploadedBy": file.get('user') or "",
                            "sourceUrls": {
                                "privateUrl": file.get('url_private'),
                                "downloadUrl": file.get('url_private_download'),
                                "publicPermalink": file.get('permalink_public')
                            },
                            "isPublic": file.get('is_public', False),
                            "isEditable": file.get('is_editable', False),
                            "permalink": file.get('permalink'),
                            "richPreview": file.get('has_rich_preview', False)
                        }

                        # Store records
                        await self.arango_service.batch_upsert_nodes(
                            [file_record],
                            CollectionNames.RECORDS.value,
                            transaction=txn
                        )

                        await self.arango_service.batch_upsert_nodes(
                            [file_metadata],
                            CollectionNames.SLACK_ATTACHMENT_METADATA.value,
                            transaction=txn
                        )

                        # Create edges
                        edges = []

                        # File to channel edge
                        edges.append({
                            "_from": f"{CollectionNames.RECORDS.value}/{file_key}",
                            "_to": f"{CollectionNames.RECORD_GROUPS.value}/{channel_key}",
                            "entityType": EntityType.SLACK_CHANNEL.value,
                            "createdAtTimestamp": timestamp,
                            "updatedAtTimestamp": timestamp
                        })

                        # File to metadata edge
                        edges.append({
                            "_from": f"{CollectionNames.RECORDS.value}/{file_key}",
                            "_to": f"{CollectionNames.SLACK_ATTACHMENT_METADATA.value}/{file_key}",
                            "createdAtTimestamp": timestamp,
                            "updatedAtTimestamp": timestamp
                        })

                        # Create all edges
                        await self.arango_service.batch_create_edges(
                            edges,
                            CollectionNames.RECORD_RELATIONS.value,
                            transaction=txn
                        )

                        processed_files.append(file_record)

                        # Create Kafka event for file with metadata
                        file_kafka_event = await self._create_file_kafka_event_with_metadata(
                            file_record, file_metadata, org_id
                        )
                        if file_kafka_event:
                            kafka_events.append(file_kafka_event)

                # Commit transaction
                await asyncio.to_thread(lambda: txn.commit_transaction())

                yield EventProcessingResult(
                    processed_records=processed_files,
                    kafka_events=kafka_events,
                    success=True
                )

            except Exception as e:
                if hasattr(txn, 'abort_transaction'):
                    await asyncio.to_thread(lambda: txn.abort_transaction())
                raise e

        except Exception as e:
            self.logger.error(f"Error handling file-only message event: {str(e)}")
            yield EventProcessingResult(success=False, error_message=str(e))

    # ==================== CHANNEL AND USER EVENT HANDLERS ====================

    async def _handle_channel_event_generator(self, event: Dict[str, Any], org_id: str) -> AsyncIterator[EventProcessingResult]:
        """Handle channel-related events using generator pattern"""
        try:
            event_type = event.get('type')
            channel_data = event.get('channel', {})
            channel_id = channel_data.get('id')

            if not channel_id:
                self.logger.error("Channel ID not found in event data")
                yield EventProcessingResult(success=False, error_message="Channel ID not found")
                return

            timestamp = get_epoch_timestamp_in_ms()

            # Get workspace info for the org
            workspace = await self.arango_service.get_workspace_by_org_id(org_id)
            if not workspace:
                self.logger.error(f"Workspace not found for org_id: {org_id}")
                yield EventProcessingResult(success=False, error_message="Workspace not found")
                return

            # Start transaction
            txn = self.arango_service.db.begin_transaction(
                write=[
                    CollectionNames.RECORD_GROUPS.value,
                    CollectionNames.BELONGS_TO_SLACK_WORKSPACE.value,
                    CollectionNames.BELONGS_TO_SLACK_CHANNEL.value,
                    CollectionNames.RECORDS.value
                ]
            )

            try:
                # Check if channel already exists
                existing_channel = await self.arango_service.get_channel_by_slack_id(channel_id, org_id, txn)
                processed_records = []
                kafka_events = []

                if event_type == 'channel_deleted':
                    if existing_channel:
                        # Mark channel as deleted
                        update_fields = {
                            "isDeletedAtSource": True,
                            "updatedAtTimestamp": timestamp,
                            "sourceLastModifiedTimestamp": timestamp
                        }
                        await self.arango_service.update_channel(existing_channel['_key'], update_fields, txn)

                        # Remove all member relationships
                        query = f"""
                        FOR edge IN {CollectionNames.BELONGS_TO_SLACK_CHANNEL.value}
                            FILTER edge._to == @channel_id
                            REMOVE edge IN {CollectionNames.BELONGS_TO_SLACK_CHANNEL.value}
                        """
                        bind_vars = {"channel_id": f"{CollectionNames.RECORD_GROUPS.value}/{existing_channel['_key']}"}
                        await asyncio.to_thread(lambda: txn.execute(query, bind_vars=bind_vars))

                        processed_records.append({**existing_channel, **update_fields})

                elif event_type == 'channel_archive':
                    if existing_channel:
                        update_fields = {
                            "isArchived": True,
                            "updatedAtTimestamp": timestamp,
                            "sourceLastModifiedTimestamp": timestamp
                        }
                        await self.arango_service.update_channel(existing_channel['_key'], update_fields, txn)
                        processed_records.append({**existing_channel, **update_fields})

                elif event_type == 'channel_unarchive':
                    if existing_channel:
                        update_fields = {
                            "isArchived": False,
                            "updatedAtTimestamp": timestamp,
                            "sourceLastModifiedTimestamp": timestamp
                        }
                        await self.arango_service.update_channel(existing_channel['_key'], update_fields, txn)
                        processed_records.append({**existing_channel, **update_fields})

                elif event_type in ['channel_created', 'channel_rename']:
                    channel_key = existing_channel['_key'] if existing_channel else str(uuid.uuid4())

                    # Create or update channel record
                    channel_record = {
                        "_key": channel_key,
                        "orgId": org_id,
                        "groupName": channel_data.get('name', 'Unnamed Channel'),
                        "externalGroupId": channel_id,
                        "groupType": GroupType.SLACK_CHANNEL.value,
                        "connectorName": Connectors.SLACK.value,
                        "createdAtTimestamp": existing_channel['createdAtTimestamp'] if existing_channel else timestamp,
                        "updatedAtTimestamp": timestamp,
                        "isDeletedAtSource": False,
                        "isArchived": channel_data.get('is_archived', False),
                        "sourceCreatedAtTimestamp": channel_data.get('created', 0) * 1000,
                        "sourceLastModifiedTimestamp": timestamp,
                        "syncState": existing_channel.get('syncState', ProgressStatus.NOT_STARTED.value) if existing_channel else ProgressStatus.NOT_STARTED.value,
                    }

                    # Store channel
                    await self.arango_service.batch_upsert_nodes(
                        [channel_record],
                        CollectionNames.RECORD_GROUPS.value,
                        transaction=txn
                    )

                    # If new channel, create workspace relationship
                    if not existing_channel:
                        workspace_edge = {
                            "_from": f"{CollectionNames.RECORD_GROUPS.value}/{channel_key}",
                            "_to": f"{CollectionNames.SLACK_WORKSPACES.value}/{workspace['_key']}",
                            "entityType": EntityType.WORKSPACE.value,
                            "createdAtTimestamp": timestamp,
                            "updatedAtTimestamp": timestamp
                        }
                        await self.arango_service.batch_create_edges(
                            [workspace_edge],
                            CollectionNames.BELONGS_TO_SLACK_WORKSPACE.value,
                            transaction=txn
                        )

                    processed_records.append(channel_record)

                # Commit transaction
                await asyncio.to_thread(lambda: txn.commit_transaction())

                yield EventProcessingResult(
                    processed_records=processed_records,
                    kafka_events=kafka_events,
                    success=True
                )

            except Exception as e:
                if hasattr(txn, 'abort_transaction'):
                    await asyncio.to_thread(lambda: txn.abort_transaction())
                raise e

        except Exception as e:
            self.logger.error(f"Error handling channel event: {str(e)}")
            yield EventProcessingResult(success=False, error_message=str(e))

    async def _handle_channel_membership_event_generator(self, event: Dict[str, Any], org_id: str) -> AsyncIterator[EventProcessingResult]:
        """Handle channel membership changes using generator pattern"""
        try:
            event_type = event.get('type')
            channel_id = event.get('channel')
            user_id = event.get('user')
            inviter_id = event.get('inviter')  # For invited members

            if not channel_id or not user_id:
                self.logger.error("Missing channel_id or user_id in event data")
                yield EventProcessingResult(success=False, error_message="Missing channel_id or user_id")
                return

            timestamp = get_epoch_timestamp_in_ms()

            # Start transaction
            txn = self.arango_service.db.begin_transaction(
                write=[
                    CollectionNames.BELONGS_TO_SLACK_CHANNEL.value,
                    CollectionNames.RECORD_GROUPS.value,
                    CollectionNames.USERS.value
                ]
            )

            try:
                # Get channel and user records
                channel = await self.arango_service.get_channel_by_slack_id(channel_id, org_id, txn)
                user = await self.arango_service.get_user_by_slack_id(user_id, org_id, txn)

                if not channel or not user:
                    self.logger.error("Channel or user not found")
                    yield EventProcessingResult(success=False, error_message="Channel or user not found")
                    return

                processed_records = []

                if event_type in ['member_joined_channel', 'group_joined']:
                    # Check if membership already exists
                    edge_exists = await self.arango_service.check_edge_exists(
                        f"{CollectionNames.USERS.value}/{user['_key']}",
                        f"{CollectionNames.RECORD_GROUPS.value}/{channel['_key']}",
                        CollectionNames.BELONGS_TO_SLACK_CHANNEL.value
                    )

                    if not edge_exists:
                        # Create membership edge
                        edge = {
                            "_from": f"{CollectionNames.USERS.value}/{user['_key']}",
                            "_to": f"{CollectionNames.RECORD_GROUPS.value}/{channel['_key']}",
                            "entityType": EntityType.SLACK_CHANNEL.value,
                            "createdAtTimestamp": timestamp,
                            "updatedAtTimestamp": timestamp,
                            "invitedBy": inviter_id
                        }

                        await self.arango_service.batch_create_edges(
                            [edge],
                            CollectionNames.BELONGS_TO_SLACK_CHANNEL.value,
                            transaction=txn
                        )

                        update_fields = {
                            "updatedAtTimestamp": timestamp
                        }
                        await self.arango_service.update_channel(channel['_key'], update_fields, txn)

                        processed_records.append(edge)

                elif event_type in ['member_left_channel', 'group_left']:
                    # Remove membership edge
                    query = f"""
                    FOR edge IN {CollectionNames.BELONGS_TO_SLACK_CHANNEL.value}
                        FILTER edge._from == @from AND edge._to == @to
                        REMOVE edge IN {CollectionNames.BELONGS_TO_SLACK_CHANNEL.value}
                    """
                    bind_vars = {
                        "from": f"{CollectionNames.USERS.value}/{user['_key']}",
                        "to": f"{CollectionNames.RECORD_GROUPS.value}/{channel['_key']}"
                    }
                    await asyncio.to_thread(lambda: txn.execute(query, bind_vars=bind_vars))

                    update_fields = {
                        "updatedAtTimestamp": timestamp
                    }
                    await self.arango_service.update_channel(channel['_key'], update_fields, txn)

                # Commit transaction
                await asyncio.to_thread(lambda: txn.commit_transaction())

                yield EventProcessingResult(
                    processed_records=processed_records,
                    kafka_events=[],  # Membership events don't generate Kafka events
                    success=True
                )

            except Exception as e:
                if hasattr(txn, 'abort_transaction'):
                    await asyncio.to_thread(lambda: txn.abort_transaction())
                raise e

        except Exception as e:
            self.logger.error(f"Error handling channel membership event: {str(e)}")
            yield EventProcessingResult(success=False, error_message=str(e))

    async def _handle_user_event_generator(self, event: Dict[str, Any], org_id: str) -> AsyncIterator[EventProcessingResult]:
        """Handle user events using generator pattern"""
        try:
            user_data = event.get('user')
            if not user_data:
                self.logger.error("User data not found in event")
                yield EventProcessingResult(success=False, error_message="User data not found")
                return

            timestamp = get_epoch_timestamp_in_ms()

            # Get workspace info
            workspace = await self.arango_service.get_workspace_by_org_id(org_id)
            if not workspace:
                self.logger.error(f"Workspace not found for org_id: {org_id}")
                yield EventProcessingResult(success=False, error_message="Workspace not found")
                return

            # Start transaction with all necessary collections
            txn = self.arango_service.db.begin_transaction(
                write=[
                    CollectionNames.USERS.value,
                    CollectionNames.BELONGS_TO.value,
                    CollectionNames.BELONGS_TO_SLACK_CHANNEL.value
                ]
            )

            try:
                # Check if user exists
                existing_user = await self.arango_service.get_user_by_slack_id(user_data.get('id'), org_id, txn)
                processed_records = []

                if existing_user:
                    # Update existing user
                    update_fields = {
                        "email": user_data.get('profile', {}).get('email'),
                        "fullName": user_data.get('profile', {}).get('real_name') or user_data.get('name'),
                        "isActive": not user_data.get('deleted', False),
                        "updatedAtTimestamp": timestamp
                    }
                    await self.arango_service.update_user(existing_user['_key'], update_fields, txn)
                    processed_records.append({**existing_user, **update_fields})
                else:
                    # Create new user
                    user_key = str(uuid.uuid4())
                    user_record = {
                        "_key": user_key,
                        "orgId": org_id,
                        "userId": user_data.get('id'),
                        "firstName": user_data.get('profile', {}).get('first_name'),
                        "lastName": user_data.get('profile', {}).get('last_name'),
                        "fullName": user_data.get('profile', {}).get('real_name') or user_data.get('name'),
                        "email": user_data.get('profile', {}).get('email'),
                        "designation": (
                            "Primary Owner" if user_data.get('is_primary_owner') else
                            "Owner" if user_data.get('is_owner') else
                            "Admin" if user_data.get('is_admin') else
                            "Bot" if user_data.get('is_bot') else
                            "Member"
                        ),
                        "isActive": not user_data.get('deleted', False),
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp,
                    }

                    # Store user
                    await self.arango_service.batch_upsert_nodes(
                        [user_record],
                        CollectionNames.USERS.value,
                        transaction=txn
                    )

                    # Create workspace membership edge
                    workspace_edge = {
                        "_from": f"{CollectionNames.USERS.value}/{user_key}",
                        "_to": f"{CollectionNames.SLACK_WORKSPACES.value}/{workspace['_key']}",
                        "entityType": EntityType.WORKSPACE.value,
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp
                    }

                    await self.arango_service.batch_create_edges(
                        [workspace_edge],
                        CollectionNames.BELONGS_TO.value,
                        transaction=txn
                    )

                    processed_records.append(user_record)

                # Commit transaction
                await asyncio.to_thread(lambda: txn.commit_transaction())

                yield EventProcessingResult(
                    processed_records=processed_records,
                    kafka_events=[],  # User events don't generate Kafka events
                    success=True
                )

            except Exception as e:
                if hasattr(txn, 'abort_transaction'):
                    await asyncio.to_thread(lambda: txn.abort_transaction())
                raise e

        except Exception as e:
            self.logger.error(f"Error handling user event: {str(e)}")
            yield EventProcessingResult(success=False, error_message=str(e))

    # ==================== MESSAGE FILE PROCESSING ====================

    async def _process_message_files_deduplicated(
        self,
        files: List[Dict[str, Any]],
        message_key: str,
        channel_key: str,
        org_id: str,
        txn
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Process files attached to a message with deduplication to avoid conflicts with file_shared events
        Returns:
            Tuple of (processed_files, kafka_events)
        """
        try:
            processed_files = []
            kafka_events = []
            timestamp = get_epoch_timestamp_in_ms()

            for file in files:
                if not file:
                    continue

                file_id = file.get('id')
                if not file_id:
                    continue

                # Check if file already exists (from file_shared event)
                existing_file_key = await self.arango_service.get_key_by_external_file_id(file_id, txn)

                if existing_file_key:
                    # File already exists, just create the attachment relationship
                    self.logger.info(f"📎 File {file_id} already exists, creating attachment relationship only")

                    # Create message to file edge (attachment relationship)
                    attachment_edge = {
                        "_from": f"{CollectionNames.RECORDS.value}/{message_key}",
                        "_to": f"{CollectionNames.RECORDS.value}/{existing_file_key}",
                        "relationshipType": RecordRelations.ATTACHMENT.value,
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp
                    }

                    await self.arango_service.batch_create_edges(
                        [attachment_edge],
                        CollectionNames.RECORD_RELATIONS.value,
                        transaction=txn
                    )

                    # Add existing file to processed list (for return)
                    existing_file_record = await self.arango_service.get_document(existing_file_key, CollectionNames.RECORDS.value, transaction=txn)
                    if existing_file_record:
                        processed_files.append(existing_file_record)

                        # Create Kafka event for existing file (since it wasn't sent before)
                        file_kafka_event = await self._create_file_kafka_event(existing_file_record, org_id)
                        if file_kafka_event:
                            kafka_events.append(file_kafka_event)

                    continue

                # File doesn't exist, create it
                file_key = str(uuid.uuid4())

                # Validate and clean file name
                file_name = file.get('name', '').strip()
                if not file_name:
                    file_name = f"Slack_File_{file_id}"
                    self.logger.warning(f"⚠️ Empty file name for {file_id}, using fallback: {file_name}")

                # Create file record
                file_record = {
                    "_key": file_key,
                    "orgId": org_id,
                    "recordName": file_name,
                    "externalRecordId": file_id,
                    "recordType": RecordTypes.FILE.value,
                    "version": 0,
                    "origin": OriginTypes.CONNECTOR.value,
                    "connectorName": Connectors.SLACK.value,
                    "virtualRecordId": None,
                    "isLatestVersion": True,
                    "isDirty": False,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                    "lastSyncTimestamp": timestamp,
                    "sourceCreatedAtTimestamp": int(float(file.get('created', 0)) * 1000),
                    "sourceLastModifiedTimestamp": int(float(file.get('updated', 0)) * 1000),
                    "isDeleted": False,
                    "isArchived": False,
                    "reason": None,
                    "lastIndexTimestamp": None,
                    "lastExtractionTimestamp": None,
                    "indexingStatus": ProgressStatus.NOT_STARTED.value,
                    "extractionStatus": ProgressStatus.NOT_STARTED.value,
                }

                # Create file metadata with proper validation
                file_metadata = {
                    "_key": file_key,
                    "orgId": org_id,
                    "slackFileId": file_id,
                    "name": file_name,
                    "extension": file.get('filetype') or None,
                    "mimeType": file.get('mimetype') or None,
                    "sizeInBytes": file.get('size', 0),
                    "channelId": channel_key,
                    "uploadedBy": file.get('user') or "",
                    "sourceUrls": {
                        "privateUrl": file.get('url_private'),
                        "downloadUrl": file.get('url_private_download'),
                        "publicPermalink": file.get('permalink_public')
                    },
                    "isPublic": file.get('is_public', False),
                    "isEditable": file.get('is_editable', False),
                    "permalink": file.get('permalink'),
                    "richPreview": file.get('has_rich_preview', False)
                }

                # Store records
                await self.arango_service.batch_upsert_nodes(
                    [file_record],
                    CollectionNames.RECORDS.value,
                    transaction=txn
                )

                await self.arango_service.batch_upsert_nodes(
                    [file_metadata],
                    CollectionNames.SLACK_ATTACHMENT_METADATA.value,
                    transaction=txn
                )

                # Create edges
                edges = []

                # File to channel edge
                edges.append({
                    "_from": f"{CollectionNames.RECORDS.value}/{file_key}",
                    "_to": f"{CollectionNames.RECORD_GROUPS.value}/{channel_key}",
                    "entityType": EntityType.SLACK_CHANNEL.value,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp
                })

                # File to metadata edge
                edges.append({
                    "_from": f"{CollectionNames.RECORDS.value}/{file_key}",
                    "_to": f"{CollectionNames.SLACK_ATTACHMENT_METADATA.value}/{file_key}",
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp
                })

                # Message to file edge (attachment relationship)
                edges.append({
                    "_from": f"{CollectionNames.RECORDS.value}/{message_key}",
                    "_to": f"{CollectionNames.RECORDS.value}/{file_key}",
                    "relationshipType": RecordRelations.ATTACHMENT.value,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp
                })

                # Create all edges
                await self.arango_service.batch_create_edges(
                    edges,
                    CollectionNames.RECORD_RELATIONS.value,
                    transaction=txn
                )

                processed_files.append(file_record)

                # Create Kafka event for new file
                file_kafka_event = await self._create_file_kafka_event_with_metadata(
                    file_record, file_metadata, org_id
                )
                if file_kafka_event:
                    kafka_events.append(file_kafka_event)

            return processed_files, kafka_events

        except Exception as e:
            self.logger.error(f"Error processing message files: {str(e)}")
            if txn:
                raise
            return [], []

    async def _create_message_kafka_event(
        self,
        message_record: Dict[str, Any],
        message_metadata: Dict[str, Any],
        org_id: str
    ) -> Optional[Dict[str, Any]]:
        """Create individual Kafka event for message"""
        try:
            # Get connector endpoint with proper error handling
            try:
                connector_endpoint = await self.config_service.get_config(config_node_constants.CONNECTOR_ENDPOINT.value)
                if not connector_endpoint:
                    connector_endpoint = "http://localhost:8088"  # Default fallback
            except Exception as config_error:
                connector_endpoint = "http://localhost:8088"  # Default fallback
                self.logger.warning(f"⚠️ Failed to get CONNECTOR_ENDPOINT config: {str(config_error)}, using fallback: {connector_endpoint}")

            event = {
                "orgId": org_id,
                "recordId": message_record.get('_key'),
                "recordName": message_record.get('recordName', ''),
                "recordType": RecordTypes.MESSAGE.value,
                "eventType": EventTypes.NEW_RECORD.value,
                "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/slack/record/{message_record.get('_key')}/signedUrl",
                "connectorName": Connectors.SLACK.value,
                "origin": OriginTypes.CONNECTOR.value,
                "mimeType": "text/slack_content",
                "createdAtSourceTimestamp": message_record.get('sourceCreatedAtTimestamp'),
                "modifiedAtSourceTimestamp": message_record.get('sourceLastModifiedTimestamp'),
                "body": {
                    "text": message_metadata.get('text', ''),
                    "userId": message_metadata.get('userId'),
                    "channelId": message_metadata.get('channelId'),
                    "threadTs": message_metadata.get('threadTs'),
                    "messageType": message_metadata.get('messageType'),
                    "replyCount": message_metadata.get('replyCount', 0),
                    "hasFiles": message_metadata.get('hasFiles', False),
                    "fileCount": message_metadata.get('fileCount', 0),
                    "mentionedUsers": message_metadata.get('mentionedUsers', []),
                    "links": message_metadata.get('links', [])
                }
            }

            return event

        except Exception as e:
            self.logger.error(f"❌ Error creating message Kafka event: {str(e)}")
            return None

    async def _find_or_fetch_parent_message(
        self,
        thread_ts: str,
        channel_id: str,
        org_id: str,
        txn
    ) -> Optional[str]:
        """Find existing parent message or fetch and store it"""
        try:
            # Check if parent exists in database
            self.logger.info(f"🔍 Looking for parent message with thread_ts: {thread_ts}")
            parent_result = await self.arango_service.get_message_by_slack_ts(thread_ts, org_id, txn)

            if parent_result:
                self.logger.info(f"📋 Parent result structure: {parent_result}")

                # The method returns a dict with 'message' and 'metadata' keys
                if isinstance(parent_result, dict):
                    # Extract the message record
                    if 'message' in parent_result and parent_result['message']:
                        parent_key = parent_result['message']['_key']
                        self.logger.info(f"✅ Found parent message key: {parent_key}")
                        return parent_key
                    elif '_key' in parent_result:
                        # Direct record case
                        parent_key = parent_result['_key']
                        self.logger.info(f"✅ Found parent message key (direct): {parent_key}")
                        return parent_key
                else:
                    # Handle case where it might return the record directly
                    if hasattr(parent_result, 'get') and parent_result.get('_key'):
                        parent_key = parent_result['_key']
                        self.logger.info(f"✅ Found parent message key (hasattr): {parent_key}")
                        return parent_key

            self.logger.warning(f"⚠️ Parent message with timestamp {thread_ts} not found in database")
            return None

        except Exception as e:
            self.logger.error(f"❌ Error finding parent message: {str(e)}")
            import traceback
            self.logger.error(f"❌ Stack trace: {traceback.format_exc()}")
            return None

    def _extract_mentions(self, message: Dict[str, Any]) -> List[str]:
        """Extract user mentions from message"""
        mentions = []
        text = message.get('text', '')

        import re
        user_mentions = re.findall(r'<@([A-Z0-9]+)>', text)
        mentions.extend(user_mentions)

        return mentions

    def _extract_links(self, message: Dict[str, Any]) -> List[str]:
        """Extract links from message"""
        links = []
        text = message.get('text', '')

        import re
        url_pattern = r'<(https?://[^>]+)(?:\|[^>]+)?>'
        urls = re.findall(url_pattern, text)
        links.extend(urls)

        return links

    async def _create_file_kafka_event_with_metadata(
        self,
        file_record: Dict[str, Any],
        file_metadata: Dict[str, Any],
        org_id: str
    ) -> Optional[Dict[str, Any]]:
        """Create individual Kafka event for file with pre-existing metadata"""
        try:
            # Get connector endpoint with proper error handling
            try:
                connector_endpoint = await self.config_service.get_config(config_node_constants.CONNECTOR_ENDPOINT.value)
                if not connector_endpoint:
                    connector_endpoint = "http://localhost:8088"  # Default fallback
            except Exception as config_error:
                connector_endpoint = "http://localhost:8088"  # Default fallback
                self.logger.warning(f"⚠️ Failed to get CONNECTOR_ENDPOINT config: {str(config_error)}, using fallback: {connector_endpoint}")

            # Get data directly from metadata
            mime_type = file_metadata.get('mimeType') or "application/octet-stream"
            file_size = file_metadata.get('sizeInBytes', 0)
            uploaded_by = file_metadata.get('uploadedBy', "")
            is_public = file_metadata.get('isPublic', False)
            is_editable = file_metadata.get('isEditable', False)
            channel_id = file_metadata.get('channelId', "")

            event = {
                "orgId": org_id,
                "recordId": file_record.get('_key'),
                "recordName": file_record.get('recordName', ''),
                "recordType": RecordTypes.FILE.value,
                "eventType": EventTypes.NEW_RECORD.value,
                "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/slack/record/{file_record.get('_key')}/signedUrl",
                "connectorName": Connectors.SLACK.value,
                "origin": OriginTypes.CONNECTOR.value,
                "mimeType": mime_type,
                "createdAtSourceTimestamp": file_record.get('sourceCreatedAtTimestamp'),
                "modifiedAtSourceTimestamp": file_record.get('sourceLastModifiedTimestamp'),
                "body": {
                    "fileName": file_record.get('recordName'),
                    "fileSize": file_size,
                    "uploadedBy": uploaded_by,
                    "isPublic": is_public,
                    "isEditable": is_editable,
                    "channelId": channel_id,
                    "extension": file_metadata.get('extension'),
                    "sourceUrls": file_metadata.get('sourceUrls', {}),
                    "permalink": file_metadata.get('permalink'),
                    "richPreview": file_metadata.get('richPreview', False)
                }
            }

            return event

        except Exception as e:
            self.logger.error(f"❌ Error creating file Kafka event with metadata: {str(e)}")
            return None

    async def _create_file_kafka_event(self, file_record: Dict[str, Any], org_id: str) -> Optional[Dict[str, Any]]:
        """Create individual Kafka event for file"""
        try:
            # Get connector endpoint with proper error handling
            try:
                connector_endpoint = await self.config_service.get_config(config_node_constants.CONNECTOR_ENDPOINT.value)
                if not connector_endpoint:
                    connector_endpoint = "http://localhost:8088"  # Default fallback
            except Exception as config_error:
                connector_endpoint = "http://localhost:8088"  # Default fallback
                self.logger.warning(f"⚠️ Failed to get CONNECTOR_ENDPOINT config: {str(config_error)}, using fallback: {connector_endpoint}")

            # Get file metadata for enriched event data
            file_metadata = None
            try:
                file_metadata = await self.arango_service.get_document(
                    file_record.get('_key'),
                    CollectionNames.SLACK_ATTACHMENT_METADATA.value
                )
            except Exception as metadata_error:
                self.logger.warning(f"⚠️ Could not retrieve file metadata for {file_record.get('_key')}: {str(metadata_error)}")

            # Determine MIME type and file size from metadata
            mime_type = "application/octet-stream"
            file_size = 0
            uploaded_by = ""
            is_public = False
            is_editable = False

            if file_metadata:
                mime_type = file_metadata.get('mimeType') or mime_type
                file_size = file_metadata.get('sizeInBytes', 0)
                uploaded_by = file_metadata.get('uploadedBy', "")
                is_public = file_metadata.get('isPublic', False)
                is_editable = file_metadata.get('isEditable', False)

            event = {
                "orgId": org_id,
                "recordId": file_record.get('_key'),
                "recordName": file_record.get('recordName', ''),
                "recordType": RecordTypes.FILE.value,
                "eventType": EventTypes.NEW_RECORD.value,
                "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/slack/record/{file_record.get('_key')}/signedUrl",
                "connectorName": Connectors.SLACK.value,
                "origin": OriginTypes.CONNECTOR.value,
                "mimeType": mime_type,
                "createdAtSourceTimestamp": file_record.get('sourceCreatedAtTimestamp'),
                "modifiedAtSourceTimestamp": file_record.get('sourceLastModifiedTimestamp'),
                "body": {
                    "fileName": file_record.get('recordName'),
                    "fileSize": file_size,
                    "uploadedBy": uploaded_by,
                    "isPublic": is_public,
                    "isEditable": is_editable
                }
            }

            return event

        except Exception as e:
            self.logger.error(f"❌ Error creating file Kafka event: {str(e)}")
            return None

    async def _send_kafka_events_generator(
        self,
        kafka_events: List[Dict[str, Any]],
        org_id: str
    ) -> AsyncIterator[Dict[str, Any]]:
        """Send Kafka events using generator pattern"""
        try:
            if not self.kafka_service or not kafka_events:
                yield {"sent": 0, "errors": []}
                return

            async for event_batch_stats in self._create_kafka_event_generator(kafka_events, org_id):
                yield event_batch_stats

        except Exception as e:
            self.logger.error(f"❌ Error sending to Kafka: {str(e)}")
            yield {"sent": 0, "errors": [str(e)]}

    async def _create_kafka_event_generator(
        self,
        events: List[Dict[str, Any]],
        org_id: str
    ) -> AsyncIterator[Dict[str, Any]]:
        """Generator that yields Kafka event batches"""
        try:
            kafka_batch_size = 20  # Kafka-specific batch size

            for i in range(0, len(events), kafka_batch_size):
                batch = events[i:i + kafka_batch_size]
                batch_stats = {"sent": 0, "errors": []}

                try:
                    for event in batch:
                        # Add orgId to event if not present
                        if "orgId" not in event:
                            event["orgId"] = org_id

                        # Send to Kafka like SlackDataHandler does
                        await self.kafka_service.send_event_to_kafka(event)
                        batch_stats["sent"] += 1

                    # Small delay between batches
                    await asyncio.sleep(0.1)

                except Exception as e:
                    self.logger.error(f"❌ Error sending Kafka batch: {str(e)}")
                    batch_stats["errors"].append(str(e))

                yield batch_stats

        except Exception as e:
            self.logger.error(f"❌ Kafka event generator error: {str(e)}")
            yield {"sent": 0, "errors": [str(e)]}

    # ==================== CLEANUP METHODS ====================

    async def cleanup_resources(self) -> None:
        """Cleanup resources and free memory"""
        try:
            # Clear user message windows
            self._user_message_windows.clear()

            self.logger.info("🧹 Cleaned up user message windows")

        except Exception as e:
            self.logger.error(f"❌ Error during cleanup: {str(e)}")

    def get_active_user_windows_count(self) -> int:
        """Get count of active user message windows"""
        return len(self._user_message_windows)

    async def flush_all_user_windows(self, org_id: str) -> List[Dict[str, Any]]:
        """Flush all remaining user windows to Kafka"""
        try:
            all_kafka_events = []

            for user_id, window in self._user_message_windows.items():
                if window.messages:
                    events = await self._create_kafka_events_from_window(window, org_id)
                    all_kafka_events.extend(events)

            # Clear windows after flushing
            self._user_message_windows.clear()

            # Send all events to Kafka
            if all_kafka_events:
                async for kafka_stats in self._send_kafka_events_generator(all_kafka_events, org_id):
                    self.logger.info(f"Flushed {kafka_stats['sent']} Kafka events from user windows")

            return all_kafka_events

        except Exception as e:
            self.logger.error(f"❌ Error flushing user windows: {str(e)}")
            return []
