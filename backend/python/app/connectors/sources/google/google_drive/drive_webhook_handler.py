import asyncio
import json
from abc import ABC, abstractmethod
from typing import Dict, Set

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.config.constants.service import WebhookConfig
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class AbstractDriveWebhookHandler(ABC):
    def __init__(
        self, logger, config_service: ConfigurationService, arango_service, change_handler
    ) -> None:
        self.logger = logger
        self.config_service = config_service
        self.arango_service = arango_service
        self.change_handler = change_handler

        # Common state management
        self.processing_lock = asyncio.Lock()
        self.scheduled_task = None
        self.last_notification_time = None
        self.processed_message_numbers = set()

    async def _log_headers(self, headers: Dict) -> Dict:
        """Log webhook headers and return important headers"""

        important_headers = {
            "resource_id": headers.get("x-goog-resource-id"),
            "changed_id": headers.get("x-goog-changed"),
            "resource_state": headers.get("x-goog-resource-state"),
            "channel_id": headers.get("x-goog-channel-id"),
            "message_id": headers.get("x-goog-message-number"),
            "timestamp": get_epoch_timestamp_in_ms(),
        }

        self.logger.info(f"🚀 Important headers: {important_headers}")

        message_number = important_headers.get("message_id")
        if message_number and message_number in self.processed_message_numbers:
            self.logger.info(f"Skipping duplicate message number: {message_number}")
            return None
        if message_number:
            self.processed_message_numbers.add(message_number)
        return important_headers

    @abstractmethod
    async def process_notification(self, headers: Dict) -> bool:
        """Process incoming webhook notification"""
        pass

    @abstractmethod
    async def _delayed_process_notifications(self) -> None:
        """Process coalesced notifications after delay"""
        pass


class IndividualDriveWebhookHandler(AbstractDriveWebhookHandler):
    """Handles webhooks for individual user accounts"""

    def __init__(
        self,
        logger,
        config_service: ConfigurationService,
        drive_user_service,
        arango_service,
        change_handler,
    ) -> None:
        super().__init__(logger, config_service, arango_service, change_handler)
        self.logger = logger
        self.drive_user_service = drive_user_service
        self.arango_service = arango_service
        self.change_handler = change_handler
        self.pending_notifications: Set[str] = set()

    async def process_notification(self, headers: Dict) -> bool:
        try:
            important_headers = await self._log_headers(headers)
            if not important_headers:
                return True

            channel_id = headers.get("x-goog-channel-id")
            if not channel_id:
                self.logger.error("No channel ID in notification")
                return False

            # Get token  for this channel
            token = await self.arango_service.get_page_token_db(channel_id=channel_id)
            if not token:
                self.logger.info(f"No user found for channel {channel_id}")
                return False

            user_email = token["userEmail"]

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
            self.logger.error(f"Error processing individual notification: {str(e)}")
            return False

    async def _delayed_process_notifications(self, user_email: str = None) -> None:
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
                self.logger.info(
                    f"🚀 Cleared processed notifications for user {user_email}"
                )

        except asyncio.CancelledError:
            self.logger.info(f"Processing delayed for user {user_email}")
        except Exception as e:
            self.logger.error(
                "Error processing notifications for user %s: %s", user_email, str(e)
            )

    async def _process_user_changes(self, user_email: str, notification: Dict) -> None:
        """Process changes for a single user"""
        user_service = self.drive_user_service

        page_token = await self.arango_service.get_page_token_db(
            notification["channel_id"], notification["resource_id"]
        )

        if not page_token:
            return

        changes, new_token = await user_service.get_changes(
            page_token=page_token["token"]
        )
        user_id = await self.arango_service.get_entity_id_by_email(user_email)

        user = await self.arango_service.get_document(
            user_id, CollectionNames.USERS.value
        )
        org_id = user.get("orgId")
        user_id = user.get("userId")

        if changes:
            for change in changes:
                try:
                    await self.change_handler.process_change(
                        change, user_service, org_id, user_id
                    )
                except Exception as e:
                    self.logger.error(f"Error processing change: {str(e)}")
                    continue

        if new_token and new_token != page_token["token"]:
            await self.arango_service.store_page_token(
                channel_id=notification["channel_id"],
                resource_id=notification["resource_id"],
                user_email=user_email,
                token=new_token,
                expiration=page_token["expiration"],
            )
            self.logger.info(f"🚀 Updated token for user {user_email}")


class EnterpriseDriveWebhookHandler(AbstractDriveWebhookHandler):
    """Handles webhooks for enterprise/organization-wide processing"""

    def __init__(
        self,
        logger,
        config_service: ConfigurationService,
        drive_admin_service,
        arango_service,
        change_handler,
    ) -> None:
        super().__init__(logger, config_service, arango_service, change_handler)
        self.logger = logger
        self.drive_admin_service = drive_admin_service
        self.pending_notifications: Set[str] = set()

    async def process_notification(self, headers: Dict) -> bool:
        try:
            important_headers = await self._log_headers(headers)
            if not important_headers:
                return True

            self.pending_notifications.add(json.dumps(important_headers))
            self.logger.info(
                "Added to pending notifications. Current count: %s",
                len(self.pending_notifications),
            )

            if self.scheduled_task and not self.scheduled_task.done():
                self.scheduled_task.cancel()

            self.scheduled_task = asyncio.create_task(
                self._delayed_process_notifications()
            )

            return True

        except Exception as e:
            self.logger.error(f"Error processing enterprise notification: {str(e)}")
            return False

    async def _delayed_process_notifications(self) -> None:
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
                    channel_id = notification["channel_id"]
                    if channel_id not in channel_notifications:
                        channel_notifications[channel_id] = notification

                self.logger.info(
                    "Processing notifications for %s channels",
                    len(channel_notifications),
                )
                await self._process_enterprise_change(channel_notifications)

                self.pending_notifications.clear()
                self.logger.info("✅ Completed processing all channel notifications")

        except asyncio.CancelledError:
            self.logger.info("Processing delayed for enterprise")
        except Exception as e:
            self.logger.error("Error processing enterprise notifications: %s", str(e))

    async def _process_enterprise_change(self, channel_notifications) -> None:
        """Process changes for an organizational unit"""
        for channel_id, notification in channel_notifications.items():
            try:
                self.logger.info("Processing changes for channel %s", channel_id)
                resource_id = notification["resource_id"]
                page_token = await self.arango_service.get_page_token_db(
                    channel_id, resource_id
                )

                if not page_token:
                    continue
                user_service = await self.drive_admin_service.create_drive_user_service(
                    page_token["userEmail"]
                )

                changes, new_token = await user_service.get_changes(
                    page_token=page_token["token"]
                )

                user_id = await self.arango_service.get_entity_id_by_email(
                    page_token["userEmail"]
                )
                # Get org_id from belongsTo relation for this user
                query = f"""
                FOR edge IN belongsTo
                    FILTER edge._from == 'users/{user_id}'
                    AND edge.entityType == 'ORGANIZATION'
                    RETURN PARSE_IDENTIFIER(edge._to).key
                """
                cursor = self.arango_service.db.aql.execute(query)
                org_id = next(cursor, None)

                user = await self.arango_service.get_document(
                    user_id, CollectionNames.USERS.value
                )
                user_id = user.get("userId")

                if changes:
                    self.logger.info(
                        "Processing %s changes for channel %s", len(changes), channel_id
                    )
                    for change in changes:
                        try:
                            await self.change_handler.process_change(
                                change, user_service, org_id, user_id
                            )
                        except Exception as e:
                            self.logger.error("Error processing change: %s", str(e))
                            continue

                    if new_token and new_token != page_token["token"]:
                        await self.arango_service.store_page_token(
                            channel_id=channel_id,
                            resource_id=resource_id,
                            user_email=page_token["userEmail"],
                            token=new_token,
                            expiration=page_token["expiration"],
                        )
                        self.logger.info("✅ Updated token for channel %s", channel_id)

                else:
                    self.logger.info("ℹ️ No changes found for channel %s", channel_id)

            except Exception as e:
                self.logger.error("Error processing channel %s: %s", channel_id, str(e))
                continue
