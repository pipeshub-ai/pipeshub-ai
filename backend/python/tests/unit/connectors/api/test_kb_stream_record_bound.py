"""``KnowledgeBaseConnector.stream_record_bound``: the connector's side of ranges and the streamed 200."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from fastapi import HTTPException

import app.edition_config  # noqa: F401  (binds the edition seams before the connector loads)
from app.config.constants.arangodb import OriginTypes
from app.connectors.sources.localKB.connector import KnowledgeBaseConnector
from app.services.storage_documents import (
    BoundReadChanged,
    StorageDocuments,
    parse_document_info,
)
from app.utils.byte_range import ByteRange

BODY = bytes(range(251)) * 1000
ETAG = "e1"


def _info(etag: str = ETAG):  # noqa: ANN202
    return parse_document_info(
        {"document": {"_id": "doc-1", "storageVendor": "local"},
         "storageFingerprint": {"version": 1, "value": "f" * 32, "verified": True, "trusted": True, "object": {"size": len(BODY), "etag": etag}}},
        verify=True,
    )


def _record(**over: object) -> SimpleNamespace:
    base = {"id": "rec-1", "org_id": "org-1", "origin": OriginTypes.UPLOAD, "external_record_id": "doc-1", "mime_type": "application/pdf"}
    return SimpleNamespace(**{**base, **over})


async def _storage(request: web.Request) -> web.Response:
    if request.query.get("ifMatch") != ETAG:
        return web.Response(status=412)
    spec = request.headers.get("Range")
    if not spec:
        return web.Response(body=BODY)
    a, b = (int(x) for x in spec.removeprefix("bytes=").split("-"))
    return web.Response(status=206, body=BODY[a : b + 1], headers={"Content-Range": f"bytes {a}-{b}/{len(BODY)}"})


async def _run(record: SimpleNamespace, info, byte_range: ByteRange | None):  # noqa: ANN202
    app = web.Application()
    app.router.add_get("/api/v1/document/internal/{doc_id}/download", _storage)
    async with TestServer(app) as server:
        connector = KnowledgeBaseConnector.__new__(KnowledgeBaseConnector)
        connector.config_service = MagicMock()
        connector.logger = logging.getLogger("t")
        with patch.object(StorageDocuments, "_storage_url", AsyncMock(return_value=str(server.make_url("")).rstrip("/"))), \
                patch("app.services.storage_documents.generate_jwt", AsyncMock(return_value="tok")):
            response = await connector.stream_record_bound(record, info, byte_range)
            body = b"".join([chunk async for chunk in response.body_iterator])
    return response, body


async def test_a_range_is_a_206_with_its_bytes_and_length_and_the_record_type() -> None:
    response, body = await _run(_record(), _info(), ByteRange(100, 199))
    assert response.status_code == 206 and body == BODY[100:200]
    assert response.headers["content-range"] == f"bytes 100-199/{len(BODY)}"
    assert response.media_type == "application/pdf"


async def test_the_whole_body_streams_as_a_200() -> None:
    response, body = await _run(_record(mime_type=None), _info(), None)
    assert response.status_code == 200 and body == BODY
    assert response.media_type == "application/octet-stream" and "content-range" not in response.headers


async def test_a_changed_object_raises_before_any_response() -> None:
    with pytest.raises(BoundReadChanged):
        await _run(_record(), _info("other"), None)


@pytest.mark.parametrize(("over", "status"), [({"origin": OriginTypes.CONNECTOR}, 400), ({"external_record_id": None}, 404)])
async def test_the_same_refusals_as_the_buffered_read(over: dict[str, object], status: int) -> None:
    with pytest.raises(HTTPException) as exc:
        await _run(_record(**over), _info(), None)
    assert exc.value.status_code == status
