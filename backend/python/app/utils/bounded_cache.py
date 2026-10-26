from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable, Hashable

V = TypeVar("V")


class SizeBoundedCache(Generic[V]):
    """Process-wide cache bounded by the summed size of its values, oldest out first.

    Thread-safe: writers include `asyncio.to_thread` workers of several requests.
    A value larger than a tenth of the budget is not stored, so one huge entry
    cannot flush everything else.
    """

    def __init__(self, max_size: int, size_of: Callable[[V], int], max_entries: int | None = None) -> None:
        self._max_size = max_size
        self._max_entries = max_entries
        self._size_of = size_of
        self._entries: dict[Hashable, V] = {}
        self._size = 0
        self._lock = threading.Lock()

    def get(self, key: Hashable) -> V | None:
        return self._entries.get(key)

    def put(self, key: Hashable, value: V) -> None:
        size = self._size_of(value)
        if size > self._max_size // 10:
            return
        with self._lock:
            if key in self._entries:
                return
            while self._entries and (
                self._size + size > self._max_size
                or (self._max_entries is not None and len(self._entries) >= self._max_entries)
            ):
                self._size -= self._size_of(self._entries.pop(next(iter(self._entries))))
            self._entries[key] = value
            self._size += size

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._size = 0
