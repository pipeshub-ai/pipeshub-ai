from abc import ABC, abstractmethod
from typing import Any


class ISyncPoint(ABC):
    @abstractmethod
    async def create_sync_point(
        self, sync_point_key: str, sync_point_data: dict[str, Any]
    ) -> dict[str, Any]:
        pass

    @abstractmethod
    async def update_sync_point(
        self, sync_point_key: str, sync_point_data: dict[str, Any]
    ) -> dict[str, Any]:
        pass

    @abstractmethod
    async def delete_sync_point(self, sync_point_key: str) -> dict[str, Any]:
        pass

    @abstractmethod
    async def read_sync_point(self, sync_point_key: str) -> dict[str, Any]:
        pass
