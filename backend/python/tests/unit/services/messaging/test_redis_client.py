from __future__ import annotations

import asyncio
import logging
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.config import RedisConfig
from app.services.messaging.redis_client import RedisClientRegistry

fakeredis_aioredis = pytest.importorskip("fakeredis.aioredis")


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("test_redis_client")


def _registry(logger: logging.Logger, **kwargs: object) -> RedisClientRegistry:
    return RedisClientRegistry(
        logger,
        RedisConfig(host="redis", port=6379),
        **{"max_connections": 8, "socket_timeout_seconds": 2.0, **kwargs},  # type: ignore[arg-type]
    )


class TestPerLoopAffinity:
    """redis.asyncio clients bind to the loop that first uses them. The
    indexing consumers run a worker-thread loop alongside the main one, and
    handing each its own client is what removes the cross-thread hop whose 5s
    deadline used to cancel in-flight commands."""

    @pytest.mark.asyncio
    async def test_same_loop_reuses_one_client(self, logger) -> None:
        registry = _registry(logger)
        with patch.object(RedisClientRegistry, "_build_client", lambda self: AsyncMock()):
            assert registry.client() is registry.client()

    def test_separate_threads_get_separate_clients(self, logger) -> None:
        registry = _registry(logger)
        seen: list[object] = []

        def run() -> None:
            async def main() -> None:
                seen.append(registry.client())

            asyncio.run(main())

        with patch.object(RedisClientRegistry, "_build_client", lambda self: AsyncMock()):
            threads = [threading.Thread(target=run) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert len(seen) == 2
        assert seen[0] is not seen[1]

    def test_a_client_from_a_closed_loop_is_discarded(self, logger) -> None:
        """A worker thread restarted between stop() and start() must not be
        handed a client bound to the loop that has since been closed — using
        it raises 'attached to a different loop' on the first command."""
        registry = _registry(logger)
        clients: list[object] = []

        with patch.object(RedisClientRegistry, "_build_client", lambda self: AsyncMock()):
            async def main() -> None:
                clients.append(registry.client())

            # Two successive loops on this same thread.
            asyncio.run(main())
            asyncio.run(main())

        assert clients[0] is not clients[1]


class TestBoundedPool:
    """redis-py defaults max_connections to 2**31. Every connection a
    cancelled command destroyed was therefore replaced by a fresh TCP
    connect, and a production host reached Redis's 10,000-client limit."""

    def test_pool_is_bounded_and_blocks_rather_than_growing(self, logger) -> None:
        registry = _registry(logger, max_connections=5)
        client = registry.client()
        pool = client.connection_pool

        assert pool.max_connections == 5
        # BlockingConnectionPool queues waiters instead of minting connections.
        assert type(pool).__name__ == "BlockingConnectionPool"

    def test_socket_timeout_is_applied_to_both_read_and_connect(self, logger) -> None:
        registry = _registry(logger, socket_timeout_seconds=2.0)
        kwargs = registry.client().connection_pool.connection_kwargs

        assert kwargs["socket_timeout"] == 2.0
        assert kwargs["socket_connect_timeout"] == 2.0

    @pytest.mark.asyncio
    async def test_concurrent_commands_never_exceed_the_pool_size(self, logger) -> None:
        """The bound has to hold under exactly the shape that broke
        production: far more concurrent commands than connections."""
        registry = _registry(logger, max_connections=4)
        with patch.object(
            RedisClientRegistry,
            "_build_client",
            lambda self: fakeredis_aioredis.FakeRedis(
                decode_responses=True, max_connections=self._max_connections
            ),
        ):
            client = registry.client()
            await asyncio.gather(*(client.ping() for _ in range(200)))

            pool = client.connection_pool
            created = len(pool._available_connections) + len(pool._in_use_connections)
            assert created <= 4

        await registry.aclose()


class TestCleanup:
    @pytest.mark.asyncio
    async def test_aclose_closes_every_client_and_clears_the_map(self, logger) -> None:
        registry = _registry(logger)
        client = AsyncMock()
        with patch.object(RedisClientRegistry, "_build_client", lambda self: client):
            registry.client()
            await registry.aclose()

        client.aclose.assert_awaited_once()
        assert registry._clients == {}

    @pytest.mark.asyncio
    async def test_one_failing_close_does_not_strand_the_others(self, logger) -> None:
        """A client bound to an already-closed loop cannot be awaited; that
        must not leave the remaining clients open."""
        registry = _registry(logger)
        bad = AsyncMock()
        bad.aclose = AsyncMock(side_effect=RuntimeError("loop is closed"))
        good = AsyncMock()
        registry._clients = {1: (bad, None), 2: (good, None)}

        await registry.aclose()

        good.aclose.assert_awaited_once()
        assert registry._clients == {}

    @pytest.mark.asyncio
    async def test_aclose_is_safe_with_no_clients(self, logger) -> None:
        await _registry(logger).aclose()


class TestRetryConfiguration:
    def test_transient_errors_are_retried_with_backoff(self, logger) -> None:
        """Matches the schedule RedisKeyValueStore already uses, so both
        clients behave the same during a Redis restart."""
        kwargs = _registry(logger).client().connection_pool.connection_kwargs

        assert kwargs["retry"] is not None
        assert kwargs["health_check_interval"] == 30
        retried = {exc.__name__ for exc in kwargs["retry_on_error"]}
        assert {"ConnectionError", "TimeoutError", "OSError"} <= retried


def test_registry_reports_its_bound(caplog: pytest.LogCaptureFixture) -> None:
    registry = RedisClientRegistry(
        MagicMock(),
        RedisConfig(host="redis", port=6379),
        max_connections=0,
        socket_timeout_seconds=0.0,
    )
    # Both are floored, so a misconfigured 0 cannot produce a pool that can
    # never hand out a connection or a timeout that fires instantly.
    assert registry.max_connections == 1
    assert registry.client().connection_pool.connection_kwargs["socket_timeout"] == 0.1
