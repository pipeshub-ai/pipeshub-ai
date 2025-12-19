from abc import ABC, abstractmethod
from typing import Any


class IEventService(ABC):
    """Base interface for event handling"""

    @abstractmethod
    async def publish_event(self, event_type: str, event_data: dict[str, Any]) -> bool:
        """Publish an event"""

    @abstractmethod
    async def subscribe_to_events(self, event_types: list[str], callback) -> str:
        """Subscribe to events"""

    @abstractmethod
    async def unsubscribe_from_events(self, subscription_id: str) -> bool:
        """Unsubscribe from events"""

    @abstractmethod
    async def process_event(self, event_type: str, payload: dict[str, Any]) -> bool:
        """Handle connector-specific events"""
