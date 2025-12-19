from abc import ABC, abstractmethod
from typing import Any


class IBatchOperationsService(ABC):
    """Base interface for batch operations"""

    @abstractmethod
    async def batch_get_metadata(self, item_ids: list[str]) -> list[dict[str, Any]]:
        """Get metadata for multiple items in batch"""

    @abstractmethod
    async def batch_get_permissions(self, item_ids: list[str]) -> list[dict[str, Any]]:
        """Get permissions for multiple items in batch"""

    @abstractmethod
    async def batch_download(self, item_ids: list[str]) -> list[dict[str, Any]]:
        """Download multiple items in batch"""
