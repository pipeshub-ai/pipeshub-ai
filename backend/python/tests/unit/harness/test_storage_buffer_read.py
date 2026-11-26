"""``BlobStorage.read_binary_from_storage`` against the storage service's internal buffer route as it answers.

Main #4398 made the route send the raw bytes (``application/octet-stream``) instead of ``res.json(buffer)``.
The harness reads skill bundles and workspace objects through this method, so a reader that still parsed JSON
crashed every coding-engine task when it materialized its skills.
"""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from app.modules.transformers.blob_storage import BlobStorage, close_shared_session

# Bytes that are not valid UTF-8, as the skill bundle object that failed was.
BYTES = bytes([0x50, 0x4B, 0x03, 0x04, 0xCB, 0xFF, 0x00, 0x80, 0x7F])


async def _read(answer: web.Response, monkeypatch: pytest.MonkeyPatch) -> bytes:
    async def buffer(_request: web.Request) -> web.Response:
        return answer

    app = web.Application()
    app.router.add_get("/api/v1/document/internal/{documentId}/buffer", buffer)
    server = TestServer(app)
    await server.start_server()
    storage = object.__new__(BlobStorage)

    async def auth_and_config(_org_id: str) -> tuple[dict[str, str], str, str]:
        return {"Authorization": "Bearer t"}, str(server.make_url("")).rstrip("/"), "local"

    monkeypatch.setattr(storage, "_get_auth_and_config", auth_and_config)
    try:
        return await storage.read_binary_from_storage("org-1", "doc-1")
    finally:
        await close_shared_session()
        await server.close()


@pytest.mark.asyncio
async def test_the_raw_bytes_the_storage_service_sends_are_returned_as_they_are(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer = web.Response(body=BYTES, content_type="application/octet-stream")
    assert await _read(answer, monkeypatch) == BYTES


@pytest.mark.asyncio
async def test_the_json_buffer_an_older_storage_service_sent_is_still_read(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = web.Response(text=json.dumps({"type": "Buffer", "data": list(BYTES)}), content_type="application/json")
    assert await _read(answer, monkeypatch) == BYTES


@pytest.mark.asyncio
async def test_a_json_answer_that_is_not_a_buffer_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = web.Response(text=json.dumps({"error": "nope"}), content_type="application/json")
    with pytest.raises(Exception, match="invalid binary object"):
        await _read(answer, monkeypatch)
