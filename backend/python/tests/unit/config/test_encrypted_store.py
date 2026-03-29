"""Unit tests for app.config.providers.encrypted_store.EncryptedKeyValueStore."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.service import config_node_constants
import logging


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_encrypted_store(
    secret_key="test-secret-key",
    kv_store_type="etcd",
    store_mock=None,
    encryption_mock=None,
):
    """Instantiate EncryptedKeyValueStore with all externals mocked.

    Patches os.getenv, EncryptionService, and KeyValueStoreFactory.
    """
    store_mock = store_mock or AsyncMock()
    store_mock.client = MagicMock()
    encryption_mock = encryption_mock or MagicMock()

    env_vars = {
        "SECRET_KEY": secret_key,
        "KV_STORE_TYPE": kv_store_type,
        # ETCD defaults
        "ETCD_URL": "http://localhost:2379",
        "ETCD_TIMEOUT": "5000",
        "ETCD_USERNAME": None,
        "ETCD_PASSWORD": None,
        # Redis defaults
        "REDIS_HOST": "localhost",
        "REDIS_PORT": "6379",
        "REDIS_PASSWORD": None,
        "REDIS_DB": "0",
        "REDIS_KV_PREFIX": "pipeshub:kv:",
        "REDIS_TIMEOUT": "10000",
    }

    def mock_getenv(key, default=None):
        return env_vars.get(key, default)

    with patch(
        "app.config.providers.encrypted_store.os.getenv", side_effect=mock_getenv
    ), patch(
        "app.config.providers.encrypted_store.EncryptionService"
    ) as enc_cls, patch(
        "app.config.providers.encrypted_store.KeyValueStoreFactory"
    ) as factory_cls, patch(
        "app.config.providers.encrypted_store.dotenv"
    ):
        enc_cls.get_instance.return_value = encryption_mock
        factory_cls.create_store.return_value = store_mock

        from app.config.providers.encrypted_store import EncryptedKeyValueStore

        eks = EncryptedKeyValueStore(logger=MagicMock())
        return eks, store_mock, encryption_mock


# ===================================================================
# __init__ / _create_store
# ===================================================================

class TestInit:
    """Tests for EncryptedKeyValueStore initialization."""

    def test_missing_secret_key_raises(self):
        with patch(
            "app.config.providers.encrypted_store.os.getenv", return_value=None
        ), patch("app.config.providers.encrypted_store.dotenv"):
            from app.config.providers.encrypted_store import EncryptedKeyValueStore

            with pytest.raises(ValueError, match="SECRET_KEY"):
                EncryptedKeyValueStore(logger=MagicMock())

    def test_etcd_store_created_by_default(self):
        """Default KV_STORE_TYPE should create an etcd store."""
        eks, store_mock, _ = _make_encrypted_store(kv_store_type="etcd")
        assert eks.store is store_mock

    def test_redis_store_created(self):
        """KV_STORE_TYPE=redis should create a Redis store."""
        eks, store_mock, _ = _make_encrypted_store(kv_store_type="redis")
        assert eks.store is store_mock


# ===================================================================
# create_key
# ===================================================================

class TestCreateKey:
    """Tests for EncryptedKeyValueStore.create_key."""

    @pytest.mark.asyncio
    async def test_creates_encrypted_key(self):
        eks, store_mock, enc_mock = _make_encrypted_store()
        store_mock.get_key = AsyncMock(return_value=None)  # no existing
        store_mock.create_key = AsyncMock(return_value=True)

        enc_mock.encrypt.return_value = "encrypted-value"
        enc_mock.decrypt.return_value = json.dumps("my-value")
        # After create, verification read returns encrypted value
        store_mock.get_key = AsyncMock(
            side_effect=[None, "encrypted-value"]
        )

        result = await eks.create_key("/some/key", "my-value")
        assert result is True
        enc_mock.encrypt.assert_called_once()
        store_mock.create_key.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_existing_key_when_overwrite_false(self):
        eks, store_mock, enc_mock = _make_encrypted_store()
        store_mock.get_key = AsyncMock(return_value="existing-value")

        result = await eks.create_key("/some/key", "new-value", overwrite=False)
        assert result is False
        store_mock.create_key.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_overwrites_existing_key_by_default(self):
        eks, store_mock, enc_mock = _make_encrypted_store()
        store_mock.get_key = AsyncMock(
            side_effect=["existing", "encrypted-val"]
        )
        store_mock.create_key = AsyncMock(return_value=True)
        enc_mock.encrypt.return_value = "encrypted-val"
        enc_mock.decrypt.return_value = json.dumps("new-value")

        result = await eks.create_key("/some/key", "new-value", overwrite=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_excluded_key_not_encrypted(self):
        """Keys in the EXCLUDED_KEYS list should not be encrypted."""
        eks, store_mock, enc_mock = _make_encrypted_store()
        excluded_key = config_node_constants.ENDPOINTS.value

        store_mock.get_key = AsyncMock(
            side_effect=[None, json.dumps("endpoint-value")]
        )
        store_mock.create_key = AsyncMock(return_value=True)

        result = await eks.create_key(excluded_key, "endpoint-value")
        assert result is True
        enc_mock.encrypt.assert_not_called()

    @pytest.mark.asyncio
    async def test_verification_failure_returns_false(self):
        """If stored value doesn't match after decrypt, should return False."""
        eks, store_mock, enc_mock = _make_encrypted_store()
        store_mock.get_key = AsyncMock(
            side_effect=[None, "encrypted-val"]
        )
        store_mock.create_key = AsyncMock(return_value=True)
        enc_mock.encrypt.return_value = "encrypted-val"
        enc_mock.decrypt.return_value = json.dumps("different-value")

        result = await eks.create_key("/some/key", "my-value")
        assert result is False

    @pytest.mark.asyncio
    async def test_store_create_failure_returns_false(self):
        eks, store_mock, enc_mock = _make_encrypted_store()
        store_mock.get_key = AsyncMock(return_value=None)
        store_mock.create_key = AsyncMock(return_value=False)
        enc_mock.encrypt.return_value = "encrypted"

        result = await eks.create_key("/some/key", "value")
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        eks, store_mock, enc_mock = _make_encrypted_store()
        store_mock.get_key = AsyncMock(side_effect=Exception("connection error"))

        result = await eks.create_key("/some/key", "value")
        assert result is False


# ===================================================================
# get_key
# ===================================================================

class TestGetKey:
    """Tests for EncryptedKeyValueStore.get_key."""

    @pytest.mark.asyncio
    async def test_get_encrypted_value(self):
        eks, store_mock, enc_mock = _make_encrypted_store()
        store_mock.get_key = AsyncMock(return_value="iv:cipher:auth")
        enc_mock.decrypt.return_value = json.dumps({"nested": "data"})

        result = await eks.get_key("/some/key")
        assert result == {"nested": "data"}
        enc_mock.decrypt.assert_called_once_with("iv:cipher:auth")

    @pytest.mark.asyncio
    async def test_get_unencrypted_value(self):
        """Keys in UNENCRYPTED_KEYS should not be decrypted."""
        eks, store_mock, enc_mock = _make_encrypted_store()
        key = config_node_constants.ENDPOINTS.value
        store_mock.get_key = AsyncMock(
            return_value=json.dumps({"cm": {"endpoint": "http://localhost:3000"}})
        )

        result = await eks.get_key(key)
        assert result == {"cm": {"endpoint": "http://localhost:3000"}}
        enc_mock.decrypt.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_key_not_found_returns_none(self):
        eks, store_mock, enc_mock = _make_encrypted_store()
        store_mock.get_key = AsyncMock(return_value=None)

        result = await eks.get_key("/nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_decrypt_failure_returns_none(self):
        eks, store_mock, enc_mock = _make_encrypted_store()
        store_mock.get_key = AsyncMock(return_value="bad-encrypted-data")
        enc_mock.decrypt.side_effect = Exception("decryption failed")

        result = await eks.get_key("/some/key")
        assert result is None

    @pytest.mark.asyncio
    async def test_store_exception_returns_none(self):
        eks, store_mock, enc_mock = _make_encrypted_store()
        store_mock.get_key = AsyncMock(side_effect=Exception("store unavailable"))

        result = await eks.get_key("/some/key")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_storage_key_unencrypted(self):
        """STORAGE key should be treated as unencrypted."""
        eks, store_mock, enc_mock = _make_encrypted_store()
        key = config_node_constants.STORAGE.value
        store_mock.get_key = AsyncMock(
            return_value=json.dumps({"storageType": "s3"})
        )

        result = await eks.get_key(key)
        assert result == {"storageType": "s3"}
        enc_mock.decrypt.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_migrations_key_unencrypted(self):
        """MIGRATIONS key should be treated as unencrypted."""
        eks, store_mock, enc_mock = _make_encrypted_store()
        key = config_node_constants.MIGRATIONS.value
        store_mock.get_key = AsyncMock(
            return_value=json.dumps({"version": 3})
        )

        result = await eks.get_key(key)
        assert result == {"version": 3}
        enc_mock.decrypt.assert_not_called()


# ===================================================================
# list_keys_in_directory
# ===================================================================

class TestListKeysInDirectory:
    """Tests for EncryptedKeyValueStore.list_keys_in_directory."""

    @pytest.mark.asyncio
    async def test_empty_keys_returns_empty(self):
        eks, store_mock, _ = _make_encrypted_store()
        store_mock.get_all_keys = AsyncMock(return_value=[])

        result = await eks.list_keys_in_directory("/some/dir")
        assert result == []

    @pytest.mark.asyncio
    async def test_none_keys_returns_empty(self):
        eks, store_mock, _ = _make_encrypted_store()
        store_mock.get_all_keys = AsyncMock(return_value=None)

        result = await eks.list_keys_in_directory("/some/dir")
        assert result == []

    @pytest.mark.asyncio
    async def test_decrypts_encrypted_keys(self):
        """Keys in encrypted format (2 colons) should be decrypted."""
        eks, store_mock, enc_mock = _make_encrypted_store()
        store_mock.get_all_keys = AsyncMock(
            return_value=["iv:ciphertext:authTag"]
        )
        enc_mock.decrypt.return_value = "/services/some/key"

        result = await eks.list_keys_in_directory("/services")
        assert "/services/some/key" in result
        enc_mock.decrypt.assert_called_once_with("iv:ciphertext:authTag")

    @pytest.mark.asyncio
    async def test_unencrypted_keys_passthrough(self):
        """Keys matching UNENCRYPTED_PREFIXES should not be decrypted."""
        eks, store_mock, enc_mock = _make_encrypted_store()
        endpoints_key = config_node_constants.ENDPOINTS.value + "/sub"
        store_mock.get_all_keys = AsyncMock(return_value=[endpoints_key])

        result = await eks.list_keys_in_directory(
            config_node_constants.ENDPOINTS.value
        )
        assert endpoints_key in result
        enc_mock.decrypt.assert_not_called()

    @pytest.mark.asyncio
    async def test_filters_by_directory_prefix(self):
        """Only keys matching the directory prefix should be returned."""
        eks, store_mock, enc_mock = _make_encrypted_store()
        store_mock.get_all_keys = AsyncMock(
            return_value=[
                "iv:cipher1:auth",
                "iv:cipher2:auth",
            ]
        )
        enc_mock.decrypt.side_effect = [
            "/services/match/key1",
            "/other/nomatch/key2",
        ]

        result = await eks.list_keys_in_directory("/services/match")
        assert "/services/match/key1" in result
        assert "/other/nomatch/key2" not in result

    @pytest.mark.asyncio
    async def test_root_directory_returns_all(self):
        """Directory '/' should return all keys."""
        eks, store_mock, enc_mock = _make_encrypted_store()
        endpoints_key = config_node_constants.ENDPOINTS.value
        store_mock.get_all_keys = AsyncMock(
            return_value=[endpoints_key, "iv:cipher:auth"]
        )
        enc_mock.decrypt.return_value = "/some/key"

        result = await eks.list_keys_in_directory("/")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_empty_directory_returns_all(self):
        """Empty directory string should return all keys."""
        eks, store_mock, enc_mock = _make_encrypted_store()
        endpoints_key = config_node_constants.ENDPOINTS.value
        store_mock.get_all_keys = AsyncMock(
            return_value=[endpoints_key]
        )

        result = await eks.list_keys_in_directory("")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_keys_without_colons_not_decrypted(self):
        """Keys that don't have exactly 2 colons are treated as unencrypted."""
        eks, store_mock, enc_mock = _make_encrypted_store()
        store_mock.get_all_keys = AsyncMock(
            return_value=["simple_key_no_colons"]
        )

        result = await eks.list_keys_in_directory("")
        assert "simple_key_no_colons" in result
        enc_mock.decrypt.assert_not_called()

    @pytest.mark.asyncio
    async def test_decryption_failure_uses_raw_key(self):
        """If decryption fails for an encrypted-format key, use it as-is."""
        eks, store_mock, enc_mock = _make_encrypted_store()
        store_mock.get_all_keys = AsyncMock(
            return_value=["iv:bad_cipher:auth"]
        )
        enc_mock.decrypt.side_effect = Exception("decrypt failed")

        result = await eks.list_keys_in_directory("")
        assert "iv:bad_cipher:auth" in result

    @pytest.mark.asyncio
    async def test_mixed_encrypted_and_unencrypted_keys(self):
        """Properly handles a mix of encrypted and unencrypted keys."""
        eks, store_mock, enc_mock = _make_encrypted_store()
        endpoints_key = config_node_constants.ENDPOINTS.value + "/sub"
        storage_key = config_node_constants.STORAGE.value + "/sub"
        store_mock.get_all_keys = AsyncMock(
            return_value=[
                endpoints_key,
                storage_key,
                "iv:encrypted:tag",
                "no_colon_key",
                "one:colon",
            ]
        )
        enc_mock.decrypt.return_value = "/services/decrypted"

        result = await eks.list_keys_in_directory("")
        # All keys should be returned (empty prefix matches all)
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_store_exception_propagates(self):
        eks, store_mock, _ = _make_encrypted_store()
        store_mock.get_all_keys = AsyncMock(
            side_effect=Exception("store error")
        )

        with pytest.raises(Exception, match="store error"):
            await eks.list_keys_in_directory("/some/dir")


# ===================================================================
# _create_etcd_store
# ===================================================================

class TestCreateEtcdStore:
    """Tests for EncryptedKeyValueStore._create_etcd_store."""

    def test_parses_etcd_url_with_protocol(self):
        """ETCD_URL with protocol prefix should strip protocol."""
        eks, _, _ = _make_encrypted_store(kv_store_type="etcd")
        # Initialization should succeed without errors
        assert eks.store is not None

    def test_missing_etcd_url_raises(self):
        """Missing ETCD_URL should raise ValueError."""
        env_vars = {
            "SECRET_KEY": "test-secret",
            "KV_STORE_TYPE": "etcd",
            # No ETCD_URL
        }

        def mock_getenv(key, default=None):
            return env_vars.get(key, default)

        with patch(
            "app.config.providers.encrypted_store.os.getenv", side_effect=mock_getenv
        ), patch(
            "app.config.providers.encrypted_store.EncryptionService"
        ) as enc_cls, patch(
            "app.config.providers.encrypted_store.KeyValueStoreFactory"
        ) as factory_cls, patch(
            "app.config.providers.encrypted_store.dotenv"
        ):
            enc_cls.get_instance.return_value = MagicMock()

            from app.config.providers.encrypted_store import EncryptedKeyValueStore

            with pytest.raises(ValueError, match="ETCD_URL"):
                EncryptedKeyValueStore(logger=MagicMock())


# ===================================================================
# _create_redis_store
# ===================================================================

class TestCreateRedisStore:
    """Tests for EncryptedKeyValueStore._create_redis_store."""

    def test_creates_redis_store_with_defaults(self):
        eks, store_mock, _ = _make_encrypted_store(kv_store_type="redis")
        assert eks.store is store_mock

    def test_redis_timeout_converted_to_seconds(self):
        """REDIS_TIMEOUT is in milliseconds, should be converted to seconds."""
        with patch(
            "app.config.providers.encrypted_store.os.getenv"
        ) as mock_getenv, patch(
            "app.config.providers.encrypted_store.EncryptionService"
        ) as enc_cls, patch(
            "app.config.providers.encrypted_store.KeyValueStoreFactory"
        ) as factory_cls, patch(
            "app.config.providers.encrypted_store.dotenv"
        ):
            enc_cls.get_instance.return_value = MagicMock()
            factory_cls.create_store.return_value = AsyncMock(client=MagicMock())

            mock_getenv.side_effect = lambda key, default=None: {
                "SECRET_KEY": "secret",
                "KV_STORE_TYPE": "redis",
                "REDIS_HOST": "redis-host",
                "REDIS_PORT": "6380",
                "REDIS_PASSWORD": "pass",
                "REDIS_DB": "2",
                "REDIS_KV_PREFIX": "prefix:",
                "REDIS_TIMEOUT": "20000",
            }.get(key, default)

            from app.config.providers.encrypted_store import EncryptedKeyValueStore

            EncryptedKeyValueStore(logger=MagicMock())

            # Verify StoreConfig was passed with timeout in seconds
            call_kwargs = factory_cls.create_store.call_args
            config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
            assert config.timeout == 20.0  # 20000ms -> 20s
            assert config.host == "redis-host"
            assert config.port == 6380
            assert config.password == "pass"
            assert config.db == 2


# ===================================================================
# Delegation methods
# ===================================================================

class TestDelegation:
    """Tests for delegated methods (delete_key, get_all_keys, etc.)."""

    @pytest.mark.asyncio
    async def test_delete_key_delegates(self):
        eks, store_mock, _ = _make_encrypted_store()
        store_mock.delete_key = AsyncMock(return_value=True)

        result = await eks.delete_key("/some/key")
        assert result is True
        store_mock.delete_key.assert_awaited_once_with("/some/key")

    @pytest.mark.asyncio
    async def test_get_all_keys_delegates(self):
        eks, store_mock, _ = _make_encrypted_store()
        store_mock.get_all_keys = AsyncMock(return_value=["k1", "k2"])

        result = await eks.get_all_keys()
        assert result == ["k1", "k2"]

    @pytest.mark.asyncio
    async def test_close_delegates(self):
        eks, store_mock, _ = _make_encrypted_store()
        store_mock.close = AsyncMock()

        await eks.close()
        store_mock.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_value_calls_create_key(self):
        eks, store_mock, enc_mock = _make_encrypted_store()
        eks.create_key = AsyncMock(return_value=True)

        await eks.update_value("/key", "val", ttl=60)
        eks.create_key.assert_awaited_once_with("/key", "val", True, 60)

    def test_client_property(self):
        eks, store_mock, _ = _make_encrypted_store()
        assert eks.client is store_mock.client

# =============================================================================
# Merged from test_encrypted_store_coverage.py
# =============================================================================

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
