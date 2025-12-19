import logging
from abc import ABC
from datetime import datetime
from typing import Any

from app.connectors.core.interfaces.event_service.ievent_service import IEventService


class BaseEventService(IEventService, ABC):
    """Base event service with common functionality"""

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self._subscribers: dict[str, list] = {}

    async def publish_event(self, event_type: str, event_data: dict[str, Any]) -> bool:
        """Publish an event"""
        try:
            self.logger.debug(f"Publishing event: {event_type}")
            if event_type in self._subscribers:
                for callback in self._subscribers[event_type]:
                    try:
                        await callback(event_data)
                    except Exception as e:
                        self.logger.error(f"Error in event callback: {e!s}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to publish event: {e!s}")
            return False

    async def subscribe_to_events(self, event_types: list[str], callback) -> str:
        """Subscribe to events"""
        try:
            subscription_id = f"sub_{datetime.now().timestamp()}"
            for event_type in event_types:
                if event_type not in self._subscribers:
                    self._subscribers[event_type] = []
                self._subscribers[event_type].append(callback)
            self.logger.info(f"Subscribed to events: {event_types}")
            return subscription_id
        except Exception as e:
            self.logger.error(f"Failed to subscribe to events: {e!s}")
            return ""

    async def unsubscribe_from_events(self, subscription_id: str) -> bool:
        """Unsubscribe from events"""
        try:
            # This should be implemented by specific event services
            self.logger.info(f"Unsubscribed from events: {subscription_id}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to unsubscribe from events: {e!s}")
            return False

    async def process_event(self, event_type: str, payload: dict[str, Any]) -> bool:
        """Handle connector-specific events"""
        try:
            self.logger.info(f"Processing event: {event_type}")
            # This should be implemented by specific event services
            return True
        except Exception as e:
            self.logger.error(f"Failed to process event: {e!s}")
            return False
