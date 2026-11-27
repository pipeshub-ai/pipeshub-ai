"""The upload limit: how storage answers an oversize file, and how the push reads the setting."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from app.services.storage_documents import (
    DEFAULT_UPLOAD_LIMIT_BYTES,
    StorageDocuments,
    StorageUploadError,
    parse_upload_limit,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

MIB = 1 << 20


class Config:
    """``ConfigurationService.get_config`` for the keys the upload path reads."""

    def __init__(self, settings: object, url: str) -> None:
        self.settings, self.url = settings, url
        self.reads: list[bool] = []
        self.fail = False

    async def get_config(self, key, default=None, use_cache=False, *, raise_on_error=False, **kw) -> object:
        if key == "/services/platform/settings":
            self.reads.append(use_cache)
            if self.fail:
                if raise_on_error:
                    raise ConnectionError("store down")
                return default
            return self.settings if self.settings is not None else default
        if key == "/services/secretKeys":
            return {"scopedJwtSecret": "s" * 32}
        return {"storage": {"endpoint": self.url}}


class NodeStorage:
    """``POST /internal/upload`` as Node answers: multer's LIMIT_FILE_SIZE is a 413 (``fp.service.ts``), and a form the
    route cannot use (``bad_form``) a 400."""

    def __init__(self, limit_ref: list[int]) -> None:
        self.limit_ref = limit_ref
        self.stored = 0
        self.bad_form = False

    async def upload(self, request: web.Request) -> web.Response:
        reader = await request.multipart()
        size = 0
        async for part in reader:
            if part.name == "file":
                while chunk := await part.read_chunk(1 << 16):
                    size += len(chunk)
        if self.bad_form:
            return web.json_response({"error": {"code": "HTTP_BAD_REQUEST", "message": "No files available for processing"}}, status=400)
        if size > self.limit_ref[0]:  # multer: `>`
            return web.json_response({"error": {"code": "HTTP_PAYLOAD_TOO_LARGE", "message": f"This file is larger than the {self.limit_ref[0] // MIB} MB limit. Make it smaller or split it, then upload it again."}}, status=413)
        self.stored += 1
        return web.json_response({"_id": "d" * 24}, headers={"X-Document-Id": "d" * 24})


@pytest.fixture
async def world(tmp_path) -> AsyncIterator[object]:
    limit = [30 * MIB]
    node = NodeStorage(limit)
    app = web.Application(client_max_size=200 * MIB)
    app.router.add_post("/api/v1/document/internal/upload", node.upload)
    server = TestServer(app)
    await server.start_server()
    config = Config({"fileUploadMaxSizeBytes": 30 * MIB}, str(server.make_url("")).rstrip("/"))
    docs = StorageDocuments(config, logging.getLogger("t"))

    def file(size: int) -> str:
        path = tmp_path / f"f{size}"
        path.write_bytes(b"x" * size)
        return str(path)

    async def create(size: int) -> str:
        return await docs.create(org_id="o", name="n", document_path="p", file_path=file(size), extension="bin", content_type="application/octet-stream", tags={})

    yield SimpleNamespace(config=config, docs=docs, node=node, limit=limit, create=create)
    await server.close()



async def test_a_413_carries_the_limit_it_was_refused_under(world) -> None:
    with pytest.raises(StorageUploadError) as e:
        await world.create(31 * MIB)
    assert (e.value.status, e.value.limit_bytes) == (413, 30 * MIB)


async def test_a_413_carries_the_setting_read_now_even_when_the_route_refused_a_smaller_file(world) -> None:
    """The route's own limit can differ from the setting (lowered since); the push quotes the setting, never a guess."""
    world.node.limit_ref[0] = 1  # the route refuses, while the setting the push reads says 30 MiB
    with pytest.raises(StorageUploadError) as e:
        await world.create(1000)
    assert (e.value.status, e.value.limit_bytes) == (413, 30 * MIB)


async def test_a_400_is_not_an_oversize_file(world) -> None:
    """A form the route cannot use is a 400 with no limit, so the push answers STORAGE_REJECTED, not a 413 or a retry."""
    world.node.bad_form = True
    with pytest.raises(StorageUploadError) as e:
        await world.create(31 * MIB)
    assert e.value.status == 400 and e.value.limit_bytes is None
    assert world.config.reads == []  # a 400 does not read the limit


async def test_file_of_exactly_the_limit_is_stored(world) -> None:
    assert await world.create(30 * MIB) == "d" * 24
    with pytest.raises(StorageUploadError) as e:
        await world.create(30 * MIB + 1)
    assert e.value.status == 413


async def test_setting_is_read_uncached_at_upload_time(world) -> None:
    """The limit is lowered between two pushes; a cached read would still say 30 MiB and quote the wrong limit."""
    world.limit[0] = 5 * MIB
    world.config.settings = {"fileUploadMaxSizeBytes": 5 * MIB}
    with pytest.raises(StorageUploadError) as e:
        await world.create(6 * MIB)
    assert e.value.status == 413 and e.value.limit_bytes == 5 * MIB and world.config.reads == [False]


async def test_unreadable_store_is_retryable_503_not_the_default(world) -> None:
    world.node.limit_ref[0] = 1
    world.config.fail = True
    with pytest.raises(StorageUploadError) as e:
        await world.create(10)
    assert e.value.status is None  # not a 413, not a 400: the service maps it to a retryable 503
    with pytest.raises(StorageUploadError):
        await world.docs.upload_limit()


@pytest.mark.parametrize("settings,expected", [
    ({"fileUploadMaxSizeBytes": 10 * MIB}, 10 * MIB),
    ({"fileUploadMaxSizeBytes": 1.5 * MIB}, int(1.5 * MIB)),
    ({"fileUploadMaxSizeBytes": True}, DEFAULT_UPLOAD_LIMIT_BYTES),
    ({"fileUploadMaxSizeBytes": "10485760"}, DEFAULT_UPLOAD_LIMIT_BYTES),
    ({"fileUploadMaxSizeBytes": 0}, DEFAULT_UPLOAD_LIMIT_BYTES),
    ({"fileUploadMaxSizeBytes": -5}, DEFAULT_UPLOAD_LIMIT_BYTES),
    ({}, DEFAULT_UPLOAD_LIMIT_BYTES),
    (None, DEFAULT_UPLOAD_LIMIT_BYTES),
    ({"fileUploadMaxSizeBytes": None}, DEFAULT_UPLOAD_LIMIT_BYTES),
], ids=["number", "float", "bool", "numeric-string", "zero", "negative", "absent-key", "no-blob", "null"])
def test_setting_follows_nodes_rule(settings, expected) -> None:
    assert parse_upload_limit(settings) == expected
