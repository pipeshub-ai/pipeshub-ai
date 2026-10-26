from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional

from app.services.messaging.config import MessageHandler

if TYPE_CHECKING:
    from app.services.messaging.lanes.backlog import LaneBacklog


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

    async def lane_backlog(self, topic: str) -> "LaneBacklog":
        """Per lane of ``topic``, when the oldest event this consumer's group
        has not finished with was published.

        One read of the broker per lane, never per message. Callable from any
        event loop. Raises when the broker cannot answer, and on consumers that
        do not report a backlog, so a caller must be ready to decide without it.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not report a lane backlog for {topic}"
        )
