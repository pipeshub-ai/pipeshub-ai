"""Handing the Neo4j client from the main event loop to the worker loop.

The indexing service builds its graph client on the main loop during container
init, then runs the pipeline on the record consumer's worker loop
(`indexing_main.start_kafka_consumers`). Two pieces of the client bind to the
loop that created them — the driver's connection pool, through the futures it
holds, and `_connect_lock`, the first time it is actually contended — so the
handover has to release both on the owning loop.

Closing the driver from the destination loop instead raises "attached to a
different loop". That still reconnects, which is why it surfaced only as a
warning, but it abandons the pool rather than closing it and prints on every
startup — noise that would bury a genuine close failure.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.neo4j.neo4j_client import Neo4jClient

pytestmark = pytest.mark.asyncio


def _client(driver=None) -> Neo4jClient:
    client = Neo4jClient.__new__(Neo4jClient)
    client.logger = MagicMock()
    client.driver = driver
    client._active_sessions = {}
    client._session_locks = {}
    client._connect_lock = asyncio.Lock()
    return client


class TestReleasesTheDriver:
    async def test_closes_the_driver(self):
        driver = AsyncMock()
        client = _client(driver)

        await client.close_for_loop_handover()

        driver.close.assert_awaited_once()

    async def test_clears_the_handle_so_connect_rebuilds(self):
        """`connect()` returns early when a driver is already set, so a stale
        handle would hand the new loop the old pool."""
        client = _client(AsyncMock())

        await client.close_for_loop_handover()

        assert client.driver is None

    async def test_clears_the_handle_even_when_close_fails(self):
        """A driver that cannot be closed must still not be reused — the point
        of the handover is that the next loop gets a fresh one.

        The failure mode is not hypothetical: the cross-loop error this method
        exists to avoid is a bare RuntimeError, which `disconnect()` does not
        catch."""
        driver = AsyncMock()
        driver.close = AsyncMock(side_effect=RuntimeError("attached to a different loop"))
        client = _client(driver)

        await client.close_for_loop_handover()

        assert client.driver is None
        client.logger.warning.assert_called()

    async def test_does_not_raise_when_close_fails(self):
        """Startup must proceed. Every caller re-deciding what an unclosable
        driver means is how the original warning ended up at the call site."""
        driver = AsyncMock()
        driver.close = AsyncMock(side_effect=RuntimeError("boom"))
        client = _client(driver)

        await client.close_for_loop_handover()  # must not raise

        assert client._connect_lock is not None

    async def test_is_safe_when_already_disconnected(self):
        client = _client(None)

        await client.close_for_loop_handover()

        assert client.driver is None

    async def test_closes_active_sessions_too(self):
        """Sessions are loop-bound as well; disconnect() is reused rather than
        reaching past it to the driver."""
        session = AsyncMock()
        client = _client(AsyncMock())
        client._active_sessions = {"txn-1": session}

        await client.close_for_loop_handover()

        session.close.assert_awaited_once()
        assert client._active_sessions == {}


class TestReleasesTheLock:
    async def test_replaces_the_connect_lock(self):
        """A lock that has bound to this loop would raise the moment it is
        contended on the worker loop."""
        client = _client(AsyncMock())
        original = client._connect_lock

        await client.close_for_loop_handover()

        assert client._connect_lock is not original

    async def test_the_replacement_is_unlocked(self):
        client = _client(AsyncMock())
        async with client._connect_lock:
            pass  # held, so it has now bound to this loop

        await client.close_for_loop_handover()

        assert not client._connect_lock.locked()

    async def test_the_replacement_is_usable_from_another_loop(self):
        """The property that matters: the worker loop must be able to contend
        for it without hitting the loop-bound guard."""
        client = _client(AsyncMock())
        async with client._connect_lock:
            pass

        await client.close_for_loop_handover()
        lock = client._connect_lock

        async def contend_on_another_loop() -> str:
            # Two waiters, so acquire() cannot take its uncontended fast path
            # and must resolve the lock's bound loop.
            async def hold():
                async with lock:
                    await asyncio.sleep(0.01)

            await asyncio.gather(hold(), hold())
            return "ok"

        result: list = []

        def run() -> None:
            result.append(asyncio.run(contend_on_another_loop()))

        import threading

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(timeout=5)

        assert result == ["ok"]


class TestWhyTheOldPlacementFailed:
    async def test_a_loop_bound_future_cannot_be_awaited_elsewhere(self):
        """The mechanism behind the reported warning, pinned so the reasoning
        does not have to be taken on trust."""
        loop_owned_future: asyncio.Future = asyncio.get_running_loop().create_future()

        async def await_it_on_another_loop() -> None:
            await asyncio.wait_for(asyncio.shield(loop_owned_future), timeout=0.05)

        error: list = []

        def run() -> None:
            try:
                asyncio.run(await_it_on_another_loop())
            except RuntimeError as e:
                error.append(str(e))
            except Exception:
                pass

        import threading

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(timeout=5)

        assert error, "expected a cross-loop RuntimeError"
        assert "attached to a different loop" in error[0]
