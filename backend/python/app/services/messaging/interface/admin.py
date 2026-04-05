from abc import ABC, abstractmethod
from typing import Optional


class IMessageAdmin(ABC):
    """Interface for message broker administration"""

    @abstractmethod
    async def ensure_topics_exist(self, topics: Optional[list[str]] = None) -> None:
        """Ensure required topics/streams exist"""
        pass

    @abstractmethod
    async def list_topics(self) -> list[str]:
        """List all topics/streams"""
        pass
