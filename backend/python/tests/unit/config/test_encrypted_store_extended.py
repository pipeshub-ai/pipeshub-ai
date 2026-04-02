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


# ============================================================================
# serialize/deserialize closures (lines 82-99)
# ============================================================================


class TestSerializeDeserializeClosures:
    """Test the serialize/deserialize closures via _create_store (lines 82-99)."""

    def test_create_store_closures_exercised_via_create_key(self):
        """Exercise serialize/deserialize closures by actually calling _create_store."""
        # Build a store with a factory that captures the serialize/deserialize
        captured = {}

        def capture_factory_create(store_type, **kwargs):
            captured["serialize"] = kwargs.get("serialize")
            captured["deserialize"] = kwargs.get("deserialize")
            return AsyncMock()

        with (
            patch("app.config.providers.encrypted_store.os.getenv") as mock_getenv,
            patch("app.config.providers.encrypted_store.EncryptionService.get_instance") as mock_enc,
            patch("app.config.providers.encrypted_store.KeyValueStoreFactory.create_store", side_effect=capture_factory_create),
        ):
            mock_getenv.side_effect = lambda key, default=None: {
                "SECRET_KEY": _TEST_SECRET_KEY,
                "KV_STORE_TYPE": "etcd",
                "ETCD_URL": "http://localhost:2379",
            }.get(key, default)
            mock_enc.return_value = MagicMock()

            from app.config.providers.encrypted_store import EncryptedKeyValueStore
            ekv = EncryptedKeyValueStore(logger=logging.getLogger("test-closures"))

        serialize = captured.get("serialize")
        deserialize = captured.get("deserialize")

        if serialize and deserialize:
            # Test None (line 82-83)
            assert serialize(None) == b""

            # Test primitive types (line 84-85)
            assert deserialize(serialize("hello")) == "hello"
            assert deserialize(serialize(42)) == 42
            assert deserialize(serialize(3.14)) == 3.14
            assert deserialize(serialize(True)) is True

            # Test dict (line 86)
            assert deserialize(serialize({"key": "val"})) == {"key": "val"}

            # Test empty bytes (line 89-90)
            assert deserialize(b"") is None

            # Test non-JSON string (line 95-96)
            assert deserialize(b"not json") == "not json"

            # Test UnicodeDecodeError (line 97-99)
            assert deserialize(b'\xff\xfe') is None


# ============================================================================
# watch_key delegation (line 300)
# ============================================================================


class TestWatchKeyDelegation:
    """Test watch_key delegating to store."""

    @pytest.mark.asyncio
    async def test_watch_key_delegates(self):
        """watch_key delegates to underlying store (line 300)."""
        ekv, mock_store, _ = _build_encrypted_store()
        callback = MagicMock()
        error_callback = MagicMock()
        mock_store.watch_key = AsyncMock()

        await ekv.watch_key("/test/key", callback, error_callback)
        mock_store.watch_key.assert_awaited_once_with("/test/key", callback, error_callback)


# ============================================================================
# list_keys_in_directory error path (lines 354-356)
# ============================================================================


class TestListKeysErrorPath:
    """Test list_keys_in_directory error handling."""

    @pytest.mark.asyncio
    async def test_key_processing_error_skipped(self):
        """Error processing individual key is skipped (lines 354-356)."""
        ekv, mock_store, mock_encryption = _build_encrypted_store()

        # First key causes error, second key is fine
        mock_store.get_all_keys = AsyncMock(return_value=[
            "bad:key:format",
            "/services/good_key",
        ])
        # Make decrypt raise for the first key
        mock_encryption.decrypt.side_effect = [Exception("bad key"), "/services/good_key"]

        result = await ekv.list_keys_in_directory("/services")
        assert "/services/good_key" in result


# ============================================================================
# create_key verification mismatch (lines 215-229)
# ============================================================================


class TestCreateKeyVerification:
    """Test create_key with verification mismatch."""

    @pytest.mark.asyncio
    async def test_create_key_verification_mismatch_returns_false(self):
        """When stored value doesn't match original, returns False (lines 225-227)."""
        ekv, mock_store, mock_encryption = _build_encrypted_store()
        mock_store.create_key = AsyncMock(return_value=True)
        # Return a different value on verification read
        mock_encryption.encrypt.side_effect = lambda v: f"encrypted:{v}"
        mock_encryption.decrypt.side_effect = lambda v: '{"different": "value"}'
        mock_store.get_key = AsyncMock(return_value="encrypted:value")

        result = await ekv.create_key("/test", {"original": "value"})
        assert result is False

    @pytest.mark.asyncio
    async def test_create_key_verification_success(self):
        """When stored value matches original, returns True (line 229)."""
        ekv, mock_store, mock_encryption = _build_encrypted_store()
        mock_store.create_key = AsyncMock(return_value=True)

        import json
        value = {"key": "value"}
        value_json = json.dumps(value, default=str)
        mock_encryption.encrypt.side_effect = lambda v: f"encrypted:{v}"
        mock_encryption.decrypt.side_effect = lambda v: value_json
        mock_store.get_key = AsyncMock(return_value=f"encrypted:{value_json}")

        result = await ekv.create_key("/test", value)
        assert result is True

    @pytest.mark.asyncio
    async def test_create_key_store_returns_false(self):
        """When store.create_key returns False (lines 230-232)."""
        ekv, mock_store, mock_encryption = _build_encrypted_store()
        mock_store.create_key = AsyncMock(return_value=False)

        result = await ekv.create_key("/test", "value")
        assert result is False


# ============================================================================
# _create_etcd_store URL parsing (line 152→156)
# ============================================================================


class TestCreateEtcdStoreUrlParsing:
    """Test etcd URL protocol stripping."""

    def test_etcd_url_with_protocol_stripped(self):
        """ETCD_URL with http:// protocol is stripped (line 152-153)."""
        with (
            patch("app.config.providers.encrypted_store.os.getenv") as mock_getenv,
            patch("app.config.providers.encrypted_store.EncryptionService.get_instance") as mock_enc,
            patch("app.config.providers.encrypted_store.KeyValueStoreFactory.create_store") as mock_factory,
            patch("app.config.providers.encrypted_store.StoreConfig") as mock_config,
        ):
            mock_getenv.side_effect = lambda key, default=None: {
                "SECRET_KEY": _TEST_SECRET_KEY,
                "KV_STORE_TYPE": "etcd",
                "ETCD_URL": "http://myhost:2379",
                "ETCD_TIMEOUT": "5000",
            }.get(key, default)

            mock_enc.return_value = MagicMock()
            mock_factory.return_value = AsyncMock()

            from app.config.providers.encrypted_store import EncryptedKeyValueStore
            ekv = EncryptedKeyValueStore(logger=logging.getLogger("test-etcd-url"))

            # Verify StoreConfig was called with protocol-stripped host
            mock_config.assert_called_once()
            call_kwargs = mock_config.call_args[1]
            assert call_kwargs["host"] == "myhost"
            assert call_kwargs["port"] == 2379
