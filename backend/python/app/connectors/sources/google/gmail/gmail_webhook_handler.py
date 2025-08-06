import asyncio
import json
from abc import ABC, abstractmethod
from typing import Dict, Optional

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class AbstractGmailWebhookHandler(ABC):
    def __init__(
        self, logger, config_service: ConfigurationService, arango_service, change_handler
    ) -> None:
        self.config_service = config_service
        self.logger = logger
        self.arango_service = arango_service
        self.change_handler = change_handler
        self.processing_lock = asyncio.Lock()
        self.handler_type = self.__class__.__name__.replace("GmailWebhookHandler", "")

    async def _parse_pubsub_message(self, message) -> Optional[Dict]:
        """Parse Pub/Sub message from string to dict

        Args:
            message: Raw message string from Pub/Sub

        Returns:
            Optional[Dict]: Parsed message or None if invalid
        """
        try:
            if isinstance(message, str):
                message_dict = json.loads(message)
            else:
                message_dict = message

            self.logger.debug(
                f"{self.handler_type} webhook: Parsed message - {json.dumps(message_dict, indent=2)}"
            )
            return message_dict
        except json.JSONDecodeError as e:
            self.logger.error(
                f"{self.handler_type} webhook: Failed to parse message: {str(e)}"
            )
            return None
        except Exception as e:
            self.logger.error(
                f"{self.handler_type} webhook: Error processing message: {str(e)}"
            )
            return None

    async def _log_headers(self, headers: Dict) -> Dict:
        """Log webhook headers and return important headers"""
        try:
            timestamp = get_epoch_timestamp_in_ms()

            important_headers = {
                "resource_id": headers.get("x-goog-resource-id"),
                "changed_id": headers.get("x-goog-changed"),
                "resource_state": headers.get("x-goog-resource-state"),
                "channel_id": headers.get("x-goog-channel-id"),
                "message_number": headers.get("x-goog-message-number"),
                "timestamp": timestamp,
                "handler_type": self.handler_type,
            }

            self.logger.debug(
                "%s webhook: Processing headers - Resource ID: %s, State: %s",
                self.handler_type,
                important_headers["resource_id"],
                important_headers["resource_state"],
            )

            return important_headers

        except Exception as e:
            self.logger.error(
                "%s webhook: Error processing headers: %s",
                self.handler_type,
                str(e),
                exc_info=True,
            )
            return {
                "resource_state": headers.get("x-goog-resource-state", "unknown"),
                "timestamp": get_epoch_timestamp_in_ms(),
                "handler_type": self.handler_type,
            }

    async def process_notification(self, headers: Dict, message: str) -> bool:
        """Process incoming webhook notification

        Args:
            headers: Dictionary containing webhook headers
            message: Raw message string from Pub/Sub

        Returns:
            bool: True if processing successful, False otherwise
        """
        try:
            self.logger.debug(
                "%s webhook: Starting notification processing", self.handler_type
            )
            important_headers = await self._log_headers(headers)

            message_data = await self._parse_pubsub_message(message)
            if not message_data:
                self.logger.error(
                    "%s webhook: Invalid message format", self.handler_type
                )
                return False

            return await self._process_notification_data(
                important_headers, message_data
            )

        except Exception as e:
            self.logger.error(
                "%s webhook: Error processing notification: %s",
                self.handler_type,
                str(e),
                exc_info=True,
            )
            return False

    @abstractmethod
    async def _process_notification_data(
        self, headers: Dict, message_data: Dict
    ) -> bool:
        """Process parsed notification data

        Args:
            headers: Important headers from the request
            message_data: Parsed Pub/Sub message data

        Returns:
            bool: True if processing successful, False otherwise
        """
        pass


class IndividualGmailWebhookHandler(AbstractGmailWebhookHandler):
    """Handles webhooks for individual user accounts"""

    def __init__(
        self,
        logger,
        config_service: ConfigurationService,
        gmail_user_service,
        arango_service,
        change_handler,
    ) -> None:
        super().__init__(logger, config_service, arango_service, change_handler)
        self.gmail_user_service = gmail_user_service

    async def _process_notification_data(
        self, headers: Dict, message_data: Dict
    ) -> bool:
        """Process parsed notification data

        Args:
            headers: Important headers from the request
            message_data: Parsed Pub/Sub message data

        Returns:
            bool: True if processing successful, False otherwise
        """
        try:
            history_id = message_data.get("historyId")
            email_address = message_data.get("emailAddress")

            if not history_id or not email_address:
                self.logger.error(
                    f"{self.handler_type} webhook: Missing historyId or emailAddress in message_data"
                )
                return False

            self.logger.info(
                "%s webhook: Received notification for user %s",
                self.handler_type,
                email_address,
            )
            self.logger.debug(
                "%s webhook: Notification details - %s",
                self.handler_type,
                json.dumps(message_data, indent=2),
            )

            async with self.processing_lock:
                # For Gmail Pub/Sub notifications, we always handle as 'exists' state
                # since these are change notifications
                history_id = message_data["historyId"]
                email_address = message_data["emailAddress"]

                self.logger.info(
                    "%s webhook: Fetching changes for %s",
                    self.handler_type,
                    email_address,
                )
                user_service = self.gmail_user_service
                channel_history = await self.arango_service.get_channel_history_id(
                    email_address
                )
                if not channel_history:
                    self.logger.warning(
                        f"""⚠️ No historyId found for {
                                   email_address}"""
                    )
                    return False

                current_history_id = channel_history["historyId"]
                changes = await user_service.fetch_gmail_changes(
                    email_address, current_history_id
                )
                if changes:
                    await self.arango_service.store_channel_history_id(
                        changes["historyId"], channel_history["expiration"], email_address
                    )

                user_id = await self.arango_service.get_entity_id_by_email(
                    email_address
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

                if changes and isinstance(changes, dict) and changes.get("history"):
                    user = await self.arango_service.get_document(
                        user_id, CollectionNames.USERS.value
                    )

                    self.logger.info(
                        "%s webhook: Found %s changes to process",
                        self.handler_type,
                        len(changes),
                    )
                    await self.change_handler.process_changes(
                        user_service, changes, org_id, user
                    )
                else:
                    self.logger.info(
                        "%s webhook: No changes to process", self.handler_type
                    )

            return True

        except Exception as e:
            self.logger.error(
                "%s webhook: Error processing notification data: %s",
                self.handler_type,
                str(e),
                exc_info=True,
            )
            return False


class EnterpriseGmailWebhookHandler(AbstractGmailWebhookHandler):
    """Handles webhooks for enterprise/organization-wide processing"""

    def __init__(
        self,
        logger,
        config_service: ConfigurationService,
        gmail_admin_service,
        arango_service,
        change_handler,
    ) -> None:
        super().__init__(logger, config_service, arango_service, change_handler)
        self.gmail_admin_service = gmail_admin_service

    async def _process_notification_data(
        self, headers: Dict, message_data: Dict
    ) -> bool:
        """Process parsed notification data

        Args:
            headers: Important headers from the request
            message_data: Parsed Pub/Sub message data

        Returns:
            bool: True if processing successful, False otherwise
        """
        try:
            history_id = message_data.get("historyId")
            email_address = message_data.get("emailAddress")

            if not history_id or not email_address:
                self.logger.error(
                    f"{self.handler_type} webhook: Missing historyId or emailAddress in message_data"
                )
                return False

            self.logger.info(
                "%s webhook: Received notification for user %s",
                self.handler_type,
                email_address,
            )
            self.logger.debug(
                "%s webhook: Notification details - %s",
                self.handler_type,
                json.dumps(message_data, indent=2),
            )

            async with self.processing_lock:
                # For Gmail Pub/Sub notifications, we always handle as 'exists' state
                # since these are change notifications
                email_address = message_data["emailAddress"]

                self.logger.info(
                    "%s webhook: Fetching changes for %s",
                    self.handler_type,
                    email_address,
                )
                user_service = await self.gmail_admin_service.create_gmail_user_service(
                    email_address
                )

                if not user_service:
                    self.logger.error(
                        "%s webhook: Failed to create user service for %s",
                        self.handler_type,
                        email_address,
                    )
                    return

                channel_history = await self.arango_service.get_channel_history_id(
                    email_address
                )
                if not channel_history:
                    self.logger.warning(
                        f"""⚠️ No historyId found for {
                                   email_address}"""
                    )
                    return False

                self.logger.debug("channel_history: %s", channel_history)
                current_history_id = channel_history["historyId"]
                if not current_history_id:
                    self.logger.warning(
                        f"""⚠️ No historyId found for {
                                   email_address}"""
                    )
                    return False

                self.logger.debug("current_history_id: %s", current_history_id)
                changes = await user_service.fetch_gmail_changes(
                    email_address, current_history_id
                )
                if changes:
                    await self.arango_service.store_channel_history_id(
                        changes["historyId"], channel_history["expiration"], email_address
                    )

                user_id = await self.arango_service.get_entity_id_by_email(
                    email_address
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

                if changes and isinstance(changes, dict) and changes.get("history"):
                    self.logger.info(
                        "%s webhook: Found %s changes to process",
                        self.handler_type,
                        len(changes),
                    )
                    user = await self.arango_service.get_document(
                        user_id, CollectionNames.USERS.value
                    )

                    await self.change_handler.process_changes(
                        user_service, changes, org_id, user
                    )
                else:
                    self.logger.info(
                        "%s webhook: No changes to process", self.handler_type
                    )

            return True

        except Exception as e:
            self.logger.error(
                "%s webhook: Error processing notification data: %s",
                self.handler_type,
                str(e),
                exc_info=True,
            )
            return False
