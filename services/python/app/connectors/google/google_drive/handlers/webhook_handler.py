from abc import ABC, abstractmethod
from typing import Dict, Set
import asyncio
from datetime import datetime, timezone, timedelta
import json
import os

from app.config.configuration_service import ConfigurationService, config_node_constants, WebhookConfig
from app.config.arangodb_constants import CollectionNames
from app.utils.logger import logger

class AbstractDriveWebhookHandler(ABC):
    def __init__(self, config: ConfigurationService, arango_service, change_handler):
        self.config_service = config
        self.arango_service = arango_service
        self.change_handler = change_handler

        # Common state management
        self.processing_lock = asyncio.Lock()
        self.scheduled_task = None
        self.last_notification_time = None

    async def _log_headers(self, headers: Dict) -> Dict:
        """Log webhook headers and return important headers"""
        os.makedirs('logs/webhook_headers', exist_ok=True)
        log_file_path = 'logs/webhook_headers/headers_log.json'

        important_headers = {
            'resource_id': headers.get('x-goog-resource-id'),
            'changed_id': headers.get('x-goog-changed'),
            'resource_state': headers.get('x-goog-resource-state'),
            'channel_id': headers.get('x-goog-channel-id'),
            'timestamp': datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat(),
        }

        try:
            with open(log_file_path, 'r+') as f:
                try:
                    log_data = json.load(f)
                except json.JSONDecodeError:
                    log_data = []
                log_data.append(important_headers)
                f.seek(0)
                json.dump(log_data, f, indent=4)
                f.truncate()
        except FileNotFoundError:
            with open(log_file_path, 'w') as f:
                json.dump([important_headers], f, indent=4)

        return important_headers

    @abstractmethod
    async def process_notification(self, headers: Dict) -> bool:
        """Process incoming webhook notification"""
        pass

    @abstractmethod
    async def _delayed_process_notifications(self):
        """Process coalesced notifications after delay"""
        pass

    @abstractmethod
    async def handle_downtime(self, org_id):
        """Handle system downtime recovery"""
        pass


class IndividualDriveWebhookHandler(AbstractDriveWebhookHandler):
    """Handles webhooks for individual user accounts"""

    def __init__(self, config: ConfigurationService, drive_user_service, arango_service, change_handler):
        super().__init__(config, arango_service, change_handler)
        self.drive_user_service = drive_user_service
        self.arango_service = arango_service
        self.change_handler = change_handler
        self.pending_notifications: Set[str] = set()

    async def process_notification(self, headers: Dict) -> bool:
        try:
            important_headers = await self._log_headers(headers)

            channel_id = headers.get('x-goog-channel-id')
            if not channel_id:
                logger.error("No channel ID in notification")
                return False

            # Get token  for this channel
            token = await self.arango_service.get_page_token_db(
                channel_id=channel_id
            )
            if not token:
                logger.error(f"No user found for channel {channel_id}")
                return False

            user_email = token['userEmail']

            # Add to user's pending notifications
            self.pending_notifications.add(json.dumps(important_headers))

            # Schedule processing for this user
            if self.scheduled_task and not self.scheduled_task.done():
                self.scheduled_task.cancel()
            self.scheduled_task = asyncio.create_task(
                self._delayed_process_notifications(user_email)
            )
            return True

        except Exception as e:
            logger.error(f"Error processing individual notification: {str(e)}")
            return False

    async def _delayed_process_notifications(self, user_email: str = None):
        """Process notifications for a specific user"""
        try:
            coalesce_delay = WebhookConfig.COALESCEDELAY.value
            await asyncio.sleep(coalesce_delay)

            async with self.processing_lock:
                if not self.pending_notifications:
                    return

                # Process user's notifications
                notifications = self.pending_notifications
                for notification_json in notifications:
                    notification = json.loads(notification_json)
                    await self._process_user_changes(user_email, notification)

                # Clear processed notifications for this user
                self.pending_notifications.clear()
                logger.info(f"🚀 Cleared processed notifications for user {user_email}")

        except asyncio.CancelledError:
            logger.info(f"Processing delayed for user {user_email}")
        except Exception as e:
            logger.error(
                "Error processing notifications for user %s: %s", user_email, str(e))

    async def _process_user_changes(self, user_email: str, notification: Dict):
        """Process changes for a single user"""
        user_service = self.drive_user_service

        page_token = await self.arango_service.get_page_token_db(
            notification['channel_id'],
            notification['resource_id']
        )

        if not page_token:
            return

        changes, new_token = await user_service.get_changes(
            page_token=page_token['token']
        )
        user_id = await self.arango_service.get_entity_id_by_email(user_email)
        
        # Get org_id from belongsTo relation for this user
        query = f"""
        FOR edge IN belongsTo
            FILTER edge._from == 'users/{user_id}'
            AND edge.entityType == 'ORGANIZATION'
            RETURN PARSE_IDENTIFIER(edge._to).key
        """
        cursor = self.arango_service.db.aql.execute(query)
        org_id = next(cursor, None)
        user = await self.arango_service.get_document(user_id, CollectionNames.USERS.value)
        user_id = user.get('userId')

        if changes:
            for change in changes:
                try:
                    await self.change_handler.process_change(change, user_service, org_id, user_id)
                except Exception as e:
                    logger.error(f"Error processing change: {str(e)}")
                    continue

        if new_token and new_token != page_token['token']:
            await self.arango_service.store_page_token(
                channel_id=notification['channel_id'],
                resource_id=notification['resource_id'],
                user_email=user_email,
                token=new_token
            )
            logger.info(f"🚀 Updated token for user {user_email}")

    async def handle_downtime(self, org_id):
        """Handle downtime for individual users"""
        try:
            user = self.arango_service.get_users(org_id)
            tokens = await self.arango_service.get_page_token_db(
                user_email=user[0]['email']
            )
            success_count = 0

            for token in tokens:
                if not token or not token.get('token'):
                    continue

                if await self._process_user_changes(token['user_email'], {
                    'channel_id': token['channel_id'],
                    'resource_id': token['resource_id']
                }):
                    success_count += 1

            return success_count > 0

        except Exception as e:
            logger.error(f"Individual downtime handling failed: {str(e)}")
            return False


class EnterpriseDriveWebhookHandler(AbstractDriveWebhookHandler):
    """Handles webhooks for enterprise/organization-wide processing"""

    def __init__(self, config: ConfigurationService, drive_admin_service, arango_service, change_handler):
        super().__init__(config, arango_service, change_handler)
        self.drive_admin_service = drive_admin_service
        self.pending_notifications: Set[str] = set()

    async def process_notification(self, headers: Dict) -> bool:
        try:
            important_headers = await self._log_headers(headers)

            self.pending_notifications.add(json.dumps(important_headers))
            logger.info("Added to pending notifications. Current count: %s", len(
                self.pending_notifications))

            if self.scheduled_task and not self.scheduled_task.done():
                self.scheduled_task.cancel()

            self.scheduled_task = asyncio.create_task(
                self._delayed_process_notifications()
            )

            return True

        except Exception as e:
            logger.error(f"Error processing enterprise notification: {str(e)}")
            return False

    async def _delayed_process_notifications(self):
        """Process notifications for the entire organization"""
        try:
            coalesce_delay = WebhookConfig.COALESCEDELAY.value
            await asyncio.sleep(coalesce_delay)

            async with self.processing_lock:
                if not self.pending_notifications:
                    return

                # Group notifications by channel
                channel_notifications = {}
                for notification_json in self.pending_notifications:
                    notification = json.loads(notification_json)
                    channel_id = notification['channel_id']
                    if channel_id not in channel_notifications:
                        channel_notifications[channel_id] = notification

                logger.info("Processing notifications for %s channels",
                            len(channel_notifications))
                await self._process_enterprise_change(channel_notifications)

                self.pending_notifications.clear()
                logger.info("✅ Completed processing all channel notifications")

        except asyncio.CancelledError:
            logger.info("Processing delayed for enterprise")
        except Exception as e:
            logger.error(
                "Error processing enterprise notifications: %s", str(e))

    async def _process_enterprise_change(self, channel_notifications):
        """Process changes for an organizational unit"""
        for channel_id, notification in channel_notifications.items():
            try:
                logger.info("Processing changes for channel %s", channel_id)
                resource_id = notification['resource_id']
                page_token = await self.arango_service.get_page_token_db(
                    channel_id,
                    resource_id
                )

                if not page_token:
                    logger.error(
                        "No page token found for channel %s", channel_id)
                    continue
                user_service = await self.drive_admin_service.create_user_service(page_token['user_email'])

                changes, new_token = await user_service.get_changes(
                    page_token=page_token['token']
                )
                
                user_id = await self.arango_service.get_entity_id_by_email(page_token['user_email'])
                # Get org_id from belongsTo relation for this user
                query = f"""
                FOR edge IN belongsTo
                    FILTER edge._from == 'users/{user_id}'
                    AND edge.entityType == 'ORGANIZATION'
                    RETURN PARSE_IDENTIFIER(edge._to).key
                """
                cursor = self.arango_service.db.aql.execute(query)
                org_id = next(cursor, None)

                user = await self.arango_service.get_document(user_id, CollectionNames.USERS.value)
                user_id = user.get('userId')

                if changes:
                    logger.info("Processing %s changes for channel %s",
                                len(changes), channel_id)
                    for change in changes:
                        try:
                            await self.change_handler.process_change(change, user_service, org_id, user_id)
                        except Exception as e:
                            logger.error("Error processing change: %s", str(e))
                            continue

                    if new_token and new_token != page_token['token']:
                        await self.arango_service.store_page_token(
                            channel_id=channel_id,
                            resource_id=resource_id,
                            user_email=page_token['user_email'],
                            token=new_token
                        )
                        logger.info(
                            "✅ Updated token for channel %s", channel_id)

                else:
                    logger.info(
                        "ℹ️ No changes found for channel %s", channel_id)

            except Exception as e:
                logger.error("Error processing channel %s: %s",
                             channel_id, str(e))
                continue

    async def handle_downtime(self, org_id):
        """Handle downtime for the entire organization"""
        try:
            users = await self.arango_service.get_users(org_id)
            success_count = 0

            for user in users:
                try:
                    user_id = user['userId']
                    user_service = await self.drive_admin_service.create_user_service(user['email'])
                    page_token = await self.arango_service.get_page_token_db(
                        user['channel_id'],
                        user['resource_id']
                    )
                    changes = await user_service.get_changes(page_token=page_token['token'])
                    if changes:
                        for change in changes:
                            await self.change_handler.process_change(change, user_service, org_id, user_id)
                        success_count += 1
                except Exception as e:
                    logger.error("Error processing user %s: %s",
                                 user['email'], str(e))
                    continue

            return success_count > 0

        except Exception as e:
            logger.error("Enterprise downtime handling failed: %s", str(e))
            return False
