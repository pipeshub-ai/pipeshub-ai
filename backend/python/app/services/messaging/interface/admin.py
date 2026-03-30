from abc import ABC, abstractmethod
from typing import List, Optional


class IMessageAdmin(ABC):
    """Interface for message broker administration"""

    @abstractmethod
    async def ensure_topics_exist(self, topics: Optional[List[str]] = None) -> None:
        """Ensure required topics/streams exist"""
        pass

    @abstractmethod
    async def list_topics(self) -> List[str]:
        """List all topics/streams"""
        pass
