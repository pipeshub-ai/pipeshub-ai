# ruff: noqa: ANN201, ANN401
"""A 200 MB stored PDF through the stream route, the localKB connector and the storage client, over real HTTP.

Only the storage service is a fake server; it serves a 200 MiB object whose bytes are computed per request, so
the test itself never holds the file. What is checked is what the route puts on the wire: page 1 costs one 1 MiB
range, a bad range is a 416 with the length, an ``If-Range`` for another version gets the whole body, and a
whole body is streamed rather than held.
"""

from __future__ import annotations

import logging
import re
import resource
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from fastapi import HTTPException

import app.edition_config  # noqa: F401  (binds the edition seams before the router loads)
from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.connectors.api import router as router_mod
from app.connectors.sources.localKB.connector import KnowledgeBaseConnector
from app.services.storage_documents import StorageDocuments
from app.utils.content_tag import content_tag
from tests.support.record_access import InMemoryRecordAccess

MIB = 1024 * 1024
SIZE = 200 * MIB
ETAG = "obj-etag-1"
RECORD_ID = "rec-big"
CHUNK = bytes(range(256)) * 256  # 64 KiB


def _pattern(first: int, last: int) -> bytes:
    """Bytes ``first..last`` of the object: the 64 KiB pattern repeated, so a slice can be checked without the file."""
    start = first % len(CHUNK)
    need = last - first + 1
    reps = (start + need) // len(CHUNK) + 1
    return (CHUNK * reps)[start : start + need]


class Storage:
    """The storage service: document info with a verified fingerprint, and the bound download."""

    def __init__(self) -> None:
        self.ranges: list[str | None] = []
        self.etag = ETAG
        #: The object is replaced by another one right after the first verification.
        self.replace_after_info = False
        self._infos = 0

    async def info(self, request: web.Request) -> web.Response:
        self._infos += 1
        if self.replace_after_info and self._infos == 2:
            self.etag = "obj-etag-2"
        return web.json_response({
            "document": {"_id": "doc-1", "storageVendor": "local", "isDeleted": False},
            "storageFingerprint": {
                "version": 1, "value": "f" * 32, "verified": True, "trusted": True,
                "object": {"size": SIZE, "etag": self.etag},
            },
        })

    async def download(self, request: web.Request) -> web.StreamResponse:
        spec = request.headers.get("Range")
        self.ranges.append(spec)
        if self.replace_after_info:
            self.etag = "obj-etag-2"
        if request.query.get("ifMatch") != self.etag:
            return web.Response(status=412)
        first, last = 0, SIZE - 1
        status = 200
        if spec:
            match = re.fullmatch(r"bytes=(\d+)-(\d+)", spec)
            assert match, spec
            first, last = int(match.group(1)), min(int(match.group(2)), SIZE - 1)
            if first >= SIZE:
                return web.Response(status=416)
            status = 206
        headers = {"Content-Length": str(last - first + 1)}
        if status == 206:
            headers["Content-Range"] = f"bytes {first}-{last}/{SIZE}"
        resp = web.StreamResponse(status=status, headers=headers)
        await resp.prepare(request)
        for at in range(first, last + 1, 256 * 1024):
            await resp.write(_pattern(at, min(at + 256 * 1024 - 1, last)))
        return resp


def _record() -> MagicMock:
    record = MagicMock()
    record.id = RECORD_ID
    record.org_id = "org-1"
    record.connector_id = "conn-1"
    record.connector_name = Connectors.KNOWLEDGE_BASE
    record.origin = OriginTypes.UPLOAD
    record.external_record_id = "doc-1"
    record.record_name = "atlas.pdf"
    record.mime_type = "application/pdf"
    record.is_placeholder = False
    record.record_type = "FILE"
    return record


async def _tag(storage: Storage, server: TestServer) -> str:
    info = await _info(server)
    tag = content_tag(RECORD_ID, info, "original")
    assert tag is not None
    return tag


async def _info(server: TestServer) -> Any:
    with patch.object(StorageDocuments, "_storage_url", AsyncMock(return_value=str(server.make_url("")).rstrip("/"))), \
            patch("app.services.storage_documents.generate_jwt", AsyncMock(return_value="tok")):
        return await StorageDocuments(AsyncMock(), logging.getLogger("t")).info("org-1", "doc-1", verify=True)


async def _stream(server: TestServer, headers: dict[str, str]) -> Any:
    kb = KnowledgeBaseConnector.__new__(KnowledgeBaseConnector)
    kb.config_service = MagicMock()
    kb.logger = logging.getLogger("t")
    record = _record()

    async def get_document(key: str, collection: str, *_a: object, **_k: object) -> dict[str, Any]:
        return {"_key": key} if collection == CollectionNames.ORGS.value else {"_key": "conn-1", "type": "KB", "isActive": True, "name": "Collections"}

    graph = MagicMock()
    graph.get_record_by_id = AsyncMock(return_value=record)
    graph.get_document = AsyncMock(side_effect=get_document)
    InMemoryRecordAccess().readable("u1", "org-1", RECORD_ID).serve(graph)
    container = SimpleNamespace(logger=lambda: MagicMock(), connectors_map={"conn-1": kb}, config_service=lambda: MagicMock())
    request = SimpleNamespace(
        state=SimpleNamespace(user={"userId": "u1", "orgId": "org-1", "role": "member"}),
        app=SimpleNamespace(container=container, state=SimpleNamespace(connector_registry=MagicMock())),
        headers={"x-record-content-open": "1", **headers},
    )
    with patch.object(StorageDocuments, "_storage_url", AsyncMock(return_value=str(server.make_url("")).rstrip("/"))), \
            patch("app.services.storage_documents.generate_jwt", AsyncMock(return_value="tok")), \
            patch.object(router_mod, "refuse_hidden_demo_record", new=AsyncMock()), \
            patch.object(router_mod, "refuse_outside_connection", new=AsyncMock()):
        return await router_mod.stream_record(
            request, RECORD_ID, convertTo=None, version=None, graph_provider=graph, config_service=MagicMock(),
        )


@pytest.fixture
async def server():
    storage = Storage()
    app = web.Application()
    app.router.add_get("/api/v1/document/internal/{doc_id}", storage.info)
    app.router.add_get("/api/v1/document/internal/{doc_id}/download", storage.download)
    srv = TestServer(app)
    await srv.start_server()
    srv.storage = storage  # type: ignore[attr-defined]
    yield srv
    await srv.close()


async def _drain(response: Any) -> int:
    total = 0
    async for chunk in response.body_iterator:
        total += len(chunk)
    return total


def _h(response: Any) -> dict[str, str]:
    return {k.lower(): v for k, v in dict(response.headers).items()}


async def test_page_one_costs_one_1_mib_range_and_nothing_else(server: TestServer) -> None:
    storage: Storage = server.storage  # type: ignore[attr-defined]
    response = await _stream(server, {"range": f"bytes=0-{MIB - 1}"})
    assert response.status_code == 206
    body = b"".join([c async for c in response.body_iterator])
    assert body == _pattern(0, MIB - 1)
    assert _h(response)["content-range"] == f"bytes 0-{MIB - 1}/{SIZE}"
    assert storage.ranges == [f"bytes=0-{MIB - 1}"]  # one range read; no whole-object read behind it


async def test_a_revisit_is_the_clients_business_and_each_page_is_its_own_small_read(server: TestServer) -> None:
    storage: Storage = server.storage  # type: ignore[attr-defined]
    for first in (150 * MIB, 151 * MIB):
        response = await _stream(server, {"range": f"bytes={first}-{first + MIB - 1}"})
        assert response.status_code == 206 and await _drain(response) == MIB
    assert storage.ranges == [f"bytes={150 * MIB}-{151 * MIB - 1}", f"bytes={151 * MIB}-{152 * MIB - 1}"]


async def test_a_range_past_the_end_is_416_with_the_length_before_any_read(server: TestServer) -> None:
    storage: Storage = server.storage  # type: ignore[attr-defined]
    with pytest.raises(HTTPException) as exc:
        await _stream(server, {"range": f"bytes={SIZE}-{SIZE + 10}"})
    assert exc.value.status_code == 416
    assert exc.value.headers["Content-Range"] == f"bytes */{SIZE}"
    assert storage.ranges == []


async def test_an_if_range_for_another_version_gets_the_whole_body_streamed_not_held(server: TestServer) -> None:
    storage: Storage = server.storage  # type: ignore[attr-defined]
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    response = await _stream(server, {"range": f"bytes=0-{MIB - 1}", "if-range": '"some-older-tag"'})
    assert response.status_code == 200
    assert _h(response)["content-length"] == str(SIZE)
    assert await _drain(response) == SIZE
    assert storage.ranges == [None]
    grown_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before
    assert grown_kib < 64 * 1024  # peak resident size grew by less than 64 MiB for a 200 MiB body


async def test_the_current_tag_as_if_range_keeps_the_partial_answer(server: TestServer) -> None:
    tag = await _tag(server.storage, server)  # type: ignore[attr-defined]
    response = await _stream(server, {"range": f"bytes=0-{MIB - 1}", "if-range": tag})
    assert response.status_code == 206 and _h(response)["etag"] == tag
    await _drain(response)


async def test_a_plain_whole_read_is_one_streamed_request_with_the_tag(server: TestServer) -> None:
    storage: Storage = server.storage  # type: ignore[attr-defined]
    tag = await _tag(storage, server)
    response = await _stream(server, {})
    assert response.status_code == 200 and _h(response)["etag"] == tag and _h(response)["accept-ranges"] == "bytes"
    assert await _drain(response) == SIZE
    assert storage.ranges == [None]


async def test_an_object_replaced_after_the_check_sends_the_new_bytes_whole(server: TestServer) -> None:
    storage: Storage = server.storage  # type: ignore[attr-defined]
    storage.replace_after_info = True
    response = await _stream(server, {"range": f"bytes=0-{MIB - 1}"})
    # The first read answered 412 (it was bound to the old object); the route verified again and sent the new bytes whole.
    assert response.status_code == 200
    assert _h(response)["etag"] == content_tag(RECORD_ID, await _info(server), "original")
    assert await _drain(response) == SIZE
    assert storage.ranges == [f"bytes=0-{MIB - 1}", None]
