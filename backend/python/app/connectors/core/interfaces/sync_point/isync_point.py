from abc import ABC, abstractmethod
from typing import Any, Dict


class ISyncPoint(ABC):
    @abstractmethod
    async def create_sync_point(self, sync_point_key: str, sync_point_data: Dict[str, Any]) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def update_sync_point(self, sync_point_key: str, sync_point_data: Dict[str, Any]) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def delete_sync_point(self, sync_point_key: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def read_sync_point(self, sync_point_key: str) -> Dict[str, Any]:
        pass
