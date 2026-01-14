import asyncio
import json
from typing import Callable, Generic, List, Optional, TypeVar

import redis.asyncio as redis
from app.config.key_value_store import KeyValueStore
from app.utils.logger import create_logger

logger = create_logger("redis_store")

T = TypeVar("T")


class RedisDistributedKeyValueStore(KeyValueStore[T], Generic[T]):
    """
    Redis-based implementation of the distributed key-value store.

    This implementation provides a persistent key-value store using Redis
    as the backend, with support for TTL and prefix-based key operations.

    Attributes:
        client: Redis async client
        serializer: Function to convert values to bytes
        deserializer: Function to convert bytes back to values
        key_prefix: Prefix for all keys stored in Redis
    """

    def __init__(
        self,
        serializer: Callable[[T], bytes],
        deserializer: Callable[[bytes], T],
        host: str,
        port: int,
        password: Optional[str] = None,
        db: int = 0,
        key_prefix: str = "kv:",
        connect_timeout: float = 10.0,
    ) -> None:
        """
        Initialize the Redis store.

        Args:
            serializer: Function to convert values to bytes
            deserializer: Function to convert bytes back to values
            host: Redis server host
            port: Redis server port
            password: Optional password for authentication
            db: Redis database number
            key_prefix: Prefix for all keys (default: "kv:")
            connect_timeout: Connection timeout in seconds
        """
        logger.debug("Initializing Redis store")
        logger.debug("Configuration:")
        logger.debug("   - Host: %s", host)
        logger.debug("   - Port: %s", port)
        logger.debug("   - DB: %s", db)
        logger.debug("   - Key prefix: %s", key_prefix)

        self.serializer = serializer
        self.deserializer = deserializer
        self.key_prefix = key_prefix
        self._pubsub = None
        self._watch_tasks: dict[str, asyncio.Task] = {}
        self._watchers: dict[str, List[tuple[Callable[[Optional[T]], None], int]]] = {}

        # Build Redis connection URL
        if password:
            self.client = redis.Redis(
                host=host,
                port=port,
                password=password,
                db=db,
                socket_connect_timeout=connect_timeout,
                decode_responses=False,
            )
        else:
            self.client = redis.Redis(
                host=host,
                port=port,
                db=db,
                socket_connect_timeout=connect_timeout,
                decode_responses=False,
            )

        logger.debug("Redis store initialized")

    def _build_key(self, key: str) -> str:
        """Build the full Redis key with prefix."""
        return f"{self.key_prefix}{key}"

    def _strip_prefix(self, key: str) -> str:
        """Strip the prefix from a Redis key."""
        if key.startswith(self.key_prefix):
            return key[len(self.key_prefix):]
        return key

    async def create_key(
        self, key: str, value: T, overwrite: bool = True, ttl: Optional[int] = None
    ) -> bool:
        """Create a new key in Redis."""
        logger.debug("Creating key in Redis: %s", key)
        logger.debug("Value: %s (type: %s)", value, type(value))
        logger.debug("TTL: %s seconds", ttl if ttl else "None")

        try:
            full_key = self._build_key(key)
            serialized_value = self.serializer(value)

            if not overwrite:
                # Check if key exists
                existing = await self.client.exists(full_key)
                if existing:
                    logger.debug("Key exists, skipping creation")
                    return True

            if ttl:
                await self.client.set(full_key, serialized_value, ex=ttl)
            else:
                await self.client.set(full_key, serialized_value)

            logger.debug("Key created successfully")

            # Notify watchers
            await self._notify_watchers(key, value)

            return True

        except Exception as e:
            logger.error("Failed to create key %s: %s", key, str(e))
            raise ConnectionError(f"Failed to create key: {str(e)}")

    async def update_value(
        self, key: str, value: T, ttl: Optional[int] = None
    ) -> None:
        """Update the value for an existing key."""
        logger.debug("Updating key: %s", key)

        try:
            full_key = self._build_key(key)

            # Check if key exists
            existing = await self.client.exists(full_key)
            if not existing:
                raise KeyError(f'Key "{key}" does not exist.')

            serialized_value = self.serializer(value)

            if ttl:
                await self.client.set(full_key, serialized_value, ex=ttl)
            else:
                await self.client.set(full_key, serialized_value)

            logger.debug("Key updated successfully")

            # Notify watchers
            await self._notify_watchers(key, value)

        except KeyError:
            raise
        except Exception as e:
            logger.error("Failed to update key %s: %s", key, str(e))
            raise ConnectionError(f"Failed to update key: {str(e)}")

    async def get_key(self, key: str) -> Optional[T]:
        """Get value for key from Redis."""
        logger.debug("Getting key from Redis: %s", key)

        try:
            full_key = self._build_key(key)
            value_bytes = await self.client.get(full_key)

            if value_bytes is None:
                logger.debug("No value found for key")
                return None

            try:
                deserialized = self.deserializer(value_bytes)
                return deserialized
            except json.JSONDecodeError as e:
                logger.error("Failed to deserialize value: %s", str(e))
                return None

        except Exception as e:
            logger.error("Failed to get key %s: %s", key, str(e))
            raise ConnectionError(f"Failed to get key: {str(e)}")

    async def delete_key(self, key: str) -> bool:
        """Delete a key from Redis."""
        logger.debug("Deleting key: %s", key)

        try:
            full_key = self._build_key(key)
            result = await self.client.delete(full_key)

            if result > 0:
                # Notify watchers about deletion
                await self._notify_watchers(key, None)

            return result > 0

        except Exception as e:
            logger.error("Failed to delete key %s: %s", key, str(e))
            raise ConnectionError(f"Failed to delete key: {str(e)}")

    async def get_all_keys(self) -> List[str]:
        """Get all keys from Redis with the configured prefix."""
        logger.debug("Getting all keys from Redis")

        try:
            pattern = f"{self.key_prefix}*"
            keys = []

            async for key in self.client.scan_iter(match=pattern):
                # Decode if bytes
                if isinstance(key, bytes):
                    key = key.decode("utf-8")
                keys.append(self._strip_prefix(key))

            logger.debug("Found %d keys: %s", len(keys), keys)
            return keys

        except Exception as e:
            logger.error("Failed to get all keys: %s", str(e))
            raise ConnectionError(f"Failed to get all keys: {str(e)}")

    async def _notify_watchers(self, key: str, value: Optional[T]) -> None:
        """Notify all watchers of a key about value changes."""
        if key in self._watchers:
            for callback, watch_id in self._watchers[key]:
                try:
                    callback(value)
                except Exception as e:
                    logger.error("Error in watcher callback: %s", str(e))

    async def watch_key(
        self,
        key: str,
        callback: Callable[[Optional[T]], None],
        error_callback: Optional[Callable[[Exception], None]] = None,
    ) -> int:
        """
        Watch a key for changes and execute callbacks when changes occur.

        Note: Redis doesn't have native watch support like etcd, so this
        implementation uses in-memory callbacks that are triggered on
        create/update/delete operations through this store instance.
        For cross-process notifications, consider using Redis Pub/Sub.
        """
        logger.debug("Setting up watch for key: %s", key)
        watch_id = id(callback)

        if key not in self._watchers:
            self._watchers[key] = []

        self._watchers[key].append((callback, watch_id))
        logger.debug("Watch setup complete. ID: %s", watch_id)

        return watch_id

    async def cancel_watch(self, key: str, watch_id: str) -> None:
        """Cancel a watch for a key."""
        logger.debug("Canceling watch for key: %s, watch_id: %s", key, watch_id)

        if key in self._watchers:
            self._watchers[key] = [
                (cb, wid) for cb, wid in self._watchers[key] if wid != watch_id
            ]
            if not self._watchers[key]:
                del self._watchers[key]
            logger.debug("Watch canceled successfully")

    async def list_keys_in_directory(self, directory: str) -> List[str]:
        """List all keys under a specific directory prefix."""
        logger.debug("Listing keys in directory: %s", directory)

        try:
            # Ensure directory ends with appropriate separator
            prefix = directory if directory.endswith("/") else f"{directory}/"
            pattern = f"{self.key_prefix}{prefix}*"

            keys = []
            async for key in self.client.scan_iter(match=pattern):
                if isinstance(key, bytes):
                    key = key.decode("utf-8")
                keys.append(self._strip_prefix(key))

            logger.debug("Found %d keys in directory", len(keys))
            return keys

        except Exception as e:
            logger.error("Failed to list keys in directory: %s", str(e))
            raise ConnectionError(f"Failed to list keys in directory: {str(e)}")

    async def close(self) -> None:
        """Clean up resources and close connection."""
        logger.debug("Closing Redis store")

        # Cancel all watch tasks
        for task in self._watch_tasks.values():
            task.cancel()
        self._watch_tasks.clear()
        self._watchers.clear()

        # Close Redis connection
        await self.client.close()
        logger.debug("Redis store closed successfully")
