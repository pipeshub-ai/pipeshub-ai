from abc import ABC, abstractmethod


class IMessagingService(ABC):
    """Base interface for messaging services that can be implemented by different brokers"""

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the messaging service"""
        pass

    @abstractmethod
    async def __aenter__(self) -> "IMessagingService":
        """Async context manager entry"""
        pass

    @abstractmethod
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit"""
        pass
