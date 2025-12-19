from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any


class IMessagingConsumer(ABC):
    """Interface for messaging consumers"""

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the messaging consumer"""

    @abstractmethod
    async def cleanup(self) -> None:
        """Clean up resources"""

    @abstractmethod
    async def start(
        self,
        message_handler: Callable[[dict[str, Any]], Awaitable[bool]],
    ) -> None:
        """Start consuming messages with a handler"""

    @abstractmethod
    async def stop(self, message_handler: Callable[[dict[str, Any]], Awaitable[bool]] | None = None) -> None:
        """Stop consuming messages"""

    @abstractmethod
    def is_running(self) -> bool:
        """Check if consumer is running"""
