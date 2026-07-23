from abc import ABC, abstractmethod
from typing import Optional

from pydantic import JsonValue


class IMessagingProducer(ABC):
    """Interface for messaging producers"""

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the messaging producer"""
        pass

    @abstractmethod
    async def cleanup(self) -> None:
        """Clean up resources"""
        pass

    @abstractmethod
    async def start(self) -> None:
        """Start the messaging producer"""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop the messaging producer"""
        pass

    @abstractmethod
    async def send_message(
        self,
        topic: str,
        message: dict[str, JsonValue],
        key: Optional[str] = None,
    ) -> bool:
        """Send a message to a topic"""
        pass

    async def send_messages(
        self,
        topic: str,
        messages: list[tuple[Optional[str], dict[str, JsonValue]]],
    ) -> list[bool]:
        """Publish many messages to one topic, returning per-message success in input order.

        Reports results rather than raising, because a batch can succeed partially and
        the caller usually needs to know exactly which messages were accepted before
        recording them as sent.

        This default sends one at a time; brokers that can pipeline should override it.

        Args:
            topic: Destination topic.
            messages: (key, message) pairs. The key drives partitioning where supported.
        """
        results: list[bool] = []
        for key, message in messages:
            try:
                await self.send_message(topic, message, key=key)
                results.append(True)
            except Exception:
                results.append(False)
        return results

    @abstractmethod
    async def send_event(
        self,
        topic: str,
        event_type: str,
        payload: dict[str, JsonValue],
        key: Optional[str] = None,
    ) -> bool:
        """Send an event message with standardized format"""
        pass
