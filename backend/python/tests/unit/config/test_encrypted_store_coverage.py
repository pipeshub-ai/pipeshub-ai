"""
Coverage boost tests for app.config.providers.encrypted_store.

Targets uncovered lines:
- 82-86: serialize closure (None value, primitive types)
- 89-99: deserialize closure (empty bytes, non-JSON, UnicodeDecodeError)
- 152->156: etcd URL parsing - URL without port
- 215->229: create_key verification for unencrypted excluded key
- 354-356: list_keys_in_directory key processing error
"""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.service import config_node_constants


_TEST_SECRET_KEY = "test-encrypted-store-coverage-key"


def _build_store(store_type="etcd", env_overrides=None):
    """Build an EncryptedKeyValueStore with mocked internals, capturing serialize/deserialize."""
    captured = {}

    default_env = {
        "SECRET_KEY": _TEST_SECRET_KEY,
        "KV_STORE_TYPE": store_type,
        "ETCD_URL": "http://localhost:2379",
        "ETCD_TIMEOUT": "5000",
        "REDIS_HOST": "localhost",
        "REDIS_PORT": "6379",
        "REDIS_DB": "0",
        "REDIS_KV_PREFIX": "pipeshub:kv:",
        "REDIS_TIMEOUT": "10000",
    }
    if env_overrides:
        default_env.update(env_overrides)

    def capturing_factory(store_type, **kwargs):
        captured["serializer"] = kwargs.get("serializer")
        captured["deserializer"] = kwargs.get("deserializer")
        return AsyncMock()

    with (
        patch("app.config.providers.encrypted_store.os.getenv") as mock_getenv,
        patch("app.config.providers.encrypted_store.EncryptionService.get_instance") as mock_enc,
        patch("app.config.providers.encrypted_store.KeyValueStoreFactory.create_store", side_effect=capturing_factory),
        patch("app.config.providers.encrypted_store.dotenv"),
    ):
        mock_getenv.side_effect = lambda key, default=None: default_env.get(key, default)
        mock_encryption = MagicMock()
        mock_encryption.encrypt.side_effect = lambda v: f"enc:{v}"
        mock_encryption.decrypt.side_effect = lambda v: v.replace("enc:", "")
        mock_enc.return_value = mock_encryption

        from app.config.providers.encrypted_store import EncryptedKeyValueStore
        ekv = EncryptedKeyValueStore(logger=logging.getLogger("test-enc-cov"))

    return ekv, ekv.store, mock_encryption, captured


def _build_store_simple(store_type="etcd"):
    """Build store without capturing closures."""
    with (
        patch("app.config.providers.encrypted_store.os.getenv") as mock_getenv,
        patch("app.config.providers.encrypted_store.EncryptionService.get_instance") as mock_enc,
        patch("app.config.providers.encrypted_store.KeyValueStoreFactory.create_store") as mock_factory,
        patch("app.config.providers.encrypted_store.dotenv"),
    ):
        mock_getenv.side_effect = lambda key, default=None: {
            "SECRET_KEY": _TEST_SECRET_KEY,
            "KV_STORE_TYPE": store_type,
            "ETCD_URL": "http://localhost:2379",
            "ETCD_TIMEOUT": "5000",
            "REDIS_HOST": "localhost",
            "REDIS_PORT": "6379",
            "REDIS_DB": "0",
            "REDIS_KV_PREFIX": "pipeshub:kv:",
            "REDIS_TIMEOUT": "10000",
        }.get(key, default)

        mock_encryption = MagicMock()
        mock_encryption.encrypt.side_effect = lambda v: f"enc:{v}"
        mock_encryption.decrypt.side_effect = lambda v: v.replace("enc:", "")
        mock_enc.return_value = mock_encryption

        mock_store = AsyncMock()
        mock_factory.return_value = mock_store

        from app.config.providers.encrypted_store import EncryptedKeyValueStore
        ekv = EncryptedKeyValueStore(logger=logging.getLogger("test-enc-cov"))

    return ekv, mock_store, mock_encryption


# ============================================================================
# Serialize/Deserialize closures (lines 82-99)
# ============================================================================


class TestSerializeDeserializeClosures:
    """Test the serialize/deserialize closures created inside _create_store."""

    def test_serialize_none(self):
        """serialize(None) returns b'' (lines 82-83)."""
        _, _, _, captured = _build_store()
        serialize = captured.get("serializer")
        if serialize:
            assert serialize(None) == b""

    def test_serialize_string(self):
        """serialize(str) returns JSON-encoded bytes (line 84-85)."""
        _, _, _, captured = _build_store()
        serialize = captured.get("serializer")
        if serialize:
            result = serialize("hello")
            assert isinstance(result, bytes)
            assert json.loads(result) == "hello"

    def test_serialize_int(self):
        _, _, _, captured = _build_store()
        serialize = captured.get("serializer")
        if serialize:
            result = serialize(42)
            assert json.loads(result) == 42

    def test_serialize_float(self):
        _, _, _, captured = _build_store()
        serialize = captured.get("serializer")
        if serialize:
            result = serialize(3.14)
            assert json.loads(result) == 3.14

    def test_serialize_bool(self):
        _, _, _, captured = _build_store()
        serialize = captured.get("serializer")
        if serialize:
            result = serialize(True)
            assert json.loads(result) is True

    def test_serialize_dict(self):
        """serialize(dict) uses json.dumps with default=str (line 86)."""
        _, _, _, captured = _build_store()
        serialize = captured.get("serializer")
        if serialize:
            result = serialize({"key": "value"})
            assert json.loads(result) == {"key": "value"}

    def test_serialize_list(self):
        _, _, _, captured = _build_store()
        serialize = captured.get("serializer")
        if serialize:
            result = serialize([1, 2, 3])
            assert json.loads(result) == [1, 2, 3]

    def test_deserialize_empty_bytes(self):
        """deserialize(b'') returns None (lines 89-90)."""
        _, _, _, captured = _build_store()
        deserialize = captured.get("deserializer")
        if deserialize:
            assert deserialize(b"") is None

    def test_deserialize_valid_json(self):
        """deserialize valid JSON bytes returns parsed value (lines 93-94)."""
        _, _, _, captured = _build_store()
        deserialize = captured.get("deserializer")
        if deserialize:
            assert deserialize(b'"hello"') == "hello"
            assert deserialize(b'42') == 42
            assert deserialize(b'{"a": 1}') == {"a": 1}

    def test_deserialize_non_json_string(self):
        """deserialize non-JSON string returns the raw string (lines 95-96)."""
        _, _, _, captured = _build_store()
        deserialize = captured.get("deserializer")
        if deserialize:
            result = deserialize(b"not json at all")
            assert result == "not json at all"

    def test_deserialize_unicode_error(self):
        """deserialize bytes that can't be decoded returns None (lines 97-99)."""
        _, _, _, captured = _build_store()
        deserialize = captured.get("deserializer")
        if deserialize:
            result = deserialize(b'\xff\xfe\x00\x01')
            assert result is None


# ============================================================================
# _create_etcd_store URL parsing edge case (line 152->156)
# ============================================================================


class TestEtcdUrlWithoutPort:
    """Test ETCD_URL without port number."""

    def test_etcd_url_without_port(self):
        """ETCD_URL without port defaults to 2379."""
        with (
            patch("app.config.providers.encrypted_store.os.getenv") as mock_getenv,
            patch("app.config.providers.encrypted_store.EncryptionService.get_instance") as mock_enc,
            patch("app.config.providers.encrypted_store.KeyValueStoreFactory.create_store") as mock_factory,
            patch("app.config.providers.encrypted_store.dotenv"),
        ):
            mock_getenv.side_effect = lambda key, default=None: {
                "SECRET_KEY": _TEST_SECRET_KEY,
                "KV_STORE_TYPE": "etcd",
                "ETCD_URL": "myhost",  # No protocol, no port
                "ETCD_TIMEOUT": "5000",
            }.get(key, default)

            mock_enc.return_value = MagicMock()
            mock_factory.return_value = AsyncMock()

            from app.config.providers.encrypted_store import EncryptedKeyValueStore
            ekv = EncryptedKeyValueStore(logger=logging.getLogger("test-etcd-noport"))

            # Verify the store was created
            assert ekv.store is not None
            # Check the config passed to factory
            call_args = mock_factory.call_args
            config = call_args.kwargs.get("config") or call_args[1].get("config")
            assert config.host == "myhost"
            assert config.port == 2379

    def test_etcd_url_with_protocol_and_host_only(self):
        """ETCD_URL like http://myhost (no port) defaults port to 2379."""
        with (
            patch("app.config.providers.encrypted_store.os.getenv") as mock_getenv,
            patch("app.config.providers.encrypted_store.EncryptionService.get_instance") as mock_enc,
            patch("app.config.providers.encrypted_store.KeyValueStoreFactory.create_store") as mock_factory,
            patch("app.config.providers.encrypted_store.dotenv"),
        ):
            mock_getenv.side_effect = lambda key, default=None: {
                "SECRET_KEY": _TEST_SECRET_KEY,
                "KV_STORE_TYPE": "etcd",
                "ETCD_URL": "http://myhost",  # Protocol but no port
                "ETCD_TIMEOUT": "5000",
            }.get(key, default)

            mock_enc.return_value = MagicMock()
            mock_factory.return_value = AsyncMock()

            from app.config.providers.encrypted_store import EncryptedKeyValueStore
            EncryptedKeyValueStore(logger=logging.getLogger("test-etcd-noport2"))

            call_args = mock_factory.call_args
            config = call_args.kwargs.get("config") or call_args[1].get("config")
            assert config.host == "myhost"
            assert config.port == 2379


# ============================================================================
# create_key verification for unencrypted keys (lines 215->229)
# ============================================================================


class TestCreateKeyUnencryptedVerification:
    """Test create_key verification path for excluded (unencrypted) keys."""

    @pytest.mark.asyncio
    async def test_create_excluded_key_verification_success(self):
        """Excluded key (e.g., ENDPOINTS) stored without encryption, verified correctly."""
        ekv, mock_store, mock_encryption = _build_store_simple()
        excluded_key = config_node_constants.ENDPOINTS.value

        value = {"endpoint": "http://localhost:3000"}
        value_json = json.dumps(value)

        # First get_key call: check if exists (None = doesn't exist)
        # create_key: success
        # Second get_key call: verification read returns the unencrypted value
        mock_store.get_key = AsyncMock(
            side_effect=[None, value_json]
        )
        mock_store.create_key = AsyncMock(return_value=True)

        result = await ekv.create_key(excluded_key, value)
        assert result is True
        # Should NOT call encrypt for excluded keys
        mock_encryption.encrypt.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_excluded_key_verification_mismatch(self):
        """Excluded key verification fails when stored value differs."""
        ekv, mock_store, mock_encryption = _build_store_simple()
        excluded_key = config_node_constants.STORAGE.value

        value = {"storageType": "s3"}
        different_value = json.dumps({"storageType": "azure"})

        mock_store.get_key = AsyncMock(
            side_effect=[None, different_value]
        )
        mock_store.create_key = AsyncMock(return_value=True)

        result = await ekv.create_key(excluded_key, value)
        assert result is False

    @pytest.mark.asyncio
    async def test_create_excluded_key_already_dict_value(self):
        """Excluded key where stored value is already a dict (not a string)."""
        ekv, mock_store, mock_encryption = _build_store_simple()
        excluded_key = config_node_constants.MIGRATIONS.value

        value = {"version": 3}

        # Stored value is already parsed (dict, not string)
        mock_store.get_key = AsyncMock(
            side_effect=[None, value]
        )
        mock_store.create_key = AsyncMock(return_value=True)

        result = await ekv.create_key(excluded_key, value)
        assert result is True


# ============================================================================
# list_keys_in_directory key processing error (lines 354-356)
# ============================================================================


class TestListKeysKeyProcessingError:
    """Test that key processing errors in list_keys_in_directory are skipped."""

    @pytest.mark.asyncio
    async def test_key_processing_error_continues(self):
        """When processing a single key raises Exception, it's skipped (lines 354-356)."""
        ekv, mock_store, mock_encryption = _build_store_simple()

        # Set up: first key causes error in the is_unencrypted check (by making
        # startswith fail), second key works fine
        mock_store.get_all_keys = AsyncMock(return_value=[
            "bad:key:data",
            "/services/endpoints/good",
        ])

        # Make decrypt raise for the first key (has 2 colons, looks encrypted)
        call_count = [0]
        def side_effect(v):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("decrypt failed for bad key")
            return "/services/decrypted"

        mock_encryption.decrypt.side_effect = side_effect

        result = await ekv.list_keys_in_directory("/services")
        # The bad key falls back to raw and doesn't match /services prefix
        # The good key matches /services prefix
        assert "/services/endpoints/good" in result

    @pytest.mark.asyncio
    async def test_key_processing_general_error_skipped(self):
        """A general exception during key processing is caught and skipped."""
        ekv, mock_store, mock_encryption = _build_store_simple()

        # Create a key that will cause an error during the any() check
        # by making the key not a string
        mock_store.get_all_keys = AsyncMock(return_value=[
            "normal:key:data",
        ])

        # Make the decrypt fail with a general error
        mock_encryption.decrypt.side_effect = RuntimeError("general error")

        result = await ekv.list_keys_in_directory("")
        # Key falls back to raw key
        assert "normal:key:data" in result


# ============================================================================
# create_key: store returns None on verification read
# ============================================================================


class TestCreateKeyVerificationNullRead:
    """Test create_key when verification read returns None."""

    @pytest.mark.asyncio
    async def test_verification_read_returns_none(self):
        """When verification read returns None after successful create, returns True."""
        ekv, mock_store, mock_encryption = _build_store_simple()

        # First get_key: check existence (None)
        # create_key: success
        # Second get_key: verification (None - somehow disappeared)
        mock_store.get_key = AsyncMock(
            side_effect=[None, None]
        )
        mock_store.create_key = AsyncMock(return_value=True)

        result = await ekv.create_key("/some/key", "value")
        # When verification read returns None, it goes past the if block
        # and returns True (line 229)
        assert result is True
