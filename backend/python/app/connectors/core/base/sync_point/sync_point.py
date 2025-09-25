from enum import Enum
from typing import Any, Dict

from app.config.constants.arangodb import Connectors
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.interfaces.sync_point.isync_point import ISyncPoint


class SyncDataPointType(Enum):
    USERS = "users"
    GROUPS = "groups"
    RECORDS = "records"
    RECORD_GROUPS = "recordGroups"


def generate_record_sync_point_key(record_type: str, entity_name: str, entity_id: str) -> str:
    return f"{record_type}/{entity_name}/{entity_id}"

class SyncPoint(ISyncPoint):
    connector_name: str
    org_id: str
    data_store_provider: DataStoreProvider
    sync_data_point_type: SyncDataPointType



    def _get_full_sync_point_key(self, sync_point_key: str) -> str:
        return f"{self.org_id}/{self.connector_name}/{self.sync_data_point_type.value}/{sync_point_key}"

    def __init__(self, connector_name: Connectors, org_id: str, sync_data_point_type: SyncDataPointType, data_store_provider: DataStoreProvider) -> None:
        self.connector_name = connector_name.value
        self.org_id = org_id
        self.data_store_provider = data_store_provider
        self.sync_data_point_type = sync_data_point_type

    async def create_sync_point(self, sync_point_key: str, sync_point_data: Dict[str, Any]) -> Dict[str, Any]:
        full_sync_point_key = self._get_full_sync_point_key(sync_point_key)
        document_data = {
            "orgId": self.org_id,
            "connectorName": self.connector_name,
            "syncPointKey": full_sync_point_key,
            "syncPointData": sync_point_data,
            "syncDataPointType": self.sync_data_point_type.value
        }

        async with self.data_store_provider.transaction() as tx_store:
            await tx_store.update_sync_point(full_sync_point_key, document_data)

        return document_data

    async def read_sync_point(self, sync_point_key: str) -> Dict[str, Any]:
        async with self.data_store_provider.transaction() as tx_store:
            full_sync_point_key = self._get_full_sync_point_key(sync_point_key)
            sync_point = await tx_store.get_sync_point(full_sync_point_key)

            return sync_point.get('syncPointData', {}) if sync_point else {}

    async def update_sync_point(self, sync_point_key: str, sync_point_data: Dict[str, Any]) -> Dict[str, Any]:
        return await self.create_sync_point(sync_point_key, sync_point_data)

    async def delete_sync_point(self, sync_point_key: str) -> Dict[str, Any]:
        full_sync_point_key = self._get_full_sync_point_key(sync_point_key)

        async with self.data_store_provider.transaction() as tx_store:
            await tx_store.delete_sync_point(full_sync_point_key)

        return {"status": "deleted", "key": full_sync_point_key}
