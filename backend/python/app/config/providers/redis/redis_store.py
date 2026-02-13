import asyncio
import json
import random
import threading
import uuid
from typing import Callable, Dict, Generic, List, Optional, TypeVar

import redis.asyncio as redis  # type: ignore
from redis.asyncio.retry import Retry  # type: ignore
from redis.backoff import ExponentialBackoff  # type: ignore
from redis.exceptions import ConnectionError as RedisConnectionError  # type: ignore
from redis.exceptions import TimeoutError as RedisTimeoutError  # type: ignore

from app.config.key_value_store import KeyValueStore
from app.utils.logger import create_logger

logger = create_logger("redis_store")

T = TypeVar("T")

# Retry configuration
MAX_RETRIES = 5
RETRY_BASE_DELAY = 0.5  # seconds
RETRY_MAX_DELAY = 30.0  # seconds


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
        key_prefix: str = "pipeshub:kv:",
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
            key_prefix: Prefix for all keys (default: "pipeshub:kv:")
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
        self._pubsub_task: Optional[asyncio.Task] = None
        self._pubsub_callback: Optional[Callable[[str], None]] = None
        self._watch_tasks: dict[str, asyncio.Task] = {}
        self._watchers: dict[str, List[tuple[Callable[[Optional[T]], None], str]]] = {}
        self._is_closing = False

        # Store connection parameters for reconnection and lazy client creation
        self._host = host
        self._port = port
        self._password = password
        self._db = db
        self._connect_timeout = connect_timeout

        # Thread-local storage for Redis clients (one per event loop/thread)
        self._local = threading.local()
        self._clients: Dict[int, redis.Redis] = {}
        self._clients_lock = threading.Lock()

        logger.debug("Redis store initialized with lazy client creation")

    def _get_client(self) -> redis.Redis:
        """Get or create a Redis client for the current thread/event loop.

        This ensures each event loop has its own Redis client to avoid
        'Future attached to a different loop' errors.
        """
        # Use thread ID as key since each thread typically has its own event loop
        thread_id = threading.get_ident()

        with self._clients_lock:
            if thread_id not in self._clients:
                logger.debug("Creating new Redis client for thread %s", thread_id)

                # Configure retry with exponential backoff
                retry = Retry(
                    ExponentialBackoff(cap=RETRY_MAX_DELAY, base=RETRY_BASE_DELAY),
                    retries=MAX_RETRIES,
                )

                # Create a new Redis client for this thread/event loop
                client = redis.Redis(
                    host=self._host,
                    port=self._port,
                    password=self._password,
                    db=self._db,
                    socket_connect_timeout=self._connect_timeout,
                    socket_timeout=self._connect_timeout,
                    decode_responses=False,
                    retry=retry,
                    retry_on_error=[RedisConnectionError, RedisTimeoutError, ConnectionError, OSError],
                    health_check_interval=30,
                    single_connection_client=True,
                )
                self._clients[thread_id] = client

            return self._clients[thread_id]

    @property
    def client(self) -> Optional[redis.Redis]:
        """Expose the underlying Redis client for watchers and diagnostics."""
        return self._get_client()

    def _build_key(self, key: str) -> str:
        """Build the full Redis key with prefix."""
        return f"{self.key_prefix}{key}"

    def _strip_prefix(self, key: str) -> str:
        """Strip the prefix from a Redis key."""
        if key.startswith(self.key_prefix):
            return key[len(self.key_prefix):]
        return key

    async def health_check(self) -> bool:
        """
        Check if the Redis connection is healthy.

        Returns:
            True if connection is healthy, False otherwise.
        """
        try:
            result = await self._get_client().ping()
            return result is True
        except Exception as e:
            logger.error("Redis health check failed: %s", str(e))
            return False

    async def wait_for_connection(self, timeout: float = 60.0) -> bool:
        """
        Wait for Redis connection to be established with retries.

        Args:
            timeout: Maximum time to wait for connection in seconds.

        Returns:
            True if connection was established, False if timeout reached.
        """
        start_time = asyncio.get_event_loop().time()
        retry_count = 0
        base_delay = RETRY_BASE_DELAY

        while (asyncio.get_event_loop().time() - start_time) < timeout:
            try:
                result = await self._get_client().ping()
                if result is True:
                    logger.info("Redis connection established successfully")
                    return True
            except Exception as e:
                retry_count += 1
                delay = min(base_delay * (2 ** (retry_count - 1)), RETRY_MAX_DELAY)
                remaining = timeout - (asyncio.get_event_loop().time() - start_time)

                if remaining <= 0:
                    break

                logger.warning(
                    "Redis connection attempt %d failed: %s. Retrying in %.1f seconds...",
                    retry_count,
                    str(e),
                    min(delay, remaining),
                )
                await asyncio.sleep(min(delay, remaining))

        logger.error("Failed to establish Redis connection within %.1f seconds", timeout)
        return False

    async def create_key(
        self, key: str, value: T, overwrite: bool = True, ttl: Optional[int] = None
    ) -> bool:
        """Create a new key in Redis."""
        full_key = self._build_key(key)
        logger.debug("Creating key in Redis: %s (original: %s)", full_key, key)

        try:
            serialized_value = self.serializer(value)

            if overwrite:
                if ttl:
                    await self._get_client().set(full_key, serialized_value, ex=ttl)
                else:
                    await self._get_client().set(full_key, serialized_value)
            else:
                # Use nx=True for atomic "set if not exists"
                was_set = await self._get_client().set(
                    full_key, serialized_value, ex=ttl, nx=True
                )
                if not was_set:
                    logger.debug(
                        "Key '%s' already exists, skipping creation as overwrite is False.", key
                    )
                    return False  # Key was not created (already exists)

            logger.debug("Key created successfully")

            # Notify watchers
            await self._notify_watchers(key, value)

            # Auto-publish cache invalidation so that any ConfigurationService
            # (in this or other processes) invalidates its LRU cache.
            await self.publish_cache_invalidation(key)

            return True

        except Exception as e:
            logger.error("Failed to create key %s: %s", key, str(e))
            raise ConnectionError(f"Failed to create key: {str(e)}")

    async def update_value(
        self, key: str, value: T, ttl: Optional[int] = None
    ) -> None:
        """Update the value for an existing key."""
        full_key = self._build_key(key)
        logger.debug("Updating key: %s (original: %s)", full_key, key)

        try:
            serialized_value = self.serializer(value)

            # Use xx=True for atomic "set if exists"
            result = await self._get_client().set(
                full_key, serialized_value, ex=ttl, xx=True
            )

            if not result:
                raise KeyError(f'Key "{key}" does not exist.')

            logger.debug("Key updated successfully")

            # Notify watchers
            await self._notify_watchers(key, value)

            # Auto-publish cache invalidation
            await self.publish_cache_invalidation(key)

        except KeyError:
            raise
        except Exception as e:
            logger.error("Failed to update key %s: %s", key, str(e))
            raise ConnectionError(f"Failed to update key: {str(e)}")

    async def get_key(self, key: str) -> Optional[T]:
        """Get value for key from Redis."""
        full_key = self._build_key(key)
        logger.debug("Getting key from Redis: %s (original: %s)", full_key, key)

        try:
            value_bytes = await self._get_client().get(full_key)

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
        full_key = self._build_key(key)
        logger.debug("Deleting key: %s (original: %s)", full_key, key)

        try:
            result = await self._get_client().delete(full_key)

            if result > 0:
                # Notify watchers about deletion
                await self._notify_watchers(key, None)

                # Auto-publish cache invalidation
                await self.publish_cache_invalidation(key)

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

            async for key in self._get_client().scan_iter(match=pattern):
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
        watch_id: Optional[str] = None,
    ) -> str:
        """
        Watch a key for changes and execute callbacks when changes occur.

        Note: Redis doesn't have native watch support like etcd, so this
        implementation uses in-memory callbacks that are triggered on
        create/update/delete operations through this store instance.
        For cross-process notifications, consider using Redis Pub/Sub.

        Args:
            key: The key to watch.
            callback: Function to call when the value changes.
            error_callback: Optional function to call when errors occur.
            watch_id: Optional unique identifier for this watch. If not provided,
                a UUID is generated. Callers may pass a custom ID for consistency
                across restarts or when registering the same callback multiple times.
        """
        logger.debug("Setting up watch for key: %s", key)
        resolved_watch_id = watch_id if watch_id is not None else uuid.uuid4().hex

        if key not in self._watchers:
            self._watchers[key] = []

        self._watchers[key].append((callback, resolved_watch_id))
        logger.debug("Watch setup complete. ID: %s", resolved_watch_id)

        return resolved_watch_id

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
        # Ensure directory ends with appropriate separator
        prefix = directory if directory.endswith("/") else f"{directory}/"
        pattern = f"{self.key_prefix}{prefix}*"
        logger.debug("Listing keys in directory: %s (pattern: %s)", directory, pattern)

        try:

            keys = []
            async for key in self._get_client().scan_iter(match=pattern):
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

        # Set closing flag to stop reconnection attempts
        self._is_closing = True

        # Cancel Pub/Sub task (may be on a different event loop if started from a background thread)
        if self._pubsub_task and not self._pubsub_task.done():
            self._pubsub_task.cancel()
            try:
                await self._pubsub_task
            except (asyncio.CancelledError, RuntimeError):
                # RuntimeError if the task belongs to a different event loop
                pass
        self._pubsub_task = None
        self._pubsub_callback = None

        # Cancel all watch tasks
        for task in self._watch_tasks.values():
            task.cancel()
        self._watch_tasks.clear()
        self._watchers.clear()

        # Close all Redis connections (one per thread/event loop)
        with self._clients_lock:
            for thread_id, client in self._clients.items():
                try:
                    await client.close()
                    logger.debug("Closed Redis client for thread %s", thread_id)
                except Exception as e:
                    logger.warning("Error closing Redis client for thread %s: %s", thread_id, str(e))
            self._clients.clear()
        logger.debug("Redis store closed successfully")

    # -------------------------------------------------------------------------
    # Pub/Sub methods for cache invalidation across processes
    # -------------------------------------------------------------------------

    CACHE_INVALIDATION_CHANNEL = "pipeshub:cache:invalidate"

    async def publish_cache_invalidation(self, key: str) -> None:
        """Publish a cache invalidation message for the given key."""
        retry_count = 0
        max_retries = 3
        base_delay = 0.1

        while retry_count < max_retries:
            try:
                await self._get_client().publish(self.CACHE_INVALIDATION_CHANNEL, key)
                logger.info("Published cache invalidation for key: %s", key)
                return
            except Exception as e:
                retry_count += 1
                if retry_count >= max_retries:
                    logger.error(
                        "Failed to publish cache invalidation for key %s after %d attempts: %s",
                        key,
                        max_retries,
                        str(e),
                    )
                    return
                delay = base_delay * (2 ** (retry_count - 1))
                logger.warning(
                    "Publish cache invalidation failed for key %s, retrying in %.1fs: %s",
                    key,
                    delay,
                    str(e),
                )
                await asyncio.sleep(delay)

    async def subscribe_cache_invalidation(
        self, callback: Callable[[str], None]
    ) -> asyncio.Task:
        """
        Subscribe to cache invalidation messages with automatic reconnection.

        Args:
            callback: Function to call with the invalidated key when a message is received.

        Returns:
            The subscription task that can be cancelled to stop listening.
        """
        logger.debug("Setting up cache invalidation subscription")

        # Store callback for potential reconnection
        self._pubsub_callback = callback

        async def _listen_with_retry() -> None:
            retry_count = 0
            max_retry_delay = RETRY_MAX_DELAY
            base_delay = RETRY_BASE_DELAY

            while not self._is_closing:
                pubsub = None
                try:
                    pubsub = self._get_client().pubsub()
                    await pubsub.subscribe(self.CACHE_INVALIDATION_CHANNEL)
                    logger.info("Subscribed to cache invalidation channel")
                    retry_count = 0  # Reset retry count on successful connection

                    async for message in pubsub.listen():
                        if self._is_closing:
                            break
                        if message["type"] == "message":
                            key = message["data"]
                            if isinstance(key, bytes):
                                key = key.decode("utf-8")
                            logger.debug("Received cache invalidation for key: %s", key)
                            try:
                                callback(key)
                            except Exception as e:
                                logger.error("Error in cache invalidation callback: %s", str(e))

                except asyncio.CancelledError:
                    logger.debug("Cache invalidation subscription cancelled")
                    break
                except Exception as e:
                    if self._is_closing:
                        break

                    retry_count += 1
                    # Calculate delay with exponential backoff and jitter
                    delay = min(base_delay * (2 ** (retry_count - 1)), max_retry_delay)
                    # Add some jitter to prevent thundering herd
                    delay = delay * (0.5 + random.random())

                    logger.warning(
                        "Redis Pub/Sub connection lost: %s. Reconnecting in %.1f seconds (attempt %d)...",
                        str(e),
                        delay,
                        retry_count,
                    )

                    try:
                        await asyncio.sleep(delay)
                    except asyncio.CancelledError:
                        break
                finally:
                    if pubsub:
                        try:
                            await pubsub.unsubscribe(self.CACHE_INVALIDATION_CHANNEL)
                            await pubsub.close()
                        except Exception:
                            pass  # Ignore errors during cleanup

            logger.debug("Pub/Sub listener loop exited")

        task = asyncio.create_task(_listen_with_retry())
        self._pubsub_task = task
        return task
