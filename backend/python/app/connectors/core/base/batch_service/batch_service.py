import logging
from abc import ABC
from typing import Any, Dict, List

from app.connectors.core.interfaces.batch_service.ibatch_service import (
    IBatchOperationsService,
)
from app.connectors.core.interfaces.data_service.idata_service import IDataService


class BaseBatchOperationsService(IBatchOperationsService, ABC):
    """Base batch operations service with common functionality"""

    def __init__(self, logger: logging.Logger, data_service: IDataService) -> None:
        self.logger = logger
        self.data_service = data_service

    async def batch_get_metadata(self, item_ids: List[str]) -> List[Dict[str, Any]]:
        """Get metadata for multiple items in batch"""
        try:
            self.logger.info(f"Getting metadata for {len(item_ids)} items in batch")
            results = []
            for item_id in item_ids:
                metadata = await self.data_service.get_item_metadata(item_id)
                if metadata:
                    results.append(metadata)
            return results
        except Exception as e:
            self.logger.error(f"Failed to batch get metadata: {str(e)}")
            return []

    async def batch_get_permissions(self, item_ids: List[str]) -> List[Dict[str, Any]]:
        """Get permissions for multiple items in batch"""
        try:
            self.logger.info(f"Getting permissions for {len(item_ids)} items in batch")
            results = []
            for item_id in item_ids:
                permissions = await self.data_service.get_item_permissions(item_id)
                if permissions:
                    results.extend(permissions)
            return results
        except Exception as e:
            self.logger.error(f"Failed to batch get permissions: {str(e)}")
            return []

    async def batch_download(self, item_ids: List[str]) -> List[Dict[str, Any]]:
        """Download multiple items in batch"""
        try:
            self.logger.info(f"Downloading {len(item_ids)} items in batch")
            results = []
            for item_id in item_ids:
                content = await self.data_service.get_item_content(item_id)
                if content:
                    results.append({"item_id": item_id, "content": content})
            return results
        except Exception as e:
            self.logger.error(f"Failed to batch download: {str(e)}")
            return []
