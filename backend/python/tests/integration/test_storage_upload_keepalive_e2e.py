"""End-to-end: record uploads against a keep-alive socket the server closed.

Reproduces the production failure (an upload on /api/v1/document/internal/upload
reusing a socket the server had already closed) with a real HTTP server and the
real shared aiohttp session, then proves both fixes:

- ``BlobStorage.save_record_to_storage`` retries an upload after any
  connection failure under one Idempotency-Key, so a retry of an upload the
  server already stored gets that document back instead of a duplicate. The
  Node storage route honours the key (utils/idempotency.ts); this stand-in
  implements the same contract.
- A server keep-alive window far longer than the client pool's (Node now holds
  idle sockets 65s, see app.ts) removes the race outright.

The race needs the client's event loop busy at two moments, as the indexing
worker loop is under load: when the previous response arrives, so the pool's
idle clock starts late, and when the server's idle-close lands, so the FIN is
not processed before the socket is reused. Both are simulated with
``time.sleep`` on the loop. The server runs on its own thread and loop, as Node
runs in its own process.
"""
from __future__ import annotations

import asyncio
import base64
import os
import select
import socket
import threading
import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from aiohttp import web

from app.modules.transformers.blob_storage import (
    BlobStorage,
    close_shared_session,
    get_shared_session,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration

UPLOAD_PATH = "/api/v1/document/internal/upload"
HEALTH_PATH = "/api/v1/health"

# Seconds after the server finishes the priming response. The client pool
# reuses a socket idle for less than NODE_KEEPALIVE_MARGIN_SECONDS (4s) since
# its release, so reuse happens at ~6.3s, 3.6s after the late release.
STALL = 2.7  # loop busy as the response arrives: released at ~2.7s
IDLE = 1.9  # ~4.6s
BUSY = 1.7  # ~6.3s: spans a 5s idle-close, which the server may round up to 6s

# Incompressible, so the upload body stays large after zstd: the connection
# dies while it is still being written, as it did for image-heavy records.
_PAYLOAD = base64.b64encode(os.urandom(1_500_000)).decode()
# Far more than loopback socket buffers hold, so a connection reset after the
# first 64 KiB always lands while the body is still being written.
_LARGE_PAYLOAD = base64.b64encode(os.urandom(16_000_000)).decode()


class _NodeLikeServer:
    """Takes multipart uploads like the Node storage route, on its own thread."""

    def __init__(
        self,
        keepalive_timeout: float,
        *,
        reset_first_upload: bool = False,
        drop_first_response: bool = False,
    ) -> None:
        self.keepalive_timeout = keepalive_timeout
        self.reset_first_upload = reset_first_upload
        self.drop_first_response = drop_first_response
        self.reset_uploads = 0
        self.dropped_responses = 0
        self.completed_uploads = 0
        self.keys_seen: list[str | None] = []
        self._documents_by_key: dict[str, dict[str, str]] = {}
        self.base_url = ""
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=lambda: asyncio.run(self._serve()), daemon=True)

    async def _health(self, _request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def _upload(self, request: web.Request) -> web.Response:
        key = request.headers.get("Idempotency-Key")
        self.keys_seen.append(key)
        if key in self._documents_by_key:
            return web.json_response(self._documents_by_key[key])
        if self.reset_first_upload and self.reset_uploads == 0:
            # Take a little of the body, then drop the connection with unread
            # data pending, so the kernel answers the rest with a reset.
            await request.content.read(64 * 1024)
            self.reset_uploads += 1
            assert request.transport is not None
            request.transport.abort()
            return web.Response(status=500)
        async for part in await request.multipart():
            await part.read()
        self.completed_uploads += 1
        document = {"_id": f"doc-{self.completed_uploads}"}
        if key:
            self._documents_by_key[key] = document
        if self.drop_first_response and self.dropped_responses == 0:
            # Stored, then the connection dies before the answer leaves: the
            # client cannot tell this from a request that never arrived.
            self.dropped_responses += 1
            assert request.transport is not None
            request.transport.abort()
            return web.Response(status=500)
        return web.json_response(document)

    async def _serve(self) -> None:
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_get(HEALTH_PATH, self._health)
        app.router.add_post(UPLOAD_PATH, self._upload)
        runner = web.AppRunner(app, keepalive_timeout=self.keepalive_timeout)
        await runner.setup()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        await web.SockSite(runner, sock).start()
        self.base_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        self._ready.set()
        await self._stop.wait()
        await runner.cleanup()

    def __enter__(self) -> _NodeLikeServer:
        self._thread.start()
        assert self._ready.wait(10), "test server did not start"
        return self

    def __exit__(self, *_exc: object) -> None:
        assert self._loop is not None and self._stop is not None
        self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(10)


@pytest.fixture
async def shared_session() -> AsyncIterator[aiohttp.ClientSession]:
    yield get_shared_session()
    await close_shared_session()


async def _pool_a_socket_released_late(session: aiohttp.ClientSession, base_url: str) -> None:
    """Leave one keep-alive socket in the pool that the server closes as it is reused."""
    connector = session.connector
    assert connector is not None

    async def fetch() -> None:
        async with session.get(base_url + HEALTH_PATH) as response:
            await response.read()

    task = asyncio.create_task(fetch())
    for _ in range(5000):
        await asyncio.sleep(0)
        # No public hook exposes an in-use connection's socket; aiohttp keeps
        # them in the private ``_acquired`` set.
        in_use = [proto for proto in connector._acquired if proto.transport is not None]
        if not in_use:
            continue
        sock = in_use[0].transport.get_extra_info("socket")
        if select.select([sock], [], [], 0.02)[0]:
            time.sleep(STALL)
            break
    else:
        pytest.fail("never observed the priming response arrive")
    await task
    await asyncio.sleep(IDLE)
    time.sleep(BUSY)


def _upload_form() -> aiohttp.FormData:
    form = aiohttp.FormData()
    form.add_field("file", _PAYLOAD.encode(), filename="record.json", content_type="application/json")
    return form


def _blob_storage(base_url: str) -> BlobStorage:
    blob = BlobStorage(logger=MagicMock(), config_service=MagicMock(), graph_provider=None)
    blob._get_auth_and_config = AsyncMock(return_value=({}, base_url, "local"))
    return blob


@pytest.mark.asyncio
async def test_the_harness_reproduces_the_production_failure(shared_session) -> None:
    """Control: a bare upload on the stale socket fails, as it did in production.

    Production saw "Can not write request body"; the same race surfaces as a
    bare reset or broken pipe instead when the kernel had already taken the
    whole body, so any connection-level OSError counts.
    """
    with _NodeLikeServer(keepalive_timeout=5.0) as server:
        await _pool_a_socket_released_late(shared_session, server.base_url)

        with pytest.raises(aiohttp.ClientOSError):
            async with shared_session.post(server.base_url + UPLOAD_PATH, data=_upload_form()) as response:
                await response.read()

        assert server.completed_uploads == 0


@pytest.mark.asyncio
async def test_an_upload_cut_off_mid_body_is_resent_under_its_key(shared_session) -> None:
    """A real connection reset mid-body, whichever errno the OS reports for it."""
    with _NodeLikeServer(keepalive_timeout=65.0, reset_first_upload=True) as server:
        document_id, size = await _blob_storage(server.base_url).save_record_to_storage(
            "org-1", "record-1", "vrid-1", {"blocks": _LARGE_PAYLOAD}
        )

        assert server.reset_uploads == 1, "the first attempt was cut off mid-body"
        assert document_id == "doc-1"
        assert size and size > 10_000_000
        assert server.completed_uploads == 1, "resent once, stored once"
        assert len(server.keys_seen) == 2
        assert server.keys_seen[0] is not None and server.keys_seen[0] == server.keys_seen[1]


@pytest.mark.asyncio
async def test_an_upload_whose_answer_was_lost_is_not_stored_twice(shared_session) -> None:
    """The case the Idempotency-Key exists for.

    The server stored the upload, then the connection died before its answer
    arrived, so the client cannot tell whether it happened. Its retry carries
    the same key and gets the stored document back.
    """
    with _NodeLikeServer(keepalive_timeout=65.0, drop_first_response=True) as server:
        document_id, _ = await _blob_storage(server.base_url).save_record_to_storage(
            "org-1", "record-1", "vrid-1", {"blocks": _PAYLOAD}
        )

        assert server.dropped_responses == 1
        assert document_id == "doc-1"
        assert server.completed_uploads == 1, "stored once, answered on the retry"


@pytest.mark.asyncio
async def test_a_long_server_keepalive_removes_the_race(shared_session) -> None:
    """Node's 65s window: the same loop stalls reuse a socket that is still open."""
    with _NodeLikeServer(keepalive_timeout=65.0) as server:
        await _pool_a_socket_released_late(shared_session, server.base_url)

        async with shared_session.post(server.base_url + UPLOAD_PATH, data=_upload_form()) as response:
            assert response.status == 200

        assert server.completed_uploads == 1
