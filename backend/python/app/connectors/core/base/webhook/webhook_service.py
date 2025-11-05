import logging
from abc import ABC
from typing import Any

from app.connectors.core.interfaces.webhook.iwebhook import IWebhookService


class BaseWebhookService(IWebhookService, ABC):
    """Base webhook service with common functionality"""

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger

    async def create_webhook(self, events: list[str], callback_url: str) -> dict[str, Any]:
        """Create a webhook subscription"""
        try:
            self.logger.info(f"Creating webhook for events: {events}")
            # This should be implemented by specific webhook services
            return {"webhook_id": "placeholder", "events": events, "callback_url": callback_url}
        except Exception as e:
            self.logger.error(f"Failed to create webhook: {e!s}")
            return {}

    async def delete_webhook(self, webhook_id: str) -> bool:
        """Delete a webhook subscription"""
        try:
            self.logger.info(f"Deleting webhook: {webhook_id}")
            # This should be implemented by specific webhook services
            return True
        except Exception as e:
            self.logger.error(f"Failed to delete webhook: {e!s}")
            return False

    async def list_webhooks(self) -> list[dict[str, Any]]:
        """List all webhook subscriptions"""
        try:
            self.logger.info("Listing webhooks")
            # This should be implemented by specific webhook services
            return []
        except Exception as e:
            self.logger.error(f"Failed to list webhooks: {e!s}")
            return []

    async def process_webhook_event(self, event_data: dict[str, Any]) -> bool:
        """Process incoming webhook event"""
        try:
            self.logger.info(f"Processing webhook event: {event_data.get('type', 'unknown')}")
            # This should be implemented by specific webhook services
            return True
        except Exception as e:
            self.logger.error(f"Failed to process webhook event: {e!s}")
            return False
