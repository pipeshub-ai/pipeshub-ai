from __future__ import annotations

import time
from functools import wraps
from typing import TYPE_CHECKING, ParamSpec, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable, Hashable

P = ParamSpec("P")
R = TypeVar("R")


class TtlCache:
    """Bounded cache with per-entry expiry.

    Cleared wholesale when full: eviction bookkeeping would cost more than the
    recompute it saves for entries this cheap to rebuild.
    """

    def __init__(
        self,
        maxsize: int,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.maxsize = maxsize
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: dict[Hashable, tuple[object, float]] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key: Hashable) -> tuple[bool, object]:
        entry = self._entries.get(key)
        if entry is None:
            return False, None
        value, expires_at = entry
        if expires_at > self._clock():
            return True, value
        self._entries.pop(key, None)
        return False, None

    def set(self, key: Hashable, value: object) -> None:
        if len(self._entries) >= self.maxsize:
            self._entries.clear()
        self._entries[key] = (value, self._clock() + self.ttl_seconds)

    def clear(self) -> None:
        self._entries.clear()


def ttl_memoize(
    cache: TtlCache,
    key_fn: Callable[P, Hashable | None],
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Memoize through `cache`. A `None` key bypasses the cache for that call."""

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            key = key_fn(*args, **kwargs)
            if key is None:
                return func(*args, **kwargs)
            hit, value = cache.get(key)
            if hit:
                return value  # type: ignore[return-value]
            result = func(*args, **kwargs)
            cache.set(key, result)
            return result

        return wrapper

    return decorator
