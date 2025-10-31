import json
import time
from threading import Lock
from typing import Any, Callable, Dict, Generic, List, Optional, TypeVar

from app.config.key_value_store import KeyValueStore
from app.utils.logger import create_logger

logger = create_logger("etcd")

T = TypeVar("T")


class KeyData(Generic[T]):
    """
    Helper class to store value and TTL information.

    Attributes:
        value: The stored value
        expiry: Optional expiration timestamp
    """

    def __init__(self, value: T, ttl: Optional[int] = None) -> None:
        logger.debug("🔧 Creating KeyData instance")
        logger.debug("📋 TTL: %s seconds", ttl if ttl else "None")

        self.value = value
        self.expiry = time.time() + ttl if ttl else None
        if self.expiry:
            logger.debug(
                "📋 Expiry set to: %s",
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.expiry)),
            )

    def is_expired(self) -> bool:
        """Check if the value has expired."""
        if not self.expiry:
            logger.debug("📋 No expiry set, returning False")
            return False
        is_expired = time.time() > self.expiry
        logger.debug(
            "🔍 Checking expiry: %s (expired: %s)",
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.expiry)),
            is_expired,
        )
        return is_expired


class InMemoryKeyValueStore(KeyValueStore[T], Generic[T]):
    """
    In-memory implementation of the distributed key-value store.

    This implementation is primarily used for testing and development.
    While it implements the full interface, it's not actually distributed
    and some features (like watching) are simulated.

    Attributes:
        store: Dictionary storing the key-value pairs
        watchers: Dictionary storing active watchers for keys
        lock: Thread-safe lock for concurrent access
    """

    def __init__(self, logger, default_json_file_path: Optional[str] = None) -> None:
        """Initialize an empty in-memory store."""
        logger.debug("🔧 Initializing InMemoryKeyValueStore")
        self.store: Dict[str, KeyData[T]] = {}
        self.watchers: Dict[str, List[tuple[Callable[[Optional[T]], None], Any]]] = {}
        self.lock = Lock()

        if default_json_file_path:
            self.json_file_path = default_json_file_path
            self.store = self._load_from_json(self.json_file_path)

        logger.debug("✅ InMemoryKeyValueStore initialized")

    def _load_from_json(self, json_file_path: str) -> Dict[str, KeyData[T]]:
        """Load data from a JSON file."""
        with open(json_file_path, 'r') as file:
            data = json.load(file)
        return {key: KeyData(value, None) for key, value in data.items()}

    def _cleanup_expired_keys(self) -> None:
        """Clean up expired keys synchronously."""
        expired_keys = [
            key for key, data in self.store.items() if data.is_expired()
        ]
        if expired_keys:
            logger.debug("📋 Found expired keys: %s", expired_keys)
            for key in expired_keys:
                logger.debug("🔄 Removing expired key: %s", key)
                del self.store[key]
            logger.debug(
                "✅ Cleanup complete, removed %d keys", len(expired_keys)
            )

    def _notify_watchers(self, key: str, value: Optional[T]) -> None:
        """Notify all watchers of a key about value changes."""
        logger.debug("🔄 Notifying watchers for key: %s", key)
        if key in self.watchers:
            logger.debug("📋 Found %d watchers", len(self.watchers[key]))
            for callback, watch_id in self.watchers[key]:
                try:
                    logger.debug("🔄 Executing callback for watch_id: %s", watch_id)
                    callback(value)
                    logger.debug("✅ Callback executed successfully")
                except Exception as e:
                    logger.error("❌ Error in watcher callback: %s", str(e))
                    logger.exception("Detailed error:")

    async def create_key(self, key: str, value: T, overwrite: bool = True, ttl: Optional[int] = None) -> None:
        """
        Create a new key-value pair in the store.

        Args:
            key: The key to create
            value: The value to associate with the key
            ttl: Optional time-to-live in seconds

        Raises:
            KeyError: If the key already exists
        """
        logger.debug("🔄 Creating key: %s", key)
        logger.debug("📋 TTL: %s seconds", ttl if ttl else "None")

        with self.lock:
            self._cleanup_expired_keys()
            if key in self.store and not self.store[key].is_expired() and not overwrite:
                logger.error("❌ Key already exists: %s", key)
                raise KeyError(f'Key "{key}" already exists.')

            logger.debug("🔄 Storing new key-value pair")
            self.store[key] = KeyData(value, ttl)
            logger.debug("🔄 Notifying watchers")
            self._notify_watchers(key, value)
            logger.debug("✅ Key created successfully")

    async def update_value(self, key: str, value: T, ttl: Optional[int] = None) -> None:
        """
        Update the value for an existing key.

        Args:
            key: The key to update
            value: The new value
            ttl: Optional time-to-live in seconds

        Raises:
            KeyError: If the key doesn't exist or has expired
        """
        logger.debug("🔄 Updating key: %s", key)
        logger.debug("📋 TTL: %s seconds", ttl if ttl else "None")

        with self.lock:
            self._cleanup_expired_keys()
            if key not in self.store or self.store[key].is_expired():
                logger.error("❌ Key does not exist: %s", key)
                raise KeyError(f'Key "{key}" does not exist.')

            logger.debug("🔄 Updating value")
            self.store[key] = KeyData(value, ttl)
            logger.debug("🔄 Notifying watchers")
            self._notify_watchers(key, value)
            logger.debug("✅ Value updated successfully")

    async def get_key(self, key: str) -> Optional[T]:
        """
        Retrieve the value associated with a key.

        Args:
            key: The key to retrieve

        Returns:
            The value associated with the key, or None if the key doesn't exist or has expired
        """
        logger.debug("🔍 Getting value for key: %s", key)

        with self.lock:
            self._cleanup_expired_keys()
            if key in self.store:
                data = self.store[key]
                if not data.is_expired():
                    return data.value
                else:
                    logger.debug("⚠️ Key exists but has expired")
            else:
                logger.debug("⚠️ Key not found")
            return None

    async def delete_key(self, key: str) -> bool:
        """
        Delete a key-value pair from the store.

        Args:
            key: The key to delete

        Returns:
            True if the key was deleted, False if it didn't exist
        """
        logger.debug("🔄 Deleting key: %s", key)

        with self.lock:
            self._cleanup_expired_keys()
            if key in self.store:
                logger.debug("🔄 Key found, removing")
                del self.store[key]
                logger.debug("🔄 Notifying watchers")
                self._notify_watchers(key, None)
                logger.debug("✅ Key deleted successfully")
                return True

            logger.debug("⚠️ Key not found, nothing to delete")
            return False

    async def get_all_keys(self) -> List[str]:
        """
        Retrieve all non-expired keys in the store.

        Returns:
            List of all valid keys in the store
        """
        logger.debug("🔍 Getting all non-expired keys")

        with self.lock:
            self._cleanup_expired_keys()
            valid_keys = [
                key for key, data in self.store.items() if not data.is_expired()
            ]
            logger.debug("📋 Found %d valid keys: %s", len(valid_keys), valid_keys)
            return valid_keys

    async def watch_key(
        self,
        key: str,
        callback: Callable[[Optional[T]], None],
        error_callback: Optional[Callable[[Exception], None]] = None,
    ) -> int:
        """
        Watch a key for changes and execute callbacks when changes occur.

        Args:
            key: The key to watch
            callback: Function to call when the value changes
            error_callback: Optional function to call when errors occur

        Returns:
            Watch identifier that can be used to cancel the watch
        """
        logger.debug("🔄 Setting up watch for key: %s", key)
        watch_id = id(callback)
        logger.debug("📋 Generated watch_id: %s", watch_id)

        with self.lock:
            if key not in self.watchers:
                logger.debug("🔄 Creating new watcher list for key")
                self.watchers[key] = []
            self.watchers[key].append((callback, watch_id))
            logger.debug(
                "✅ Watch setup complete. Total watchers for key: %d",
                len(self.watchers[key]),
            )
        return watch_id

    def cancel_watch(self, key: str, watch_id: str) -> None:
        """
        Cancel a watch operation.

        Args:
            key: The key being watched
            watch_id: The watch identifier returned from watch_key
        """
        logger.debug("🔄 Canceling watch for key: %s, watch_id: %s", key, watch_id)

        with self.lock:
            if key in self.watchers:
                original_count = len(self.watchers[key])
                self.watchers[key] = [
                    (cb, wid) for cb, wid in self.watchers[key] if wid != watch_id
                ]
                new_count = len(self.watchers[key])
                logger.debug("📋 Removed %d watchers", original_count - new_count)

                if not self.watchers[key]:
                    logger.debug("🔄 No more watchers for key, removing watcher list")
                    del self.watchers[key]
                logger.debug("✅ Watch canceled successfully")
            else:
                logger.debug("⚠️ No watchers found for key")

    async def list_keys_in_directory(self, directory: str) -> List[str]:
        """
        List all non-expired keys under a specific directory prefix.

        Args:
            directory: The directory prefix to search under

        Returns:
            List of keys under the specified directory
        """
        logger.debug("🔍 Listing keys in directory: %s", directory)

        with self.lock:
            self._cleanup_expired_keys()
            matching_keys = [
                key
                for key, data in self.store.items()
                if key.startswith(directory) and not data.is_expired()
            ]
            logger.debug(
                "📋 Found %d matching keys: %s", len(matching_keys), matching_keys
            )
            return matching_keys

    async def close(self) -> None:
        """Clean up resources."""
        logger.debug("🔄 Closing InMemoryKeyValueStore")

        with self.lock:
            logger.debug("🔄 Clearing store and watchers")
            store_count = len(self.store)
            watcher_count = len(self.watchers)
            self.store.clear()
            self.watchers.clear()
            logger.debug(
                "✅ Cleared %d stored items and %d watchers", store_count, watcher_count
            )
