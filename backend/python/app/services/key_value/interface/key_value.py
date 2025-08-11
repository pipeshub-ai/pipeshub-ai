from abc import ABC, abstractmethod
from typing import Dict, Optional


class IKeyValueService(ABC):
    @abstractmethod
    async def connect(self) -> bool:
        pass

    @abstractmethod
    async def disconnect(self) -> bool:
        pass

    @abstractmethod
    async def set(self, key: str, value: str, expire: int = 86400) -> bool:
        pass

    @abstractmethod
    async def get(self, key: str) -> Optional[str]:
        pass

    @abstractmethod
    async def delete(self, key: str) -> bool:
        pass

    @abstractmethod
    async def store_progress(self, progress: Dict) -> bool:
        pass

    @abstractmethod
    async def get_progress(self) -> Optional[Dict]:
        pass
