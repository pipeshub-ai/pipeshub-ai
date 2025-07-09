import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from app.config.configuration_service import (
    ConfigurationService,
    DefaultEndpoints,
    config_node_constants,
)
from app.config.utils.named_constants.arangodb_constants import (
    Connectors,
    EventTypes,
    OriginTypes,
    ProgressStatus,
    RecordTypes,
)
from app.config.utils.named_constants.timestamp_constants import (
    DEFAULT_SYNC_INTERVAL_SECONDS,
)
from app.connectors.services.kafka_service import KafkaService
from app.connectors.sources.google.common.arango_service import ArangoService
from app.connectors.sources.slack.handlers.slack_data_handler import SlackDataHandler
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class SlackSyncProgress:
    """Class to track sync progress"""

    def __init__(self) -> None:
        self.total_channels = 0
        self.processed_channels = 0
        self.total_messages = 0
        self.processed_messages = 0
        self.percentage = 0
        self.status = "initializing"
        self.lastUpdatedTimestamp = get_epoch_timestamp_in_ms()


class BaseSlackSyncService(ABC):
    """Abstract base class for Slack sync services"""

    def __init__(
        self,
        logger,
        config: ConfigurationService,
        arango_service: ArangoService,
        kafka_service: KafkaService,
        celery_app,
    ) -> None:
        self.logger = logger
        self.config_service = config
        self.arango_service = arango_service
        self.kafka_service = kafka_service
        self.celery_app = celery_app

        # Common state
        self.progress = SlackSyncProgress()
        self._current_batch = None
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._stop_requested = False

        # Locks
        self._sync_lock = asyncio.Lock()
        self._transition_lock = asyncio.Lock()

        # Configuration
        self._sync_task = None
        self.batch_size = 100
        self.rate_limit_delay = 1.0

        # Slack client will be initialized in connect_services
        self.slack_client = None
        self.workspace_id = None
        self.data_handler = None

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
        self, org_id, action: str = "start"
    ) -> bool:
        """Perform initial sync"""
        pass

    async def start(self, org_id) -> bool:
        """Start Slack sync based on event trigger"""
        self.logger.info("🚀 Starting Slack sync")
        async with self._transition_lock:
            try:
                # Check current state
                sync_state = await self.arango_service.get_workspace_sync_state(org_id)
                current_state = (
                    sync_state.get("syncState") if sync_state else ProgressStatus.NOT_STARTED.value
                )

                if current_state == ProgressStatus.IN_PROGRESS.value:
                    self.logger.warning("💥 Slack sync service is already running")
                    return False

                # if current_state == ProgressStatus.PAUSED.value:
                #     self.logger.warning("💥 Slack sync is paused, use resume to continue")
                #     return False

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

                self.logger.info("✅ Slack sync service started")
                return True

            except Exception as e:
                self.logger.error("❌ Failed to start Slack sync service: %s", str(e))
                return False

    async def pause(self, org_id) -> bool:
        self.logger.info("⏸️ Pausing Slack sync service")
        async with self._transition_lock:
            try:
                # Check current state
                sync_state = await self.arango_service.get_workspace_sync_state(org_id)
                current_state = (
                    sync_state.get("syncState") if sync_state else ProgressStatus.NOT_STARTED.value
                )

                if current_state != ProgressStatus.IN_PROGRESS.value:
                    self.logger.warning("💥 Slack sync service is not running")
                    return False

                self._stop_requested = True

                # Update workspace state
                await self.arango_service.update_workspace_sync_state(
                    org_id, ProgressStatus.PAUSED.value
                )

                # Cancel current sync task
                if self._sync_task and not self._sync_task.done():
                    self._sync_task.cancel()
                    try:
                        await self._sync_task
                    except asyncio.CancelledError:
                        pass

                self.logger.info("✅ Slack sync service paused")
                return True

            except Exception as e:
                self.logger.error("❌ Failed to pause Slack sync service: %s", str(e))
                return False

    async def resume(self, org_id) -> bool:
        self.logger.info("🔄 Resuming Slack sync service")
        async with self._transition_lock:
            try:
                # Check current state
                sync_state = await self.arango_service.get_workspace_sync_state(org_id)
                if not sync_state:
                    self.logger.warning("⚠️ No sync state found, starting fresh")
                    return await self.start(org_id)

                current_state = sync_state.get("syncState")
                if current_state == ProgressStatus.IN_PROGRESS.value:
                    self.logger.warning("💥 Slack sync service is already running")
                    return False

                if current_state != ProgressStatus.PAUSED.value:
                    self.logger.warning("💥 Slack sync was not paused, use start instead")
                    return False

                self._pause_event.set()
                self._stop_requested = False

                # Start sync with resume state
                self._sync_task = asyncio.create_task(
                    self.perform_initial_sync(org_id, action="resume")
                )

                self.logger.info("✅ Slack sync service resumed")
                return True

            except Exception as e:
                self.logger.error("❌ Failed to resume Slack sync service: %s", str(e))
                return False

    async def _should_stop(self, org_id) -> bool:
        """Check if operation should stop"""
        if self._stop_requested:
            current_state = await self.arango_service.get_workspace_sync_state(org_id)
            if current_state:
                current_state = current_state.get("syncState")
                if current_state == ProgressStatus.IN_PROGRESS.value:
                    await self.arango_service.update_workspace_sync_state(
                        org_id, ProgressStatus.PAUSED.value
                    )
                    self.logger.info("✅ Slack sync state updated before stopping")
                    return True
            return False
        return False


class SlackSyncEnterpriseService(BaseSlackSyncService):
    """Slack sync service for enterprise/workspace setup"""

    def __init__(
        self,
        logger,
        config: ConfigurationService,
        arango_service: ArangoService,
        kafka_service: KafkaService,
        celery_app,
    ) -> None:
        super().__init__(
            logger, config, arango_service, kafka_service, celery_app
        )
        self.initialized = False

    async def connect_services(self, org_id: str) -> bool:
        """Connect to Slack services using SlackTokenHandler"""
        try:
            self.logger.info("🚀 Connecting to Slack services")

            # Use SlackTokenHandler to fetch bot credentials
            from app.connectors.sources.slack.core.slack_token_handler import (
                SlackTokenHandler,
            )
            slack_token_handler = SlackTokenHandler(self.logger, self.config_service)
            creds = await slack_token_handler.get_bot_config(org_id=org_id)
            if not creds:
                raise Exception("Slack bot credentials not found from config manager")

            bot_token = creds.get('bot_token')
            signing_secret = creds.get('signing_secret')
            if not bot_token:
                raise Exception("Slack bot token not found in credentials")
            if not signing_secret:
                raise Exception("Slack signing secret not found in credentials")

            # Initialize Slack client
            from app.connectors.sources.slack.core.slack_bot import SlackBot
            self.slack_client = SlackBot(
                token=bot_token,
                signing_secret=signing_secret
            )

            # Initialize the bot
            if not await self.slack_client.initialize():
                raise Exception("Failed to initialize Slack bot")

            self.workspace_id = self.slack_client.get_workspace_id
            if not self.workspace_id:
                raise Exception("Failed to get workspace ID")

            self.logger.info(f"Connected to workspace: {self.slack_client.workspace_info['name']} ({self.workspace_id})")

            # Initialize data handler
            self.data_handler = SlackDataHandler(
                logger=self.logger,
                arango_service=self.arango_service,
                slack_client=self.slack_client.client,  # Use the AsyncWebClient
                kafka_service=self.kafka_service,
                config=self.config_service
            )

            self.logger.info("✅ Slack services connected successfully")
            return True

        except Exception as e:
            self.logger.error("❌ Slack service connection failed: %s", str(e))
            return False

    async def initialize(self, org_id) -> bool:
        """Initialize Slack sync service"""
        try:
            if self.initialized:
                self.logger.info("Slack sync service already initialized")
                return True

            self.logger.info("🚀 Initializing Slack sync service")

            if not await self.connect_services(org_id):
                return False

            # Check sync state
            sync_state = await self.arango_service.get_workspace_sync_state(org_id)
            current_state = (
                sync_state.get("syncState") if sync_state else ProgressStatus.NOT_STARTED.value
            )

            if current_state == ProgressStatus.IN_PROGRESS.value:
                self.logger.warning(
                    f"Sync is currently RUNNING for workspace {self.workspace_id}. Pausing it."
                )
                await self.arango_service.update_workspace_sync_state(
                    org_id, ProgressStatus.PAUSED.value
                )

            # Initialize Celery if provided
            if self.celery_app:
                await self.celery_app.setup_app()

            self.logger.info("✅ Slack sync service initialized successfully")
            return True

        except Exception as e:
            self.logger.error("❌ Failed to initialize Slack sync: %s", str(e))
            return False

    async def perform_initial_sync(self, org_id, action: str = "start") -> bool:
        """Perform initial synchronization of all Slack data"""
        try:
            if await self._should_stop(org_id):
                self.logger.info("Sync stopped before starting")
                return False

            # Check if this is really first sync
            sync_state = await self.arango_service.get_workspace_sync_state(org_id)
            if sync_state is None:
                # Create initial sync state
                await self.arango_service.create_workspace_sync_state(
                    org_id, ProgressStatus.NOT_STARTED.value
                )
                sync_state = {"syncState": ProgressStatus.NOT_STARTED.value}

            current_state = sync_state.get("syncState")
            if current_state == ProgressStatus.COMPLETED.value:
                self.logger.info("✅ Initial sync already completed, performing incremental sync")
                return await self.perform_incremental_sync(org_id)

            # Update workspace sync state to RUNNING
            await self.arango_service.update_workspace_sync_state(
                org_id, ProgressStatus.IN_PROGRESS.value
            )

            if await self._should_stop(org_id):
                self.logger.info("Sync stopped during initialization")
                await self.arango_service.update_workspace_sync_state(
                    org_id, ProgressStatus.PAUSED.value
                )
                return False

            # Start comprehensive sync
            sync_stats = await self.data_handler.sync_workspace_data(
                workspace_id=self.workspace_id,
                org_id=org_id
            )

            # Check if sync completed successfully
            if sync_stats["errors"]:
                self.logger.warning(f"Sync completed with {len(sync_stats['errors'])} errors")
                await self.arango_service.update_workspace_sync_state(
                    org_id, ProgressStatus.FAILED.value
                )
                return False

            # Update workspace state to COMPLETED
            await self.arango_service.update_workspace_sync_state(
                org_id, ProgressStatus.COMPLETED.value
            )

            self.logger.info("✅ Initial sync completed successfully")
            self.logger.info(f"Sync statistics: {sync_stats}")
            return True

        except Exception as e:
            # Update workspace state to FAILED
            await self.arango_service.update_workspace_sync_state(
                org_id, ProgressStatus.FAILED.value
            )
            self.logger.error(f"❌ Initial sync failed: {str(e)}")
            return False

    async def perform_incremental_sync(self, org_id) -> bool:
        """Perform incremental sync for changes since last sync"""
        try:
            self.logger.info("🔄 Starting incremental sync")

            # Get last sync timestamp
            last_sync = await self.arango_service.get_last_sync_timestamp(org_id)
            if not last_sync:
                self.logger.warning("No last sync timestamp found, performing full sync")
                return await self.perform_initial_sync(org_id)

            # Use data handler's incremental sync
            sync_stats = await self.data_handler.sync_incremental_changes(
                workspace_id=self.workspace_id,
                since_timestamp=last_sync
            )

            self.logger.info(f"✅ Incremental sync completed: {sync_stats}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Incremental sync failed: {str(e)}")
            return False

    async def sync_specific_channel(self, channel_id: str, org_id: str) -> bool:
        """Synchronize a specific channel's content"""
        try:
            self.logger.info(f"🚀 Starting sync for specific channel: {channel_id}")

            if not self.data_handler:
                if not await self.connect_services(org_id):
                    return False

            # Sync specific channel messages
            channel_stats = await self.data_handler.sync_channel_messages_with_threads(
                channel_id=channel_id,
                channel_key=None,  # Will be resolved by data handler
                org_id=org_id
            )

            if channel_stats["errors"]:
                self.logger.error(f"Channel sync failed with errors: {channel_stats['errors']}")
                return False

            self.logger.info(f"✅ Successfully completed sync for channel {channel_id}")
            self.logger.info(f"Channel sync stats: {channel_stats}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to sync channel {channel_id}: {str(e)}")
            return False

    async def resync_workspace(self, org_id) -> bool:
        """Resync entire workspace data"""
        try:
            self.logger.info(f"🔄 Resyncing workspace for org {org_id}")

            if not self.data_handler:
                if not await self.connect_services(org_id):
                    return False

            # Check if workspace needs recovery (e.g., after downtime)
            last_shutdown = await self.arango_service.get_last_shutdown_time()
            current_time = datetime.now(timezone.utc).timestamp()

            if last_shutdown and (current_time - last_shutdown) > DEFAULT_SYNC_INTERVAL_SECONDS:  # 5 minutes
                self.logger.info("Detected downtime, performing recovery sync")
                return await self.perform_recovery_sync(org_id, last_shutdown, current_time)
            else:
                return await self.perform_incremental_sync(org_id)

        except Exception as e:
            self.logger.error(f"❌ Error resyncing workspace: {str(e)}")
            return False

    async def perform_recovery_sync(self, org_id: str, start_time: float, end_time: float) -> bool:
        """Perform recovery sync for messages missed during downtime"""
        try:
            self.logger.info(f"🔄 Starting recovery sync from {start_time} to {end_time}")

            # Get all channels
            channels = await self.arango_service.get_workspace_channels(org_id)
            total_channels = len(channels)
            processed_channels = 0

            for channel_data in channels:
                try:
                    channel_info = channel_data['channel']

                    # Skip archived channels
                    if channel_info.get('isDeletedAtSource'):
                        continue

                    # Sync messages in timeframe
                    oldest = str(start_time)
                    latest = str(end_time)

                    channel_stats = await self.data_handler.sync_channel_messages_with_threads(
                        channel_id=channel_info['externalGroupId'],
                        channel_key=channel_info['_key'],
                        org_id=org_id,
                        oldest=oldest,
                        latest=latest
                    )

                    processed_channels += 1
                    self.logger.info(
                        f"Recovery progress: {processed_channels}/{total_channels} channels, "
                        f"processed {channel_stats['processed']} messages"
                    )

                    # Rate limiting
                    await asyncio.sleep(self.rate_limit_delay)

                except Exception as e:
                    self.logger.error(f"Error in recovery sync for channel: {str(e)}")
                    continue

            self.logger.info(f"✅ Recovery sync completed for {processed_channels} channels")
            return True

        except Exception as e:
            self.logger.error(f"❌ Recovery sync failed: {str(e)}")
            return False

    async def reindex_failed_records(self, org_id) -> bool | None:
        """Reindex failed records"""
        try:
            self.logger.info("🔄 Starting reindexing of failed records")

            # Query to get all failed records for Slack connector
            failed_records = await self.arango_service.get_failed_records(
                org_id, Connectors.SLACK.value
            )

            if not failed_records:
                self.logger.info("⚠️ No failed records found")
                return True

            endpoints = await self.config_service.get_config(
                config_node_constants.ENDPOINTS.value
            )
            connector_endpoint = endpoints.get("connectors").get(
                "endpoint", DefaultEndpoints.CONNECTOR_ENDPOINT.value
            )

            count = 0
            for record in failed_records:
                try:
                    # Prepare reindex event
                    event = {
                        "orgId": org_id,
                        "recordId": record["_key"],
                        "recordName": record["recordName"],
                        "recordVersion": record["version"],
                        "recordType": record["recordType"],
                        "eventType": EventTypes.REINDEX_RECORD.value,
                        "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/slack/record/{record['_key']}/signedUrl",
                        "connectorName": Connectors.SLACK.value,
                        "origin": OriginTypes.CONNECTOR.value,
                        "createdAtSourceTimestamp": record.get("sourceCreatedAtTimestamp"),
                        "modifiedAtSourceTimestamp": record.get("sourceLastModifiedTimestamp"),
                    }

                    # Add type-specific fields
                    if record["recordType"] == RecordTypes.MESSAGE.value:
                        event["mimeType"] = "text/slack_content"
                    elif record["recordType"] == RecordTypes.ATTACHMENT.value:
                        # Get file metadata for mime type
                        file_metadata = await self.arango_service.get_file_metadata(record["_key"])
                        if file_metadata:
                            event["mimeType"] = file_metadata.get("mimeType", "application/octet-stream")

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
