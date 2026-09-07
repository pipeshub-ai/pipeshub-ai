"""Domain-shaped cache interfaces (R16).

These are deliberately *not* a thin wrapper over Redis's own API (no
``hget``/``hset``/``expire``): callers need "give me this computed value, or
compute and cache it" and "drop what's cached for this scope", not a hash
table. The Redis-backed implementation owns its key/hash layout as a private
detail; a different cache backend (or an EE one) implements the same
interface however it likes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

Loader = Callable[[], Awaitable[dict[str, str]]]


class IAccessibleRecordsCache(ABC):
    """Read-through cache of `virtualRecordId -> recordId` accessible-record maps."""

    @abstractmethod
    async def get_or_compute_kb(
        self, org_id: str, kb_id: str, loader: Loader
    ) -> dict[str, str]: ...

    @abstractmethod
    async def get_or_compute_app_connector(
        self, org_id: str, connector_id: str, loader: Loader
    ) -> dict[str, str]: ...

    @abstractmethod
    async def get_or_compute_user_connector(
        self, org_id: str, connector_id: str, user_id: str, loader: Loader
    ) -> dict[str, str]: ...

    @abstractmethod
    async def invalidate_connector(self, org_id: str, connector_id: str) -> None: ...

    @abstractmethod
    async def invalidate_kb(self, org_id: str, kb_id: str) -> None: ...

    @property
    @abstractmethod
    def enabled(self) -> bool: ...

    @abstractmethod
    async def close(self) -> None: ...


class NoopAccessibleRecordsCache(IAccessibleRecordsCache):
    """Always-disabled cache: every call falls through to ``loader()``.

    Replaces the ad hoc ``enabled=False`` construction path that used to
    require every caller to know how to build a disabled
    ``AccessibleRecordsCache`` (with ``redis_client=None``); this is the same
    behaviour behind the interface, usable wherever the cache is optional.
    """

    async def get_or_compute_kb(
        self, org_id: str, kb_id: str, loader: Loader  # noqa: ARG002
    ) -> dict[str, str]:
        return await loader()

    async def get_or_compute_app_connector(
        self, org_id: str, connector_id: str, loader: Loader  # noqa: ARG002
    ) -> dict[str, str]:
        return await loader()

    async def get_or_compute_user_connector(
        self, org_id: str, connector_id: str, user_id: str, loader: Loader  # noqa: ARG002
    ) -> dict[str, str]:
        return await loader()

    async def invalidate_connector(self, org_id: str, connector_id: str) -> None:  # noqa: ARG002
        return None

    async def invalidate_kb(self, org_id: str, kb_id: str) -> None:  # noqa: ARG002
        return None

    @property
    def enabled(self) -> bool:
        return False

    async def close(self) -> None:
        return None


class ISignedUrlCache(ABC):
    """Replaces ``blob_storage.get_shared_redis()``'s direct Redis access."""

    @abstractmethod
    async def get(self, key: str) -> str | None: ...

    @abstractmethod
    async def set(self, key: str, url: str, ttl_seconds: int) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...


class NoopSignedUrlCache(ISignedUrlCache):
    """Used when the cache is disabled or Redis is unreachable; every read is
    a miss and every write is dropped, matching pre-cache behaviour."""

    async def get(self, key: str) -> str | None:  # noqa: ARG002
        return None

    async def set(self, key: str, url: str, ttl_seconds: int) -> None:  # noqa: ARG002
        return None

    async def close(self) -> None:
        return None
