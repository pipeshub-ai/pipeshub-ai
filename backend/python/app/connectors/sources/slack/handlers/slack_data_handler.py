import asyncio
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Optional

from app.config.configuration_service import ConfigurationService, config_node_constants
from app.config.utils.named_constants.arangodb_constants import (
    CollectionNames,
    Connectors,
    EntityType,
    EventTypes,
    GroupType,
    MessageType,
    OriginTypes,
    PermissionType,
    ProgressStatus,
    RecordRelations,
    RecordTypes,
)
from app.config.utils.named_constants.timestamp_constants import (
    SYNC_ERROR_RATE_THRESHOLD,
    SYNC_LAG_WARNING_MINUTES,
)
from app.connectors.services.kafka_service import KafkaService
from app.connectors.sources.google.common.arango_service import ArangoService
from app.utils.time_conversion import get_epoch_timestamp_in_ms


@dataclass
class UserMessageWindow:
    """Container for user messages within time window"""
    user_id: str
    messages: List[Dict[str, Any]] = field(default_factory=list)
    files: List[Dict[str, Any]] = field(default_factory=list)
    window_start: Optional[float] = None
    window_end: Optional[float] = None


@dataclass
class SyncCheckpoint:
    """Represents a sync checkpoint for recovery"""
    channel_id: str
    last_processed_ts: Optional[str] = None
    processed_count: int = 0
    error_count: int = 0
    last_cursor: Optional[str] = None
    checkpoint_timestamp: int = 0


@dataclass
class ProcessedBatch:
    """Container for processed batch data"""
    message_records: List[Dict[str, Any]]
    message_metadata: List[Dict[str, Any]]
    file_records: List[Dict[str, Any]]
    file_metadata: List[Dict[str, Any]]
    edges: Dict[str, List[Dict[str, Any]]]
    checkpoint: SyncCheckpoint


class SyncState(Enum):
    """Sync state enumeration"""
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RECOVERING = "RECOVERING"


class SlackDataHandler:
    """Optimized Slack Data Handler with proper Kafka events and user message grouping"""

    def __init__(
        self,
        logger,
        arango_service: ArangoService,
        slack_client,
        kafka_service: KafkaService,
        config: ConfigurationService,
    ) -> None:
        self.logger = logger
        self.arango_service = arango_service
        self.slack_client = slack_client
        self.kafka_service = kafka_service
        self.config = config
        self.batch_size = 50  # Optimized batch size
        self.rate_limit_delay = 1.0
        self.checkpoint_interval = 100

        # State tracking
        self._active_generators = {}
        self._sync_checkpoints = {}

        # User message windows for grouping (same as SlackDataHandler)
        self.user_message_window_seconds = 60  # 1 minute window for user message grouping
        self._user_message_windows = defaultdict(lambda: UserMessageWindow(user_id=""))

    # ==================== WORKSPACE SYNC METHODS ====================

    async def sync_workspace_data(
        self,
        workspace_id: str,
        org_id: str
    ) -> Dict[str, Any]:
        """Sync complete workspace data using optimized generators"""
        try:
            self.logger.info("🚀 Starting workspace sync: %s", workspace_id)

            sync_stats = {
                "users_processed": 0,
                "channels_processed": 0,
                "messages_processed": 0,
                "files_processed": 0,
                "errors": []
            }

            # Begin transaction for workspace setup
            txn = self.arango_service.db.begin_transaction(
                read=[
                    CollectionNames.USERS.value,
                    CollectionNames.RECORD_GROUPS.value,
                    CollectionNames.RECORDS.value,
                    CollectionNames.SLACK_WORKSPACES.value,
                    CollectionNames.SLACK_MESSAGE_METADATA.value,
                    CollectionNames.SLACK_ATTACHMENT_METADATA.value,
                ],
                write=[
                    CollectionNames.USERS.value,
                    CollectionNames.RECORD_GROUPS.value,
                    CollectionNames.RECORDS.value,
                    CollectionNames.SLACK_MESSAGE_METADATA.value,
                    CollectionNames.SLACK_ATTACHMENT_METADATA.value,
                    CollectionNames.PERMISSIONS.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.IS_OF_TYPE.value,
                    CollectionNames.SLACK_WORKSPACES.value,
                    CollectionNames.BELONGS_TO_SLACK_WORKSPACE.value,
                    CollectionNames.BELONGS_TO_SLACK_CHANNEL.value,
                    CollectionNames.BELONGS_TO.value,
                    CollectionNames.SLACK_MESSAGE_TO_METADATA.value,
                    CollectionNames.SLACK_FILE_TO_ATTACHMENT_METADATA.value,
                ]
            )

            # 1. Sync workspace info
            workspace_key = await self._sync_workspace_info(workspace_id, org_id, txn)
            if not workspace_key:
                sync_stats["errors"].append("Failed to sync workspace info")
                return sync_stats

            # 2. Sync users
            users_stats = await self._sync_users_generator(org_id, workspace_key)
            sync_stats["users_processed"] = users_stats["processed"]
            sync_stats["errors"].extend(users_stats["errors"])

            # 3. Sync channels
            channels_stats = await self._sync_channels_generator(workspace_key, org_id)
            sync_stats["channels_processed"] = channels_stats["processed"]
            sync_stats["errors"].extend(channels_stats["errors"])

            # 4. Sync channel members
            await self._sync_channel_members_generator(org_id)

            # 5. Sync messages
            messages_stats = await self._sync_workspace_messages_generator(org_id)
            sync_stats["messages_processed"] = messages_stats["processed"]
            sync_stats["files_processed"] = messages_stats["files_processed"]
            sync_stats["errors"].extend(messages_stats["errors"])

            self.logger.info("✅ Workspace sync completed: %s", sync_stats)
            return sync_stats

        except Exception as e:
            self.logger.error("❌ Workspace sync failed: %s", str(e))
            sync_stats["errors"].append(str(e))
            return sync_stats

    async def _sync_workspace_info(self, workspace_id: str, org_id: str, transaction = None) -> Optional[str]:
        """Sync workspace information with proper org edge creation"""
        try:
            workspace_info = await self.slack_client.team_info()
            if not workspace_info.get('ok'):
                return None

            team = workspace_info['team']
            timestamp = get_epoch_timestamp_in_ms()

            # Check if workspace exists
            existing_workspace = await self.arango_service.get_workspace_by_org_id(org_id)

            if existing_workspace:
                workspace_key = existing_workspace['_key']
                await self.arango_service.update_workspace(workspace_key, {
                    "syncState": SyncState.IN_PROGRESS.value,
                    "updatedAtTimestamp": timestamp
                })
            else:
                workspace_key = str(uuid.uuid4())
                workspace_doc = {
                    "_key": workspace_key,
                    "orgId": org_id,
                    "externalId": team.get('id'),
                    "name": team.get('name'),
                    "domain": team.get('domain'),
                    "emailDomain": team.get('email_domain'),
                    "url": team.get('url'),
                    "isActive": True,
                    "syncState": SyncState.IN_PROGRESS.value,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                }
                await self.arango_service.batch_upsert_nodes(
                    [workspace_doc], CollectionNames.SLACK_WORKSPACES.value, transaction=transaction
                )

                # Create edge between workspace and organization
                workspace_org_edge = {
                    "_from": f"{CollectionNames.SLACK_WORKSPACES.value}/{workspace_key}",
                    "_to": f"orgs/{org_id}",
                    "entityType": EntityType.WORKSPACE.value,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp
                }

                await self.arango_service.batch_create_edges(
                    [workspace_org_edge],
                    collection=CollectionNames.BELONGS_TO.value,
                    transaction=transaction
                )

            return workspace_key

        except Exception as e:
            self.logger.error("❌ Error syncing workspace info: %s", str(e))
            return None

    # ==================== USER SYNC WITH GENERATORS ====================

    async def _sync_users_generator(self, org_id: str, workspace_key: str) -> Dict[str, Any]:
        """Sync users using optimized generator with workspace relationship"""
        try:
            self.logger.info("🚀 Syncing users with generator")
            stats = {"processed": 0, "errors": []}

            async for user_batch in self._create_user_generator():
                try:
                    batch_stats = await self._process_user_batch(user_batch, org_id, workspace_key)
                    stats["processed"] += batch_stats["processed"]
                    stats["errors"].extend(batch_stats["errors"])

                    await asyncio.sleep(self.rate_limit_delay)

                except Exception as e:
                    self.logger.error(f"❌ Error processing user batch: {str(e)}")
                    stats["errors"].append(str(e))

            return stats

        except Exception as e:
            self.logger.error("❌ User sync failed: %s", str(e))
            return {"processed": 0, "errors": [str(e)]}

    async def _create_user_generator(self) -> AsyncIterator[List[Dict[str, Any]]]:
        """Optimized user generator - yields batches for efficient processing"""
        try:
            cursor = None
            while True:
                params = {"limit": self.batch_size}
                if cursor:
                    params["cursor"] = cursor

                response = await self.slack_client.users_list(**params)
                if not response.get('ok'):
                    break

                users = response.get('members', [])
                if not users:
                    break

                # Yield entire batch - optimal for database operations
                yield users

                cursor = response.get('response_metadata', {}).get('next_cursor')
                if not cursor:
                    break

        except Exception as e:
            self.logger.error(f"❌ User generator error: {str(e)}")

    async def _process_user_batch(self, users: List[Dict[str, Any]], org_id: str, workspace_key: str) -> Dict[str, Any]:
        """Process batch of users efficiently with workspace relationship"""
        try:
            stats = {"processed": 0, "errors": []}
            user_docs = []
            workspace_edges = []
            timestamp = get_epoch_timestamp_in_ms()

            for user in users:
                email = user.get('profile', {}).get('email')
                if not email or not isinstance(email, str) or not email.strip():
                    continue

                # Check if user exists
                existing_user = await self.arango_service.get_user_by_slack_id(user.get('id'), org_id)
                if existing_user:
                    # Update existing user
                    await self.arango_service.update_user(existing_user['_key'], {
                        "isActive": not user.get('deleted', False),
                        "updatedAtTimestamp": timestamp,
                        "designation": self._get_user_designation(user)
                    })
                else:
                    # Create new user
                    user_key = str(uuid.uuid4())
                    user_doc = {
                        "_key": user_key,
                        "userId": user.get('id'),
                        "orgId": org_id,
                        "firstName": user.get('profile', {}).get('first_name'),
                        "lastName": user.get('profile', {}).get('last_name'),
                        "fullName": user.get('profile', {}).get('real_name') or user.get('name'),
                        "email": email,
                        "designation": self._get_user_designation(user),
                        "isActive": not user.get('deleted', False),
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp,
                    }
                    user_docs.append(user_doc)

                    # Create workspace membership edge
                    workspace_edges.append({
                        "_from": f"{CollectionNames.USERS.value}/{user_key}",
                        "_to": f"{CollectionNames.SLACK_WORKSPACES.value}/{workspace_key}",
                        "entityType": EntityType.WORKSPACE.value,
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp
                    })

            # Batch insert new users and workspace relationships
            if user_docs:
                success = await self.arango_service.batch_upsert_nodes(
                    user_docs, CollectionNames.USERS.value
                )
                if success and workspace_edges:
                    await self.arango_service.batch_create_edges(
                        workspace_edges, CollectionNames.BELONGS_TO.value
                    )
                    stats["processed"] = len(user_docs)
                else:
                    stats["errors"].append("Failed to store user batch")

            return stats

        except Exception as e:
            self.logger.error(f"❌ Error processing user batch: {str(e)}")
            return {"processed": 0, "errors": [str(e)]}

    def _get_user_designation(self, user: Dict[str, Any]) -> str:
        """Get user designation based on roles"""
        if user.get('is_primary_owner'):
            return "Primary Owner"
        elif user.get('is_owner'):
            return "Owner"
        elif user.get('is_admin'):
            return "Admin"
        elif user.get('is_bot'):
            return "Bot"
        else:
            return "Member"

    # ==================== CHANNEL SYNC WITH GENERATORS ====================

    async def _sync_channels_generator(self, workspace_key: str, org_id: str) -> Dict[str, Any]:
        """Sync channels using optimized generator"""
        try:
            self.logger.info("🚀 Syncing channels with generator")
            stats = {"processed": 0, "errors": []}

            async for channel_batch in self._create_channel_generator():
                try:
                    batch_stats = await self._process_channel_batch(channel_batch, workspace_key, org_id)
                    stats["processed"] += batch_stats["processed"]
                    stats["errors"].extend(batch_stats["errors"])

                    await asyncio.sleep(self.rate_limit_delay)

                except Exception as e:
                    self.logger.error(f"❌ Error processing channel batch: {str(e)}")
                    stats["errors"].append(str(e))

            return stats

        except Exception as e:
            self.logger.error("❌ Channel sync failed: %s", str(e))
            return {"processed": 0, "errors": [str(e)]}

    async def _create_channel_generator(self) -> AsyncIterator[List[Dict[str, Any]]]:
        """Optimized channel generator - yields batches efficiently"""
        try:
            channel_types = ['public_channel', 'private_channel', 'mpim', 'im']

            for channel_type in channel_types:
                cursor = None
                while True:
                    params = {
                        "types": channel_type,
                        "limit": self.batch_size,
                        "exclude_archived": False
                    }
                    if cursor:
                        params["cursor"] = cursor

                    response = await self.slack_client.conversations_list(**params)
                    if not response.get('ok'):
                        break

                    channels = response.get('channels', [])
                    if not channels:
                        break

                    # Yield entire batch for optimal processing
                    yield channels

                    cursor = response.get('response_metadata', {}).get('next_cursor')
                    if not cursor:
                        break

        except Exception as e:
            self.logger.error(f"❌ Channel generator error: {str(e)}")

    async def _process_channel_batch(
        self,
        channels: List[Dict[str, Any]],
        workspace_key: str,
        org_id: str
    ) -> Dict[str, Any]:
        """Process batch of channels efficiently"""
        try:
            stats = {"processed": 0, "errors": []}
            channel_docs = []
            workspace_edges = []
            timestamp = get_epoch_timestamp_in_ms()

            for channel in channels:
                existing_channel = await self.arango_service.get_channel_by_slack_id(channel.get('id'), org_id)
                if existing_channel:
                    # Update existing channel
                    await self.arango_service.update_channel(existing_channel['_key'], {
                        "isDeletedAtSource": channel.get('is_archived', False),
                        "updatedAtTimestamp": timestamp,
                        "syncState": SyncState.NOT_STARTED.value
                    })
                else:
                    # Create new channel
                    channel_key = str(uuid.uuid4())
                    channel_doc = {
                        "_key": channel_key,
                        "orgId": org_id,
                        "groupName": channel.get('name', 'Unnamed Channel'),
                        "externalGroupId": channel.get('id'),
                        "groupType": GroupType.SLACK_CHANNEL.value,
                        "connectorName": Connectors.SLACK.value,
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp,
                        "isDeletedAtSource": channel.get('is_archived', False),
                        "sourceCreatedAtTimestamp": int(channel.get('created', 0) * 1000),
                        "sourceLastModifiedTimestamp": int(channel.get('updated', 0) * 1000),
                        "syncState": SyncState.NOT_STARTED.value,
                    }
                    channel_docs.append(channel_doc)

                    # Create workspace relationship edge
                    workspace_edges.append({
                        "_from": f"{CollectionNames.RECORD_GROUPS.value}/{channel_key}",
                        "_to": f"{CollectionNames.SLACK_WORKSPACES.value}/{workspace_key}",
                        "entityType": EntityType.WORKSPACE.value,
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp,
                    })

            # Batch operations
            if channel_docs:
                success = await self.arango_service.batch_upsert_nodes(
                    channel_docs, CollectionNames.RECORD_GROUPS.value
                )
                if success and workspace_edges:
                    await self.arango_service.batch_create_edges(
                        workspace_edges, CollectionNames.BELONGS_TO_SLACK_WORKSPACE.value
                    )
                    stats["processed"] = len(channel_docs)
                else:
                    stats["errors"].append("Failed to store channel batch")

            return stats

        except Exception as e:
            self.logger.error(f"❌ Error processing channel batch: {str(e)}")
            return {"processed": 0, "errors": [str(e)]}

    # ==================== CHANNEL MEMBERS SYNC ====================

    async def _sync_channel_members_generator(self, org_id: str) -> Dict[str, Any]:
        """Sync channel members using generator"""
        try:
            self.logger.info("🚀 Syncing channel members with generator")
            stats = {"processed": 0, "errors": []}

            channels = await self.arango_service.get_workspace_channels(org_id)

            async for member_batch in self._create_channel_members_generator(channels, org_id):
                try:
                    batch_stats = await self._process_channel_members_batch(member_batch)
                    stats["processed"] += batch_stats["processed"]
                    stats["errors"].extend(batch_stats["errors"])

                    await asyncio.sleep(self.rate_limit_delay)

                except Exception as e:
                    self.logger.error(f"❌ Error processing member batch: {str(e)}")
                    stats["errors"].append(str(e))

            return stats

        except Exception as e:
            self.logger.error("❌ Channel members sync failed: %s", str(e))
            return {"processed": 0, "errors": [str(e)]}

    async def _create_channel_members_generator(
        self,
        channels: List[Dict[str, Any]],
        org_id: str
    ) -> AsyncIterator[Dict[str, Any]]:
        """Generator for channel members - yields complete member data per channel"""
        try:
            for channel_data in channels:
                channel_info = channel_data.get('channel', {})
                channel_key = channel_info.get('_key')
                channel_id = channel_info.get('externalGroupId')

                if not channel_key or not channel_id:
                    continue

                # Get all members for this channel
                cursor = None
                all_members = []

                while True:
                    response = await self.slack_client.conversations_members(
                        channel=channel_id,
                        cursor=cursor
                    )

                    if not response or not response.get('ok'):
                        break

                    members = response.get('members', [])
                    if not members:
                        break

                    all_members.extend(members)

                    cursor = response.get('response_metadata', {}).get('next_cursor')
                    if not cursor:
                        break

                    await asyncio.sleep(0.5)  # Rate limiting

                if all_members:
                    # Yield complete channel member data
                    yield {
                        "channel_key": channel_key,
                        "channel_id": channel_id,
                        "members": all_members,
                        "org_id": org_id
                    }

        except Exception as e:
            self.logger.error(f"❌ Channel members generator error: {str(e)}")

    async def _process_channel_members_batch(self, member_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process channel members batch efficiently"""
        try:
            stats = {"processed": 0, "errors": []}

            channel_key = member_data["channel_key"]
            members = member_data["members"]
            org_id = member_data["org_id"]

            member_edges = []
            permission_edges = []
            timestamp = get_epoch_timestamp_in_ms()

            for member_id in members:
                try:
                    user = await self.arango_service.get_user_by_slack_id(member_id, org_id)
                    if not user:
                        continue

                    user_key = user.get('_key')
                    if not user_key:
                        continue

                    # Create membership edge
                    member_edges.append({
                        "_from": f"{CollectionNames.USERS.value}/{user_key}",
                        "_to": f"{CollectionNames.RECORD_GROUPS.value}/{channel_key}",
                        "entityType": EntityType.SLACK_CHANNEL.value,
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp
                    })

                    # Create permission edge
                    permission_edges.append({
                        "_from": f"{CollectionNames.USERS.value}/{user_key}",
                        "_to": f"{CollectionNames.RECORD_GROUPS.value}/{channel_key}",
                        "type": PermissionType.USER.value,
                        "role": "READER",
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp,
                        "lastUpdatedTimestampAtSource": timestamp
                    })

                    stats["processed"] += 1

                except Exception as e:
                    self.logger.error(f"❌ Error processing member {member_id}: {str(e)}")
                    stats["errors"].append(f"Member {member_id}: {str(e)}")

            # Batch create edges
            if member_edges:
                await self.arango_service.batch_create_edges(
                    member_edges, CollectionNames.BELONGS_TO_SLACK_CHANNEL.value
                )
            if permission_edges:
                await self.arango_service.batch_create_edges(
                    permission_edges, CollectionNames.PERMISSIONS.value
                )

            return stats

        except Exception as e:
            self.logger.error(f"❌ Error processing channel members: {str(e)}")
            return {"processed": 0, "errors": [str(e)]}

    # ==================== MESSAGE SYNC WITH GENERATORS ====================

    async def _sync_workspace_messages_generator(self, org_id: str) -> Dict[str, Any]:
        """Sync all workspace messages using generator"""
        try:
            self.logger.info("🚀 Syncing workspace messages with generator")
            stats = {"processed": 0, "files_processed": 0, "errors": []}

            channels = await self.arango_service.get_workspace_channels(org_id)

            for channel_data in channels:
                try:
                    channel_info = channel_data.get('channel', {})

                    if channel_info.get('isDeletedAtSource'):
                        continue

                    await self.arango_service.update_channel_sync_state(
                        channel_info['_key'], SyncState.IN_PROGRESS.value
                    )

                    channel_stats = await self.sync_channel_messages_with_threads(
                        channel_info['externalGroupId'],
                        channel_info['_key'],
                        org_id
                    )

                    stats["processed"] += channel_stats["processed"]
                    stats["files_processed"] += channel_stats["files_processed"]
                    stats["errors"].extend(channel_stats["errors"])

                    final_state = SyncState.COMPLETED.value if not channel_stats["errors"] else SyncState.FAILED.value
                    await self.arango_service.update_channel_sync_state(
                        channel_info['_key'], final_state
                    )

                    await asyncio.sleep(self.rate_limit_delay)

                except Exception as e:
                    self.logger.error(f"❌ Error syncing channel messages: {str(e)}")
                    stats["errors"].append(str(e))

            return stats

        except Exception as e:
            self.logger.error("❌ Workspace messages sync failed: %s", str(e))
            return {"processed": 0, "files_processed": 0, "errors": [str(e)]}

    async def sync_channel_messages_with_threads(
        self,
        channel_id: str,
        channel_key: str,
        org_id: str,
        oldest: Optional[str] = None,
        latest: Optional[str] = None,
        resume_from_checkpoint: bool = False
    ) -> Dict[str, Any]:
        """Sync channel messages with threads using optimized generator"""
        try:
            self.logger.info("🚀 Syncing channel messages with threads: %s", channel_id)

            stats = {"processed": 0, "files_processed": 0, "errors": []}

            # Ensure bot has access to channel
            access_result = await self.ensure_bot_channel_access(channel_id)
            if not access_result["success"]:
                stats["errors"].append(f"Cannot access channel: {access_result.get('error')}")
                return stats

            # Initialize checkpoint
            checkpoint = await self._initialize_checkpoint(
                channel_id, channel_key, org_id, resume_from_checkpoint
            )

            processed_threads = set()

            # Process messages using optimized generator
            async for batch in self._create_message_batch_generator(
                channel_id, channel_key, org_id, oldest, latest, checkpoint, processed_threads
            ):
                try:
                    batch_stats = await self._store_message_batch(batch, org_id)

                    stats["processed"] += batch_stats["processed"]
                    stats["files_processed"] += batch_stats["files_processed"]
                    stats["errors"].extend(batch_stats["errors"])

                    # Save checkpoint periodically
                    if batch.checkpoint.processed_count % self.checkpoint_interval == 0:
                        await self._save_checkpoint(batch.checkpoint)

                    await asyncio.sleep(self.rate_limit_delay)

                except Exception as e:
                    self.logger.error(f"❌ Error processing message batch: {str(e)}")
                    stats["errors"].append(str(e))

            self.logger.info("✅ Channel messages sync completed: %s", stats)
            return stats

        except Exception as e:
            self.logger.error("❌ Channel messages sync failed: %s", str(e))
            stats["errors"].append(str(e))
            return stats

    async def _create_message_batch_generator(
        self,
        channel_id: str,
        channel_key: str,
        org_id: str,
        oldest: Optional[str],
        latest: Optional[str],
        checkpoint: SyncCheckpoint,
        processed_threads: set
    ) -> AsyncIterator[ProcessedBatch]:
        """Optimized message batch generator - yields complete processed batches"""
        try:
            cursor = checkpoint.last_cursor

            while True:
                # Get messages from Slack API
                params = {
                    "channel": channel_id,
                    "limit": self.batch_size,
                    "inclusive": True
                }
                if oldest:
                    params["oldest"] = oldest
                if latest:
                    params["latest"] = latest
                if cursor:
                    params["cursor"] = cursor

                response = await self.slack_client.conversations_history(**params)

                if not response.get('ok'):
                    if response.get('error') == 'not_in_channel':
                        join_result = await self.join_channel(channel_id)
                        if join_result["success"]:
                            continue
                    break

                messages = response.get('messages', [])
                if not messages:
                    break

                # Process complete batch
                batch = await self._process_message_batch_with_threads(
                    messages, channel_id, channel_key, org_id, checkpoint, processed_threads
                )

                if batch.message_records or batch.file_records:
                    yield batch

                # Update cursor and check for more
                cursor = response.get('response_metadata', {}).get('next_cursor')
                checkpoint.last_cursor = cursor

                if not response.get('has_more', False) and not cursor:
                    break

        except Exception as e:
            self.logger.error(f"❌ Message batch generator error: {str(e)}")

    async def _process_message_batch_with_threads(
        self,
        messages: List[Dict[str, Any]],
        channel_id: str,
        channel_key: str,
        org_id: str,
        checkpoint: SyncCheckpoint,
        processed_threads: set
    ) -> ProcessedBatch:
        """Process complete message batch with threads and files"""
        try:
            timestamp = get_epoch_timestamp_in_ms()

            message_records = []
            message_metadata = []
            file_records = []
            file_metadata = []
            edges = {
                CollectionNames.BELONGS_TO_SLACK_CHANNEL.value: [],
                CollectionNames.SLACK_MESSAGE_TO_METADATA.value: [],
                CollectionNames.SLACK_FILE_TO_ATTACHMENT_METADATA.value: [],
                CollectionNames.RECORD_RELATIONS.value: []
            }

            for message in messages:
                if not message:
                    continue

                message_key = str(uuid.uuid4())

                # Create message record
                message_record = {
                    "_key": message_key,
                    "orgId": org_id,
                    "recordName": f"Slack_Message_{message.get('ts', '')}",
                    "externalRecordId": message.get('ts'),
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
                    "sourceCreatedAtTimestamp": int(float(message.get('ts', 0)) * 1000),
                    "sourceLastModifiedTimestamp": int(float(message.get('edited', {}).get('ts', 0)) * 1000) if message.get('edited') else int(float(message.get('ts', 0)) * 1000),
                    "isDeleted": False,
                    "isArchived": False,
                    "reason": None,
                    "lastIndexTimestamp": None,
                    "lastExtractionTimestamp": None,
                    "indexingStatus": ProgressStatus.NOT_STARTED.value,
                    "extractionStatus": ProgressStatus.NOT_STARTED.value,
                }
                message_records.append(message_record)

                # Create message metadata
                message_metadata_doc = {
                    "_key": message_key,
                    "orgId": org_id,
                    "slackTs": message.get('ts'),
                    "threadTs": message.get('thread_ts'),
                    "channelId": channel_id,
                    "userId": message.get('user'),
                    "messageType": MessageType.THREAD_MESSAGE.value if message.get('thread_ts') else MessageType.ROOT_MESSAGE.value,
                    "text": message.get('text', ''),
                    "replyCount": message.get('reply_count', 0),
                    "replyUsersCount": message.get('reply_users_count', 0),
                    "replyUsers": message.get('reply_users', []),
                    "hasFiles": bool(message.get('files')),
                    "fileCount": len(message.get('files', [])),
                    "botId": message.get('bot_id'),
                    "mentionedUsers": self._extract_mentions(message),
                    "links": self._extract_links(message)
                }
                message_metadata.append(message_metadata_doc)

                # Create edges
                edges[CollectionNames.BELONGS_TO_SLACK_CHANNEL.value].append({
                    "_from": f"{CollectionNames.RECORDS.value}/{message_key}",
                    "_to": f"{CollectionNames.RECORD_GROUPS.value}/{channel_key}",
                    "entityType": EntityType.SLACK_CHANNEL.value,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp
                })

                edges[CollectionNames.SLACK_MESSAGE_TO_METADATA.value].append({
                    "_from": f"{CollectionNames.RECORDS.value}/{message_key}",
                    "_to": f"{CollectionNames.SLACK_MESSAGE_METADATA.value}/{message_key}",
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp
                })

                # Process files if present
                if message.get('files'):
                    file_data = await self._process_message_files(
                        message['files'], message_key, channel_key, org_id, timestamp
                    )
                    file_records.extend(file_data["files"])
                    file_metadata.extend(file_data["metadata"])
                    edges[CollectionNames.SLACK_FILE_TO_ATTACHMENT_METADATA.value].extend(file_data["metadata_edges"])
                    edges[CollectionNames.RECORD_RELATIONS.value].extend(file_data["attachment_edges"])

                # Process thread replies if this is a parent message
                if (message.get('thread_ts') == message.get('ts') and
                    message.get('ts') not in processed_threads):

                    thread_data = await self._process_thread_replies(
                        channel_id, message.get('ts'), message_key, channel_key, org_id, timestamp
                    )

                    if thread_data:
                        message_records.extend(thread_data["replies"])
                        message_metadata.extend(thread_data["metadata"])
                        edges[CollectionNames.BELONGS_TO_SLACK_CHANNEL.value].extend(thread_data["channel_edges"])
                        edges[CollectionNames.SLACK_MESSAGE_TO_METADATA.value].extend(thread_data["metadata_edges"])
                        edges[CollectionNames.RECORD_RELATIONS.value].extend(thread_data["thread_edges"])

                        # Add thread files
                        file_records.extend(thread_data["files"])
                        file_metadata.extend(thread_data["file_metadata"])
                        edges[CollectionNames.SLACK_FILE_TO_ATTACHMENT_METADATA.value].extend(thread_data["file_metadata_edges"])
                        edges[CollectionNames.RECORD_RELATIONS.value].extend(thread_data["file_attachment_edges"])

                    processed_threads.add(message.get('ts'))

                # Update checkpoint
                checkpoint.processed_count += 1
                checkpoint.last_processed_ts = message.get('ts')

            return ProcessedBatch(
                message_records=message_records,
                message_metadata=message_metadata,
                file_records=file_records,
                file_metadata=file_metadata,
                edges=edges,
                checkpoint=checkpoint
            )

        except Exception as e:
            self.logger.error(f"❌ Error processing message batch: {str(e)}")
            return ProcessedBatch([], [], [], [], {}, checkpoint)

    async def _process_message_files(
        self,
        files: List[Dict[str, Any]],
        message_key: str,
        channel_key: str,
        org_id: str,
        timestamp: int
    ) -> Dict[str, Any]:
        """Process message files efficiently"""
        try:
            processed_files = []
            file_metadata_list = []
            metadata_edges = []
            attachment_edges = []

            for file in files:
                if not file:
                    continue

                file_key = str(uuid.uuid4())

                file_name = file.get('name', '').strip()
                if not file_name:
                    # Generate fallback name if empty
                    file_id = file.get('id', 'unknown')
                    file_name = f"Slack_File_{file_id}"
                    self.logger.warning(f"⚠️ Empty file name for {file_id}, using fallback: {file_name}")


                # Create file record
                file_record = {
                    "_key": file_key,
                    "orgId": org_id,
                    "recordName": file_name,
                    "externalRecordId": file.get('id'),
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

                processed_files.append(file_record)

                # Create file metadata
                file_metadata_doc = {
                    "_key": file_key,
                    "orgId": org_id,
                    "slackFileId": file.get('id'),
                    "name": file.get('name', ''),
                    "extension": file.get('filetype'),
                    "mimeType": file.get('mimetype'),
                    "sizeInBytes": file.get('size', 0),
                    "channelId": channel_key,
                    "uploadedBy": file.get('user',''),
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
                file_metadata_list.append(file_metadata_doc)

                # Create edges
                metadata_edges.append({
                    "_from": f"{CollectionNames.RECORDS.value}/{file_key}",
                    "_to": f"{CollectionNames.SLACK_ATTACHMENT_METADATA.value}/{file_key}",
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp
                })

                attachment_edges.append({
                    "_from": f"{CollectionNames.RECORDS.value}/{message_key}",
                    "_to": f"{CollectionNames.RECORDS.value}/{file_key}",
                    "relationshipType": RecordRelations.ATTACHMENT.value,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp
                })

            return {
                "files": processed_files,
                "metadata": file_metadata_list,
                "metadata_edges": metadata_edges,
                "attachment_edges": attachment_edges
            }

        except Exception as e:
            self.logger.error(f"❌ Error processing files: {str(e)}")
            return {"files": [], "metadata": [], "metadata_edges": [], "attachment_edges": []}

    async def _process_thread_replies(
        self,
        channel_id: str,
        thread_ts: str,
        parent_key: str,
        channel_key: str,
        org_id: str,
        timestamp: int
    ) -> Optional[Dict[str, Any]]:
        """Process thread replies efficiently"""
        try:
            thread_response = await self.slack_client.conversations_replies(
                channel=channel_id,
                ts=thread_ts,
                inclusive=False
            )

            if not thread_response or not thread_response.get('ok'):
                return None

            thread_replies = thread_response.get('messages', [])
            if not thread_replies:
                return None

            replies = []
            metadata = []
            channel_edges = []
            metadata_edges = []
            thread_edges = []
            files = []
            file_metadata = []
            file_metadata_edges = []
            file_attachment_edges = []

            for reply in thread_replies:
                if not reply or reply.get('ts') == thread_ts:
                    continue

                reply_key = str(uuid.uuid4())

                # Create reply record
                reply_record = {
                    "_key": reply_key,
                    "orgId": org_id,
                    "recordName": f"Slack_Message_{reply.get('ts', '')}",
                    "externalRecordId": reply.get('ts'),
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
                    "sourceCreatedAtTimestamp": int(float(reply.get('ts', 0)) * 1000),
                    "sourceLastModifiedTimestamp": int(float(reply.get('edited', {}).get('ts', 0)) * 1000) if reply.get('edited') else int(float(reply.get('ts', 0)) * 1000),
                    "isDeleted": False,
                    "isArchived": False,
                    "reason": None,
                    "lastIndexTimestamp": None,
                    "lastExtractionTimestamp": None,
                    "indexingStatus": ProgressStatus.NOT_STARTED.value,
                    "extractionStatus": ProgressStatus.NOT_STARTED.value,
                }
                replies.append(reply_record)

                # Create reply metadata
                reply_metadata = {
                    "_key": reply_key,
                    "orgId": org_id,
                    "slackTs": reply.get('ts'),
                    "threadTs": reply.get('thread_ts'),
                    "channelId": channel_id,
                    "userId": reply.get('user'),
                    "messageType": MessageType.THREAD_MESSAGE.value,
                    "text": reply.get('text', ''),
                    "replyCount": 0,
                    "replyUsersCount": 0,
                    "replyUsers": [],
                    "hasFiles": bool(reply.get('files')),
                    "fileCount": len(reply.get('files', [])),
                    "botId": reply.get('bot_id'),
                    "mentionedUsers": self._extract_mentions(reply),
                    "links": self._extract_links(reply)
                }
                metadata.append(reply_metadata)

                # Create edges
                channel_edges.append({
                    "_from": f"{CollectionNames.RECORDS.value}/{reply_key}",
                    "_to": f"{CollectionNames.RECORD_GROUPS.value}/{channel_key}",
                    "entityType": EntityType.SLACK_CHANNEL.value,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp
                })

                metadata_edges.append({
                    "_from": f"{CollectionNames.RECORDS.value}/{reply_key}",
                    "_to": f"{CollectionNames.SLACK_MESSAGE_METADATA.value}/{reply_key}",
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp
                })

                thread_edges.append({
                    "_from": f"{CollectionNames.RECORDS.value}/{parent_key}",
                    "_to": f"{CollectionNames.RECORDS.value}/{reply_key}",
                    "relationshipType": RecordRelations.PARENT_CHILD.value,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp
                })

                # Process reply files
                if reply.get('files'):
                    reply_file_data = await self._process_message_files(
                        reply['files'], reply_key, channel_key, org_id, timestamp
                    )
                    files.extend(reply_file_data["files"])
                    file_metadata.extend(reply_file_data["metadata"])
                    file_metadata_edges.extend(reply_file_data["metadata_edges"])
                    file_attachment_edges.extend(reply_file_data["attachment_edges"])

            return {
                "replies": replies,
                "metadata": metadata,
                "channel_edges": channel_edges,
                "metadata_edges": metadata_edges,
                "thread_edges": thread_edges,
                "files": files,
                "file_metadata": file_metadata,
                "file_metadata_edges": file_metadata_edges,
                "file_attachment_edges": file_attachment_edges
            }

        except Exception as e:
            self.logger.error(f"❌ Error processing thread replies: {str(e)}")
            return None

    async def _store_message_batch(self, batch: ProcessedBatch, org_id: str) -> Dict[str, Any]:
        """Store processed message batch efficiently with proper Kafka events and user grouping"""
        try:
            stats = {"processed": 0, "files_processed": 0, "errors": []}

            # Perform batch operations in transaction
            try:
                # Store all records
                if batch.message_records:
                    await self.arango_service.batch_upsert_nodes(
                        batch.message_records, CollectionNames.RECORDS.value
                    )

                if batch.message_metadata:
                    await self.arango_service.batch_upsert_nodes(
                        batch.message_metadata, CollectionNames.SLACK_MESSAGE_METADATA.value
                    )

                if batch.file_records:
                    await self.arango_service.batch_upsert_nodes(
                        batch.file_records, CollectionNames.RECORDS.value
                    )

                if batch.file_metadata:
                    await self.arango_service.batch_upsert_nodes(
                        batch.file_metadata, CollectionNames.SLACK_ATTACHMENT_METADATA.value
                    )

                # Create all edges
                for collection, edges in batch.edges.items():
                    if edges:
                        await self.arango_service.batch_create_edges(edges, collection)

                # Process user message grouping and send Kafka events
                await self._process_batch_for_user_grouping_and_kafka(batch, org_id)

                stats["processed"] = len(batch.message_records)
                stats["files_processed"] = len(batch.file_records)

            except Exception as e:
                self.logger.error("❌ Batch storage failed: %s", str(e))
                stats["errors"].append(f"Storage error: {str(e)}")

            return stats

        except Exception as e:
            self.logger.error("❌ Error storing message batch: %s", str(e))
            return {"processed": 0, "files_processed": 0, "errors": [str(e)]}

    async def _process_batch_for_user_grouping_and_kafka(self, batch: ProcessedBatch, org_id: str) -> None:
        """Process batch for user message grouping and send appropriate Kafka events"""
        try:
            # Get connector endpoint
            try:
                connector_endpoint = await self.config.get_config(config_node_constants.CONNECTOR_ENDPOINT.value)
                if not connector_endpoint:
                    connector_endpoint = "http://localhost:8088"
            except Exception:
                connector_endpoint = "http://localhost:8088"

            # Process messages for user grouping
            messages_for_grouping = []
            for i, message_record in enumerate(batch.message_records):
                message_metadata = batch.message_metadata[i] if i < len(batch.message_metadata) else None
                if message_metadata:
                    # Create message object for grouping
                    message_obj = {
                        "record": message_record,
                        "metadata": message_metadata,
                        "ts": message_metadata.get('slackTs'),
                        "user": message_metadata.get('userId'),
                        "text": message_metadata.get('text', ''),
                        "channel": message_metadata.get('channelId'),
                        "thread_ts": message_metadata.get('threadTs'),
                        "files": []  # Files will be handled separately
                    }
                    messages_for_grouping.append(message_obj)

            # Apply user message grouping logic
            kafka_events = await self._apply_user_message_grouping(messages_for_grouping, org_id, connector_endpoint)

            # Send grouped message events
            for event in kafka_events:
                await self.kafka_service.send_event_to_kafka(event)

            # Send individual file events (always separate)
            for i, file_record in enumerate(batch.file_records):
                try:
                    file_metadata = batch.file_metadata[i] if i < len(batch.file_metadata) else None

                    event = await self._create_file_kafka_event(
                        file_record, file_metadata, org_id, connector_endpoint
                    )

                    if event:
                        await self.kafka_service.send_event_to_kafka(event)
                        self.logger.info(f"📨 Sent Kafka event for file {file_record.get('_key')}")

                except Exception as e:
                    self.logger.error(f"❌ Error sending file Kafka event: {str(e)}")

        except Exception as e:
            self.logger.error(f"❌ Error processing batch for user grouping and Kafka: {str(e)}")

    async def _apply_user_message_grouping(
        self,
        messages: List[Dict[str, Any]],
        org_id: str,
        connector_endpoint: str
    ) -> List[Dict[str, Any]]:
        """Apply user message grouping logic and return Kafka events"""
        try:
            kafka_events = []
            current_time = datetime.now().timestamp()

            # Process each message for grouping
            for message in messages:
                user_id = message.get('user')
                if not user_id:
                    # Send individual event for message without user
                    event = await self._create_message_kafka_event_from_objects(
                        message.get('record'), message.get('metadata'), org_id, connector_endpoint
                    )
                    if event:
                        kafka_events.append(event)
                    continue

                # Parse Slack timestamp - convert from string to float
                message_ts_str = message.get('ts')
                if not message_ts_str:
                    continue

                try:
                    message_ts = float(message_ts_str)
                except (ValueError, TypeError):
                    self.logger.warning(f"⚠️ Invalid timestamp format: {message_ts_str}")
                    continue

                window = self._user_message_windows[user_id]

                # Initialize window if empty
                if not window.user_id:
                    window.user_id = user_id
                    window.window_start = message_ts
                    window.window_end = message_ts + self.user_message_window_seconds

                # Check if message fits in current window (60 second window)
                if message_ts <= window.window_end:
                    # Add to current window
                    window.messages.append(message)

                    # Extend window end time
                    window.window_end = max(window.window_end, message_ts + self.user_message_window_seconds)
                else:
                    # Window expired, create events for current window
                    if window.messages:
                        events = await self._create_kafka_events_from_message_window(
                            window, org_id, connector_endpoint
                        )
                        kafka_events.extend(events)

                    # Start new window with current message
                    window.messages = [message]
                    window.files = []  # Reset files
                    window.window_start = message_ts
                    window.window_end = message_ts + self.user_message_window_seconds

            # Check for expired windows and flush them
            for user_id, window in list(self._user_message_windows.items()):
                if window.messages and (current_time - window.window_end) > self.user_message_window_seconds:
                    # Window expired, create events
                    events = await self._create_kafka_events_from_message_window(
                        window, org_id, connector_endpoint
                    )
                    kafka_events.extend(events)

                    # Clear window
                    del self._user_message_windows[user_id]

            return kafka_events

        except Exception as e:
            self.logger.error(f"❌ Error in user message grouping: {str(e)}")
            return []

    async def _create_kafka_events_from_message_window(
        self,
        window: UserMessageWindow,
        org_id: str,
        connector_endpoint: str
    ) -> List[Dict[str, Any]]:
        """Create Kafka events from user message window with proper timestamps"""
        try:
            events = []

            if len(window.messages) > 1:
                # Create batch event for multiple messages
                first_message = window.messages[0]
                last_message = window.messages[-1]

                # Use first message's Slack timestamp for createdAtTimestamp
                first_ts = float(first_message.get('ts', 0))
                last_ts = float(last_message.get('ts', 0))

                batch_event = {
                    "orgId": org_id,
                    "recordId": f"batch_{window.user_id}_{int(first_ts)}",
                    "recordName": f"User Message Batch - {len(window.messages)} messages",
                    "recordType": RecordTypes.MESSAGE.value,
                    "eventType": EventTypes.NEW_RECORD.value,
                    "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/slack/batch/{window.user_id}_{int(first_ts)}/signedUrl",
                    "connectorName": Connectors.SLACK.value,
                    "origin": OriginTypes.CONNECTOR.value,
                    "mimeType": "application/slack_batch",
                    "createdAtSourceTimestamp": int(first_ts * 1000),  # Use Slack timestamp
                    "modifiedAtSourceTimestamp": int(last_ts * 1000),   # Use Slack timestamp
                    "body": {
                        "userId": window.user_id,
                        "messageCount": len(window.messages),
                        "windowStart": first_ts,
                        "windowEnd": last_ts,
                        "messages": [
                            {
                                "recordId": msg.get('record', {}).get('_key'),
                                "ts": msg.get('ts'),
                                "text": msg.get('text', ''),
                                "channel": msg.get('channel'),
                                "threadTs": msg.get('thread_ts')
                            }
                            for msg in window.messages
                        ]
                    }
                }
                events.append(batch_event)
                self.logger.info(f"📦 Created batch event for {len(window.messages)} messages from user {window.user_id}")
            else:
                # Single message, create individual event
                message = window.messages[0]
                event = await self._create_message_kafka_event_from_objects(
                    message.get('record'), message.get('metadata'), org_id, connector_endpoint
                )
                if event:
                    events.append(event)
                    self.logger.info(f"📨 Created individual event for single message from user {window.user_id}")

            return events

        except Exception as e:
            self.logger.error(f"❌ Error creating Kafka events from message window: {str(e)}")
            return []

    async def _create_message_kafka_event_from_objects(
        self,
        message_record: Dict[str, Any],
        message_metadata: Dict[str, Any],
        org_id: str,
        connector_endpoint: str
    ) -> Optional[Dict[str, Any]]:
        """Create individual Kafka event for message from record and metadata objects with proper format"""
        try:
            # Use Slack timestamp for createdAtTimestamp
            slack_ts = message_metadata.get('slackTs')
            slack_ts_ms = int(float(slack_ts) * 1000) if slack_ts else message_record.get('sourceCreatedAtTimestamp')

            # Create event in the format expected by Kafka service
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
                "createdAtSourceTimestamp": slack_ts_ms,  # Use Slack timestamp
                "modifiedAtSourceTimestamp": slack_ts_ms,  # Use Slack timestamp
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
            self.logger.error(f"❌ Error creating message Kafka event from objects: {str(e)}")
            return None

    async def _create_message_kafka_event(
        self,
        message_record: Dict[str, Any],
        message_metadata: Optional[Dict[str, Any]],
        org_id: str,
        connector_endpoint: str
    ) -> Optional[Dict[str, Any]]:
        """Create individual Kafka event for message matching change handler pattern with correct timestamps"""
        try:
            # Use Slack timestamp for createdAtTimestamp
            slack_ts_ms = message_record.get('sourceCreatedAtTimestamp')
            if message_metadata and message_metadata.get('slackTs'):
                try:
                    slack_ts_ms = int(float(message_metadata.get('slackTs')) * 1000)
                except (ValueError, TypeError):
                    pass  # Fall back to existing value

            # Create event in the format expected by Kafka service
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
                "createdAtSourceTimestamp": slack_ts_ms,  # Use Slack timestamp
                "modifiedAtSourceTimestamp": slack_ts_ms,  # Use Slack timestamp
                "body": {}
            }

            # Add metadata to body if available
            if message_metadata:
                event["body"] = {
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

            return event

        except Exception as e:
            self.logger.error(f"❌ Error creating message Kafka event: {str(e)}")
            return None

    async def _create_file_kafka_event(
        self,
        file_record: Dict[str, Any],
        file_metadata: Optional[Dict[str, Any]],
        org_id: str,
        connector_endpoint: str
    ) -> Optional[Dict[str, Any]]:
        """Create individual Kafka event for file matching change handler pattern with correct timestamps"""
        try:
            # Use file's Slack timestamp for createdAtTimestamp
            file_ts_ms = file_record.get('sourceCreatedAtTimestamp')

            # Determine MIME type and file properties from metadata
            mime_type = "application/octet-stream"
            file_size = 0
            uploaded_by = ""
            is_public = False
            is_editable = False
            channel_id = ""
            extension = None
            source_urls = {}
            permalink = None
            rich_preview = False

            if file_metadata:
                mime_type = file_metadata.get('mimeType') or mime_type
                file_size = file_metadata.get('sizeInBytes', 0)
                uploaded_by = file_metadata.get('uploadedBy', "")
                is_public = file_metadata.get('isPublic', False)
                is_editable = file_metadata.get('isEditable', False)
                channel_id = file_metadata.get('channelId', "")
                extension = file_metadata.get('extension')
                source_urls = file_metadata.get('sourceUrls', {})
                permalink = file_metadata.get('permalink')
                rich_preview = file_metadata.get('richPreview', False)

            # Create event in the format expected by Kafka service
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
                "createdAtSourceTimestamp": file_ts_ms,  # Use file's Slack timestamp
                "modifiedAtSourceTimestamp": file_ts_ms,  # Use file's Slack timestamp
                "body": {
                    "fileName": file_record.get('recordName'),
                    "fileSize": file_size,
                    "uploadedBy": uploaded_by,
                    "isPublic": is_public,
                    "isEditable": is_editable,
                    "channelId": channel_id,
                    "extension": extension,
                    "sourceUrls": source_urls,
                    "permalink": permalink,
                    "richPreview": rich_preview
                }
            }

            return event

        except Exception as e:
            self.logger.error(f"❌ Error creating file Kafka event: {str(e)}")
            return None

    # ==================== USER MESSAGE GROUPING (PRESERVED FROM ORIGINAL) ====================

    async def _process_user_message_grouping(
        self,
        messages: List[Dict[str, Any]],
        org_id: str
    ) -> List[Dict[str, Any]]:
        """Process user message grouping (preserved functionality)"""
        try:
            current_time = datetime.now().timestamp()
            kafka_events = []

            # Group messages by user
            for message in messages:
                user_id = message.get('user')
                if not user_id:
                    continue

                message_ts = float(message.get('ts', 0))
                window = self._user_message_windows[user_id]

                # Initialize window if empty
                if not window.user_id:
                    window.user_id = user_id
                    window.window_start = message_ts
                    window.window_end = message_ts + self.user_message_window_seconds

                # Check if message fits in current window
                if message_ts <= window.window_end:
                    # Add to current window
                    window.messages.append(message)
                    if message.get('files'):
                        window.files.extend(message['files'])

                    # Extend window end time
                    window.window_end = max(window.window_end, message_ts + self.user_message_window_seconds)
                else:
                    # Window expired, create events for current window
                    if window.messages:
                        events = await self._create_kafka_events_from_window(window, org_id)
                        kafka_events.extend(events)

                    # Start new window
                    window.messages = [message]
                    window.files = list(message.get('files', []))
                    window.window_start = message_ts
                    window.window_end = message_ts + self.user_message_window_seconds

            # Check for expired windows
            for user_id, window in list(self._user_message_windows.items()):
                if window.messages and (current_time - window.window_end) > self.user_message_window_seconds:
                    # Window expired, create events
                    events = await self._create_kafka_events_from_window(window, org_id)
                    kafka_events.extend(events)

                    # Clear window
                    del self._user_message_windows[user_id]

            return kafka_events

        except Exception as e:
            self.logger.error(f"❌ Error in user message grouping: {str(e)}")
            return []

    async def _create_kafka_events_from_window(
        self,
        window: UserMessageWindow,
        org_id: str
    ) -> List[Dict[str, Any]]:
        """Create Kafka events from user message window"""
        try:
            events = []

            # Get connector endpoint
            try:
                connector_endpoint = await self.config.get_config(config_node_constants.CONNECTOR_ENDPOINT.value)
                if not connector_endpoint:
                    connector_endpoint = "http://localhost:8088"
            except Exception:
                connector_endpoint = "http://localhost:8088"

            # Create events for grouped messages
            if len(window.messages) > 1:
                # Create batch event for multiple messages
                batch_event = {
                    "orgId": org_id,
                    "recordId": f"batch_{window.user_id}_{int(window.window_start)}",
                    "recordName": f"User Message Batch - {len(window.messages)} messages",
                    "recordType": RecordTypes.MESSAGE.value,
                    "eventType": EventTypes.NEW_RECORD.value,
                    "connectorName": Connectors.SLACK.value,
                    "origin": OriginTypes.CONNECTOR.value,
                    "mimeType": "application/slack_batch",
                    "body": {
                        "userId": window.user_id,
                        "messageCount": len(window.messages),
                        "fileCount": len(window.files),
                        "windowStart": window.window_start,
                        "windowEnd": window.window_end,
                        "messages": [
                            {
                                "ts": msg.get('ts'),
                                "text": msg.get('text', ''),
                                "channel": msg.get('channel')
                            }
                            for msg in window.messages
                        ]
                    }
                }
                events.append(batch_event)
            else:
                # Single message, create individual event
                message = window.messages[0]
                event = {
                    "orgId": org_id,
                    "recordId": message.get('ts'),
                    "recordName": f"Slack_Message_{message.get('ts')}",
                    "recordType": RecordTypes.MESSAGE.value,
                    "eventType": EventTypes.NEW_RECORD.value,
                    "connectorName": Connectors.SLACK.value,
                    "origin": OriginTypes.CONNECTOR.value,
                    "mimeType": "text/slack_content",
                    "body": {
                        "text": message.get('text', ''),
                        "userId": message.get('user'),
                        "channelId": message.get('channel'),
                        "threadTs": message.get('thread_ts')
                    }
                }
                events.append(event)

            # Create individual events for files (always separate)
            for file in window.files:
                file_event = {
                    "orgId": org_id,
                    "recordId": file.get('id'),
                    "recordName": file.get('name', f"Slack_File_{file.get('id')}"),
                    "recordType": RecordTypes.FILE.value,
                    "eventType": EventTypes.NEW_RECORD.value,
                    "connectorName": Connectors.SLACK.value,
                    "origin": OriginTypes.CONNECTOR.value,
                    "mimeType": file.get('mimetype', 'application/octet-stream'),
                    "body": {
                        "fileName": file.get('name'),
                        "fileSize": file.get('size', 0),
                        "uploadedBy": file.get('user'),
                        "isPublic": file.get('is_public', False)
                    }
                }
                events.append(file_event)

            return events

        except Exception as e:
            self.logger.error(f"❌ Error creating Kafka events from window: {str(e)}")
            return []

    # ==================== UTILITY METHODS ====================

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

    # ==================== BOT CHANNEL ACCESS ====================

    async def ensure_bot_channel_access(self, channel_id: str) -> Dict[str, Any]:
        """Ensure bot has access to channel"""
        try:
            channel_info = await self.slack_client.conversations_info(channel=channel_id)

            if not channel_info.get('ok'):
                return {
                    "success": False,
                    "error": f"Failed to get channel info: {channel_info.get('error')}",
                    "action": "none"
                }

            channel_data = channel_info['channel']
            is_private = channel_data.get('is_private', False)
            is_member = channel_data.get('is_member', False)
            is_im = channel_data.get('is_im', False)

            if is_im:
                return {"success": True, "action": "dm_channel"}

            if is_member:
                return {"success": True, "action": "already_member"}

            if is_private:
                return {
                    "success": False,
                    "error": "Cannot join private channel - bot must be invited",
                    "action": "manual_invite_required"
                }

            return await self.join_channel(channel_id)

        except Exception as e:
            self.logger.error("❌ Error ensuring bot access: %s", str(e))
            return {"success": False, "error": str(e), "action": "error"}

    async def join_channel(self, channel_id: str) -> Dict[str, Any]:
        """Join a public channel"""
        try:
            response = await self.slack_client.conversations_join(channel=channel_id)

            if response.get('ok'):
                return {
                    "success": True,
                    "action": "joined",
                    "message": f"Successfully joined channel {channel_id}"
                }
            else:
                error_msg = response.get('error')
                if error_msg == 'already_in_channel':
                    return {"success": True, "action": "already_member"}
                else:
                    return {
                        "success": False,
                        "error": f"Failed to join channel: {error_msg}",
                        "action": "join_failed"
                    }

        except Exception as e:
            self.logger.error("❌ Error joining channel: %s", str(e))
            return {"success": False, "error": str(e), "action": "error"}

    # ==================== INCREMENTAL SYNC ====================

    async def sync_incremental_changes(
        self,
        workspace_id: str,
        since_timestamp: Optional[int] = None
    ) -> Dict[str, Any]:
        """Sync incremental changes using generators"""
        try:
            self.logger.info("🚀 Starting incremental sync")

            stats = {"processed": 0, "errors": []}

            if not since_timestamp:
                sync_metadata = await self.arango_service.get_sync_metadata("incremental")
                since_timestamp = sync_metadata.get('lastSyncTimestamp') if sync_metadata else 0

            channels = await self.arango_service.get_workspace_channels(workspace_id)

            for channel_data in channels:
                try:
                    channel_info = channel_data.get('channel', {})

                    if channel_info.get('isDeletedAtSource'):
                        continue

                    oldest = str(since_timestamp / 1000.0) if since_timestamp else None

                    channel_stats = await self.sync_channel_messages_with_threads(
                        channel_info['externalGroupId'],
                        channel_info['_key'],
                        workspace_id,
                        oldest=oldest
                    )

                    stats["processed"] += channel_stats["processed"]
                    stats["errors"].extend(channel_stats["errors"])

                    await asyncio.sleep(self.rate_limit_delay)

                except Exception as e:
                    self.logger.error("❌ Error in incremental sync: %s", str(e))
                    stats["errors"].append(str(e))

            # Update sync metadata
            current_timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
            await self.arango_service.store_sync_metadata("incremental", {
                "lastSyncTimestamp": current_timestamp,
                "lastSyncProcessed": stats["processed"],
                "lastSyncErrors": len(stats["errors"])
            })

            return stats

        except Exception as e:
            self.logger.error("❌ Incremental sync failed: %s", str(e))
            return {"processed": 0, "errors": [str(e)]}

    # ==================== DOWNTIME RECOVERY ====================

    async def handle_downtime_recovery(
        self,
        org_id: str,
        downtime_start: int,
        downtime_end: int
    ) -> Dict[str, Any]:
        """Handle recovery from downtime using generators"""
        try:
            self.logger.info("🔄 Starting downtime recovery")

            recovery_stats = {
                "recovered_messages": 0,
                "recovered_files": 0,
                "channels_recovered": 0,
                "errors": []
            }

            channels = await self.arango_service.get_workspace_channels(org_id)

            for channel_data in channels:
                try:
                    channel_info = channel_data.get('channel', {})

                    if channel_info.get('isDeletedAtSource'):
                        continue

                    oldest = str(downtime_start / 1000.0)
                    latest = str(downtime_end / 1000.0)

                    channel_stats = await self.sync_channel_messages_with_threads(
                        channel_info['externalGroupId'],
                        channel_info['_key'],
                        org_id,
                        oldest=oldest,
                        latest=latest
                    )

                    recovery_stats["recovered_messages"] += channel_stats["processed"]
                    recovery_stats["recovered_files"] += channel_stats["files_processed"]
                    recovery_stats["channels_recovered"] += 1 if channel_stats["processed"] > 0 else 0
                    recovery_stats["errors"].extend(channel_stats["errors"])

                    await asyncio.sleep(self.rate_limit_delay)

                except Exception as e:
                    self.logger.error(f"❌ Error in recovery: {str(e)}")
                    recovery_stats["errors"].append(str(e))

            return recovery_stats

        except Exception as e:
            self.logger.error("❌ Downtime recovery failed: %s", str(e))
            return {"recovered_messages": 0, "recovered_files": 0, "channels_recovered": 0, "errors": [str(e)]}

    # ==================== SYNC STATE MANAGEMENT ====================

    async def pause_sync(self, org_id: str, channel_id: Optional[str] = None) -> bool:
        """Pause sync for organization or specific channel"""
        try:
            if channel_id:
                await self.arango_service.update_channel_sync_state(channel_id, SyncState.PAUSED.value)
                self.logger.info(f"⏸️ Paused sync for channel: {channel_id}")
            else:
                await self.arango_service.update_workspace_sync_state(org_id, SyncState.PAUSED.value)
                self.logger.info(f"⏸️ Paused sync for workspace: {org_id}")

            return True

        except Exception as e:
            self.logger.error(f"❌ Error pausing sync: {str(e)}")
            return False

    async def resume_sync(self, org_id: str, channel_id: Optional[str] = None) -> bool:
        """Resume sync from checkpoint"""
        try:
            if channel_id:
                await self.arango_service.update_channel_sync_state(channel_id, SyncState.IN_PROGRESS.value)

                channel = await self.arango_service.get_channel_by_key(channel_id)
                if channel:
                    await self.sync_channel_messages_with_threads(
                        channel['externalGroupId'],
                        channel['_key'],
                        org_id,
                        resume_from_checkpoint=True
                    )
                    self.logger.info(f"▶️ Resumed sync for channel: {channel_id}")
                    return True
            else:
                workspace_state = await self.arango_service.get_workspace_sync_state(org_id)
                if workspace_state and workspace_state.get('syncState') == SyncState.PAUSED.value:
                    await self.sync_workspace_data(
                        workspace_state['workspace']['externalId'],
                        org_id
                    )
                    self.logger.info(f"▶️ Resumed workspace sync: {org_id}")
                    return True

            return False

        except Exception as e:
            self.logger.error(f"❌ Error resuming sync: {str(e)}")
            return False

    # ==================== CHECKPOINT MANAGEMENT ====================

    async def _initialize_checkpoint(
        self,
        channel_id: str,
        channel_key: str,
        org_id: str,
        resume_from_checkpoint: bool = False
    ) -> SyncCheckpoint:
        """Initialize or resume from sync checkpoint"""
        try:
            if resume_from_checkpoint:
                checkpoint_data = await self.arango_service.get_sync_checkpoint(channel_id, org_id)
                if checkpoint_data:
                    return SyncCheckpoint(
                        channel_id=channel_id,
                        last_processed_ts=checkpoint_data.get('last_processed_ts'),
                        processed_count=checkpoint_data.get('processed_count', 0),
                        error_count=checkpoint_data.get('error_count', 0),
                        last_cursor=checkpoint_data.get('last_cursor'),
                        checkpoint_timestamp=checkpoint_data.get('checkpoint_timestamp', 0)
                    )

            return SyncCheckpoint(
                channel_id=channel_id,
                checkpoint_timestamp=get_epoch_timestamp_in_ms()
            )

        except Exception as e:
            self.logger.error(f"❌ Error initializing checkpoint: {str(e)}")
            return SyncCheckpoint(channel_id=channel_id)

    async def _save_checkpoint(self, checkpoint: SyncCheckpoint) -> bool:
        """Save sync checkpoint"""
        try:
            checkpoint_data = {
                "channel_id": checkpoint.channel_id,
                "last_processed_ts": checkpoint.last_processed_ts,
                "processed_count": checkpoint.processed_count,
                "error_count": checkpoint.error_count,
                "last_cursor": checkpoint.last_cursor,
                "checkpoint_timestamp": checkpoint.checkpoint_timestamp,
                "updated_at": get_epoch_timestamp_in_ms()
            }

            await self.arango_service.save_sync_checkpoint(checkpoint.channel_id, checkpoint_data)
            return True

        except Exception as e:
            self.logger.error(f"❌ Error saving checkpoint: {str(e)}")
            return False

    # ==================== CLEANUP AND MONITORING ====================

    async def cleanup_resources(self) -> None:
        """Cleanup resources and free memory"""
        try:
            self._active_generators.clear()

            # Clear user message windows
            self._user_message_windows.clear()

            current_time = get_epoch_timestamp_in_ms()
            expired_checkpoints = [
                key for key, checkpoint in self._sync_checkpoints.items()
                if current_time - checkpoint.checkpoint_timestamp > 24 * 60 * 60 * 1000
            ]

            for key in expired_checkpoints:
                del self._sync_checkpoints[key]

            self.logger.info(f"🧹 Cleaned up {len(expired_checkpoints)} expired checkpoints and user windows")

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
                for event in all_kafka_events:
                    await self.kafka_service.send_event_to_kafka(event)

                self.logger.info(f"Flushed {len(all_kafka_events)} Kafka events from user windows")

            return all_kafka_events

        except Exception as e:
            self.logger.error(f"❌ Error flushing user windows: {str(e)}")
            return []

    # ==================== HEALTH AND STATISTICS ====================

    async def get_workspace_statistics(self, workspace_id: str) -> Dict[str, Any]:
        """Get comprehensive workspace statistics"""
        try:
            stats = {
                "channels": {"total": 0, "public": 0, "private": 0, "archived": 0},
                "users": {"total": 0, "active": 0},
                "messages": {"total": 0, "with_files": 0, "threads": 0},
                "files": {"total": 0}
            }

            channels = await self.arango_service.get_workspace_channels(workspace_id)
            stats["channels"]["total"] = len(channels)

            for channel_data in channels:
                channel_info = channel_data.get('channel', {})
                if channel_info.get('isDeletedAtSource'):
                    stats["channels"]["archived"] += 1
                elif channel_info.get('groupType') == GroupType.PRIVATE_CHANNEL.value:
                    stats["channels"]["private"] += 1
                else:
                    stats["channels"]["public"] += 1

            return stats

        except Exception as e:
            self.logger.error("❌ Error getting workspace statistics: %s", str(e))
            return stats

    async def get_sync_health(self, workspace_id: str) -> Dict[str, Any]:
        """Get sync health status"""
        try:
            health_status = {
                "status": "healthy",
                "last_sync": None,
                "sync_lag_minutes": 0,
                "error_rate": 0.0,
                "issues": []
            }

            sync_metadata = await self.arango_service.get_sync_metadata("incremental")

            if sync_metadata:
                health_status["last_sync"] = sync_metadata.get('lastSyncTimestamp')

                current_time = int(datetime.now(timezone.utc).timestamp() * 1000)
                if sync_metadata.get('lastSyncTimestamp'):
                    lag_ms = current_time - sync_metadata['lastSyncTimestamp']
                    health_status["sync_lag_minutes"] = lag_ms / (1000 * 60)

                processed = sync_metadata.get('lastSyncProcessed', 0)
                errors = sync_metadata.get('lastSyncErrors', 0)
                if processed > 0:
                    health_status["error_rate"] = errors / processed

            if health_status["sync_lag_minutes"] > SYNC_LAG_WARNING_MINUTES:
                health_status["status"] = "degraded"
                health_status["issues"].append("High sync lag detected")

            if health_status["error_rate"] > SYNC_ERROR_RATE_THRESHOLD:
                health_status["status"] = "unhealthy"
                health_status["issues"].append("High error rate detected")

            return health_status

        except Exception as e:
            self.logger.error("❌ Error getting sync health: %s", str(e))
            return {
                "status": "error",
                "error": str(e),
                "issues": ["Unable to determine sync health"]
            }

# ==================== USER MESSAGE GROUPING (ENHANCED FOR PROPER TIMESTAMP HANDLING) ====================

    async def _process_user_message_grouping(
        self,
        messages: List[Dict[str, Any]],
        org_id: str
    ) -> List[Dict[str, Any]]:
        """Process user message grouping with proper Slack timestamp handling"""
        try:
            current_time = datetime.now().timestamp()
            kafka_events = []

            # Get connector endpoint
            try:
                connector_endpoint = await self.config.get_config(config_node_constants.CONNECTOR_ENDPOINT.value)
                if not connector_endpoint:
                    connector_endpoint = "http://localhost:8088"
            except Exception:
                connector_endpoint = "http://localhost:8088"

            # Group messages by user with proper timestamp parsing
            for message in messages:
                user_id = message.get('user')
                if not user_id:
                    continue

                # Parse Slack timestamp properly (convert string to float)
                message_ts_str = message.get('ts')
                if not message_ts_str:
                    continue

                try:
                    message_ts = float(message_ts_str)
                except (ValueError, TypeError):
                    self.logger.warning(f"⚠️ Invalid timestamp format: {message_ts_str}")
                    continue

                window = self._user_message_windows[user_id]

                # Initialize window if empty
                if not window.user_id:
                    window.user_id = user_id
                    window.window_start = message_ts
                    window.window_end = message_ts + self.user_message_window_seconds

                # Check if message fits in current window (60 second window)
                if message_ts <= window.window_end:
                    # Add to current window
                    window.messages.append(message)
                    if message.get('files'):
                        window.files.extend(message['files'])

                    # Extend window end time
                    window.window_end = max(window.window_end, message_ts + self.user_message_window_seconds)
                else:
                    # Window expired, create events for current window
                    if window.messages:
                        events = await self._create_kafka_events_from_window(window, org_id)
                        kafka_events.extend(events)

                    # Start new window
                    window.messages = [message]
                    window.files = list(message.get('files', []))
                    window.window_start = message_ts
                    window.window_end = message_ts + self.user_message_window_seconds

            # Check for expired windows
            for user_id, window in list(self._user_message_windows.items()):
                if window.messages and (current_time - window.window_end) > self.user_message_window_seconds:
                    # Window expired, create events
                    events = await self._create_kafka_events_from_window(window, org_id)
                    kafka_events.extend(events)

                    # Clear window
                    del self._user_message_windows[user_id]

            return kafka_events

        except Exception as e:
            self.logger.error(f"❌ Error in user message grouping: {str(e)}")
            return []

    async def _create_kafka_events_from_window(
        self,
        window: UserMessageWindow,
        org_id: str
    ) -> List[Dict[str, Any]]:
        """Create Kafka events from user message window with proper Slack timestamps"""
        try:
            events = []

            # Get connector endpoint
            try:
                connector_endpoint = await self.config.get_config(config_node_constants.CONNECTOR_ENDPOINT.value)
                if not connector_endpoint:
                    connector_endpoint = "http://localhost:8088"
            except Exception:
                connector_endpoint = "http://localhost:8088"

            # Create events for grouped messages
            if len(window.messages) > 1:
                # Create batch event for multiple messages
                first_message = window.messages[0]
                last_message = window.messages[-1]

                # Use Slack timestamps properly
                first_ts = float(first_message.get('ts', 0))
                last_ts = float(last_message.get('ts', 0))

                batch_event = {
                    "orgId": org_id,
                    "recordId": f"batch_{window.user_id}_{int(first_ts)}",
                    "recordName": f"User Message Batch - {len(window.messages)} messages",
                    "recordType": RecordTypes.MESSAGE.value,
                    "eventType": EventTypes.NEW_RECORD.value,
                    "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/slack/batch/{window.user_id}_{int(first_ts)}/signedUrl",
                    "connectorName": Connectors.SLACK.value,
                    "origin": OriginTypes.CONNECTOR.value,
                    "mimeType": "application/slack_batch",
                    "createdAtSourceTimestamp": int(first_ts * 1000),  # Use Slack timestamp in milliseconds
                    "modifiedAtSourceTimestamp": int(last_ts * 1000),   # Use Slack timestamp in milliseconds
                    "body": {
                        "userId": window.user_id,
                        "messageCount": len(window.messages),
                        "fileCount": len(window.files),
                        "windowStart": first_ts,
                        "windowEnd": last_ts,
                        "messages": [
                            {
                                "ts": msg.get('ts'),
                                "text": msg.get('text', ''),
                                "channel": msg.get('channel'),
                                "threadTs": msg.get('thread_ts')
                            }
                            for msg in window.messages
                        ]
                    }
                }
                events.append(batch_event)
                self.logger.info(f"📦 Created batch event for {len(window.messages)} messages from user {window.user_id}")
            else:
                # Single message, create individual event
                message = window.messages[0]
                slack_ts = float(message.get('ts', 0))

                event = {
                    "orgId": org_id,
                    "recordId": message.get('ts'),
                    "recordName": f"Slack_Message_{message.get('ts')}",
                    "recordType": RecordTypes.MESSAGE.value,
                    "eventType": EventTypes.NEW_RECORD.value,
                    "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/slack/record/{message.get('ts')}/signedUrl",
                    "connectorName": Connectors.SLACK.value,
                    "origin": OriginTypes.CONNECTOR.value,
                    "mimeType": "text/slack_content",
                    "createdAtSourceTimestamp": int(slack_ts * 1000),  # Use Slack timestamp in milliseconds
                    "modifiedAtSourceTimestamp": int(slack_ts * 1000),  # Use Slack timestamp in milliseconds
                    "body": {
                        "text": message.get('text', ''),
                        "userId": message.get('user'),
                        "channelId": message.get('channel'),
                        "threadTs": message.get('thread_ts')
                    }
                }
                events.append(event)
                self.logger.info(f"📨 Created individual event for single message from user {window.user_id}")

            # Create individual events for files (always separate)
            for file in window.files:
                file_ts = float(file.get('created', 0))

                file_event = {
                    "orgId": org_id,
                    "recordId": file.get('id'),
                    "recordName": file.get('name', f"Slack_File_{file.get('id')}"),
                    "recordType": RecordTypes.FILE.value,
                    "eventType": EventTypes.NEW_RECORD.value,
                    "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/slack/record/{file.get('id')}/signedUrl",
                    "connectorName": Connectors.SLACK.value,
                    "origin": OriginTypes.CONNECTOR.value,
                    "mimeType": file.get('mimetype', 'application/octet-stream'),
                    "createdAtSourceTimestamp": int(file_ts * 1000),  # Use file's Slack timestamp
                    "modifiedAtSourceTimestamp": int(file_ts * 1000),  # Use file's Slack timestamp
                    "body": {
                        "fileName": file.get('name'),
                        "fileSize": file.get('size', 0),
                        "uploadedBy": file.get('user'),
                        "isPublic": file.get('is_public', False),
                        "extension": file.get('filetype'),
                        "permalink": file.get('permalink')
                    }
                }
                events.append(file_event)

            return events

        except Exception as e:
            self.logger.error(f"❌ Error creating Kafka events from window: {str(e)}")
            return []
