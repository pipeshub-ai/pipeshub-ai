"""The cache role: ``REDIS_CACHE_URL`` moves rebuildable caches to their own Redis; everything else stays on the main one."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.config import RedisConfig
from app.services.redis.config import RedisConnectionConfig

if TYPE_CHECKING:
    from collections.abc import Iterator

MAIN = RedisConfig(host="main-redis", port=6379, password="main-secret", db=0)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("REDIS_CACHE_URL", "REDIS_PASSWORD", "REDIS_USERNAME", "REDIS_TLS_ENABLED", "REDIS_MODE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("REDIS_KEY_NAMESPACE", "ns")


class TestForCache:
    def test_unset_is_the_main_redis_unchanged(self) -> None:
        assert RedisConnectionConfig.for_cache(MAIN) == RedisConnectionConfig.from_redis_config(MAIN)

    def test_blank_is_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REDIS_CACHE_URL", "  ")
        assert RedisConnectionConfig.for_cache(MAIN).host == "main-redis"

    def test_the_url_names_the_whole_connection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REDIS_CACHE_URL", "rediss://cacheuser:p%40ss%3Aw@cache-redis:6390/3")
        monkeypatch.setenv("REDIS_PASSWORD", "main-secret")
        monkeypatch.setenv("REDIS_CLUSTER_ENDPOINTS", "main-a:6379,main-b:6379")
        cfg = RedisConnectionConfig.for_cache(MAIN)
        assert (cfg.host, cfg.port, cfg.username, cfg.password, cfg.db, cfg.tls) == (
            "cache-redis", 6390, "cacheuser", "p@ss:w", 3, True)
        # On cluster the URL's host is the seed, not the main cluster's endpoints.
        assert cfg.cluster_endpoints == []
        assert cfg.key_namespace == "ns"

    def test_no_credentials_in_the_url_means_none_not_the_main_ones(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REDIS_CACHE_URL", "redis://cache-redis")
        monkeypatch.setenv("REDIS_PASSWORD", "main-secret")
        monkeypatch.setenv("REDIS_USERNAME", "main-user")
        monkeypatch.setenv("REDIS_TLS_ENABLED", "true")
        cfg = RedisConnectionConfig.for_cache(MAIN)
        assert (cfg.port, cfg.username, cfg.password, cfg.db, cfg.tls) == (6379, None, None, 0, False)

    def test_ipv6_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REDIS_CACHE_URL", "redis://[::1]:6391")
        assert (RedisConnectionConfig.for_cache(MAIN).host, RedisConnectionConfig.for_cache(MAIN).port) == ("::1", 6391)

    @pytest.mark.parametrize("url", [
        "cache-redis:6379", "http://cache-redis", "redis://", "redis://cache-redis:notaport",
        "redis://cache-redis/x", "redis://cache-redis:70000",
    ])
    def test_a_malformed_url_is_refused(self, monkeypatch: pytest.MonkeyPatch, url: str) -> None:
        monkeypatch.setenv("REDIS_CACHE_URL", url)
        with pytest.raises(ValueError, match="REDIS_CACHE_URL"):
            RedisConnectionConfig.for_cache(MAIN)


def _recording_provider(seen: list[RedisConnectionConfig]) -> Any:  # noqa: ANN401
    def get(config: RedisConnectionConfig, *_: object, **__: object) -> MagicMock:
        seen.append(config)
        client = MagicMock()
        client.ping = AsyncMock(return_value=True)
        client.aclose = AsyncMock()
        provider = MagicMock(key_namespace="ns")
        provider.get_client.return_value = client
        provider.create_client.return_value = client
        return provider
    return get


@pytest.fixture
def config_service() -> MagicMock:
    return MagicMock(get_redis_config=AsyncMock(return_value=MAIN))


@pytest.fixture
def _fresh_blob_state() -> Iterator[None]:
    from app.modules.transformers import blob_storage as bs

    bs._shared_redis.clear()
    bs._record_redis.clear()
    yield
    bs._shared_redis.clear()
    bs._record_redis.clear()


async def _open_every_cache(config_service: MagicMock) -> dict[str, list[str]]:
    """The host each cache connected to, per cache, in the order it asked for them."""
    from app.modules.transformers import blob_storage as bs
    from app.services.cache import accessible_records_cache as arc
    from app.services.permissions.cache import ViewerScopeCache

    seen: list[RedisConnectionConfig] = []
    hosts: dict[str, list[str]] = {}
    get = _recording_provider(seen)
    openers = {
        "viewer_scopes": lambda: ViewerScopeCache.create(config_service),
        "accessible_records": lambda: arc.AccessibleRecordsCache.create(MagicMock(), config_service),
        "signed_urls": lambda: bs.get_shared_signed_url_cache(config_service, MagicMock()),
        "record_cache": lambda: bs._record_redis_client(config_service, MagicMock()),
    }
    with patch("app.services.redis.connection_provider_factory.get_redis_provider", get), \
            patch.object(arc, "get_redis_provider", get), patch.object(bs, "get_redis_provider", get), \
            patch.object(bs, "signed_url_cache_seconds", return_value=60):
        for name, open_cache in openers.items():
            start = len(seen)
            await open_cache()
            hosts[name] = [c.host for c in seen[start:]]
    return hosts


async def _open_main_role_clients(config_service: MagicMock, seen: list[RedisConnectionConfig]) -> None:
    """The key-value store and the broker's Redis Streams client, the main role's representatives."""
    from app.services.key_value.redis import redis as kv
    from app.services.messaging.config import RedisStreamsConfig
    from app.services.messaging.redis_streams import producer

    get = _recording_provider(seen)
    with patch.object(kv, "get_redis_provider", get), patch.object(producer, "get_redis_provider", get):
        await kv.RedisService.create(MagicMock(), config_service)
        producer.RedisStreamsProducer(MagicMock(), RedisStreamsConfig(**MAIN.model_dump()))


@pytest.mark.usefixtures("_fresh_blob_state")
class TestCallersPickTheirRole:
    async def test_without_a_cache_url_every_cache_uses_the_main_redis(self, config_service: MagicMock) -> None:
        hosts = await _open_every_cache(config_service)
        # Four caches, plus the counters of the viewer-scope cache and the accessible-records epochs.
        assert hosts == {"viewer_scopes": ["main-redis"] * 2, "accessible_records": ["main-redis"] * 2,
                         "signed_urls": ["main-redis"], "record_cache": ["main-redis"]}

    async def test_with_a_cache_url_caches_go_there_and_the_main_role_does_not(
        self, config_service: MagicMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("REDIS_CACHE_URL", "redis://:cache-secret@cache-redis:6390")
        hosts = await _open_every_cache(config_service)
        # The cached values go to the cache Redis (asked for first); the viewer-scope counters and the accessible-records
        # epochs, whose loss would change what is served, stay on the main one (asked for second).
        assert hosts == {"viewer_scopes": ["cache-redis", "main-redis"], "accessible_records": ["cache-redis", "main-redis"],
                         "signed_urls": ["cache-redis"], "record_cache": ["cache-redis"]}
        assert sum(len(h) for h in hosts.values()) == 6

        main: list[RedisConnectionConfig] = []
        await _open_main_role_clients(config_service, main)
        assert len(main) == 2
        assert {c.host for c in main} == {"main-redis"}
