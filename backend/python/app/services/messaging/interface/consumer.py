from abc import ABC, abstractmethod
from typing import Optional

from app.services.messaging.config import MessageHandler


class IMessagingConsumer(ABC):
    """Interface for messaging consumers"""

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the messaging consumer"""
        pass

    @abstractmethod
    async def cleanup(self) -> None:
        """Clean up resources"""
        pass

    @abstractmethod
    async def start(
        self,
        message_handler: MessageHandler,
    ) -> None:
        """Start consuming messages with a handler"""
        pass

    @abstractmethod
    async def stop(self, message_handler: Optional[MessageHandler] = None) -> None:
        """Stop consuming messages"""
        pass

    @abstractmethod
    def is_running(self) -> bool:
        """Check if consumer is running"""
        pass
