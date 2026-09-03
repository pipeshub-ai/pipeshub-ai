"""Caching the storage download URL.

Resolving one is a gateway round trip that does a Mongo document lookup, a KV
config read and an S3 signing call, and a chat turn does ~120 of them. The URLs
are signed for 3600s, so caching them well inside that window removes almost all
of those hops. These pin the behaviour that makes it safe: off by default, keyed
per org, and never fatal when Redis or a stale URL misbehaves.

The cache is exercised at the `IRedisConnectionProvider` seam (R18): tests stub
`get_redis_provider`, never `redis.asyncio.Redis` directly, since `blob_storage`
builds its client through the connection provider (R12) rather than
constructing one itself.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import asyncio

import pytest

from app.services.cache.redis_signed_url_cache import RedisSignedUrlCache

fakeredis_aioredis = pytest.importorskip("fakeredis.aioredis")

from app.modules.transformers import blob_storage as bs
from app.modules.transformers.blob_storage import BlobStorage, signed_url_cache_seconds
from app.services.cache.interface import NoopSignedUrlCache
from app.services.cache.redis_signed_url_cache import RedisSignedUrlCache


@pytest.fixture(autouse=True)
def _reset_shared_redis():
    """The cache is cached per event loop at module scope; clear it between
    tests so one test's verdict does not leak into the next."""
    bs._shared_redis.clear()
    yield
    bs._shared_redis.clear()


def _blob() -> BlobStorage:
    return BlobStorage(logger=MagicMock(), config_service=MagicMock(), graph_provider=MagicMock())


def _fake_provider(client: AsyncMock) -> MagicMock:
    provider = MagicMock()
    provider.create_client.return_value = client
    return provider


class TestTTLConfig:
    def test_disabled_by_default(self, monkeypatch) -> None:
        monkeypatch.delenv("PIPESHUB_SIGNED_URL_CACHE_SECONDS", raising=False)
        assert signed_url_cache_seconds() == 0

    def test_reads_the_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_SIGNED_URL_CACHE_SECONDS", "1800")
        assert signed_url_cache_seconds() == 1800

    def test_clamped_below_the_signed_url_lifetime(self, monkeypatch) -> None:
        """URLs are signed for 3600s; handing out one about to expire is worse
        than not caching."""
        monkeypatch.setenv("PIPESHUB_SIGNED_URL_CACHE_SECONDS", "99999")
        assert signed_url_cache_seconds() == 3000

    def test_garbage_falls_back_to_disabled(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_SIGNED_URL_CACHE_SECONDS", "soon")
        assert signed_url_cache_seconds() == 0


class TestKeying:
    def test_key_is_scoped_per_org(self) -> None:
        """A document id alone would share a URL across tenants."""
        a = BlobStorage._signed_url_key("org-a", "doc-1")
        b = BlobStorage._signed_url_key("org-b", "doc-1")
        assert a != b
        assert "org-a" in a and "doc-1" in a


class TestCacheDisabled:
    @pytest.mark.asyncio
    async def test_noop_cache_when_ttl_is_zero(self, monkeypatch) -> None:
        monkeypatch.delenv("PIPESHUB_SIGNED_URL_CACHE_SECONDS", raising=False)
        cache = await _blob()._signed_url_client()
        assert isinstance(cache, NoopSignedUrlCache)

    @pytest.mark.asyncio
    async def test_reads_and_writes_are_noops_when_disabled(self, monkeypatch) -> None:
        monkeypatch.delenv("PIPESHUB_SIGNED_URL_CACHE_SECONDS", raising=False)
        b = _blob()
        assert await b._cached_signed_url("org", "doc") is None
        await b._store_signed_url("org", "doc", "https://s3/x")  # must not raise


class TestFailureIsNeverFatal:
    @pytest.mark.asyncio
    async def test_redis_read_failure_reports_a_miss(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_SIGNED_URL_CACHE_SECONDS", "600")
        b = _blob()
        client = MagicMock()
        client.get = AsyncMock(side_effect=RuntimeError("redis down"))
        bs._shared_redis[asyncio.get_running_loop()] = RedisSignedUrlCache(client)

        assert await b._cached_signed_url("org", "doc") is None

    @pytest.mark.asyncio
    async def test_redis_write_failure_is_swallowed(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_SIGNED_URL_CACHE_SECONDS", "600")
        b = _blob()
        client = MagicMock()
        client.set = AsyncMock(side_effect=RuntimeError("redis down"))
        bs._shared_redis[asyncio.get_running_loop()] = RedisSignedUrlCache(client)

        await b._store_signed_url("org", "doc", "https://s3/x")  # must not raise

    @pytest.mark.asyncio
    async def test_client_construction_failure_disables_quietly(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_SIGNED_URL_CACHE_SECONDS", "600")
        b = _blob()
        b.config_service.get_redis_config = AsyncMock(side_effect=RuntimeError("no config"))
        cache = await b._signed_url_client()
        assert isinstance(cache, NoopSignedUrlCache)

    @pytest.mark.asyncio
    async def test_ttl_is_passed_to_redis(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_SIGNED_URL_CACHE_SECONDS", "900")
        b = _blob()
        client = MagicMock()
        client.set = AsyncMock()
        bs._shared_redis[asyncio.get_running_loop()] = RedisSignedUrlCache(client)

        await b._store_signed_url("org", "doc", "https://s3/x")

        assert client.set.await_args.kwargs["ex"] == 900


class TestClientConstruction:
    """RedisConfig is an object, not a dict — subscripting it silently disabled
    the cache on the first deployment."""

    @pytest.mark.asyncio
    async def test_builds_the_client_from_redis_config_attributes(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_SIGNED_URL_CACHE_SECONDS", "600")
        b = _blob()
        cfg = MagicMock(host="r", port=6379, password="pw", db=0)
        b.config_service.get_redis_config = AsyncMock(return_value=cfg)

        fake_client = AsyncMock()
        with patch(
            "app.modules.transformers.blob_storage.get_redis_provider",
            return_value=_fake_provider(fake_client),
        ) as get_provider:
            cache = await b._signed_url_client()

        assert isinstance(cache, RedisSignedUrlCache)
        get_provider.assert_called_once()
        conn_config = get_provider.call_args[0][0]
        assert (conn_config.host, conn_config.port, conn_config.password, conn_config.db) == (
            "r",
            6379,
            "pw",
            0,
        )
        fake_client.ping.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unreachable_redis_disables_rather_than_raising(self, monkeypatch) -> None:
        """Redis needs auth here; a failed ping must not break record fetches."""
        monkeypatch.setenv("PIPESHUB_SIGNED_URL_CACHE_SECONDS", "600")
        b = _blob()
        b.config_service.get_redis_config = AsyncMock(
            return_value=MagicMock(host="r", port=6379, password=None, db=0)
        )

        fake_client = AsyncMock()
        fake_client.ping = AsyncMock(side_effect=RuntimeError("NOAUTH"))
        with patch(
            "app.modules.transformers.blob_storage.get_redis_provider",
            return_value=_fake_provider(fake_client),
        ):
            cache = await b._signed_url_client()

        assert isinstance(cache, NoopSignedUrlCache)
        fake_client.aclose.assert_awaited_once()


class TestClientIsSharedNotPerInstance:
    """BlobStorage is constructed ad hoc at ~20 call sites, several of them per
    request and per tool call. A per-instance client opened and leaked a Redis
    connection pool per request -- the same trap get_shared_session documents."""

    @pytest.mark.asyncio
    async def test_many_instances_share_one_cache(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_SIGNED_URL_CACHE_SECONDS", "600")
        cfg = MagicMock(host="r", port=6379, password=None, db=0)
        fake_client = AsyncMock()

        with patch(
            "app.modules.transformers.blob_storage.get_redis_provider",
            return_value=_fake_provider(fake_client),
        ) as get_provider:
            caches = []
            for _ in range(5):
                b = _blob()
                b.config_service.get_redis_config = AsyncMock(return_value=cfg)
                caches.append(await b._signed_url_client())

        assert get_provider.call_count == 1, "one pool for the process, not one per instance"
        assert all(c is caches[0] for c in caches)

    @pytest.mark.asyncio
    async def test_unreachable_redis_is_only_attempted_once(self, monkeypatch) -> None:
        """An outage must not cost a 2s connect per record fetch."""
        monkeypatch.setenv("PIPESHUB_SIGNED_URL_CACHE_SECONDS", "600")
        cfg = MagicMock(host="r", port=6379, password=None, db=0)
        fake_client = AsyncMock()
        fake_client.ping = AsyncMock(side_effect=RuntimeError("down"))

        with patch(
            "app.modules.transformers.blob_storage.get_redis_provider",
            return_value=_fake_provider(fake_client),
        ) as get_provider:
            for _ in range(4):
                b = _blob()
                b.config_service.get_redis_config = AsyncMock(return_value=cfg)
                cache = await b._signed_url_client()
                assert isinstance(cache, NoopSignedUrlCache)

        assert get_provider.call_count == 1, "the failure verdict must be cached"
        fake_client.aclose.assert_awaited_once()


class TestSignedUrlKeyNamespacing:
    """`REDIS_KEY_NAMESPACE` has to reach these keys too (R9).

    Every signed-URL key is `sigurl:<org>:<doc>`, and org ids do not differ
    between a staging and a production copy of the same tenant -- so two
    deployments sharing one Redis would serve each other's signed URLs, which
    are bearer credentials for blob content.
    """

    @pytest.mark.asyncio
    async def test_a_value_written_under_one_namespace_misses_under_another(
        self,
    ) -> None:
        fake = fakeredis_aioredis.FakeRedis(decode_responses=True)
        cache_a = RedisSignedUrlCache(fake, "tenant-a")
        cache_b = RedisSignedUrlCache(fake, "tenant-b")

        await cache_a.set("sigurl:org1:doc1", "https://a.example/signed", 60)

        assert await cache_a.get("sigurl:org1:doc1") == "https://a.example/signed"
        assert await cache_b.get("sigurl:org1:doc1") is None

    @pytest.mark.asyncio
    async def test_an_unset_namespace_leaves_the_key_unchanged(self) -> None:
        """Existing deployments keep their current keys; the namespace is opt-in."""
        fake = fakeredis_aioredis.FakeRedis(decode_responses=True)
        cache = RedisSignedUrlCache(fake)

        await cache.set("sigurl:org1:doc1", "https://example/signed", 60)

        assert await fake.get("sigurl:org1:doc1") == "https://example/signed"
