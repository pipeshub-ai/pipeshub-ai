from typing import Dict, List
from confluent_kafka import Consumer, KafkaError
import json
import asyncio
import time
from dependency_injector.wiring import inject
from app.config.configuration_service import config_node_constants, KafkaConfig

class SyncKafkaRouteConsumer:
    def __init__(self, logger, config_service, arango_service, sync_tasks):
        self.logger = logger
        self.consumer = None
        self.running = False
        self.config_service = config_service
        self.arango_service = arango_service
        self.sync_tasks = sync_tasks
        self.processed_messages: Dict[str, List[int]] = {}
        self.route_mapping = {
            'sync-events': {
                'drive.init': self.drive_init,
                'drive.start': self.drive_start_sync,
                'drive.pause': self.drive_pause_sync,
                'drive.resume': self.drive_resume_sync,
                'drive.user': self.drive_sync_user,
                'gmail.init': self.gmail_init,
                'gmail.start': self.gmail_start_sync,
                'gmail.pause': self.gmail_pause_sync,
                'gmail.resume': self.gmail_resume_sync,
                'gmail.user': self.gmail_sync_user,
                'drive.resync': self.resync_drive,
                'gmail.resync': self.resync_gmail,
                'connectorPublicUrlChanged': self.connector_public_url_changed,
                'gmailUpdatesEnabledEvent': self.gmail_updates_enabled_event,
                'gmailUpdatesDisabledEvent': self.gmail_updates_disabled_event
            }
        }
        self.consume_task = None

    async def create_consumer(self):
        """Initialize the Kafka consumer"""
        try:
            async def get_kafka_config():
                kafka_config = await self.config_service.get_config(config_node_constants.KAFKA.value)
                
                brokers = kafka_config['brokers']
    
                return {
                    'bootstrap.servers': ",".join(brokers),
                    'group.id': 'record_consumer_group',
                    'auto.offset.reset': 'earliest',
                    'enable.auto.commit': True,
                    'isolation.level': 'read_committed',
                    'enable.partition.eof': False,
                    'client.id': KafkaConfig.CLIENT_ID_RECORDS.value
                }

            KAFKA_CONFIG = await get_kafka_config()
            
            self.consumer = Consumer(KAFKA_CONFIG)
            # Add a small delay to allow for topic creation
            time.sleep(2)
            # Subscribe to the two main topics
            self.consumer.subscribe(['sync-events'])
            self.logger.info("Successfully subscribed to topics: sync-events")
        except Exception as e:
            self.logger.error(f"Failed to create consumer: {e}")
            raise

    def is_message_processed(self, message_id: str) -> bool:
        """Check if a message has already been processed."""
        topic_partition = '-'.join(message_id.split('-')[:-1])
        offset = int(message_id.split('-')[-1])
        return (topic_partition in self.processed_messages and
                offset in self.processed_messages[topic_partition])

    def mark_message_processed(self, message_id: str):
        """Mark a message as processed."""
        topic_partition = '-'.join(message_id.split('-')[:-1])
        offset = int(message_id.split('-')[-1])
        if topic_partition not in self.processed_messages:
            self.processed_messages[topic_partition] = []
        self.processed_messages[topic_partition].append(offset)

    @inject
    async def process_message(self, message):
        """Process incoming Kafka messages and route them to appropriate handlers"""
        try:
            message_id = f"{message.topic()}-{message.partition()}-{message.offset()}"

            if self.is_message_processed(message_id):
                self.logger.info(f"Message {message_id} already processed, skipping")
                return True

            topic = message.topic()
            message_value = message.value()
            event_type = None  # Initialize event_type
            
            if isinstance(message_value, bytes):
                message_value = message_value.decode('utf-8')
            
            if isinstance(message_value, str):
                try:
                    value = json.loads(message_value)

                    if isinstance(value, str):
                        value = json.loads(value)
                    self.logger.debug(f"Type of value: {type(value)}")
                    self.logger.debug(f"Value: {value}")
                    event_type = value.get('eventType')
                except json.JSONDecodeError as e:
                    self.logger.error(f"Failed to parse JSON: {e}")
            else:
                self.logger.error(f"Unexpected message value type: {type(message_value)}")
            
            if not event_type or topic not in self.route_mapping:
                self.logger.error(f"Invalid topic or missing event_type: {topic}, {event_type}")
                return False

            if topic == 'sync-events':
                return await self._handle_sync_event(event_type, value)

        except Exception as e:
            self.logger.error(f"Error processing message from topic {message.topic()}: {str(e)}")
            return False
        finally:
            self.mark_message_processed(message_id)

    async def _handle_sync_event(self, event_type: str, value: dict) -> bool:
        """Handle sync-related events by calling appropriate ArangoDB methods"""
        handler = self.route_mapping['sync-events'].get(event_type)
        if not handler:
            self.logger.error(f"Unknown entity event type: {event_type}")
            return False

        return await handler(value['payload'])

    async def drive_init(self, payload):
        """Initialize sync service and wait for schedule"""
        try:
            org_id = payload.get('orgId')
            if not org_id:
                raise ValueError("orgId is required")
                
            self.logger.info(f"Initializing sync service for org_id: {org_id}")
            # Initialize directly since we can't use BackgroundTasks in Kafka consumer
            await self.sync_tasks.drive_sync_service.initialize(org_id)
            return {
                "status": "accepted",
                "message": "Sync service initialization queued",
                "service": "Google Drive Sync"
            }
        except Exception as e:
            self.logger.error(
                "Failed to queue sync service initialization: %s", str(e))
            return False

    async def drive_start_sync(self, payload):
        """Queue immediate start of the sync service"""
        try:
            org_id = payload.get('orgId')
            if not org_id:
                raise ValueError("orgId is required")
                
            self.logger.info(f"Starting sync service for org_id: {org_id}")
            await self.sync_tasks.drive_manual_sync_control('start', org_id)
            return {
                "status": "accepted",
                "message": "Sync service start request queued"
            }
        except Exception as e:
            self.logger.error("Failed to queue sync service start: %s", str(e))
            return False

    async def drive_pause_sync(self, payload):
        """Pause the sync service"""
        try:
            org_id = payload.get('orgId')
            if not org_id:
                raise ValueError("orgId is required")
                
            self.logger.info(f"Pausing sync service for org_id: {org_id}")
            await self.sync_tasks.drive_manual_sync_control('pause', org_id)
            return {
                "status": "accepted",
                "message": "Sync service pause request queued"
            }
        except Exception as e:
            self.logger.error("Failed to queue sync service pause: %s", str(e))
            return False

    async def drive_resume_sync(self, payload):
        """Resume the sync service"""
        try:
            org_id = payload.get('orgId')
            if not org_id:
                raise ValueError("orgId is required")
                
            self.logger.info(f"Resuming sync service for org_id: {org_id}")
            await self.sync_tasks.drive_manual_sync_control('resume', org_id)
            return {
                "status": "accepted",
                "message": "Sync service resume request queued"
            }
        except Exception as e:
            self.logger.error("Failed to queue sync service resume: %s", str(e))
            return False

    async def drive_sync_user(self, payload):
        """Sync a user's Google Drive"""
        try:
            user_email = payload.get('email')
            if not user_email:
                raise ValueError("email is required")
                
            self.logger.info(f"Syncing user: {user_email}")
            return await self.sync_tasks.drive_sync_service.sync_specific_user(user_email)
        except Exception as e:
            self.logger.error("Error syncing user: %s", str(e))
            return False

    async def gmail_init(self, payload):
        """Initialize sync service and wait for schedule"""
        try:
            org_id = payload.get('orgId')
            if not org_id:
                raise ValueError("orgId is required")
                
            self.logger.info(f"Initializing sync service for org_id: {org_id}")
            await self.sync_tasks.gmail_sync_service.initialize(org_id)
            return {
                "status": "accepted",
                "message": "Sync service initialization queued",
                "service": "Google Gmail Sync"
            }
        except Exception as e:
            self.logger.error(
                "Failed to queue sync service initialization: %s", str(e))
            return False

    async def gmail_start_sync(self, payload):
        """Queue immediate start of the sync service"""
        try:
            org_id = payload.get('orgId')
            if not org_id:
                raise ValueError("orgId is required")
                
            self.logger.info(f"Starting sync service for org_id: {org_id}")
            await self.sync_tasks.gmail_manual_sync_control('start', org_id)
            return {
                "status": "accepted",
                "message": "Sync service start request queued"
            }
        except Exception as e:
            self.logger.error("Failed to queue sync service start: %s", str(e))
            return False

    async def gmail_pause_sync(self, payload):
        """Pause the sync service"""
        try:
            org_id = payload.get('orgId')
            if not org_id:
                raise ValueError("orgId is required")
                
            self.logger.info(f"Pausing sync service for org_id: {org_id}")
            await self.sync_tasks.gmail_manual_sync_control('pause', org_id)
            return {
                "status": "accepted",
                "message": "Sync service pause request queued"
            }
        except Exception as e:
            self.logger.error("Failed to queue sync service pause: %s", str(e))
            return False

    async def gmail_resume_sync(self, payload):
        """Resume the sync service"""
        try:
            org_id = payload.get('orgId')
            if not org_id:
                raise ValueError("orgId is required")
                
            self.logger.info(f"Resuming sync service for org_id: {org_id}")
            await self.sync_tasks.gmail_manual_sync_control('resume', org_id)
            return {
                "status": "accepted",
                "message": "Sync service resume request queued"
            }
        except Exception as e:
            self.logger.error("Failed to queue sync service resume: %s", str(e))
            return False

    async def gmail_sync_user(self, payload):
        """Sync a user's Google Drive"""
        try:
            user_email = payload.get('email')
            if not user_email:
                raise ValueError("email is required")
                
            self.logger.info(f"Syncing user: {user_email}")
            return await self.sync_tasks.gmail_sync_service.sync_specific_user(user_email)
        except Exception as e:
            self.logger.error("Error syncing user: %s", str(e))
            return False

    async def resync_drive(self, payload):
        """Resync a user's Google Drive"""
        try:
            org_id = payload.get('orgId')
            if not org_id:
                raise ValueError("orgId is required")
            
            user_id = payload.get('userId')
            if user_id:
                self.logger.info(f"Resyncing user: {user_id}")
                user = await self.arango_service.get_user_by_user_id(user_id)
                return await self.sync_tasks.drive_sync_service.resync_drive(org_id, user)
            else:
                self.logger.info(f"Resyncing all users for org: {org_id}")
                users = await self.arango_service.get_users(org_id, active = True)
                for user in users:
                    if not await self.sync_tasks.drive_sync_service.resync_drive(org_id, user):
                        self.logger.error(f"Error resyncing user {user['email']}")
                        continue
                return True
        except Exception as e:
            self.logger.error("Error resyncing user: %s", str(e))
            return False

    async def resync_gmail(self, payload):
        """Resync a user's Google Gmail"""
        try:
            org_id = payload.get('orgId')
            if not org_id:
                raise ValueError("orgId is required")
            
            user_id = payload.get('userId')
            if user_id:
                self.logger.info(f"Resyncing user: {user_id}")
                user = await self.arango_service.get_user_by_user_id(user_id)
                return await self.sync_tasks.gmail_sync_service.resync_gmail(org_id, user)
            else:
                self.logger.info(f"Resyncing all users for org: {org_id}")
                users = await self.arango_service.get_users(org_id, active = True)
                for user in users:
                    if not await self.sync_tasks.gmail_sync_service.resync_gmail(org_id, user):
                        self.logger.error(f"Error resyncing user {user['email']}")
                        continue
                return True
        except Exception as e:
            self.logger.error("Error resyncing user: %s", str(e))
            return False
        
    async def connector_public_url_changed(self, payload):
        """Handle connector public URL changed event"""
        try:
            org_id = payload.get('orgId')
            if not org_id:
                raise ValueError("orgId is required")
            users = await self.arango_service.get_users(org_id, active = True)
            for user in users:
                await self.sync_tasks.drive_sync_service.setup_changes_watch(user['email'])
            await self.resync_drive(payload)
            return True
        except Exception as e:
            self.logger.error("Error handling connector public URL changed event: %s", str(e))
            return False
        
    async def gmail_updates_enabled_event(self, payload):
        """Handle Gmail updates enabled event"""
        try:
            self.logger.info(f"Gmail updates enabled event: {payload}")
            org_id = payload.get('orgId')
            if not org_id:
                raise ValueError("orgId is required")
            users = await self.arango_service.get_users(org_id, active = True)
            for user in users:
                await self.sync_tasks.gmail_sync_service.setup_changes_watch(org_id, user['email'])
            await self.resync_gmail(payload)
            return True
        except Exception as e:
            self.logger.error("Error handling Gmail updates enabled event: %s", str(e))
            return False
    
    async def gmail_updates_disabled_event(self, payload):
        """Handle Gmail updates disabled event"""
        try:
            self.logger.info(f"Gmail updates disabled event: {payload}")
            org_id = payload.get('orgId')
            if not org_id:
                raise ValueError("orgId is required")
            users = await self.arango_service.get_users(org_id, active = True)
            for user in users:
                await self.sync_tasks.gmail_sync_service.stop_changes_watch(user['email'])
            return True
        except Exception as e:
            self.logger.error("Error handling Gmail updates disabled event: %s", str(e))
            return False

    async def consume_messages(self):
        """Main consumption loop."""
        try:
            self.logger.info("Starting Kafka consumer loop")
            while self.running:
                try:
                    message = self.consumer.poll(1.0)

                    if message is None:
                        await asyncio.sleep(0.1)
                        continue

                    if message.error():
                        if message.error().code() == KafkaError._PARTITION_EOF:
                            continue
                        else:
                            self.logger.error(f"Kafka error: {message.error()}")
                            continue

                    self.logger.info(f"Received message: {message}")
                    success = await self.process_message(message)

                    if success:
                        self.consumer.commit(message)
                        self.logger.info(f"Committed offset for topic-partition {message.topic()}-{message.partition()} at offset {message.offset()}")

                except asyncio.CancelledError:
                    self.logger.info("Kafka consumer task cancelled")
                    break
                except Exception as e:
                    self.logger.error(f"Error processing Kafka message: {e}")
                    await asyncio.sleep(1)

        except Exception as e:
            self.logger.error(f"Fatal error in consume_messages: {e}")
        finally:
            if self.consumer:
                self.consumer.close()
                self.logger.info("Kafka consumer closed")

    async def start(self):
        """Start the Kafka consumer."""
        try:
            self.running = True
            # Create a task for consuming messages
            await self.create_consumer()
            self.consume_task = asyncio.create_task(self.consume_messages())
            self.logger.info("Started Kafka consumer task")
        except Exception as e:
            self.logger.error(f"Failed to start Kafka consumer: {str(e)}")
            raise

    def stop(self):
        """Stop the Kafka consumer."""
        self.running = False
        if self.consume_task:
            self.consume_task.cancel()
