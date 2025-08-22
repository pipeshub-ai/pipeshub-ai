import os
from abc import ABC, abstractmethod

from aiolimiter import AsyncLimiter
from azure.identity.aio import ClientSecretCredential
from msgraph import GraphServiceClient

from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
)
from app.connectors.services.base_arango_service import BaseArangoService
from app.connectors.sources.microsoft.common.msgraph_client import (
    MSGraphClient,
)


class BaseMSGraphStorageConnector(ABC):
    def __init__(self, logger, data_entities_processor: DataSourceEntitiesProcessor,
                 arango_service: BaseArangoService, connector_name: str) -> None:
        self.logger = logger
        self.data_entities_processor = data_entities_processor
        self.arango_service = arango_service
        self.connector_name = connector_name

        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_name=connector_name,
                org_id=data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                arango_service=arango_service
            )

        # Initialize MS Graph client
        credential = ClientSecretCredential(
            tenant_id=os.getenv("AZURE_TENANT_ID"),
            client_id=os.getenv("AZURE_CLIENT_ID"),
            client_secret=os.getenv("AZURE_CLIENT_SECRET"),
        )
        self.client = GraphServiceClient(credential, scopes=["https://graph.microsoft.com/.default"])
        self.msgraph_client = MSGraphClient(self.client, self.logger)

        # Initialize sync points
        self.drive_delta_sync_point = _create_sync_point(SyncDataPointType.RECORDS)
        self.user_sync_point = _create_sync_point(SyncDataPointType.USERS)
        self.user_group_sync_point = _create_sync_point(SyncDataPointType.USER_GROUPS)

        # Batch processing configuration
        self.batch_size = 100
        self.max_concurrent_batches = 3

        self.rate_limiter = AsyncLimiter(50, 1)  # 50 requests per second


    @abstractmethod
    async def run(self) -> None:
        pass

    @abstractmethod
    async def run_incremental_sync(self) -> None:
        pass
