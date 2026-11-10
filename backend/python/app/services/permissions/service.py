"""`ViewerScopeService`: the resolver behind the version-keyed cache, and the serving-path rule.

A search never waits for the graph here. `scopes` answers from the cache or raises `ViewerScopesUnavailable`
(the caller uses the legacy filter, which costs what it always cost); on a miss it also starts one background
resolve per viewer and version that fills the cache for the next search. `resolve_now` waits, for tests, a shadow
comparator or a warm-up job.

Order matters. The version is read before the graph, and the result is stored under that version: if a permission
change commits and bumps meanwhile, the entry lands under a version nobody reads again. A cache hit is looked up
under the current version only, so a revoke is visible on the next call after its bump.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, cast

from app.core.org_context import for_org, spawn_detached

from .cache import count
from .errors import ViewerScopesUnavailable
from .scopes import UNVERSIONED

if TYPE_CHECKING:
    from .cache import ViewerScopeCache
    from .scopes import ViewerScopes
    from .viewer import ViewerScopeResolver

_logger = logging.getLogger(__name__)


class ViewerScopeService:
    def __init__(
        self,
        resolver: ViewerScopeResolver,
        cache: ViewerScopeCache | None = None,
        timeout_seconds: float | None = 5.0,
        max_background: int = 8,
    ) -> None:
        """`timeout_seconds`: a resolve that takes longer is `ViewerScopesUnavailable("timeout")` (for a background
        fill: it is dropped and the next miss starts another). `max_background`: fills running at once; a miss that finds that many running is
        legacy and starts none (the next miss of that viewer tries again), so a burst never queues work."""
        self._resolver = resolver
        self._cache = cache
        self._timeout = timeout_seconds
        self._max_background = max_background
        self._filling: dict[tuple[str, str, int], asyncio.Task[None]] = {}

    async def _resolve(self, org_id: str, user_key: str, version: int) -> ViewerScopes:
        try:
            return await asyncio.wait_for(self._resolver.resolve(org_id, user_key, version=version), self._timeout)
        except TimeoutError:
            raise ViewerScopesUnavailable("timeout") from None

    async def resolve_now(self, org_id: str, user_key: str) -> ViewerScopes:
        """Cache, else the graph; waits. Raises `ViewerScopesUnavailable`. Not for the serving path."""
        if self._cache is None:
            return await self._resolve(org_id, user_key, UNVERSIONED)
        version = await self._cache.version(org_id)
        if version is None:
            count("bypass")
            return await self._resolve(org_id, user_key, UNVERSIONED)
        hit = await self._cache.get(org_id, user_key, version)
        if hit is not None:
            count("hit")
            return hit
        count("miss")
        fresh = await self._resolve(org_id, user_key, version)
        await self._cache.put(fresh)
        return fresh

    async def scopes(self, org_id: str, user_key: str) -> ViewerScopes:
        """The cached scopes, or `ViewerScopesUnavailable` at once (reason `miss`, `bypass` or `no_cache`)."""
        if self._cache is None:
            raise ViewerScopesUnavailable("no_cache")
        version = await self._cache.version(org_id)
        if version is None:
            count("bypass")
            raise ViewerScopesUnavailable("bypass")  # nothing can be stored either: Redis is down or a bump is pending
        hit = await self._cache.get(org_id, user_key, version)
        if hit is not None:
            count("hit")
            return hit
        count("miss")
        self._fill_in_background(org_id, user_key, version)
        raise ViewerScopesUnavailable("miss")

    def _fill_in_background(self, org_id: str, user_key: str, version: int) -> None:
        key = (org_id, user_key, version)
        if key in self._filling or len(self._filling) >= self._max_background:
            return  # single flight per viewer and version; never more than `max_background` running, none queued
        try:
            task = cast("asyncio.Task[None]", spawn_detached(self._fill(org_id, user_key, version), name="viewer-scope-fill"))
        except RuntimeError:  # no running loop
            return
        self._filling[key] = task
        task.add_done_callback(lambda _t, k=key: self._filling.pop(k, None))

    async def _fill(self, org_id: str, user_key: str, version: int) -> None:
        assert self._cache is not None
        try:
            if await self._cache.version(org_id) != version:
                return  # a permission event moved the version since the miss: nothing would read this entry
            with for_org(org_id, job="viewer-scopes"):
                fresh = await self._resolve(org_id, user_key, version)
            await self._cache.put(fresh)
        except ViewerScopesUnavailable as exc:
            _logger.info("viewer-scope fill failed: %s", exc.reason)
        except Exception as exc:
            _logger.warning("viewer-scope fill failed: %s", type(exc).__name__)

    async def wait_for_fills(self) -> None:
        """Wait for the background fills running now (tests, shutdown)."""
        if self._filling:
            await asyncio.gather(*self._filling.values(), return_exceptions=True)
