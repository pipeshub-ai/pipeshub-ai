from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, Dict, Optional


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
        message_handler: Callable[[Dict[str, Any]], Awaitable[bool]]
    ) -> None:
        """Start consuming messages with a handler"""
        pass

    @abstractmethod
    async def stop(self, message_handler: Optional[Callable[[Dict[str, Any]], Awaitable[bool]]] = None) -> None:
        """Stop consuming messages"""
        pass

    @abstractmethod
    def is_running(self) -> bool:
        """Check if consumer is running"""
        pass
