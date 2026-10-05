"""
Regression test: one ArangoHTTPClient used from two event loops.

The indexing service shares one graph provider between the consumer's worker
loop (record handlers) and the main loop (stale-record recovery and the vector
membership backfill). A call from one loop must not break a request that is in
flight on the other loop.

Runs against a real local HTTP server so the sockets, the connector and the
two loops behave as they do in production.
"""

import asyncio
import contextlib
import logging
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar
from unittest.mock import MagicMock

from aiohttp import web

from app.services.graph_db.arango.arango_http_client import ArangoHTTPClient

TIMEOUT = 10
T = TypeVar("T")


class _LoopThread:
    """An event loop running forever in its own thread."""

    def __init__(self, name: str) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name=name, daemon=True)
        self.thread.start()

    def run(self, coro: Coroutine[Any, Any, T]) -> T:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(TIMEOUT)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(TIMEOUT)
        self.loop.close()


class _FakeArango:
    """Answers /_api/version at once and holds each cursor request until released."""

    def __init__(self) -> None:
        self.query_arrived = threading.Event()
        self.release_query = threading.Event()
        self.server = _LoopThread("fake-arango")
        self.port = self.server.run(self._start())

    async def _version(self, request: web.Request) -> web.Response:
        return web.json_response({"version": "3.11.0"})

    async def _cursor(self, request: web.Request) -> web.Response:
        self.query_arrived.set()
        await asyncio.get_running_loop().run_in_executor(None, self.release_query.wait, TIMEOUT)
        return web.json_response({"result": [1], "hasMore": False, "error": False}, status=201)

    async def _start(self) -> int:
        app = web.Application()
        app.router.add_get("/_api/version", self._version)
        app.router.add_post("/_db/test_db/_api/cursor", self._cursor)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        return self.runner.addresses[0][1]

    def stop(self) -> None:
        self.release_query.set()
        self.server.run(self.runner.cleanup())
        self.server.stop()


def test_call_from_second_loop_does_not_break_in_flight_request() -> None:
    arango = _FakeArango()
    worker = _LoopThread("indexing-worker")
    main = _LoopThread("main")
    client = ArangoHTTPClient(
        base_url=f"http://127.0.0.1:{arango.port}",
        username="root",
        password="secret",
        database="test_db",
        logger=MagicMock(spec=logging.Logger),
    )
    try:
        # A record handler on the worker loop has a query in flight...
        in_flight = asyncio.run_coroutine_threadsafe(
            client.execute_aql("RETURN 1"), worker.loop
        )
        assert arango.query_arrived.wait(TIMEOUT)

        # ...when a recovery/backfill tick on the main loop uses the same client.
        assert main.run(client.connect()) is True

        # The server answers. The worker's request must still succeed.
        arango.release_query.set()
        worker.loop.call_soon_threadsafe(lambda: None)  # wake the worker's selector
        assert in_flight.result(TIMEOUT) == [1]
    finally:
        for loop_thread in (worker, main):
            with contextlib.suppress(Exception):
                loop_thread.run(client.disconnect())
            loop_thread.stop()
        arango.stop()


def test_session_table_is_safe_when_two_threads_create_sessions() -> None:
    """Two loop threads creating their first session at the same time.

    The worker thread is part-way through dropping sessions of closed loops
    when the main thread adds its own session. The other thread must not be
    able to change the table in the middle of that walk.
    """
    worker = _LoopThread("indexing-worker")
    main = _LoopThread("main")
    client = ArangoHTTPClient(
        base_url="http://127.0.0.1:1",
        username="root",
        password="secret",
        database="test_db",
        logger=MagicMock(spec=logging.Logger),
    )
    main_created = threading.Event()
    main_errors: list[BaseException] = []

    def main_gets_session() -> None:
        try:
            main.run(client._get_session())
        except BaseException as e:  # reported by the assertions below
            main_errors.append(e)
        finally:
            main_created.set()

    interleaved = False

    def dead_loop_is_closed() -> bool:
        # The first check happens on the worker thread, mid-walk. Let the main
        # thread run its own _get_session right here, and give it time to finish
        # unless something makes it wait.
        nonlocal interleaved
        if not interleaved:
            interleaved = True
            threading.Thread(target=main_gets_session, daemon=True).start()
            main_created.wait(0.5)
        return True

    dead_loop = MagicMock()
    dead_loop.is_closed.side_effect = dead_loop_is_closed
    client._sessions[dead_loop] = MagicMock()

    try:
        worker.run(client._get_session())
        assert main_created.wait(TIMEOUT)
        assert main_errors == []
        assert dead_loop not in client._sessions
        assert set(client._sessions) == {worker.loop, main.loop}
    finally:
        for loop_thread in (worker, main):
            with contextlib.suppress(Exception):
                loop_thread.run(client.disconnect())
            loop_thread.stop()
