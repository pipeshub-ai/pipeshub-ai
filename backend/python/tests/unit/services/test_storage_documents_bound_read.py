"""``StorageDocuments.open_bound`` against real HTTP servers standing in for the storage service and the object store.

A local object is read through the download route's ``ifMatch``; an S3 or Azure object through a signed link with
``If-Match``. Both honour one range, answer 412 for another object, and stream in chunks.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from app.services.storage_documents import (
    BoundReadChanged,
    BoundReadOutOfRange,
    StorageDocumentInfo,
    StorageDocuments,
    StorageUploadError,
    parse_document_info,
)
from app.utils.byte_range import ByteRange

BODY = bytes(range(256)) * 4096  # 1 MiB
ETAG = "abc123"


def _info(vendor: str, etag: str = ETAG, trusted: bool = True) -> StorageDocumentInfo | None:
    return parse_document_info(
        {
            "document": {"_id": "d1", "storageVendor": vendor},
            "storageFingerprint": {
                "version": 1, "value": "f" * 32, "verified": trusted, "trusted": trusted,
                "object": {"size": len(BODY), "etag": etag},
            },
        },
        verify=True,
    )


def _serve(request: web.Request, tag_ok: bool) -> web.StreamResponse:
    if not tag_ok:
        return web.Response(status=412)
    spec = request.headers.get("Range")
    if spec is None:
        return web.Response(body=BODY, headers={"ETag": f'"{ETAG}"'})
    a, b = (int(x) for x in re.fullmatch(r"bytes=(\d+)-(\d+)", spec).groups())  # type: ignore[union-attr]
    if a >= len(BODY):
        return web.Response(status=416)
    return web.Response(status=206, body=BODY[a : b + 1], headers={"Content-Range": f"bytes {a}-{min(b, len(BODY) - 1)}/{len(BODY)}"})


class Stores:
    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []

    async def download_local(self, request: web.Request) -> web.StreamResponse:
        self.seen.append({"route": "download", "auth": request.headers.get("Authorization"), "query": dict(request.query), "range": request.headers.get("Range")})
        return _serve(request, request.query.get("ifMatch") == ETAG)

    async def download_signed(self, request: web.Request) -> web.StreamResponse:
        self.seen.append({"route": "download", "auth": request.headers.get("Authorization"), "query": dict(request.query), "range": request.headers.get("Range")})
        return web.json_response({"signedUrl": f"{request.url.scheme}://{request.host}/object/key"})

    async def obj(self, request: web.Request) -> web.StreamResponse:
        self.seen.append({"route": "object", "if_match": request.headers.get("If-Match"), "range": request.headers.get("Range"), "auth": request.headers.get("Authorization")})
        return _serve(request, request.headers.get("If-Match") == f'"{ETAG}"')


async def _open(vendor: str, info: StorageDocumentInfo | None, byte_range: ByteRange | None = None):  # noqa: ANN202
    stores = Stores()
    app = web.Application()
    app.router.add_get("/api/v1/document/internal/{doc_id}/download", stores.download_local if vendor == "local" else stores.download_signed)
    app.router.add_get("/object/key", stores.obj)
    server = TestServer(app)
    await server.start_server()
    try:
        sd = StorageDocuments(AsyncMock(), logging.getLogger("t"))
        with patch.object(StorageDocuments, "_storage_url", AsyncMock(return_value=str(server.make_url("")).rstrip("/"))), \
                patch("app.services.storage_documents.generate_jwt", AsyncMock(return_value="tok")):
            try:
                read = await sd.open_bound("org1", "d1", info, byte_range=byte_range)
            except BaseException:
                await server.close()
                raise
        return read, stores, server
    except BaseException:
        await server.close()
        raise


@pytest.mark.parametrize("vendor", ["local", "s3"])
async def test_a_whole_read_streams_the_exact_bytes_bound_to_the_tag(vendor: str) -> None:
    read, stores, server = await _open(vendor, _info(vendor))
    try:
        assert read.status == 200
        got = [c async for c in read.chunks(64 * 1024)]
        assert b"".join(got) == BODY and len(got) >= 2  # chunked, never one buffer
    finally:
        await server.close()
    bound = stores.seen[0]["query"].get("ifMatch") if vendor == "local" else stores.seen[-1]["if_match"]
    assert bound in (ETAG, f'"{ETAG}"')
    assert stores.seen[0]["auth"] == "Bearer tok"


@pytest.mark.parametrize("vendor", ["local", "s3"])
async def test_one_range_is_exactly_those_bytes_with_the_length(vendor: str) -> None:
    read, stores, server = await _open(vendor, _info(vendor), ByteRange(1000, 70999))
    try:
        assert read.status == 206 and read.content_range == f"bytes 1000-70999/{len(BODY)}"
        assert b"".join([c async for c in read.chunks()]) == BODY[1000:71000]
    finally:
        await server.close()
    assert stores.seen[-1]["range"] == "bytes=1000-70999"


@pytest.mark.parametrize("vendor", ["local", "s3"])
async def test_another_object_is_a_bound_read_failure_before_any_byte(vendor: str) -> None:
    with pytest.raises(BoundReadChanged):
        await _open(vendor, _info(vendor, etag="zzz"))


@pytest.mark.parametrize("vendor", ["local", "s3"])
async def test_a_range_past_the_end_is_out_of_range(vendor: str) -> None:
    with pytest.raises(BoundReadOutOfRange):
        await _open(vendor, _info(vendor), ByteRange(len(BODY), len(BODY) + 10))


async def test_an_unverified_object_is_never_read_unbound() -> None:
    sd = StorageDocuments(AsyncMock(), logging.getLogger("t"))
    with pytest.raises(StorageUploadError):
        await sd.open_bound("org1", "d1", _info("local", trusted=False))
