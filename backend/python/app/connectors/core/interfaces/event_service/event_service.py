from abc import ABC, abstractmethod
from typing import Any, Dict, List


class IEventService(ABC):
    """Base interface for event handling"""

    @abstractmethod
    async def publish_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Publish an event"""
        pass

    @abstractmethod
    async def subscribe_to_events(self, event_types: List[str], callback) -> str:
        """Subscribe to events"""
        pass

    @abstractmethod
    async def unsubscribe_from_events(self, subscription_id: str) -> bool:
        """Unsubscribe from events"""
        pass

    @abstractmethod
    async def process_event(self, event_type: str, payload: Dict[str, Any]) -> bool:
        """Handle connector-specific events"""
        pass
