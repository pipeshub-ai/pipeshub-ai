"""One Neo4j driver per event loop.

A driver's connection pool binds to the loop that created it, through the
futures it holds. The indexing service builds this client on the main loop
during container init and then runs its pipeline on the record consumer's
worker loop, so a single shared driver made every query from over there fail
with "attached to a different loop".

That used to be patched at the call site in `indexing_main`: close the driver
on the main loop, then reconnect onto the worker loop. It worked, but it left
the client itself unsafe for any second loop and put the burden on each caller
to remember. Keying the driver by loop — the way `QdrantService` keys its
clients — makes the client correct on its own and removed that hack.
"""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.graph_db.neo4j.neo4j_client import Neo4jClient

pytestmark = pytest.mark.asyncio


def _client() -> Neo4jClient:
    client = Neo4jClient.__new__(Neo4jClient)
    client.logger = MagicMock()
    client.uri = "bolt://localhost:7687"
    client.username = "neo4j"
    client.password = "pw"
    client.database = "neo4j"
    client._drivers = {}
    client._driver_override = None
    client._connect_locks = {}
    client._drivers_lock = threading.Lock()
    client._active_sessions = {}
    client._session_locks = {}
    client._session_loops = {}
    return client


def _run_on_another_loop(coro_factory):
    """Run `coro_factory()` on a loop in another thread, as the consumer does."""
    result: list = []
    errors: list = []

    def run() -> None:
        try:
            result.append(asyncio.run(coro_factory()))
        except BaseException as error:  # noqa: BLE001 - re-raised on the caller
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(timeout=10)
    # Swallowing either of these would let a test pass on a worker loop that
    # actually blew up or hung — the None it returned would read as "no driver
    # here", which is exactly what some of these tests assert.
    assert not thread.is_alive(), "the worker loop did not finish"
    if errors:
        raise errors[0]
    return result[0]


class TestDriverIsPerLoop:
    async def test_a_second_loop_builds_its_own_driver(self):
        """The bug this design removes: the worker loop must not be handed the
        main loop's pool."""
        client = _client()
        built: list = []

        def fake_driver(*a, **kw):
            driver = AsyncMock()
            built.append(driver)
            return driver

        with patch(
            "app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver",
            side_effect=fake_driver,
        ), patch.object(Neo4jClient, "_ensure_database_exists", AsyncMock()):
            await client.connect()
            main_driver = client.driver

            other = _run_on_another_loop(client.connect)

            assert other is True
            assert len(built) == 2, "the second loop reused the first loop's driver"
            assert client.driver is main_driver, "this loop's driver was replaced"

    async def test_the_same_loop_reuses_its_driver(self):
        """Per-loop must not mean per-call."""
        client = _client()
        with patch(
            "app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver",
            side_effect=lambda *a, **kw: AsyncMock(),
        ), patch.object(Neo4jClient, "_ensure_database_exists", AsyncMock()):
            await client.connect()
            first = client.driver
            await client.connect()

            assert client.driver is first

    async def test_driver_is_none_on_a_loop_that_never_connected(self):
        client = _client()
        with patch(
            "app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver",
            side_effect=lambda *a, **kw: AsyncMock(),
        ), patch.object(Neo4jClient, "_ensure_database_exists", AsyncMock()):
            await client.connect()

        assert _run_on_another_loop(_seen_driver(client)) is None


def _seen_driver(client):
    async def _read():
        return client.driver

    return _read


class TestConnectLockIsPerLoop:
    async def test_each_loop_gets_its_own_lock(self):
        """A shared asyncio.Lock binds to the loop that first contends for it,
        so the second loop would raise the moment two coroutines raced."""
        client = _client()
        with patch(
            "app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver",
            side_effect=lambda *a, **kw: AsyncMock(),
        ), patch.object(Neo4jClient, "_ensure_database_exists", AsyncMock()):
            await client.connect()

            async def connect_twice_concurrently() -> str:
                # Two waiters, so acquire() cannot take its uncontended fast
                # path and must resolve the lock's bound loop.
                await asyncio.gather(client.connect(), client.connect())
                return "ok"

            assert _run_on_another_loop(connect_twice_concurrently) == "ok"


class TestAssignmentAndDisconnect:
    async def test_an_assigned_driver_is_served_to_every_loop(self):
        """Tests and legacy callers set `client.driver = <double>` and expect
        it back regardless of where they read it."""
        client = _client()
        double = AsyncMock()
        client.driver = double

        assert client.driver is double
        assert _run_on_another_loop(_seen_driver(client)) is double

    async def test_assigning_none_clears_it(self):
        client = _client()
        client.driver = AsyncMock()
        client.driver = None

        assert client.driver is None

    async def test_disconnect_closes_every_loops_driver(self):
        """Shutdown runs on one loop; the others' drivers must still be closed
        rather than abandoned with their pools open."""
        client = _client()
        built: list = []

        with patch(
            "app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver",
            side_effect=lambda *a, **kw: built.append(AsyncMock()) or built[-1],
        ), patch.object(Neo4jClient, "_ensure_database_exists", AsyncMock()):
            await client.connect()
            _run_on_another_loop(client.connect)

        await client.disconnect()

        assert len(built) == 2
        for driver in built:
            driver.close.assert_awaited_once()
        assert client._drivers == {}

    async def test_a_foreign_loops_driver_is_closed_on_that_loop(self):
        """Not merely "close was awaited" — closing a driver from the wrong
        loop is what raises "attached to a different loop" and abandons the
        pool. The close must run on the loop that built it."""
        client = _client()
        closed_on: list = []

        worker_loop: list = []
        ready = threading.Event()
        stop = threading.Event()

        def run_worker() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            worker_loop.append(loop)
            ready.set()
            loop.call_soon(lambda: None)
            while not stop.is_set():
                loop.run_until_complete(asyncio.sleep(0.01))
            loop.close()

        thread = threading.Thread(target=run_worker, daemon=True)
        thread.start()
        assert ready.wait(timeout=5)

        async def _close() -> None:
            closed_on.append(asyncio.get_running_loop())

        driver = MagicMock()
        driver.close = _close
        client._drivers = {worker_loop[0]: driver}

        try:
            await client.disconnect()
        finally:
            stop.set()
            thread.join(timeout=5)

        assert closed_on == [worker_loop[0]], (
            "the driver was closed from the caller's loop, not its owner's"
        )

    async def test_a_driver_whose_loop_has_stopped_is_discarded(self):
        """Retention is only worth it while a retry is possible.

        Once the owning loop has stopped, nothing can ever run that close: the
        pool went with the loop. Keeping the entry would leak it and, worse,
        leave it in `_drivers` for `connect()` to hand back on a loop that can
        no longer serve it.
        """
        client = _client()

        dead_loop = asyncio.new_event_loop()
        dead_loop.close()

        driver = AsyncMock()
        driver.close = AsyncMock(side_effect=RuntimeError("loop is closed"))
        client._drivers = {dead_loop: driver}
        client._connect_locks = {dead_loop: asyncio.Lock()}

        await client.disconnect()

        assert client._drivers == {}, "a driver on a stopped loop must not be kept"
        assert client._connect_locks == {}

    async def test_a_driver_on_a_live_loop_is_kept_for_retry(self):
        """The paired case: this one can still be closed, so it stays."""
        client = _client()
        driver = AsyncMock()
        driver.close = AsyncMock(side_effect=RuntimeError("busy"))
        # The running loop is this test's own, so a retry remains possible.
        client._drivers = {asyncio.get_running_loop(): driver}

        await client.disconnect()

        assert list(client._drivers.values()) == [driver]

    async def test_disconnect_survives_a_driver_that_cannot_be_closed(self):
        """Closing a driver bound to an already-stopped loop raises; the
        remaining ones must still be closed."""
        client = _client()
        bad, good = AsyncMock(), AsyncMock()
        bad.close = AsyncMock(side_effect=RuntimeError("attached to a different loop"))
        client._drivers = {"loop-a": bad, "loop-b": good}

        await client.disconnect()

        good.close.assert_awaited_once()
        # The one that closed is forgotten; the one still holding an open pool
        # is kept, because dropping it would discard the only reference to it.
        assert client._drivers == {"loop-a": bad}
        client.logger.warning.assert_called()
