import asyncio
import hashlib
import json
import os
from typing import Callable, Dict, Generic, List, Optional, TypeVar, Union

import dotenv  # type: ignore

from app.config.constants.service import config_node_constants
from app.config.constants.store_type import StoreType
from app.config.key_value_store import KeyValueStore
from app.config.key_value_store_factory import KeyValueStoreFactory, StoreConfig
from app.utils.encryption.encryption_service import EncryptionService

dotenv.load_dotenv()

T = TypeVar("T")


class EncryptedKeyValueStore(KeyValueStore[T], Generic[T]):
    """
    Configurable encrypted key-value store that supports multiple backends (Redis, etcd).

    The backend is determined by the KV_STORE_TYPE environment variable:
    - 'redis': Uses Redis as the backend
    - 'etcd': Uses etcd as the backend (default)
    """

    def __init__(
        self,
        logger,
    ) -> None:
        self.logger = logger

        self.logger.debug("Initializing EncryptedKeyValueStore")

        # Get and hash the secret key to ensure 32 bytes
        secret_key = os.getenv("SECRET_KEY")
        if not secret_key:
            raise ValueError("SECRET_KEY environment variable is required")

        # Hash the secret key to get exactly 32 bytes and convert to hex
        hashed_key = hashlib.sha256(secret_key.encode()).digest()
        hex_key = hashed_key.hex()
        self.logger.debug("Secret key hashed to 32 bytes and converted to hex")

        self.encryption_service = EncryptionService.get_instance(
            "aes-256-gcm", hex_key, logger
        )
        self.logger.debug("Initialized EncryptionService")

        # Determine store type from environment
        store_type_str = os.getenv("KV_STORE_TYPE", "etcd").lower()
        self.logger.debug("KV_STORE_TYPE: %s", store_type_str)

        self.logger.debug("Creating key-value store...")
        self.store = self._create_store(store_type_str)

        self.logger.debug("KeyValueStore initialized successfully")

    @property
    def client(self) -> object:
        """Expose the underlying client for watchers and diagnostics."""
        return self.store.client

    def _create_store(self, store_type_str: str) -> KeyValueStore:
        """Create the appropriate key-value store based on configuration."""

        def serialize(value: Union[str, int, float, bool, Dict, list, None]) -> bytes:
            if value is None:
                return b""
            if isinstance(value, (str, int, float, bool)):
                return json.dumps(value).encode("utf-8")
            return json.dumps(value, default=str).encode("utf-8")

        def deserialize(value: bytes) -> Union[str, int, float, bool, dict, list, None]:
            if not value:
                return None
            try:
                decoded = value.decode("utf-8")
                try:
                    return json.loads(decoded)
                except json.JSONDecodeError:
                    return decoded
            except UnicodeDecodeError as e:
                self.logger.error("Failed to decode bytes: %s", str(e))
                return None

        if store_type_str == "redis":
            return self._create_redis_store(serialize, deserialize)
        else:
            return self._create_etcd_store(serialize, deserialize)

    def _create_redis_store(self, serialize, deserialize) -> KeyValueStore:
        """Create a Redis-backed key-value store."""
        self.logger.debug("Creating Redis store configuration...")

        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_port = int(os.getenv("REDIS_PORT", "6379"))
        redis_password = os.getenv("REDIS_PASSWORD", None)
        redis_db = int(os.getenv("REDIS_DB", "0"))
        redis_key_prefix = os.getenv("REDIS_KV_PREFIX", "pipeshub:kv:")

        self.logger.debug("Redis Host: %s", redis_host)
        self.logger.debug("Redis Port: %s", redis_port)
        self.logger.debug("Redis DB: %s", redis_db)
        self.logger.debug("Redis Key Prefix: %s", redis_key_prefix)

        config = StoreConfig(
            host=redis_host,
            port=redis_port,
            password=redis_password,
            db=redis_db,
            key_prefix=redis_key_prefix,
            # REDIS_TIMEOUT is in milliseconds (consistent with Node.js), convert to seconds
            timeout=float(os.getenv("REDIS_TIMEOUT", "10000")) / 1000,
        )

        store = KeyValueStoreFactory.create_store(
            store_type=StoreType.REDIS,
            serializer=serialize,
            deserializer=deserialize,
            config=config,
        )
        self.logger.info("Redis store created successfully")
        return store

    def _create_etcd_store(self, serialize, deserialize) -> KeyValueStore:
        """Create an etcd-backed key-value store."""
        self.logger.debug("Creating ETCD store configuration...")

        etcd_url = os.getenv("ETCD_URL")
        if not etcd_url:
            raise ValueError("ETCD_URL environment variable is required")

        self.logger.debug("ETCD URL: %s", etcd_url)
        self.logger.debug("ETCD Timeout: %s", os.getenv("ETCD_TIMEOUT", "5.0"))

        # Remove protocol if present
        if "://" in etcd_url:
            etcd_url = etcd_url.split("://")[1]

        # Split host and port
        parts = etcd_url.split(":")
        etcd_host = parts[0]
        etcd_port = parts[1] if len(parts) > 1 else "2379"

        config = StoreConfig(
            host=etcd_host,
            port=int(etcd_port),
            # ETCD_TIMEOUT is in milliseconds (consistent with Node.js), convert to seconds
            timeout=float(os.getenv("ETCD_TIMEOUT", "5000")) / 1000,
            username=os.getenv("ETCD_USERNAME", None),
            password=os.getenv("ETCD_PASSWORD", None),
        )

        store = KeyValueStoreFactory.create_store(
            store_type=StoreType.ETCD3,
            serializer=serialize,
            deserializer=deserialize,
            config=config,
        )
        self.logger.debug("ETCD store created successfully")
        return store

    async def create_key(
        self, key: str, value: T, overwrite: bool = True, ttl: Optional[int] = None
    ) -> bool:
        """Create a new key with optional encryption."""
        try:
            # Check if key exists
            existing_value = await self.store.get_key(key)
            if existing_value is not None and not overwrite:
                self.logger.debug("Skipping existing key: %s", key)
                return False  # Key was not created (already exists)

            # Convert value to JSON string
            value_json = json.dumps(value)

            EXCLUDED_KEYS = [
                config_node_constants.ENDPOINTS.value,
                config_node_constants.STORAGE.value,
                config_node_constants.MIGRATIONS.value,
            ]
            encrypt_value = key not in EXCLUDED_KEYS

            if encrypt_value:
                # Encrypt the value
                encrypted_value = self.encryption_service.encrypt(value_json)
            else:
                encrypted_value = value_json

            self.logger.debug("Encrypted value for key %s", key)

            # Store the encrypted value
            success = await self.store.create_key(key, encrypted_value, overwrite, ttl)
            if success:
                self.logger.debug("Successfully stored encrypted key: %s", key)

                # Verify the stored value
                encrypted_stored_value = await self.store.get_key(key)
                if encrypted_stored_value:
                    if encrypt_value:
                        decrypted_value = self.encryption_service.decrypt(
                            encrypted_stored_value
                        )
                    else:
                        decrypted_value = encrypted_stored_value
                    # decrypted_value is always a JSON string, parse it
                    stored_value = json.loads(decrypted_value)

                    if stored_value != value:
                        self.logger.warning("Verification failed for key: %s", key)
                        return False

                return True
            else:
                self.logger.error("Failed to store key: %s", key)
                return False

        except Exception as e:
            self.logger.error(
                "Failed to store config value for key %s: %s", key, str(e)
            )
            self.logger.exception("Detailed error:")
            return False

    async def update_value(
        self, key: str, value: T, ttl: Optional[int] = None
    ) -> None:
        return await self.create_key(key, value, True, ttl)

    async def get_key(self, key: str) -> Optional[T]:
        try:
            encrypted_value = await self.store.get_key(key)

            if encrypted_value is not None:
                try:
                    # Determine if value needs decryption
                    UNENCRYPTED_KEYS = [
                        config_node_constants.ENDPOINTS.value,
                        config_node_constants.STORAGE.value,
                        config_node_constants.MIGRATIONS.value,
                    ]
                    needs_decryption = key not in UNENCRYPTED_KEYS

                    # Get decrypted or raw value
                    value = (
                        self.encryption_service.decrypt(encrypted_value)
                        if needs_decryption
                        else encrypted_value
                    )

                    # Parse value if it's not already a dict
                    result = (
                        json.loads(value) if not isinstance(value, dict) else value
                    )

                    return result

                except Exception as e:
                    self.logger.error(
                        f"Failed to process value for key {key}: {str(e)}"
                    )
                    return None
            else:
                self.logger.debug(f"No value found for key: {key}")
                return None

        except Exception as e:
            self.logger.error("Failed to get config %s: %s", key, str(e))
            self.logger.exception("Detailed error:")
            return None

    async def delete_key(self, key: str) -> bool:
        return await self.store.delete_key(key)

    async def get_all_keys(self) -> List[str]:
        return await self.store.get_all_keys()

    async def watch_key(
        self,
        key: str,
        callback: Callable[[Optional[T]], None],
        error_callback: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        return await self.store.watch_key(key, callback, error_callback)

    async def list_keys_in_directory(self, directory: str) -> List[str]:
        return await self.store.list_keys_in_directory(directory)

    async def cancel_watch(self, key: str, watch_id: str) -> None:
        return await self.store.cancel_watch(key, watch_id)

    async def close(self) -> None:
        """Clean up resources and close connection."""
        await self.store.close()

    # -------------------------------------------------------------------------
    # Pub/Sub methods for cache invalidation (delegated to underlying store)
    # -------------------------------------------------------------------------

    async def publish_cache_invalidation(self, key: str) -> None:
        """Publish a cache invalidation message for the given key.

        Only works when using Redis as the backend.
        """
        if hasattr(self.store, 'publish_cache_invalidation'):
            await self.store.publish_cache_invalidation(key)
        else:
            self.logger.debug(
                "Underlying store doesn't support publish_cache_invalidation"
            )

    async def subscribe_cache_invalidation(
        self, callback: Callable[[str], None]
    ) -> asyncio.Task:
        """Subscribe to cache invalidation messages.

        Only works when using Redis as the backend.

        Args:
            callback: Function to call with the invalidated key when a message is received.

        Returns:
            The subscription task that can be cancelled to stop listening.
        """
        if hasattr(self.store, 'subscribe_cache_invalidation'):
            return await self.store.subscribe_cache_invalidation(callback)
        else:
            self.logger.debug(
                "Underlying store doesn't support subscribe_cache_invalidation"
            )
            # Return a no-op task for stores that don't support Pub/Sub
            async def noop()  -> None:
                pass
            return asyncio.create_task(noop())
