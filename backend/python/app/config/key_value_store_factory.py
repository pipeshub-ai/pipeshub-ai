import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, TypeVar

from app.config.constants.store_type import StoreType
from app.config.key_value_store import KeyValueStore
from app.config.providers.etcd.etcd3_store import Etcd3DistributedKeyValueStore
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.config.providers.redis.redis_store import RedisDistributedKeyValueStore
from app.utils.logger import create_logger

logger = create_logger("KV Store:" + os.getenv("KV_STORE_TYPE", "etcd"))

T = TypeVar("T")


@dataclass
class StoreConfig:
    """Configuration for key-value store creation."""

    host: str
    port: int
    timeout: float = 5.0
    username: Optional[str] = None
    password: Optional[str] = None
    ca_cert: Optional[str] = None
    cert_key: Optional[str] = None
    cert_cert: Optional[str] = None
    additional_options: Dict[str, Any] = None
    # Redis-specific options
    db: int = 0
    key_prefix: str = "pipeshub:kv:"


class KeyValueStoreFactory:
    """
    Factory class for creating different types of key-value stores.

    This factory handles the creation of different store implementations
    while managing their dependencies and configuration.
    """

    @staticmethod
    def create_store(
        store_type: StoreType,
        serializer: Optional[Callable[[T], bytes]] = None,
        deserializer: Optional[Callable[[bytes], T]] = None,
        config: Optional[StoreConfig] = None,
    ) -> KeyValueStore[T]:
        """
        Create a new key-value store instance.

        Args:
            store_type: Type of store to create
            serializer: Function to convert values to bytes (required for ETCD3)
            deserializer: Function to convert bytes back to values (required for ETCD3)
            config: Optional configuration for the store

        Returns:
            A new key-value store instance

        Raises:
            ValueError: If required configuration is missing
            TypeError: If serializer/deserializer types are incorrect
        """
        logger.debug("🔧 Creating new key-value store")
        logger.debug("📋 Store type: %s", store_type)
        logger.debug("📋 Serializer provided: %s", serializer is not None)
        logger.debug("📋 Deserializer provided: %s", deserializer is not None)

        config = config or StoreConfig()
        logger.debug("📋 Configuration:")
        logger.debug("   - Host: %s", config.host)
        logger.debug("   - Port: %s", config.port)
        logger.debug("   - Timeout: %s", config.timeout)
        logger.debug("   - Auth enabled: %s", bool(config.username and config.password))
        logger.debug("   - SSL enabled: %s", bool(config.ca_cert or config.cert_key))

        try:
            if store_type == StoreType.ETCD3:
                logger.debug("🔄 Creating ETCD3 store")
                store = KeyValueStoreFactory._create_etcd3_store(
                    serializer, deserializer, config
                )
                logger.info("✅ ETCD3 store created successfully")
                return store
            elif store_type == StoreType.IN_MEMORY:
                logger.debug("🔄 Creating in-memory store")
                store = KeyValueStoreFactory._create_in_memory_store()
                logger.info("✅ In-memory store created successfully")
                return store
            elif store_type == StoreType.REDIS:
                logger.debug("🔄 Creating Redis store")
                store = KeyValueStoreFactory._create_redis_store(
                    serializer, deserializer, config
                )
                logger.info("✅ Redis store created successfully")
                return store
            else:
                logger.error("❌ Unsupported store type: %s", store_type)
                raise ValueError(f"Unsupported store type: {store_type}")

        except Exception as e:
            logger.error("❌ Failed to create store: %s", str(e))
            logger.error("📋 Error details:")
            logger.error("   - Type: %s", type(e).__name__)
            logger.error("   - Message: %s", str(e))
            logger.exception("Detailed error stack:")
            raise ValueError(f"Failed to create store: {str(e)}") from e

    @staticmethod
    def _create_etcd3_store(
        serializer: Optional[Callable[[T], bytes]],
        deserializer: Optional[Callable[[bytes], T]],
        config: StoreConfig,
    ) -> Etcd3DistributedKeyValueStore[T]:
        """Create an ETCD3 store instance with validation."""
        logger.debug("🔄 Validating ETCD3 store requirements")

        if not serializer or not deserializer:
            logger.error("❌ Missing serializer or deserializer")
            logger.debug("📋 Validation details:")
            logger.debug("   - Serializer present: %s", serializer is not None)
            logger.debug("   - Deserializer present: %s", deserializer is not None)
            raise ValueError(
                "Serializer and deserializer functions must be provided for ETCD3 store."
            )

        # Validate serializer/deserializer types
        logger.debug("🔍 Checking serializer/deserializer types")
        if not callable(serializer) or not callable(deserializer):
            logger.error("❌ Invalid serializer or deserializer type")
            logger.debug("📋 Type details:")
            logger.debug("   - Serializer type: %s", type(serializer))
            logger.debug("   - Deserializer type: %s", type(deserializer))
            raise TypeError("Serializer and deserializer must be callable functions")

        logger.debug("🔄 Creating ETCD3 store instance")
        store = Etcd3DistributedKeyValueStore[T](
            serializer=serializer,
            deserializer=deserializer,
            host=config.host,
            port=config.port,
            timeout=config.timeout,
            ca_cert=config.ca_cert,
            cert_key=config.cert_key,
            cert_cert=config.cert_cert,
        )
        logger.debug("✅ ETCD3 store instance created successfully")
        return store

    @staticmethod
    def _create_in_memory_store() -> InMemoryKeyValueStore[T]:
        """Create an in-memory store instance."""
        logger.debug("🔄 Creating in-memory store instance")
        store = InMemoryKeyValueStore[T]()
        logger.debug("✅ In-memory store instance created successfully")
        return store

    @staticmethod
    def _create_redis_store(
        serializer: Optional[Callable[[T], bytes]],
        deserializer: Optional[Callable[[bytes], T]],
        config: StoreConfig,
    ) -> RedisDistributedKeyValueStore[T]:
        """Create a Redis store instance with validation."""
        logger.debug("🔄 Validating Redis store requirements")

        if not serializer or not deserializer:
            logger.error("❌ Missing serializer or deserializer")
            logger.debug("📋 Validation details:")
            logger.debug("   - Serializer present: %s", serializer is not None)
            logger.debug("   - Deserializer present: %s", deserializer is not None)
            raise ValueError(
                "Serializer and deserializer functions must be provided for Redis store."
            )

        # Validate serializer/deserializer types
        logger.debug("🔍 Checking serializer/deserializer types")
        if not callable(serializer) or not callable(deserializer):
            logger.error("❌ Invalid serializer or deserializer type")
            logger.debug("📋 Type details:")
            logger.debug("   - Serializer type: %s", type(serializer))
            logger.debug("   - Deserializer type: %s", type(deserializer))
            raise TypeError("Serializer and deserializer must be callable functions")

        logger.debug("🔄 Creating Redis store instance")
        store = RedisDistributedKeyValueStore[T](
            serializer=serializer,
            deserializer=deserializer,
            host=config.host,
            port=config.port,
            password=config.password,
            db=config.db,
            key_prefix=config.key_prefix,
            connect_timeout=config.timeout,
        )
        logger.debug("✅ Redis store instance created successfully")
        return store
