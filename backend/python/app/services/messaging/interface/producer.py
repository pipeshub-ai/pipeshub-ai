from abc import ABC, abstractmethod
from typing import Any


class IMessagingProducer(ABC):
    """Interface for messaging producers"""

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the messaging producer"""

    @abstractmethod
    async def cleanup(self) -> None:
        """Clean up resources"""

    @abstractmethod
    async def start(self) -> None:
        """Start the messaging producer"""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the messaging producer"""

    @abstractmethod
    async def send_message(
        self,
        topic: str,
        message: dict[str, Any],
        key: str | None = None,
    ) -> bool:
        """Send a message to a topic"""

    @abstractmethod
    async def send_event(
        self,
        topic: str,
        event_type: str,
        payload: dict[str, Any],
        key: str | None = None,
    ) -> bool:
        """Send an event message with standardized format"""
