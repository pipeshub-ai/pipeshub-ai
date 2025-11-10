from abc import ABC, abstractmethod
from typing import Any


class IDataService(ABC):
    """Base interface for data operations"""

    @abstractmethod
    async def list_items(
        self, path: str = "/", recursive: bool = True
    ) -> list[dict[str, Any]]:
        """List items from the service"""

    @abstractmethod
    async def get_item_metadata(self, item_id: str) -> dict[str, Any] | None:
        """Get metadata for a specific item"""

    @abstractmethod
    async def get_item_content(self, item_id: str) -> bytes | None:
        """Get content for a specific item"""

    @abstractmethod
    async def search_items(
        self, query: str, filters: dict[str, Any] = None
    ) -> list[dict[str, Any]]:
        """Search for items"""

    @abstractmethod
    async def get_item_permissions(self, item_id: str) -> list[dict[str, Any]]:
        """Get permissions for a specific item"""


class IDataProcessor(ABC):
    """Base interface for data processing"""

    @abstractmethod
    async def process_item(self, item_data: dict[str, Any]) -> dict[str, Any]:
        """Process a single item"""

    @abstractmethod
    async def process_batch(
        self, items_data: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Process multiple items"""

    @abstractmethod
    def validate_item(self, item_data: dict[str, Any]) -> bool:
        """Validate item data"""
