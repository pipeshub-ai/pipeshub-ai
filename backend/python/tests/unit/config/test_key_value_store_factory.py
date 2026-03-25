"""Tests for KeyValueStoreFactory and StoreConfig."""

from unittest.mock import MagicMock, patch

import pytest

from app.config.constants.store_type import StoreType
from app.config.key_value_store_factory import KeyValueStoreFactory, StoreConfig


class TestStoreConfig:
    """Tests for the StoreConfig dataclass."""

    def test_default_values(self):
        config = StoreConfig(host="localhost", port=2379)
        assert config.host == "localhost"
        assert config.port == 2379
        assert config.timeout == 5.0
        assert config.username is None
        assert config.password is None
        assert config.ca_cert is None
        assert config.cert_key is None
        assert config.cert_cert is None
        assert config.additional_options is None
        assert config.db == 0
        assert config.key_prefix == "pipeshub:kv:"

    def test_custom_values(self):
        config = StoreConfig(
            host="10.0.0.1",
            port=6379,
            timeout=10.0,
            username="admin",
            password="secret",
            ca_cert="/path/ca.crt",
            cert_key="/path/key.pem",
            cert_cert="/path/cert.pem",
            additional_options={"retry": True},
            db=2,
            key_prefix="custom:prefix:",
        )
        assert config.host == "10.0.0.1"
        assert config.port == 6379
        assert config.timeout == 10.0
        assert config.username == "admin"
        assert config.password == "secret"
        assert config.ca_cert == "/path/ca.crt"
        assert config.cert_key == "/path/key.pem"
        assert config.cert_cert == "/path/cert.pem"
        assert config.additional_options == {"retry": True}
        assert config.db == 2
        assert config.key_prefix == "custom:prefix:"


class TestKeyValueStoreFactoryCreateStore:
    """Tests for KeyValueStoreFactory.create_store dispatch."""

    @patch("app.config.key_value_store_factory.Etcd3DistributedKeyValueStore")
    def test_create_etcd3_store(self, mock_etcd3_cls):
        mock_store = MagicMock()
        mock_etcd3_cls.__getitem__ = MagicMock(return_value=mock_etcd3_cls)
        mock_etcd3_cls.return_value = mock_store

        serializer = lambda x: x.encode()
        deserializer = lambda x: x.decode()
        config = StoreConfig(host="localhost", port=2379)

        result = KeyValueStoreFactory.create_store(
            StoreType.ETCD3, serializer=serializer, deserializer=deserializer, config=config
        )
        assert result == mock_store

    @patch("app.config.key_value_store_factory.InMemoryKeyValueStore")
    def test_create_in_memory_store(self, mock_inmem_cls):
        mock_store = MagicMock()
        mock_inmem_cls.__getitem__ = MagicMock(return_value=mock_inmem_cls)
        mock_inmem_cls.return_value = mock_store

        config = StoreConfig(host="localhost", port=0)
        result = KeyValueStoreFactory.create_store(StoreType.IN_MEMORY, config=config)
        assert result == mock_store

    @patch("app.config.key_value_store_factory.RedisDistributedKeyValueStore")
    def test_create_redis_store(self, mock_redis_cls):
        mock_store = MagicMock()
        mock_redis_cls.__getitem__ = MagicMock(return_value=mock_redis_cls)
        mock_redis_cls.return_value = mock_store

        serializer = lambda x: x.encode()
        deserializer = lambda x: x.decode()
        config = StoreConfig(host="localhost", port=6379, password="pass", db=1, key_prefix="test:")

        result = KeyValueStoreFactory.create_store(
            StoreType.REDIS, serializer=serializer, deserializer=deserializer, config=config
        )
        assert result == mock_store

    def test_create_store_default_config_when_none_raises(self):
        """When config is None, StoreConfig() fails because host/port are required.
        The TypeError happens before the try/except block, so it propagates raw."""
        with pytest.raises(TypeError):
            KeyValueStoreFactory.create_store(StoreType.IN_MEMORY, config=None)


class TestKeyValueStoreFactoryEtcd3Validation:
    """Tests for ETCD3-specific validation in the factory."""

    def test_etcd3_missing_serializer_raises(self):
        config = StoreConfig(host="localhost", port=2379)
        with pytest.raises(ValueError, match="Failed to create store"):
            KeyValueStoreFactory.create_store(
                StoreType.ETCD3, serializer=None, deserializer=lambda x: x, config=config
            )

    def test_etcd3_missing_deserializer_raises(self):
        config = StoreConfig(host="localhost", port=2379)
        with pytest.raises(ValueError, match="Failed to create store"):
            KeyValueStoreFactory.create_store(
                StoreType.ETCD3, serializer=lambda x: x, deserializer=None, config=config
            )

    def test_etcd3_non_callable_serializer_raises(self):
        config = StoreConfig(host="localhost", port=2379)
        with pytest.raises(ValueError, match="Failed to create store"):
            KeyValueStoreFactory.create_store(
                StoreType.ETCD3, serializer="not_callable", deserializer=lambda x: x, config=config
            )

    def test_etcd3_non_callable_deserializer_raises(self):
        config = StoreConfig(host="localhost", port=2379)
        with pytest.raises(ValueError, match="Failed to create store"):
            KeyValueStoreFactory.create_store(
                StoreType.ETCD3, serializer=lambda x: x, deserializer="not_callable", config=config
            )


class TestKeyValueStoreFactoryRedisValidation:
    """Tests for Redis-specific validation in the factory."""

    def test_redis_missing_serializer_raises(self):
        config = StoreConfig(host="localhost", port=6379)
        with pytest.raises(ValueError, match="Failed to create store"):
            KeyValueStoreFactory.create_store(
                StoreType.REDIS, serializer=None, deserializer=lambda x: x, config=config
            )

    def test_redis_missing_deserializer_raises(self):
        config = StoreConfig(host="localhost", port=6379)
        with pytest.raises(ValueError, match="Failed to create store"):
            KeyValueStoreFactory.create_store(
                StoreType.REDIS, serializer=lambda x: x, deserializer=None, config=config
            )

    def test_redis_non_callable_serializer_raises(self):
        config = StoreConfig(host="localhost", port=6379)
        with pytest.raises(ValueError, match="Failed to create store"):
            KeyValueStoreFactory.create_store(
                StoreType.REDIS, serializer=42, deserializer=lambda x: x, config=config
            )

    def test_redis_non_callable_deserializer_raises(self):
        config = StoreConfig(host="localhost", port=6379)
        with pytest.raises(ValueError, match="Failed to create store"):
            KeyValueStoreFactory.create_store(
                StoreType.REDIS, serializer=lambda x: x, deserializer=42, config=config
            )


class TestKeyValueStoreFactoryUnsupportedType:
    """Tests for unsupported store types."""

    def test_unsupported_store_type_raises(self):
        """An unknown enum value should raise ValueError."""
        # Use ENVIRONMENT which is defined in the enum but not handled in the factory
        config = StoreConfig(host="localhost", port=0)
        with pytest.raises(ValueError, match="Failed to create store"):
            KeyValueStoreFactory.create_store(StoreType.ENVIRONMENT, config=config)


class TestKeyValueStoreFactoryExceptionWrapping:
    """Tests that construction exceptions are wrapped properly."""

    @patch("app.config.key_value_store_factory.Etcd3DistributedKeyValueStore")
    def test_etcd3_constructor_exception_is_wrapped(self, mock_etcd3_cls):
        mock_etcd3_cls.__getitem__ = MagicMock(return_value=mock_etcd3_cls)
        mock_etcd3_cls.side_effect = RuntimeError("connection refused")

        config = StoreConfig(host="localhost", port=2379)
        with pytest.raises(ValueError, match="Failed to create store.*connection refused"):
            KeyValueStoreFactory.create_store(
                StoreType.ETCD3,
                serializer=lambda x: x,
                deserializer=lambda x: x,
                config=config,
            )

    @patch("app.config.key_value_store_factory.RedisDistributedKeyValueStore")
    def test_redis_constructor_exception_is_wrapped(self, mock_redis_cls):
        mock_redis_cls.__getitem__ = MagicMock(return_value=mock_redis_cls)
        mock_redis_cls.side_effect = ConnectionError("redis down")

        config = StoreConfig(host="localhost", port=6379)
        with pytest.raises(ValueError, match="Failed to create store.*redis down"):
            KeyValueStoreFactory.create_store(
                StoreType.REDIS,
                serializer=lambda x: x,
                deserializer=lambda x: x,
                config=config,
            )
