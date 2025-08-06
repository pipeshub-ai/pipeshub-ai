from abc import ABC, abstractmethod
from typing import Callable, Generic, List, Optional, TypeVar

T = TypeVar("T")


class KeyValueStore(ABC, Generic[T]):
    """
    Abstract base class defining the interface for distributed key-value stores.

    This interface provides a common contract for different key-value store implementations,
    ensuring consistent behavior across different backends.

    Type Parameters:
        T: The type of values stored in the key-value store
    """

    @abstractmethod
    async def create_key(self, key: str, value: T, overwrite: bool = True, ttl: Optional[int] = None) -> None:
        """
        Create a new key-value pair in the store.

        Args:
            key: The key to create
            value: The value to associate with the key
            ttl: Optional time-to-live in seconds

        Raises:
            KeyError: If the key already exists
            ValueError: If the key or value is invalid
            ConnectionError: If the store is unavailable
        """
        pass

    @abstractmethod
    async def update_value(self, key: str, value: T, ttl: Optional[int] = None) -> None:
        """
        Update the value for an existing key.

        Args:
            key: The key to update
            value: The new value
            ttl: Optional time-to-live in seconds

        Raises:
            KeyError: If the key doesn't exist
            ValueError: If the value is invalid
            ConnectionError: If the store is unavailable
        """
        pass

    @abstractmethod
    async def get_key(self, key: str) -> Optional[T]:
        """
        Retrieve the value associated with a key.

        Args:
            key: The key to retrieve

        Returns:
            The value associated with the key, or None if the key doesn't exist

        Raises:
            ConnectionError: If the store is unavailable
        """
        pass

    @abstractmethod
    async def delete_key(self, key: str) -> bool:
        """
        Delete a key-value pair from the store.

        Args:
            key: The key to delete

        Returns:
            True if the key was deleted, False if it didn't exist

        Raises:
            ConnectionError: If the store is unavailable
        """
        pass

    @abstractmethod
    async def get_all_keys(self) -> List[str]:
        """
        Retrieve all keys in the store.

        Returns:
            List of all keys in the store

        Raises:
            ConnectionError: If the store is unavailable
        """
        pass

    @abstractmethod
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

        Raises:
            ConnectionError: If the store is unavailable
            NotImplementedError: If watching is not supported
        """
        pass

    @abstractmethod
    async def cancel_watch(self, key: str, watch_id: str) -> None:
        """
        Cancel a watch for a key.
        """
        pass

    @abstractmethod
    async def list_keys_in_directory(self, directory: str) -> List[str]:
        """
        List all keys under a specific directory prefix.

        Args:
            directory: The directory prefix to search under

        Returns:
            List of keys under the specified directory

        Raises:
            ConnectionError: If the store is unavailable
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """
        Clean up resources and close connections.

        This method should be called when the store is no longer needed.
        """
        pass
