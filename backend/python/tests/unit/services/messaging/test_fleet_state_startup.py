"""The indexing service binds its fleet state (breaker, pause budget) on every state store.

Nothing here patches the messaging factory: the retry tracker is what ``MessagingFactory.create_retry_manager`` really returns for
the configured state store (``FailSafeRetryTracker`` on Redis, ``PgRetryTracker`` on PostgreSQL), and the binding is the one
``start_kafka_consumers`` calls.
"""

import logging
import os
from types import SimpleNamespace

import pytest

from app.indexing_main import _bind_fleet_state
from app.modules.pipeline.pause_budget import PauseState
from app.modules.pipeline.pause_store import PauseStore
from app.services.llm_gateway.fleet_breaker import ALLOW, FleetBreaker
from app.services.llm_gateway.fleet_redis import bind_fleet_redis, get_fleet_redis
from app.services.messaging.config import RedisConfig
from app.services.messaging.fail_safe_retry_tracker import FailSafeRetryTracker
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.pg_state import PgRetryTracker


class _Config:
    """An in-memory ConfigurationService for the two calls the install secret makes."""

    def __init__(self, store: dict[str, str]) -> None:
        self.store = store

    async def create_config_if_absent(self, key: str, value: str) -> bool:
        return self.store.setdefault(key, value) is value

    async def get_config(self, key: str, **_: object) -> str | None:
        return self.store.get(key)


def _modes(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    for name in ("REDIS_MODE", "KV_STORE_TYPE", "MESSAGE_BROKER"):
        monkeypatch.delenv(name, raising=False)
    if mode == "postgres":
        monkeypatch.setenv("KV_STORE_TYPE", "postgres")
        monkeypatch.setenv("MESSAGE_BROKER", "postgres")
    else:
        monkeypatch.setenv("REDIS_MODE", mode)
    from app.services.redis import connection_provider_factory as f

    f.reset_redis_provider_registry() if hasattr(f, "reset_redis_provider_registry") else None


@pytest.mark.parametrize(("mode", "tracker"), [("inprocess", FailSafeRetryTracker), ("postgres", PgRetryTracker)])
async def test_the_fleet_state_binds_beside_the_real_retry_tracker(monkeypatch: pytest.MonkeyPatch, mode: str, tracker: type) -> None:
    _modes(monkeypatch, mode)
    config = RedisConfig()
    retry = MessagingFactory.create_retry_manager(logging.getLogger("t"), config)
    assert isinstance(retry, tracker), "the factory's own product, not a stand-in"
    shared: dict[str, str] = {}
    container = SimpleNamespace(config_service=lambda: _Config(shared))
    try:
        fleet = await _bind_fleet_state(container, config, logging.getLogger("t"))
        assert get_fleet_redis() is fleet
        assert len(shared["/services/indexing/spendGuard/hmacKey"]) >= 32, "the install secret exists"
        # the breaker and the pause budget work on what was bound
        breaker = FleetBreaker(fleet)
        assert (await breaker.admit("pk", "s", "job")).code == ALLOW
        await PauseStore().put("vr:rev:classify", PauseState(since_ms=1, sends=2))
        assert (await PauseStore().get("vr:rev:classify")).sends == 2
    finally:
        bind_fleet_redis(None)
        registry = getattr(container, "fleet_redis_registry", None)
        if registry is not None:
            await registry.aclose()
    assert os.environ.get("REDIS_MODE") in (None, mode)


def test_start_kafka_consumers_binds_the_fleet_state_before_anything_pauses() -> None:
    """The binding is part of the real start-up path, not only a helper nobody calls."""
    import inspect

    from app.indexing_main import start_kafka_consumers

    source = inspect.getsource(start_kafka_consumers)
    assert source.index("create_retry_manager") < source.index("_bind_fleet_state(") < source.index("create_producer")


class _ServiceConfig(_Config):
    async def get_redis_config(self) -> RedisConfig:
        return RedisConfig()

    def close(self) -> None:
        return None


async def _assert_the_lifespan_binds_the_fleet(lifespan, app) -> None:
    assert get_fleet_redis() is None
    async with lifespan(app):
        fleet = get_fleet_redis()
        assert fleet is not None, "the service's model calls would use a breaker of their own"
        assert (await FleetBreaker(fleet).admit("pk", "s", "job")).code == ALLOW
    assert get_fleet_redis() is None, "shutdown unbinds the fleet"


async def test_the_extraction_service_binds_the_fleet_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Extraction service classifies through its own gateway: it must use the fleet's breaker, or an outage it sees
    is invisible to the indexing service and every replica."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from fastapi import FastAPI

    import app.extraction_main as extraction_main

    _modes(monkeypatch, "inprocess")
    container = MagicMock()
    container.config_service.return_value = _ServiceConfig({})
    container.logger.return_value = logging.getLogger("t")
    with (
        patch.object(extraction_main, "_get_initialized_container", AsyncMock(return_value=container)),
        patch.object(extraction_main, "DocumentExtraction"),
        patch.object(extraction_main, "mark_process_non_dumpable"),
    ):
        await _assert_the_lifespan_binds_the_fleet(extraction_main.lifespan, FastAPI())


async def test_the_parsing_service_binds_the_fleet_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Table summaries, image descriptions and VLM OCR call models through the parsing service's gateway."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from fastapi import FastAPI

    import app.parsing_main as parsing_main

    _modes(monkeypatch, "inprocess")
    container = MagicMock()
    container.config_service.return_value = _ServiceConfig({})
    container.logger.return_value = logging.getLogger("t")
    governor = MagicMock()
    governor.ceilings = SimpleNamespace(heavy=1, light=1)
    governor.run = AsyncMock()
    with (
        patch.object(parsing_main, "_get_initialized_container", AsyncMock(return_value=container)),
        patch.object(parsing_main, "_build_registry", MagicMock()),
        patch.object(parsing_main, "ResourceGovernor", MagicMock(return_value=governor)),
        patch.object(parsing_main, "is_local_cpu_embedding_configured", AsyncMock(return_value=False)),
        patch.object(parsing_main, "mark_process_non_dumpable"),
    ):
        await _assert_the_lifespan_binds_the_fleet(parsing_main.lifespan, FastAPI())
