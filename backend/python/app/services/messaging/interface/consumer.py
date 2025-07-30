from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Callable, Awaitable


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
        self
    ) -> None:
        """Start consuming messages with a handler"""
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """Stop consuming messages"""
        pass
    
    @abstractmethod
    def is_running(self) -> bool:
        """Check if consumer is running"""
        pass 