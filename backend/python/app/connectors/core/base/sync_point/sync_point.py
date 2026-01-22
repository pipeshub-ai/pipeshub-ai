import hashlib
import logging
import os
from enum import Enum
from typing import Any, Dict, List, Optional

from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.interfaces.sync_point.isync_point import ISyncPoint
from app.utils.encryption.encryption_service import EncryptionService


class SyncDataPointType(Enum):
    USERS = "users"
    GROUPS = "groups"
    RECORDS = "records"
    RECORD_GROUPS = "recordGroups"


def generate_record_sync_point_key(record_type: str, entity_name: str, entity_id: str) -> str:
    return f"{record_type}/{entity_name}/{entity_id}"

class SyncPoint(ISyncPoint):
    connector_id: str
    org_id: str
    data_store_provider: DataStoreProvider
    sync_data_point_type: SyncDataPointType
    encryption_service: Optional[EncryptionService]

    def _get_full_sync_point_key(self, sync_point_key: str) -> str:
        return f"{self.org_id}/{self.connector_id}/{self.sync_data_point_type.value}/{sync_point_key}"

    def __init__(self, connector_id: str, org_id: str, sync_data_point_type: SyncDataPointType, data_store_provider: DataStoreProvider) -> None:
        self.org_id = org_id
        self.data_store_provider = data_store_provider
        self.sync_data_point_type = sync_data_point_type
        self.connector_id = connector_id

        # Initialize encryption service for delta links
        secret_key = os.getenv("SECRET_KEY")
        if not secret_key:
            raise ValueError("SECRET_KEY environment variable is required for encrypting sensitive sync point data")

        hashed_key = hashlib.sha256(secret_key.encode()).digest()
        hex_key = hashed_key.hex()
        self.encryption_service = EncryptionService.get_instance(
            "aes-256-gcm", hex_key, logging.getLogger("sync_point")
        )

    def _encrypt_sensitive_fields(self, data: Dict[str, Any], fields_to_encrypt: List[str]) -> Dict[str, Any]:
        """Encrypt specified fields before storage."""
        if not fields_to_encrypt:
            return data

        encrypted_data = data.copy()

        for field in fields_to_encrypt:
            if field in encrypted_data and encrypted_data[field]:
                try:
                    encrypted_value = self.encryption_service.encrypt(encrypted_data[field])
                    encrypted_data[field] = encrypted_value
                    encrypted_data[f'{field}_encrypted'] = True
                except Exception as e:
                    logging.error(f"Failed to encrypt {field}: {e}", exc_info=True)

        return encrypted_data

    def _decrypt_sensitive_fields(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Decrypt fields marked as encrypted when reading from storage."""
        if not data:
            return data

        decrypted_data = data.copy()

        # Find all fields marked as encrypted
        encrypted_markers = [k for k in data if k.endswith('_encrypted') and data[k] is True]

        for marker in encrypted_markers:
            field = marker.replace('_encrypted', '')
            if field in decrypted_data:
                try:
                    decrypted_value = self.encryption_service.decrypt(decrypted_data[field])
                    decrypted_data[field] = decrypted_value
                    del decrypted_data[marker]
                except Exception as e:
                    logging.warning(f"Failed to decrypt {field}: {e}")

        return decrypted_data

    async def create_sync_point(self, sync_point_key: str, sync_point_data: Dict[str, Any], encrypt_fields: Optional[List[str]] = None) -> Dict[str, Any]:
        full_sync_point_key = self._get_full_sync_point_key(sync_point_key)

        # Encrypt specified fields before storage
        encrypted_data = self._encrypt_sensitive_fields(sync_point_data, encrypt_fields or [])

        document_data = {
            "orgId": self.org_id,
            "connectorId": self.connector_id,
            "syncPointKey": full_sync_point_key,
            "syncDataPointType": self.sync_data_point_type.value,
            **encrypted_data
        }

        async with self.data_store_provider.transaction() as tx_store:
            await tx_store.update_sync_point(full_sync_point_key, document_data)

        return document_data

    async def read_sync_point(self, sync_point_key: str) -> Dict[str, Any]:
        async with self.data_store_provider.transaction() as tx_store:
            full_sync_point_key = self._get_full_sync_point_key(sync_point_key)
            sync_point = await tx_store.get_sync_point(full_sync_point_key)

            if not sync_point:
                return {}

            # Return all fields except metadata fields
            metadata_fields = {'orgId', 'connectorId', 'syncPointKey', 'syncDataPointType', '_key', '_id', '_rev'}
            filtered_data = {k: v for k, v in sync_point.items() if k not in metadata_fields}

            # Decrypt sensitive fields before returning
            decrypted_data = self._decrypt_sensitive_fields(filtered_data)

            return decrypted_data

    async def update_sync_point(self, sync_point_key: str, sync_point_data: Dict[str, Any], encrypt_fields: Optional[List[str]] = None) -> Dict[str, Any]:
        return await self.create_sync_point(sync_point_key, sync_point_data, encrypt_fields)

    async def delete_sync_point(self, sync_point_key: str) -> Dict[str, Any]:
        full_sync_point_key = self._get_full_sync_point_key(sync_point_key)

        async with self.data_store_provider.transaction() as tx_store:
            await tx_store.delete_sync_point(full_sync_point_key)

        return {"status": "deleted", "key": full_sync_point_key}
