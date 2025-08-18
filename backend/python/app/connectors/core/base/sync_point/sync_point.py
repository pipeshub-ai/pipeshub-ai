from enum import Enum
from typing import Any, Dict

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.interfaces.sync_point.isync_point import ISyncPoint
from app.connectors.services.base_arango_service import BaseArangoService


class SyncDataPointType(Enum):
    USERS = "users"
    USER_GROUPS = "userGroups"
    RECORDS = "records"
    RECORD_GROUPS = "recordGroups"


def generate_record_sync_point_key(record_type: str, entity_name: str, entity_id: str) -> str:
    return f"{record_type}/{entity_name}/{entity_id}"


class SyncPoint(ISyncPoint):
    connector_name: str
    org_id: str
    arango_service: BaseArangoService


    def __init__(self, connector_name: str, org_id: str, sync_data_point_type: SyncDataPointType, arango_service: BaseArangoService) -> None:
        self.connector_name = connector_name
        self.org_id = org_id
        self.arango_service = arango_service
        self.sync_data_point_type = sync_data_point_type

    async def create_sync_point(self, sync_point_key: str, sync_point_data: Dict[str, Any]) -> Dict[str, Any]:
        full_sync_point_key = f"{self.org_id}/{self.connector_name}/{self.sync_data_point_type.value}/{sync_point_key}"
        sync_point_data = {
            "orgId": self.org_id,
            "connectorName": self.connector_name,
            "syncPointKey": full_sync_point_key,
            "syncPointData": sync_point_data,
            "syncDataPointType": self.sync_data_point_type.value
        }

        await self.arango_service.upsert_sync_point_node(full_sync_point_key, sync_point_data, CollectionNames.SYNC_POINTS.value)

        return sync_point_data

    async def read_sync_point(self, sync_point_key: str) -> Dict[str, Any]:
        full_sync_point_key = f"{self.org_id}/{self.connector_name}/{self.sync_data_point_type.value}/{sync_point_key}"
        sync_point = await self.arango_service.get_sync_point_node(full_sync_point_key, CollectionNames.SYNC_POINTS.value)

        return sync_point.get('syncPointData', {}) if sync_point else {}

    async def update_sync_point(self, sync_point_key: str, sync_point_data: Dict[str, Any]) -> Dict[str, Any]:
        full_sync_point_key = f"{self.org_id}/{self.connector_name}/{self.sync_data_point_type.value}/{sync_point_key}"
        sync_point_data = {
            "orgId": self.org_id,
            "connectorName": self.connector_name,
            "syncPointKey": full_sync_point_key,
            "syncPointData": sync_point_data,
            "syncDataPointType": self.sync_data_point_type.value
        }

        await self.arango_service.upsert_sync_point_node(full_sync_point_key, sync_point_data, CollectionNames.SYNC_POINTS.value)

        return sync_point_data

    async def delete_sync_point(self, sync_point_key: str) -> Dict[str, Any]:
        full_sync_point_key = f"{self.org_id}/{self.connector_name}/{self.sync_data_point_type.value}/{sync_point_key}"

        await self.arango_service.remove_sync_point_node(full_sync_point_key, CollectionNames.SYNC_POINTS.value)

        return {"status": "deleted", "key": full_sync_point_key}
