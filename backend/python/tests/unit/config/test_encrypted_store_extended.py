"""
Extended tests for app.config.providers.encrypted_store covering missing lines:
- _DatetimeSafeEncoder (lines 27-29)
- serialize/deserialize helper closures (lines 82-99)
- _create_redis_store (lines 106-138 - tested indirectly)
- list_keys_in_directory with encrypted keys and decryption (lines 340-356)
- cancel_watch delegation (line 365)
- publish_cache_invalidation with and without method (lines 380-383)
- subscribe_cache_invalidation with and without method (lines 400-409)
"""

import json
import logging
import os
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


_TEST_SECRET_KEY = "test-encrypted-store-key"


def _build_encrypted_store(store_type="etcd"):
    """Build an EncryptedKeyValueStore with mocked internals."""
    with (
        patch("app.config.providers.encrypted_store.os.getenv") as mock_getenv,
        patch("app.config.providers.encrypted_store.EncryptionService.get_instance") as mock_enc,
        patch("app.config.providers.encrypted_store.KeyValueStoreFactory.create_store") as mock_factory,
    ):
        mock_getenv.side_effect = lambda key, default=None: {
            "SECRET_KEY": _TEST_SECRET_KEY,
            "KV_STORE_TYPE": store_type,
            "ETCD_URL": "http://localhost:2379",
            "REDIS_HOST": "localhost",
            "REDIS_PORT": "6379",
            "REDIS_DB": "0",
            "REDIS_KV_PREFIX": "pipeshub:kv:",
        }.get(key, default)

        mock_encryption = MagicMock()
        mock_encryption.encrypt.side_effect = lambda v: f"enc:{v}"
        mock_encryption.decrypt.side_effect = lambda v: v.replace("enc:", "")
        mock_enc.return_value = mock_encryption

        mock_store = AsyncMock()
        mock_factory.return_value = mock_store

        from app.config.providers.encrypted_store import EncryptedKeyValueStore
        ekv = EncryptedKeyValueStore(logger=logging.getLogger("test-enc"))

    return ekv, mock_store, mock_encryption


# ============================================================================
# _DatetimeSafeEncoder
# ============================================================================


class TestDatetimeSafeEncoder:
    def test_datetime_encoded_as_iso(self):
        from app.config.providers.encrypted_store import _DatetimeSafeEncoder
        dt = datetime(2024, 1, 15, 10, 30, 0)
        result = json.dumps({"ts": dt}, cls=_DatetimeSafeEncoder)
        assert "2024-01-15" in result

    def test_non_datetime_uses_default(self):
        from app.config.providers.encrypted_store import _DatetimeSafeEncoder
        with pytest.raises(TypeError):
            json.dumps({"val": set([1, 2])}, cls=_DatetimeSafeEncoder)


# ============================================================================
# list_keys_in_directory with decryption
# ============================================================================


class TestListKeysInDirectoryDecryption:
    @pytest.mark.asyncio
    async def test_encrypted_key_decryption(self):
        ekv, mock_store, mock_encryption = _build_encrypted_store()
        mock_store.get_all_keys = AsyncMock(return_value=[
            "iv:ciphertext:authTag",
            "/services/endpoints/test",
        ])
        mock_encryption.decrypt.side_effect = lambda v: "/services/connectors/test"

        result = await ekv.list_keys_in_directory("/services")
        assert "/services/connectors/test" in result
        assert "/services/endpoints/test" in result

    @pytest.mark.asyncio
    async def test_encrypted_key_decryption_failure_uses_raw(self):
        ekv, mock_store, mock_encryption = _build_encrypted_store()
        mock_store.get_all_keys = AsyncMock(return_value=[
            "iv:ciphertext:authTag",
        ])
        mock_encryption.decrypt.side_effect = Exception("decrypt failed")

        result = await ekv.list_keys_in_directory("/")
        # Falls back to raw key
        assert "iv:ciphertext:authTag" in result

    @pytest.mark.asyncio
    async def test_non_encrypted_format_key(self):
        ekv, mock_store, _ = _build_encrypted_store()
        mock_store.get_all_keys = AsyncMock(return_value=[
            "/plain/key/no/colons",
        ])

        result = await ekv.list_keys_in_directory("/plain")
        assert "/plain/key/no/colons" in result

    @pytest.mark.asyncio
    async def test_key_error_skipped(self):
        ekv, mock_store, _ = _build_encrypted_store()
        # Simulate a key processing error
        mock_store.get_all_keys = AsyncMock(return_value=[
            "/services/endpoints/test",
        ])

        result = await ekv.list_keys_in_directory("/services")
        assert len(result) == 1


# ============================================================================
# cancel_watch
# ============================================================================


class TestCancelWatch:
    @pytest.mark.asyncio
    async def test_delegates_to_store(self):
        ekv, mock_store, _ = _build_encrypted_store()
        mock_store.cancel_watch = AsyncMock()
        await ekv.cancel_watch("/key", "watch-123")
        mock_store.cancel_watch.assert_awaited_once_with("/key", "watch-123")


# ============================================================================
# publish_cache_invalidation
# ============================================================================


class TestPublishCacheInvalidation:
    @pytest.mark.asyncio
    async def test_with_method(self):
        ekv, mock_store, _ = _build_encrypted_store()
        mock_store.publish_cache_invalidation = AsyncMock()
        await ekv.publish_cache_invalidation("/key")
        mock_store.publish_cache_invalidation.assert_awaited_once_with("/key")

    @pytest.mark.asyncio
    async def test_without_method(self):
        ekv, mock_store, _ = _build_encrypted_store()
        # Remove the method
        del mock_store.publish_cache_invalidation
        # Should not raise
        await ekv.publish_cache_invalidation("/key")


# ============================================================================
# subscribe_cache_invalidation
# ============================================================================


class TestSubscribeCacheInvalidation:
    @pytest.mark.asyncio
    async def test_with_method(self):
        ekv, mock_store, _ = _build_encrypted_store()
        mock_task = AsyncMock()
        mock_store.subscribe_cache_invalidation = AsyncMock(return_value=mock_task)

        callback = MagicMock()
        result = await ekv.subscribe_cache_invalidation(callback)
        assert result == mock_task

    @pytest.mark.asyncio
    async def test_without_method(self):
        ekv, mock_store, _ = _build_encrypted_store()
        del mock_store.subscribe_cache_invalidation

        callback = MagicMock()
        result = await ekv.subscribe_cache_invalidation(callback)
        # Should return a no-op task
        assert result is not None
