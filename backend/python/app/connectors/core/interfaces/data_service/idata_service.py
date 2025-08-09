from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class IDataService(ABC):
    """Base interface for data operations"""

    @abstractmethod
    async def list_items(self, path: str = "/", recursive: bool = True) -> List[Dict[str, Any]]:
        """List items from the service"""
        pass

    @abstractmethod
    async def get_item_metadata(self, item_id: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a specific item"""
        pass

    @abstractmethod
    async def get_item_content(self, item_id: str) -> Optional[bytes]:
        """Get content for a specific item"""
        pass

    @abstractmethod
    async def search_items(self, query: str, filters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Search for items"""
        pass

    @abstractmethod
    async def get_item_permissions(self, item_id: str) -> List[Dict[str, Any]]:
        """Get permissions for a specific item"""
        pass


class IDataProcessor(ABC):
    """Base interface for data processing"""

    @abstractmethod
    async def process_item(self, item_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single item"""
        pass

    @abstractmethod
    async def process_batch(self, items_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process multiple items"""
        pass

    @abstractmethod
    def validate_item(self, item_data: Dict[str, Any]) -> bool:
        """Validate item data"""
        pass
