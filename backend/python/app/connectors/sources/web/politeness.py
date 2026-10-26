"""How hard the crawl may press each site, and the connectors service as a whole.

Shared by every web connector in the process: two connectors crawling the same site share its
per-site limit, and all of them share the process-wide fetch slots. Waiting is done by polling
real time for a free slot, so the same gate serves every event loop; only the spacing a site
asks for is waited through the caller's ``sleep``, which a test's virtual clock replaces.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

HOST_CONCURRENCY_ENV = "WEB_HOST_MAX_CONCURRENCY"
FETCH_CONCURRENCY_ENV = "WEB_FETCH_MAX_CONCURRENCY"
CRAWL_CONCURRENCY_ENV = "WEB_CRAWL_CONCURRENCY"
DEFAULT_HOST_CONCURRENCY = 4
DEFAULT_CRAWL_CONCURRENCY = 8
# A site's Crawl-delay above this is read as this: an hour between pages would never finish a sync.
MAX_INTERVAL_SECONDS = 30.0
POLL_SECONDS = 0.05
# Successes in a row before a site that slowed us down gets one more slot back.
RECOVER_AFTER = 20
# Rate limits within this long of a backoff are the same burst: requests already in flight at the
# old pace answer 429 too, and counting each of them would throttle a site for one refusal.
BACKOFF_WINDOW_SECONDS = 10.0
# A step back also after this long without a rate limit: success counts alone would recover a site
# slowed to one request every few seconds in minutes, and slower the further it was slowed.
RECOVER_SECONDS = 30.0

Sleep = Callable[[float], Awaitable[object]]


def _env_int(name: str, default: int, env: dict[str, str] | None = None) -> int:
    raw = (env if env is not None else os.environ).get(name, "")
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def fetch_slots_for(cpu_quota: float | None, env: dict[str, str] | None = None) -> int:
    """Pages the process fetches at once: most of a fetch is waiting on the network, but each
    page is parsed afterwards, so the slots follow the CPUs the container may use."""
    computed = max(4, min(32, round((cpu_quota or 1) * 4)))
    return _env_int(FETCH_CONCURRENCY_ENV, computed, env)


@dataclass
class _Site:
    active: int = 0
    limit: int = DEFAULT_HOST_CONCURRENCY
    next_at: float = 0.0
    extra_interval: float = 0.0
    successes: int = 0
    calm_until: float = 0.0
    stepped_at: float = 0.0


class HostGate:
    """Per-site concurrency and spacing, slowed down by 429/503 answers and recovered by successes."""

    def __init__(self, max_per_host: int) -> None:
        self.max_per_host = max(1, max_per_host)
        self._sites: dict[str, _Site] = {}

    def _site(self, host: str) -> _Site:
        return self._sites.setdefault(host, _Site(limit=self.max_per_host))

    @asynccontextmanager
    async def slot(
        self, host: str, *, min_interval: float | None, now: Callable[[], float], sleep: Sleep,
    ) -> AsyncIterator[None]:
        site = self._site(host)
        interval = min(MAX_INTERVAL_SECONDS, max(min_interval or 0.0, site.extra_interval))
        while site.active >= site.limit or now() < site.next_at:
            if site.active >= site.limit:
                await asyncio.sleep(POLL_SECONDS)
            else:
                await sleep(min(site.next_at - now(), MAX_INTERVAL_SECONDS))
            interval = min(MAX_INTERVAL_SECONDS, max(min_interval or 0.0, site.extra_interval))
        site.active += 1
        site.next_at = now() + interval
        try:
            yield
        finally:
            site.active -= 1

    def observe(self, host: str, status: int | None, *, now: float) -> None:
        """Adapt to the site's answer: back off once per burst of rate limits, first in requests at
        once and then in spacing, and win it back in the reverse order after steady success."""
        site = self._site(host)
        if status in (429, 503):
            site.successes = 0
            if now < site.calm_until:
                return
            site.calm_until = now + BACKOFF_WINDOW_SECONDS
            site.stepped_at = now
            if site.limit > 1:
                site.limit //= 2
            else:
                site.extra_interval = min(MAX_INTERVAL_SECONDS, max(1.0, site.extra_interval * 2))
        elif status is not None and status < 400:
            site.successes += 1
            if site.successes >= RECOVER_AFTER or now - site.stepped_at >= RECOVER_SECONDS:
                site.successes = 0
                site.stepped_at = now
                if site.extra_interval:
                    site.extra_interval = site.extra_interval / 2 if site.extra_interval > 0.5 else 0.0
                else:
                    site.limit = min(self.max_per_host, site.limit + 1)

    def reset(self) -> None:
        self._sites.clear()


class FetchSlots:
    """A process-wide cap on pages being fetched at once, across every connector."""

    def __init__(self, size: int) -> None:
        self.size = max(1, size)
        self.active = 0

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        while self.active >= self.size:  # noqa: ASYNC110 - polled so one object serves every event loop
            await asyncio.sleep(POLL_SECONDS)
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1


def crawl_concurrency(env: dict[str, str] | None = None) -> int:
    """Pages one connector fetches at once (the per-site limit still applies within it)."""
    return _env_int(CRAWL_CONCURRENCY_ENV, DEFAULT_CRAWL_CONCURRENCY, env)


def _cpu_quota() -> float | None:
    try:
        from app.services.resource_governor.probe import SystemResourceProbe

        return SystemResourceProbe().snapshot().cpu_quota
    except Exception:
        return None


shared_host_gate = HostGate(_env_int(HOST_CONCURRENCY_ENV, DEFAULT_HOST_CONCURRENCY))
shared_fetch_slots = FetchSlots(fetch_slots_for(_cpu_quota()))
