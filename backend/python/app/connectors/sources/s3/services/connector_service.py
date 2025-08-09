"""Main connector service for S3"""

import logging
from typing import Any, Dict, Optional

from app.connectors.core.base.connector.connector_service import BaseConnectorService
from app.connectors.core.interfaces.connector.iconnector_config import ConnectorConfig
from app.connectors.core.interfaces.data_processor.idata_processor import IDataProcessor
from app.connectors.core.interfaces.data_service.idata_service import IDataService
from app.connectors.core.interfaces.error.ierror import IErrorHandlingService
from app.connectors.core.interfaces.event_service.ievent_service import IEventService
from app.connectors.core.interfaces.rate_limiter.irate_limiter import IRateLimiter
from app.connectors.core.interfaces.sync_service.isync_service import ISyncService
from app.connectors.core.interfaces.token_service.itoken_service import ITokenService
from app.connectors.core.interfaces.user_service.iuser_service import IUserService
from app.connectors.enums.enums import ConnectorType
from app.connectors.sources.s3.const.const import (
    AWS_S3_API_VERSION,
    OPERATION_GET_BUCKET_METADATA,
    OPERATION_GET_OBJECT_CONTENT,
    OPERATION_LIST_BUCKET_OBJECTS,
    OPERATION_LIST_BUCKETS,
    OPERATION_SEARCH_OBJECTS,
)


class S3ConnectorService(BaseConnectorService):
    """Main S3 connector service"""

    def __init__(
        self,
        logger: logging.Logger,
        connector_type: ConnectorType,
        config: ConnectorConfig,
        token_service: ITokenService,
        data_service: IDataService,
        data_processor: IDataProcessor,
        error_service: IErrorHandlingService,
        event_service: IEventService,
        sync_service: ISyncService,
        user_service: IUserService,
        rate_limiter: IRateLimiter,
    ) -> None:
        super().__init__(
            logger, connector_type, config, token_service,
            data_service, data_processor, error_service, event_service,
            rate_limiter, user_service, sync_service
        )

    async def test_connection(self) -> bool:
        """Test S3 connection using aioboto3"""
        try:
            self.logger.info("🧪 Testing S3 connection using aioboto3")

            if not self.token_service.is_connected():
                self.logger.error("❌ Token service not connected")
                return False

            # Test by listing S3 buckets using aioboto3
            session = self.token_service.get_service()
            if not session:
                self.logger.error("❌ No active AWS session")
                return False

            # Use aioboto3 session to create S3 client
            async with session.client('s3') as s3_client:
                try:
                    response = await s3_client.list_buckets()
                    bucket_count = len(response.get('Buckets', []))

                    self.logger.info("✅ S3 connection test successful using aioboto3")
                    self.logger.info(f"📦 Found {bucket_count} S3 buckets")
                    return True

                except Exception as e:
                    self.logger.error(f"❌ S3 connection test failed: {str(e)}")
                    return False

        except Exception as e:
            self.error_service.log_error(e, "test_connection", {
                "connector_type": self.connector_type.value
            })
            self.logger.error(f"❌ S3 connection test failed: {str(e)}")
            return False

    def get_service_info(self) -> Dict[str, Any]:
        """Get S3 service information"""
        base_info = super().get_service_info()

        # Add S3-specific information
        s3_info = {
            **base_info,
            "aws_region": self.token_service.get_region_name() if hasattr(self.token_service, 'get_region_name') else None,
            "api_version": AWS_S3_API_VERSION,
            "supported_operations": [
                OPERATION_LIST_BUCKETS,
                OPERATION_LIST_BUCKET_OBJECTS,
                OPERATION_GET_BUCKET_METADATA,
                OPERATION_GET_OBJECT_CONTENT,
                OPERATION_SEARCH_OBJECTS
            ],
        }

        return s3_info

    async def list_buckets(self) -> list:
        """List all S3 buckets"""
        try:
            return await self.data_service.list_items()
        except Exception as e:
            self.logger.error(f"❌ Failed to list S3 buckets: {str(e)}")
            return []

    async def list_bucket_objects(self, bucket_name: str, prefix: str = '', max_keys: int = 1000) -> list:
        """List objects in a specific S3 bucket"""
        try:
            if hasattr(self.data_service, 'list_bucket_objects'):
                return await self.data_service.list_bucket_objects(bucket_name, prefix, max_keys)
            else:
                self.logger.error("❌ list_bucket_objects method not available")
                return []
        except Exception as e:
            self.logger.error(f"❌ Failed to list bucket objects: {str(e)}")
            return []

    async def get_bucket_metadata(self, bucket_name: str) -> Dict[str, Any]:
        """Get metadata for a specific S3 bucket"""
        try:
            metadata = await self.data_service.get_item_metadata(bucket_name)
            return metadata or {}
        except Exception as e:
            self.logger.error(f"❌ Failed to get bucket metadata: {str(e)}")
            return {}

    async def get_object_content(self, bucket_name: str, object_key: str) -> Optional[bytes]:
        """Get content for a specific S3 object"""
        try:
            item_id = f"{bucket_name}:{object_key}"
            return await self.data_service.get_item_content(item_id)
        except Exception as e:
            self.logger.error(f"❌ Failed to get object content: {str(e)}")
            return None

    async def search_objects(self, query: str, bucket_name: str = None) -> list:
        """Search for S3 objects"""
        try:
            filters = {"bucket_name": bucket_name} if bucket_name else None
            return await self.data_service.search_items(query, filters)
        except Exception as e:
            self.logger.error(f"❌ Failed to search objects: {str(e)}")
            return []
