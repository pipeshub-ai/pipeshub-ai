"""AccessibleRecordsCache: key schema, read-through, single-flight, TTL and the
guarantee that a broken Redis can never fail or stall a search."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.cache.accessible_records_cache import AccessibleRecordsCache

ORG = "org-1"
KB = "kb-1"
CONNECTOR = "conn-1"
USER = "user-1"


class FakeRedis:
    """In-memory stand-in for the handful of commands the cache uses."""

    def __init__(self) -> None:
        self.strings: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.expires: dict[str, int] = {}
        self.calls: list[tuple] = []

    async def get(self, key):
        self.calls.append(("get", key))
        return self.strings.get(key)

    async def set(self, key, value, ex=None):
        self.calls.append(("set", key, ex))
        self.strings[key] = value
        if ex is not None:
            self.expires[key] = ex
        return True

    async def hget(self, key, field):
        self.calls.append(("hget", key, field))
        return self.hashes.get(key, {}).get(field)

    async def hset(self, key, field, value):
        self.calls.append(("hset", key, field))
        self.hashes.setdefault(key, {})[field] = value
        return 1

    async def expire(self, key, ttl):
        self.calls.append(("expire", key, ttl))
        self.expires[key] = ttl
        return True

    async def delete(self, *keys):
        self.calls.append(("delete", *keys))
        removed = 0
        for key in keys:
            removed += 1 if self.strings.pop(key, None) is not None else 0
            removed += 1 if self.hashes.pop(key, None) is not None else 0
            self.expires.pop(key, None)
        return removed

    async def ping(self):
        return True

    async def aclose(self):
        return None


class BrokenRedis(FakeRedis):
    async def get(self, key):
        raise ConnectionError("redis down")

    async def set(self, key, value, ex=None):
        raise ConnectionError("redis down")

    async def hget(self, key, field):
        raise ConnectionError("redis down")

    async def hset(self, key, field, value):
        raise ConnectionError("redis down")

    async def delete(self, *keys):
        raise ConnectionError("redis down")


def _cache(redis=None, ttl=300, enabled=True) -> AccessibleRecordsCache:
    return AccessibleRecordsCache(MagicMock(), redis if redis is not None else FakeRedis(), ttl, enabled)


def _loader(value, counter=None):
    async def load():
        if counter is not None:
            counter.append(1)
        return value
    return load


class TestKeySchema:
    def test_keys_are_namespaced_and_org_scoped(self) -> None:
        cache = _cache()
        assert cache._kb_key(ORG, KB) == f"pipeshub:accessible_records:v1:kb:{ORG}:{KB}"
        assert cache._app_connector_key(ORG, CONNECTOR) == f"pipeshub:accessible_records:v1:capp:{ORG}:{CONNECTOR}"
        assert cache._user_connector_key(ORG, CONNECTOR) == f"pipeshub:accessible_records:v1:cusr:{ORG}:{CONNECTOR}"

    def test_key_classes_do_not_collide(self) -> None:
        cache = _cache()
        keys = {
            cache._kb_key(ORG, "x"),
            cache._app_connector_key(ORG, "x"),
            cache._user_connector_key(ORG, "x"),
        }
        assert len(keys) == 3

    def test_orgs_are_isolated(self) -> None:
        cache = _cache()
        assert cache._kb_key("org-a", KB) != cache._kb_key("org-b", KB)


class TestReadThrough:
    async def test_miss_then_hit(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis)
        calls: list = []
        value = {"vr-1": "rec-1"}

        assert await cache.get_or_compute_kb(ORG, KB, _loader(value, calls)) == value
        assert await cache.get_or_compute_kb(ORG, KB, _loader(value, calls)) == value
        assert len(calls) == 1, "second call must be served from Redis"

    async def test_value_is_stored_with_ttl(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis, ttl=123)
        await cache.get_or_compute_kb(ORG, KB, _loader({"vr-1": "rec-1"}))
        assert redis.expires[cache._kb_key(ORG, KB)] == 123

    async def test_empty_map_is_cached(self) -> None:
        """A user with no access must not re-run the traversal on every search."""
        redis = FakeRedis()
        cache = _cache(redis)
        calls: list = []

        assert await cache.get_or_compute_kb(ORG, KB, _loader({}, calls)) == {}
        assert await cache.get_or_compute_kb(ORG, KB, _loader({}, calls)) == {}
        assert len(calls) == 1

    async def test_app_connector_entry_is_user_independent(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis)
        calls: list = []
        value = {"vr-1": "rec-1"}

        await cache.get_or_compute_app_connector(ORG, CONNECTOR, _loader(value, calls))
        await cache.get_or_compute_app_connector(ORG, CONNECTOR, _loader(value, calls))
        assert len(calls) == 1

    async def test_user_connector_entries_are_per_user(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis)
        a = {"vr-a": "rec-a"}
        b = {"vr-b": "rec-b"}

        assert await cache.get_or_compute_user_connector(ORG, CONNECTOR, "user-a", _loader(a)) == a
        assert await cache.get_or_compute_user_connector(ORG, CONNECTOR, "user-b", _loader(b)) == b
        # One hash, one field per user.
        assert set(redis.hashes[cache._user_connector_key(ORG, CONNECTOR)]) == {"user-a", "user-b"}

    async def test_corrupt_payload_is_treated_as_a_miss(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis)
        redis.strings[cache._kb_key(ORG, KB)] = "not-json"

        assert await cache.get_or_compute_kb(ORG, KB, _loader({"vr-1": "rec-1"})) == {"vr-1": "rec-1"}


class TestHashFreshness:
    async def test_stale_field_is_recomputed(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis, ttl=60)
        key = cache._user_connector_key(ORG, CONNECTOR)
        redis.hashes[key] = {
            USER: json.dumps({"t": int(time.time()) - 3600, "m": {"old": "value"}})
        }

        out = await cache.get_or_compute_user_connector(ORG, CONNECTOR, USER, _loader({"new": "value"}))

        assert out == {"new": "value"}

    async def test_fresh_field_is_served(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis, ttl=60)
        key = cache._user_connector_key(ORG, CONNECTOR)
        redis.hashes[key] = {USER: json.dumps({"t": int(time.time()), "m": {"cached": "hit"}})}
        calls: list = []

        out = await cache.get_or_compute_user_connector(ORG, CONNECTOR, USER, _loader({}, calls))

        assert out == {"cached": "hit"}
        assert not calls

    async def test_envelope_without_timestamp_is_a_miss(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis)
        redis.hashes[cache._user_connector_key(ORG, CONNECTOR)] = {USER: json.dumps({"m": {"a": "b"}})}

        assert await cache.get_or_compute_user_connector(ORG, CONNECTOR, USER, _loader({"x": "y"})) == {"x": "y"}

    async def test_write_refreshes_the_hash_key_ttl(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis, ttl=77)
        await cache.get_or_compute_user_connector(ORG, CONNECTOR, USER, _loader({"a": "b"}))
        assert redis.expires[cache._user_connector_key(ORG, CONNECTOR)] == 77


class TestSingleFlight:
    async def test_concurrent_misses_run_the_loader_once(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis)
        calls: list = []

        async def slow_loader():
            calls.append(1)
            await asyncio.sleep(0.02)
            return {"vr-1": "rec-1"}

        results = await asyncio.gather(
            *[cache.get_or_compute_kb(ORG, KB, slow_loader) for _ in range(10)]
        )

        assert all(r == {"vr-1": "rec-1"} for r in results)
        assert len(calls) == 1

    async def test_distinct_keys_are_not_serialized(self) -> None:
        cache = _cache(FakeRedis())
        started: list = []

        def make(key_id):
            async def load():
                started.append(key_id)
                await asyncio.sleep(0.02)
                return {key_id: "rec"}
            return load

        await asyncio.gather(
            cache.get_or_compute_kb(ORG, "kb-a", make("a")),
            cache.get_or_compute_kb(ORG, "kb-b", make("b")),
        )
        assert set(started) == {"a", "b"}

    async def test_lock_table_is_bounded(self) -> None:
        cache = _cache(FakeRedis())
        cache.MAX_LOCKS = 8
        for i in range(40):
            await cache.get_or_compute_kb(ORG, f"kb-{i}", _loader({}))
        assert len(cache._locks) <= cache.MAX_LOCKS


class TestKillSwitch:
    async def test_disabled_cache_always_calls_the_loader(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis, enabled=False)
        calls: list = []

        await cache.get_or_compute_kb(ORG, KB, _loader({"a": "b"}, calls))
        await cache.get_or_compute_kb(ORG, KB, _loader({"a": "b"}, calls))

        assert len(calls) == 2
        assert not redis.calls, "a disabled cache must not touch Redis at all"

    async def test_create_honours_the_env_kill_switch(self, monkeypatch) -> None:
        monkeypatch.setenv(AccessibleRecordsCache.ENV_ENABLED, "off")
        config = MagicMock()
        config.get_redis_config = AsyncMock()

        cache = await AccessibleRecordsCache.create(MagicMock(), config)

        assert cache.enabled is False
        config.get_redis_config.assert_not_called()

    async def test_create_survives_an_unreachable_redis(self, monkeypatch) -> None:
        monkeypatch.delenv(AccessibleRecordsCache.ENV_ENABLED, raising=False)
        config = MagicMock()
        config.get_redis_config = AsyncMock(side_effect=RuntimeError("no redis config"))

        cache = await AccessibleRecordsCache.create(MagicMock(), config)

        assert cache.enabled is False
        assert await cache.get_or_compute_kb(ORG, KB, _loader({"a": "b"})) == {"a": "b"}

    async def test_ttl_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv(AccessibleRecordsCache.ENV_ENABLED, "off")
        monkeypatch.setenv(AccessibleRecordsCache.ENV_TTL, "45")
        cache = await AccessibleRecordsCache.create(MagicMock(), MagicMock())
        assert cache.ttl_seconds == 45

    async def test_invalid_ttl_env_falls_back(self, monkeypatch) -> None:
        monkeypatch.setenv(AccessibleRecordsCache.ENV_ENABLED, "off")
        monkeypatch.setenv(AccessibleRecordsCache.ENV_TTL, "soon")
        cache = await AccessibleRecordsCache.create(MagicMock(), MagicMock())
        assert cache.ttl_seconds == AccessibleRecordsCache.DEFAULT_TTL_SECONDS


class TestRedisDown:
    async def test_read_failure_falls_through_to_the_loader(self) -> None:
        cache = _cache(BrokenRedis())
        assert await cache.get_or_compute_kb(ORG, KB, _loader({"a": "b"})) == {"a": "b"}

    async def test_failure_trips_the_backoff(self) -> None:
        redis = BrokenRedis()
        cache = _cache(redis)

        await cache.get_or_compute_kb(ORG, KB, _loader({"a": "b"}))
        assert cache.enabled is False

        redis.calls.clear()
        assert await cache.get_or_compute_kb(ORG, KB, _loader({"a": "b"})) == {"a": "b"}
        assert not redis.calls, "while down, Redis must not be contacted at all"

    async def test_backoff_expires(self) -> None:
        cache = _cache(BrokenRedis())
        await cache.get_or_compute_kb(ORG, KB, _loader({"a": "b"}))
        assert cache.enabled is False

        cache._down_until = 0.0
        assert cache.enabled is True

    async def test_invalidation_failure_is_swallowed(self) -> None:
        cache = _cache(BrokenRedis())
        await cache.invalidate_kb(ORG, KB)
        await cache.invalidate_connector(ORG, CONNECTOR)

    async def test_loader_exceptions_propagate_unchanged(self) -> None:
        cache = _cache(FakeRedis())

        async def failing():
            raise RuntimeError("graph exploded")

        with pytest.raises(RuntimeError, match="graph exploded"):
            await cache.get_or_compute_kb(ORG, KB, failing)


class TestInvalidation:
    async def test_invalidate_kb_drops_only_that_kb(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis)
        await cache.get_or_compute_kb(ORG, "kb-a", _loader({"a": "1"}))
        await cache.get_or_compute_kb(ORG, "kb-b", _loader({"b": "2"}))

        await cache.invalidate_kb(ORG, "kb-a")

        assert cache._kb_key(ORG, "kb-a") not in redis.strings
        assert cache._kb_key(ORG, "kb-b") in redis.strings

    async def test_invalidate_connector_drops_both_shapes(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis)
        await cache.get_or_compute_app_connector(ORG, CONNECTOR, _loader({"a": "1"}))
        await cache.get_or_compute_user_connector(ORG, CONNECTOR, USER, _loader({"b": "2"}))

        await cache.invalidate_connector(ORG, CONNECTOR)

        assert cache._app_connector_key(ORG, CONNECTOR) not in redis.strings
        assert cache._user_connector_key(ORG, CONNECTOR) not in redis.hashes

    async def test_one_delete_clears_every_user_of_a_connector(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis)
        for i in range(5):
            await cache.get_or_compute_user_connector(ORG, CONNECTOR, f"user-{i}", _loader({"a": "1"}))

        await cache.invalidate_connector(ORG, CONNECTOR)

        assert cache._user_connector_key(ORG, CONNECTOR) not in redis.hashes

    async def test_invalidation_is_a_noop_when_disabled(self) -> None:
        redis = FakeRedis()
        cache = _cache(redis, enabled=False)
        await cache.invalidate_kb(ORG, KB)
        assert not redis.calls


class TestClose:
    async def test_close_disables_and_releases(self) -> None:
        redis = FakeRedis()
        redis.aclose = AsyncMock()
        cache = _cache(redis)

        await cache.close()

        assert cache.enabled is False
        redis.aclose.assert_awaited_once()

    async def test_close_is_idempotent(self) -> None:
        cache = _cache(FakeRedis())
        await cache.close()
        await cache.close()
