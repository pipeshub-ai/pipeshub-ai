from abc import ABC, abstractmethod
from typing import Any


class IWebhookService(ABC):
    """Base interface for webhook operations"""

    @abstractmethod
    async def create_webhook(
        self, events: list[str], callback_url: str
    ) -> dict[str, Any]:
        """Create a webhook subscription"""

    @abstractmethod
    async def delete_webhook(self, webhook_id: str) -> bool:
        """Delete a webhook subscription"""

    @abstractmethod
    async def list_webhooks(self) -> list[dict[str, Any]]:
        """List all webhook subscriptions"""

    @abstractmethod
    async def process_webhook_event(self, event_data: dict[str, Any]) -> bool:
        """Process incoming webhook event"""
