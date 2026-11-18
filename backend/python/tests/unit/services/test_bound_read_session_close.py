"""A bound read streamed by Starlette's StreamingResponse under uvicorn,
with the client leaving mid-body and before the body starts. The aiohttp session behind ``BoundRead`` must be
closed every time (the live stack logged ``asyncio ERROR Unclosed client session`` / ``Unclosed connection``
to localhost:3000 after a viewer left).
"""

import asyncio
import contextlib
import gc
import socket

import aiohttp
import pytest
import uvicorn
from aiohttp import web
from starlette.applications import Starlette
from starlette.responses import StreamingResponse
from starlette.routing import Route

from app.services.storage_documents import BoundRead

SIZE = 32 * 1024 * 1024


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _storage(port: int) -> web.AppRunner:
    async def big(_req: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(headers={"Content-Length": str(SIZE)})
        await resp.prepare(_req)
        chunk = b"x" * 65536
        for _ in range(SIZE // len(chunk)):
            await resp.write(chunk)
        return resp

    app = web.Application()
    app.router.add_get("/big", big)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner


@pytest.mark.parametrize("leave", ["mid-body", "at-headers"])
@pytest.mark.asyncio
async def test_bound_read_session_closes_when_the_client_leaves(leave: str) -> None:
    sport, aport = _free_port(), _free_port()
    storage = await _storage(sport)
    sessions: list[aiohttp.ClientSession] = []

    async def route(_request: object) -> StreamingResponse:
        session = aiohttp.ClientSession()
        sessions.append(session)
        resp = await session.get(f"http://127.0.0.1:{sport}/big")
        read = BoundRead(200, SIZE, None, resp, session)
        return StreamingResponse(read.chunks(), media_type="application/pdf", headers={"Content-Length": str(SIZE)})

    server = uvicorn.Server(uvicorn.Config(Starlette(routes=[Route("/s", route)]), host="127.0.0.1", port=aport, log_level="warning", lifespan="off"))
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:  # noqa: ASYNC110
            await asyncio.sleep(0.02)
        reader, writer = await asyncio.open_connection("127.0.0.1", aport)
        writer.write(b"GET /s HTTP/1.1\r\nHost: x\r\n\r\n")
        await writer.drain()
        got = 0
        target = 1 if leave == "at-headers" else 2 * 1024 * 1024
        while got < target:
            data = await reader.read(65536)
            if not data:
                break
            got += len(data)
        writer.transport.abort()
        for _ in range(100):
            if sessions and sessions[0].closed:
                break
            await asyncio.sleep(0.05)
        gc.collect()
        assert sessions, "the route never ran"
        assert sessions[0].closed, f"the bound read's aiohttp session is still open 5 s after the client left ({leave})"
    finally:
        server.should_exit = True
        await task
        for s in sessions:
            if not s.closed:
                await s.close()
        await storage.cleanup()


@pytest.mark.parametrize("when", ["storage-headers-pending", "after-open-before-body"])
@pytest.mark.asyncio
async def test_open_bound_closes_its_session_when_cancelled(monkeypatch: pytest.MonkeyPatch, when: str) -> None:
    """The request task is cancelled (BaseHTTPMiddleware cancels on a client disconnect) while ``open_bound`` waits for
    the storage service, or after it returned but before StreamingResponse iterates the body."""
    import app.services.storage_documents as sd

    sport = _free_port()
    started = asyncio.Event()

    async def slow(_req: web.Request) -> web.StreamResponse:
        started.set()
        await asyncio.sleep(3 if when == "storage-headers-pending" else 0)
        resp = web.StreamResponse(headers={"Content-Length": "10"})
        await resp.prepare(_req)
        await resp.write(b"0123456789")
        return resp

    app = web.Application()
    app.router.add_get("/api/v1/document/internal/d1/download", slow)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", sport).start()
    created: list[aiohttp.ClientSession] = []
    real = aiohttp.ClientSession

    def spy(*a: object, **k: object) -> aiohttp.ClientSession:
        s = real(*a, **k)
        created.append(s)
        return s

    monkeypatch.setattr(sd.aiohttp, "ClientSession", spy)

    async def jwt(*_a: object, **_k: object) -> str:
        return "t"

    monkeypatch.setattr(sd, "generate_jwt", jwt)
    docs = sd.StorageDocuments.__new__(sd.StorageDocuments)
    docs.config_service = None
    docs.logger = None

    async def url() -> str:
        return f"http://127.0.0.1:{sport}"

    docs._storage_url = url  # type: ignore[method-assign]
    info = sd.StorageDocumentInfo(
        document_id="d1", is_deleted=False, awaiting_direct_upload=False, declared_size=10, fingerprint="f", trusted=True,
        object_size=10, object_etag="e1", object_unverifiable=False, reason=None, is_artifact_set=False, storage_vendor="local",
    )
    try:
        if when == "storage-headers-pending":
            t = asyncio.create_task(docs.open_bound("o1", "d1", info))
            await started.wait()
            await asyncio.sleep(0.05)
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t
        else:
            # The route builds the response; the body never starts (the client left first), so only the response's
            # background task can close the read.
            from types import SimpleNamespace

            from app.connectors.sources.localKB.connector import KnowledgeBaseConnector

            conn = KnowledgeBaseConnector.__new__(KnowledgeBaseConnector)
            conn.config_service = None
            conn.logger = None
            monkeypatch.setattr(sd, "StorageDocuments", lambda *_a, **_k: docs)
            import app.connectors.sources.localKB.connector as kb

            monkeypatch.setattr(kb, "StorageDocuments", lambda *_a, **_k: docs)
            record = SimpleNamespace(origin=kb.OriginTypes.UPLOAD, external_record_id="d1", org_id="o1", id="r1", mime_type="application/pdf")
            response = await conn.stream_record_bound(record, info)  # type: ignore[arg-type]
            # Serve it the way the server does when the client is already gone: the disconnect arrives while the
            # response is still waiting to send its headers, so the body generator never runs and only the
            # response's background task can close the read.
            sent: list[dict] = []

            async def receive() -> dict:
                return {"type": "http.disconnect"}

            async def send(message: dict) -> None:
                sent.append(message)
                await asyncio.sleep(30)  # the transport is not accepting writes

            scope = {"type": "http", "asgi": {"spec_version": "2.3"}, "method": "GET", "path": "/s", "headers": []}
            with contextlib.suppress(Exception):  # a disconnect may surface as an error; the read is closed either way
                await response(scope, receive, send)
            assert not any(m["type"] == "http.response.body" for m in sent), "the body must not have started"
        await asyncio.sleep(0.2)
        gc.collect()
        assert created, "open_bound made no session"
        assert all(s.closed for s in created), f"{sum(not s.closed for s in created)} aiohttp session(s) left open ({when})"
    finally:
        for s in created:
            if not s.closed:
                await s.close()
        await runner.cleanup()
