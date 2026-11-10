"""The viewer-scope cache without a shared Redis (REDIS_MODE=postgres): each process has its own cache, and a bump reaches the others
through a NOTIFY bus. Here two caches on separate in-memory servers stand for two processes, joined by a fake bus; the real channel
is covered by tests/integration/pg_runtime/test_viewer_scope_cache.py."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import fakeredis
import pytest

from app.services.permissions.cache import ViewerScopeCache


class FakeBus:
    """Delivers every publish to every joined cache, the publisher included, as PostgreSQL does."""

    def __init__(self) -> None:
        self.caches: list[ViewerScopeCache] = []
        self.up = True
        self.fail_publish = False
        self.sent: list[tuple[str, str, str]] = []
        self.started = False

    def join(self, cache: ViewerScopeCache) -> ViewerScopeCache:
        cache._bus = self  # type: ignore[assignment]
        self.caches.append(cache)
        return cache

    @property
    def available(self) -> bool:
        return True

    def start(self) -> None:
        self.started = True

    def healthy(self) -> bool:
        return self.up

    async def publish(self, kind: str, org_id: str, scope_id: str) -> None:
        if self.fail_publish:
            raise ConnectionError("database away")
        self.sent.append((kind, org_id, scope_id))
        for c in self.caches:
            await c._apply_remote(kind, org_id, scope_id)

    async def stop(self) -> None:
        self.started = False


def _process(bus: FakeBus, **kw) -> ViewerScopeCache:
    client = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)
    return bus.join(ViewerScopeCache(lambda: client, **kw))


async def test_a_bump_in_one_process_moves_the_version_in_the_other() -> None:
    bus = FakeBus()
    connectors, query = _process(bus), _process(bus)
    before = await query.version("o1")
    other_org = await query.version("o2")
    assert before is not None and await query.version("o1") == before      # stable until a bump: entries are served

    await connectors.bump("o1")

    assert bus.sent == [("v", "o1", "")]
    assert await query.version("o1") != before
    assert await query.version("o2") == other_org        # another organisation keeps its entries


async def test_a_global_bump_moves_every_organisation_in_every_process() -> None:
    bus = FakeBus()
    connectors, query = _process(bus), _process(bus)
    before = [await query.version(o) for o in ("o1", "o2")]
    await connectors.bump(None)
    assert [await query.version(o) for o in ("o1", "o2")] != before
    assert all(a != b for a, b in zip(before, [await query.version(o) for o in ("o1", "o2")], strict=True))


async def test_while_the_bus_is_down_nothing_is_served_from_the_cache() -> None:
    bus = FakeBus()
    query = _process(bus)
    assert await query.version("o1") is not None
    bus.up = False       # another process's bump could be lost
    assert await query.version("o1") is None
    bus.up = True
    assert await query.version("o1") is not None


async def test_a_reconnect_drops_every_entry_cached_before_it() -> None:
    bus = FakeBus()
    query = _process(bus)
    before = [await query.version(o) for o in ("o1", "o2")]
    await query._flush_local()       # what the bus calls after a LISTEN reconnect
    after = [await query.version(o) for o in ("o1", "o2")]
    assert all(a != b for a, b in zip(before, after, strict=True))


async def test_a_message_of_another_kind_drops_everything_rather_than_guess() -> None:
    bus = FakeBus()
    query = _process(bus)
    before = await query.version("o1")
    await query._apply_remote("x", "o2", "")
    assert await query.version("o1") != before


async def test_a_publish_that_fails_stops_this_process_serving_until_the_retry_lands(monkeypatch) -> None:
    monkeypatch.setattr(ViewerScopeCache, "BUMP_RETRY_DELAYS_SECONDS", (0.01,))
    monkeypatch.setattr(ViewerScopeCache, "DOWN_BACKOFF_SECONDS", 0.0)      # a failed bump also starts the store backoff
    bus = FakeBus()
    connectors, query = _process(bus), _process(bus)
    before = await query.version("o1")
    bus.fail_publish = True

    await connectors.bump("o1")

    assert await connectors.version("o1") is None        # the bump counts as lost here: no cached scopes
    assert await query.version("o1") == before           # the other process did not hear; its TTL bounds the cost
    bus.fail_publish = False
    for _ in range(100):
        if not connectors._unbumped:
            break
        await asyncio.sleep(0.01)
    assert connectors._unbumped == {}
    assert await query.version("o1") != before           # the retried bump was published
    assert await connectors.version("o1") is not None
    await connectors.aclose()


class _Cfg:
    async def get_redis_config(self) -> SimpleNamespace:
        return SimpleNamespace(host="localhost", port=6379, password=None, db=0, tls=False)


def _provider() -> SimpleNamespace:
    client = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)
    return SimpleNamespace(get_client=lambda: client, key_namespace="ns")


@pytest.fixture
def pg_mode():  # noqa: ANN201
    with (
        patch("app.services.redis.connection_provider_factory.uses_pg_state", return_value=True),
        patch("app.services.redis.connection_provider_factory.get_redis_provider", return_value=_provider()),
    ):
        yield


async def test_create_starts_the_bus_and_caps_the_ttl(pg_mode) -> None:
    bus = FakeBus()
    with patch("app.services.cache.pg_invalidation.PgCacheBus", return_value=bus) as made:
        cache = await ViewerScopeCache.create(_Cfg())  # type: ignore[arg-type]
    assert made.call_args.kwargs["channel"] == ViewerScopeCache.CHANNEL
    assert cache._bus is bus and bus.started
    assert cache._ttl == ViewerScopeCache.PG_STATE_TTL_SECONDS
    assert await cache.version("o1") is not None
    await cache.aclose()
    assert not bus.started


async def test_create_keeps_no_cache_when_no_bump_could_reach_the_other_processes(pg_mode) -> None:
    bus = SimpleNamespace(available=False)
    with patch("app.services.cache.pg_invalidation.PgCacheBus", return_value=bus):
        cache = await ViewerScopeCache.create(_Cfg())  # type: ignore[arg-type]
    assert cache._client is None and cache._bus is None
    assert await cache.version("o1") is None


async def test_create_never_raises_when_the_channel_cannot_be_set_up(pg_mode) -> None:
    with patch("app.services.cache.pg_invalidation.PgCacheBus", side_effect=RuntimeError("no config")):
        cache = await ViewerScopeCache.create(_Cfg())  # type: ignore[arg-type]
    assert cache._client is None
    assert await cache.version("o1") is None
