import logging
from abc import ABC
from typing import Any, Dict, List, Optional

from app.connectors.core.interfaces.data_service.idata_service import IDataService
from app.connectors.core.interfaces.token_service.itoken_service import ITokenService


class BaseDataService(IDataService, ABC):
    """Base data service with common functionality"""

    def __init__(self, logger: logging.Logger, token_service: ITokenService) -> None:
        self.logger = logger
        self.token_service = token_service

    async def list_items(self, path: str = "/", recursive: bool = True) -> List[Dict[str, Any]]:
        """List items from the service"""
        try:
            # This should be implemented by specific data services
            self.logger.info(f"Listing items from path: {path}")
            return []
        except Exception as e:
            self.logger.error(f"Failed to list items: {str(e)}")
            return []

    async def get_item_metadata(self, item_id: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a specific item"""
        try:
            # This should be implemented by specific data services
            self.logger.info(f"Getting metadata for item: {item_id}")
            return None
        except Exception as e:
            self.logger.error(f"Failed to get item metadata: {str(e)}")
            return None

    async def get_item_content(self, item_id: str) -> Optional[bytes]:
        """Get content for a specific item"""
        try:
            # This should be implemented by specific data services
            self.logger.info(f"Getting content for item: {item_id}")
            return None
        except Exception as e:
            self.logger.error(f"Failed to get item content: {str(e)}")
            return None

    async def search_items(self, query: str, filters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Search for items"""
        try:
            # This should be implemented by specific data services
            self.logger.info(f"Searching items with query: {query}")
            return []
        except Exception as e:
            self.logger.error(f"Failed to search items: {str(e)}")
            return []

    async def get_item_permissions(self, item_id: str) -> List[Dict[str, Any]]:
        """Get permissions for a specific item"""
        try:
            # This should be implemented by specific data services
            self.logger.info(f"Getting permissions for item: {item_id}")
            return []
        except Exception as e:
            self.logger.error(f"Failed to get item permissions: {str(e)}")
            return []
