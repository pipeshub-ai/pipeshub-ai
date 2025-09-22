import asyncio
import json
from typing import Any, Callable, Generic, List, Optional, TypeVar

import etcd3

from app.config.key_value_store import KeyValueStore
from app.config.providers.etcd.etcd3_connection_manager import (
    ConnectionConfig,
    Etcd3ConnectionManager,
)
from app.utils.logger import create_logger

logger = create_logger("etcd")

T = TypeVar("T")


class Etcd3DistributedKeyValueStore(KeyValueStore[T], Generic[T]):
    """
    ETCD3-based implementation of the distributed key-value store.

    This implementation provides a robust, distributed key-value store using ETCD3
    as the backend, with support for watching keys, TTL, and automatic reconnection.

    Attributes:
        connection_manager: Manages ETCD3 connection and reconnection
        serializer: Function to convert values to bytes
        deserializer: Function to convert bytes back to values
    """

    def __init__(
        self,
        serializer: Callable[[T], bytes],
        deserializer: Callable[[bytes], T],
        host: str,
        port: int,
        timeout: float = 5.0,
        ca_cert: Optional[str] = None,
        cert_key: Optional[str] = None,
        cert_cert: Optional[str] = None,
    ) -> None:
        """
        Initialize the ETCD3 store.

        Args:
            serializer: Function to convert values to bytes
            deserializer: Function to convert bytes back to values
            host: ETCD server host
            port: ETCD server port
            timeout: Connection timeout in seconds
            username: Optional username for authentication
            password: Optional password for authentication
            ca_cert: Optional CA certificate path for TLS
            cert_key: Optional client key path for TLS
            cert_cert: Optional client certificate path for TLS
        """
        logger.debug("🔧 Initializing ETCD3 store")
        logger.debug("📋 Configuration:")
        logger.debug("   - Host: %s", host)
        logger.debug("   - Port: %s", port)
        logger.debug("   - Timeout: %s", timeout)
        logger.debug("   - SSL enabled: %s", bool(ca_cert or cert_key))

        config = ConnectionConfig(
            hosts=[host],
            port=port,
            timeout=timeout,
            ca_cert=ca_cert,
            cert_key=cert_key,
            cert_cert=cert_cert,
        )
        self.client = None
        self.connection_manager = Etcd3ConnectionManager(config)
        self.serializer = serializer
        self.deserializer = deserializer
        self._active_watchers: List[Any] = []
        logger.debug("✅ ETCD3 store initialized")

    async def _get_client(self) -> etcd3.client:
        """Get the ETCD client, ensuring connection is available."""
        logger.debug("🔄 Getting ETCD client")
        client = await self.connection_manager.get_client()
        logger.debug("✅ Got ETCD client: %s", client)
        self.client = client
        return client

    async def create_key(self, key: str, value: T, overwrite: bool = True, ttl: Optional[int] = None) -> bool:
        """Create a new key in etcd."""
        logger.debug("🔄 Creating key in ETCD: %s", key)
        logger.debug("📋 Value: %s (type: %s)", value, type(value))
        logger.debug("📋 TTL: %s seconds", ttl if ttl else "None")

        try:
            client = await self._get_client()

            # Convert value to string if it's not already
            value_str = str(value) if not isinstance(value, str) else value
            logger.debug("📋 Serialized value: %s", value_str)

            # Check if key exists
            logger.debug("🔍 Checking if key exists")
            existing_value = await asyncio.to_thread(lambda: client.get(key))

            if existing_value[0] is not None and not overwrite:
                logger.debug("📋 Key exists, skipping creation")
                return True
            elif existing_value[0] is not None:
                logger.debug("📋 Key exists, updating value")
                success = await asyncio.to_thread(
                    lambda: client.put(key, value_str.encode())
                )
            else:
                logger.debug("📋 Key doesn't exist, creating new")
                if ttl:
                    logger.debug("🔄 Creating lease with TTL: %s seconds", ttl)
                    lease = await asyncio.to_thread(lambda: client.lease(ttl))
                    success = await asyncio.to_thread(
                        lambda: client.put(key, value_str.encode(), lease=lease)
                    )
                else:
                    success = await asyncio.to_thread(
                        lambda: client.put(key, value_str.encode())
                    )

            logger.debug("✅ Key operation successful: %s", success is not None)
            return success is not None

        except Exception as e:
            logger.error("❌ Failed to create key %s: %s", key, str(e))
            logger.error("📋 Error details:")
            logger.error("   - Type: %s", type(e).__name__)
            logger.error("   - Message: %s", str(e))
            logger.exception("Detailed error stack:")
            raise ConnectionError(f"Failed to create key: {str(e)}")

    async def update_value(self, key: str, value: T, ttl: Optional[int] = None) -> None:
        client = await self._get_client()

        # Check if key exists
        existing_value = await client.get(key)
        if existing_value[0] is None:
            raise KeyError(f'Key "{key}" does not exist.')

        # Create lease if TTL is specified
        lease = None
        if ttl is not None:
            lease = client.lease(ttl)

        # Update value with optional lease
        try:
            serialized_value = self.serializer(value)
            if lease:
                await client.put(key, serialized_value, lease=lease)
            else:
                await client.put(key, serialized_value)
        except Exception as e:
            if lease:
                await lease.revoke()
            raise ConnectionError(f"Failed to update key: {str(e)}")

    async def get_key(self, key: str) -> Optional[T]:
        """Get value for key from etcd."""
        logger.debug("🔍 Getting key from ETCD: %s", key)
        try:
            client = await self._get_client()
            logger.debug("🔄 Executing get operation")
            result = await asyncio.to_thread(lambda: client.get(key))

            if result[0] is None:
                logger.debug("⚠️ No value found for key")
                return None

            value_bytes = result[0]
            if not value_bytes:
                logger.debug("⚠️ Empty value found for key")
                return None

            try:
                deserialized = self.deserializer(value_bytes)
                return deserialized
            except json.JSONDecodeError as e:
                logger.error("❌ Failed to deserialize value: %s", str(e))
                logger.error("📋 Value that failed: %s", value_bytes)
                return None

        except Exception as e:
            logger.error("❌ Failed to get key %s: %s", key, str(e))
            logger.error("📋 Error details:")
            logger.error("   - Type: %s", type(e).__name__)
            logger.error("   - Message: %s", str(e))
            logger.exception("Detailed error stack:")
            raise ConnectionError(f"Failed to get key: {str(e)}")

    async def delete_key(self, key: str) -> bool:
        client = await self._get_client()
        try:
            result = await client.delete(key)
            return result is not None
        except Exception as e:
            raise ConnectionError(f"Failed to delete key: {str(e)}")

    async def get_all_keys(self) -> List[str]:
        """Get all keys from etcd."""
        logger.debug("🔍 Getting all keys from ETCD")
        try:
            client = await self._get_client()
            logger.debug("🔄 Executing get_all operation")
            keys = await asyncio.to_thread(lambda: list(client.get_all()))
            decoded_keys = [key[1].key.decode("utf-8") for key in keys]
            logger.debug("✅ Found %d keys: %s", len(decoded_keys), decoded_keys)
            return decoded_keys
        except Exception as e:
            logger.error("❌ Failed to get all keys: %s", str(e))
            logger.error("📋 Error details:")
            logger.error("   - Type: %s", type(e).__name__)
            logger.error("   - Message: %s", str(e))
            logger.exception("Detailed error stack:")
            raise ConnectionError(f"Failed to get all keys: {str(e)}")

    async def watch_key(
        self,
        key: str,
        callback: Callable[[Optional[T]], None],
        error_callback: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        logger.debug("🔄 Setting up watch for key: %s", key)
        client = await self._get_client()

        def watch_callback(event) -> None:
            logger.debug("📋 Watch event received for key: %s", key)
            logger.debug("   - Event type: %s", event.type)
            logger.debug("   - Event value: %s", event.value)
            try:
                if event.type == "PUT":
                    value = self.deserializer(event.value)
                    logger.debug("🔄 Executing callback with value: %s", value)
                    callback(value)
                elif event.type == "DELETE":
                    logger.debug("🔄 Executing callback for deletion")
                    callback(None)
                logger.debug("✅ Watch callback completed successfully")
            except Exception as e:
                logger.error("❌ Error in watch callback: %s", str(e))
                if error_callback:
                    logger.debug("🔄 Executing error callback")
                    error_callback(e)

        try:
            logger.debug("🔄 Adding watch callback")
            watch_id = await client.add_watch_callback(key, watch_callback)
            self._active_watchers.append(watch_id)
            logger.debug("✅ Watch setup complete. ID: %s", watch_id)
            return watch_id
        except Exception as e:
            logger.error("❌ Failed to setup watch: %s", str(e))
            logger.exception("Detailed error stack:")
            raise ConnectionError(f"Failed to watch key: {str(e)}")

    async def list_keys_in_directory(self, directory: str) -> List[str]:
        client = await self._get_client()
        try:
            # Ensure directory ends with '/' for proper prefix matching
            prefix = directory if directory.endswith("/") else f"{directory}/"
            return [key.decode("utf-8") for key, _ in await client.get_prefix(prefix)]
        except Exception as e:
            raise ConnectionError(f"Failed to list keys in directory: {str(e)}")

    async def cancel_watch(self, key: str, watch_id: str) -> None:
        client = await self._get_client()
        await client.cancel_watch(watch_id)

    async def close(self) -> None:
        """Clean up resources and close connection."""
        logger.debug("🔄 Closing ETCD3 store")
        logger.debug("📋 Active watchers: %d", len(self._active_watchers))

        for watch_id in self._active_watchers:
            try:
                logger.debug("🔄 Canceling watch: %s", watch_id)
                client = await self.connection_manager.get_client()
                await client.cancel_watch(watch_id)
                logger.debug("✅ Watch canceled successfully")
            except Exception as e:
                logger.warning("⚠️ Failed to cancel watch %s: %s", watch_id, str(e))

        self._active_watchers.clear()
        logger.debug("🔄 Closing connection manager")
        await self.connection_manager.close()
        logger.debug("✅ ETCD3 store closed successfully")
