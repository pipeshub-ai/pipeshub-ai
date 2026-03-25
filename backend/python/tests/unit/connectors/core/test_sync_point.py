"""Unit tests for app.connectors.core.base.sync_point.sync_point.

Covers:
- SyncDataPointType enum
- generate_record_sync_point_key helper
- SyncPoint.__init__: encryption service setup
- SyncPoint._get_full_sync_point_key
- SyncPoint._encrypt_sensitive_fields / _decrypt_sensitive_fields
- SyncPoint.create_sync_point
- SyncPoint.read_sync_point
- SyncPoint.update_sync_point
- SyncPoint.delete_sync_point
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
    generate_record_sync_point_key,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sync_point(
    connector_id="conn-1",
    org_id="org-1",
    sync_data_point_type=SyncDataPointType.RECORDS,
    data_store_provider=None,
):
    """Create a SyncPoint with mocked dependencies."""
    if data_store_provider is None:
        data_store_provider = MagicMock()

    with patch.dict("os.environ", {"SECRET_KEY": "test-secret-key-12345"}):
        sp = SyncPoint(
            connector_id=connector_id,
            org_id=org_id,
            sync_data_point_type=sync_data_point_type,
            data_store_provider=data_store_provider,
        )
    return sp


def _make_data_store_provider():
    """Create a mock data store provider with transaction support."""
    provider = MagicMock()
    tx_store = AsyncMock()
    ctx_manager = AsyncMock()
    ctx_manager.__aenter__ = AsyncMock(return_value=tx_store)
    ctx_manager.__aexit__ = AsyncMock(return_value=False)
    provider.transaction.return_value = ctx_manager
    return provider, tx_store


# ===========================================================================
# SyncDataPointType enum
# ===========================================================================


class TestSyncDataPointType:
    def test_values(self):
        assert SyncDataPointType.USERS.value == "users"
        assert SyncDataPointType.GROUPS.value == "groups"
        assert SyncDataPointType.RECORDS.value == "records"
        assert SyncDataPointType.RECORD_GROUPS.value == "recordGroups"


# ===========================================================================
# generate_record_sync_point_key
# ===========================================================================


class TestGenerateRecordSyncPointKey:
    def test_generates_correct_key(self):
        result = generate_record_sync_point_key("FILE", "doc", "123")
        assert result == "FILE/doc/123"

    def test_empty_values(self):
        result = generate_record_sync_point_key("", "", "")
        assert result == "//"

    def test_various_types(self):
        result = generate_record_sync_point_key("EMAIL", "inbox", "msg-42")
        assert result == "EMAIL/inbox/msg-42"


# ===========================================================================
# SyncPoint.__init__
# ===========================================================================


class TestSyncPointInit:
    def test_attributes_set(self):
        sp = _make_sync_point()
        assert sp.connector_id == "conn-1"
        assert sp.org_id == "org-1"
        assert sp.sync_data_point_type == SyncDataPointType.RECORDS
        assert sp.encryption_service is not None

    def test_missing_secret_key_raises(self):
        """Missing SECRET_KEY raises ValueError."""
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="SECRET_KEY"):
                SyncPoint(
                    connector_id="c1",
                    org_id="o1",
                    sync_data_point_type=SyncDataPointType.RECORDS,
                    data_store_provider=MagicMock(),
                )


# ===========================================================================
# SyncPoint._get_full_sync_point_key
# ===========================================================================


class TestGetFullSyncPointKey:
    def test_builds_correct_key(self):
        sp = _make_sync_point(connector_id="c1", org_id="o1")
        result = sp._get_full_sync_point_key("my_key")
        assert result == "o1/c1/records/my_key"

    def test_users_type(self):
        sp = _make_sync_point(sync_data_point_type=SyncDataPointType.USERS)
        result = sp._get_full_sync_point_key("key")
        assert "/users/" in result

    def test_groups_type(self):
        sp = _make_sync_point(sync_data_point_type=SyncDataPointType.GROUPS)
        result = sp._get_full_sync_point_key("key")
        assert "/groups/" in result


# ===========================================================================
# SyncPoint._encrypt_sensitive_fields
# ===========================================================================


class TestEncryptSensitiveFields:
    def test_encrypts_specified_fields(self):
        sp = _make_sync_point()
        sp.encryption_service = MagicMock()
        sp.encryption_service.encrypt.return_value = "encrypted_value"

        data = {"deltaLink": "https://api.example.com/delta?token=abc", "other": "value"}
        result = sp._encrypt_sensitive_fields(data, ["deltaLink"])

        assert result["deltaLink"] == "encrypted_value"
        assert result["deltaLink_encrypted"] is True
        assert result["other"] == "value"

    def test_empty_fields_list(self):
        sp = _make_sync_point()
        data = {"key": "value"}
        result = sp._encrypt_sensitive_fields(data, [])
        assert result == data

    def test_field_not_in_data_skipped(self):
        sp = _make_sync_point()
        sp.encryption_service = MagicMock()
        data = {"key": "value"}
        result = sp._encrypt_sensitive_fields(data, ["nonexistent"])
        assert result == {"key": "value"}
        sp.encryption_service.encrypt.assert_not_called()

    def test_none_field_value_skipped(self):
        sp = _make_sync_point()
        sp.encryption_service = MagicMock()
        data = {"deltaLink": None}
        result = sp._encrypt_sensitive_fields(data, ["deltaLink"])
        assert result["deltaLink"] is None
        sp.encryption_service.encrypt.assert_not_called()

    def test_encryption_failure_logged(self):
        sp = _make_sync_point()
        sp.encryption_service = MagicMock()
        sp.encryption_service.encrypt.side_effect = Exception("encrypt failed")

        data = {"deltaLink": "value"}
        result = sp._encrypt_sensitive_fields(data, ["deltaLink"])
        # Field value remains unchanged on error
        assert result["deltaLink"] == "value"

    def test_data_is_copied(self):
        sp = _make_sync_point()
        sp.encryption_service = MagicMock()
        sp.encryption_service.encrypt.return_value = "enc"

        original = {"deltaLink": "orig"}
        result = sp._encrypt_sensitive_fields(original, ["deltaLink"])
        # Original should not be mutated
        assert original["deltaLink"] == "orig"
        assert result["deltaLink"] == "enc"


# ===========================================================================
# SyncPoint._decrypt_sensitive_fields
# ===========================================================================


class TestDecryptSensitiveFields:
    def test_decrypts_marked_fields(self):
        sp = _make_sync_point()
        sp.encryption_service = MagicMock()
        sp.encryption_service.decrypt.return_value = "decrypted_value"

        data = {"deltaLink": "encrypted_blob", "deltaLink_encrypted": True}
        result = sp._decrypt_sensitive_fields(data)

        assert result["deltaLink"] == "decrypted_value"
        assert "deltaLink_encrypted" not in result

    def test_no_encrypted_fields(self):
        sp = _make_sync_point()
        data = {"key": "value", "other": 42}
        result = sp._decrypt_sensitive_fields(data)
        assert result == data

    def test_empty_data(self):
        sp = _make_sync_point()
        result = sp._decrypt_sensitive_fields({})
        assert result == {}

    def test_none_data(self):
        sp = _make_sync_point()
        result = sp._decrypt_sensitive_fields(None)
        assert result is None

    def test_decryption_failure_logged(self):
        sp = _make_sync_point()
        sp.encryption_service = MagicMock()
        sp.encryption_service.decrypt.side_effect = Exception("decrypt failed")

        data = {"token": "encrypted", "token_encrypted": True}
        result = sp._decrypt_sensitive_fields(data)
        # Value remains encrypted on failure
        assert result["token"] == "encrypted"

    def test_encrypted_marker_false_skipped(self):
        sp = _make_sync_point()
        sp.encryption_service = MagicMock()
        data = {"token": "plain", "token_encrypted": False}
        result = sp._decrypt_sensitive_fields(data)
        # Marker is False so decryption is skipped
        assert result["token"] == "plain"
        sp.encryption_service.decrypt.assert_not_called()

    def test_data_is_copied(self):
        sp = _make_sync_point()
        sp.encryption_service = MagicMock()
        sp.encryption_service.decrypt.return_value = "dec"

        original = {"token": "enc", "token_encrypted": True}
        result = sp._decrypt_sensitive_fields(original)
        assert original["token"] == "enc"  # original unchanged


# ===========================================================================
# SyncPoint.create_sync_point
# ===========================================================================


class TestCreateSyncPoint:
    @pytest.mark.asyncio
    async def test_success(self):
        provider, tx_store = _make_data_store_provider()
        sp = _make_sync_point(data_store_provider=provider)

        result = await sp.create_sync_point("my_key", {"data": "value"})

        tx_store.update_sync_point.assert_awaited_once()
        assert result["orgId"] == "org-1"
        assert result["connectorId"] == "conn-1"
        assert "syncPointKey" in result
        assert result["data"] == "value"

    @pytest.mark.asyncio
    async def test_with_encryption(self):
        provider, tx_store = _make_data_store_provider()
        sp = _make_sync_point(data_store_provider=provider)
        sp.encryption_service = MagicMock()
        sp.encryption_service.encrypt.return_value = "encrypted"

        result = await sp.create_sync_point(
            "key", {"deltaLink": "sensitive"}, encrypt_fields=["deltaLink"]
        )

        assert result["deltaLink"] == "encrypted"
        assert result["deltaLink_encrypted"] is True

    @pytest.mark.asyncio
    async def test_without_encrypt_fields(self):
        provider, tx_store = _make_data_store_provider()
        sp = _make_sync_point(data_store_provider=provider)

        result = await sp.create_sync_point("key", {"plain": "data"})
        assert result["plain"] == "data"


# ===========================================================================
# SyncPoint.read_sync_point
# ===========================================================================


class TestReadSyncPoint:
    @pytest.mark.asyncio
    async def test_found(self):
        provider, tx_store = _make_data_store_provider()
        tx_store.get_sync_point.return_value = {
            "orgId": "o1",
            "connectorId": "c1",
            "syncPointKey": "k1",
            "syncDataPointType": "records",
            "_key": "x",
            "_id": "y",
            "_rev": "z",
            "data": "value",
        }
        sp = _make_sync_point(data_store_provider=provider)

        result = await sp.read_sync_point("my_key")

        assert "data" in result
        assert "orgId" not in result
        assert "_key" not in result

    @pytest.mark.asyncio
    async def test_not_found(self):
        provider, tx_store = _make_data_store_provider()
        tx_store.get_sync_point.return_value = None
        sp = _make_sync_point(data_store_provider=provider)

        result = await sp.read_sync_point("missing_key")
        assert result == {}

    @pytest.mark.asyncio
    async def test_with_encrypted_fields(self):
        provider, tx_store = _make_data_store_provider()
        tx_store.get_sync_point.return_value = {
            "orgId": "o1",
            "connectorId": "c1",
            "syncPointKey": "k1",
            "syncDataPointType": "records",
            "_key": "x",
            "token": "encrypted_blob",
            "token_encrypted": True,
        }
        sp = _make_sync_point(data_store_provider=provider)
        sp.encryption_service = MagicMock()
        sp.encryption_service.decrypt.return_value = "decrypted_token"

        result = await sp.read_sync_point("key")

        assert result["token"] == "decrypted_token"
        assert "token_encrypted" not in result


# ===========================================================================
# SyncPoint.update_sync_point
# ===========================================================================


class TestUpdateSyncPoint:
    @pytest.mark.asyncio
    async def test_delegates_to_create(self):
        provider, tx_store = _make_data_store_provider()
        sp = _make_sync_point(data_store_provider=provider)

        result = await sp.update_sync_point("key", {"data": "updated"})

        tx_store.update_sync_point.assert_awaited_once()
        assert result["data"] == "updated"

    @pytest.mark.asyncio
    async def test_with_encrypt_fields(self):
        provider, tx_store = _make_data_store_provider()
        sp = _make_sync_point(data_store_provider=provider)
        sp.encryption_service = MagicMock()
        sp.encryption_service.encrypt.return_value = "enc"

        result = await sp.update_sync_point(
            "key", {"secret": "plain"}, encrypt_fields=["secret"]
        )

        assert result["secret"] == "enc"


# ===========================================================================
# SyncPoint.delete_sync_point
# ===========================================================================


class TestDeleteSyncPoint:
    @pytest.mark.asyncio
    async def test_success(self):
        provider, tx_store = _make_data_store_provider()
        sp = _make_sync_point(data_store_provider=provider)

        result = await sp.delete_sync_point("my_key")

        tx_store.delete_sync_point.assert_awaited_once()
        assert result["status"] == "deleted"
        assert "key" in result
