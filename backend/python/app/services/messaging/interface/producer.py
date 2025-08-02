from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


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
        message: Dict[str, Any],
        key: Optional[str] = None
    ) -> bool:
        """Send a message to a topic"""
        pass

    @abstractmethod
    async def send_event(
        self,
        topic: str,
        event_type: str,
        payload: Dict[str, Any],
        key: Optional[str] = None
    ) -> bool:
        """Send an event message with standardized format"""
        pass
