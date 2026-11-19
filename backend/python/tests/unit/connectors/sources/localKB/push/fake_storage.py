"""A storage service for tests that behaves like the Node one on the routes the push uses.

Mirrors ``storage.controller.ts:2130-2160`` (download: local files streamed, S3/Azure answer ``200 {signedUrl}``)
and ``storage/utils/utils.ts:486-560`` (``serveFileFromLocalStorage``: the ETag is the quoted digest of the file;
``?ifMatch=`` that differs answers 412 ``PreconditionFailed``; the ETag header is set only on a match). The signed
side mirrors an object store: ``If-Match`` that differs answers 412.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


def etag_of(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


@dataclass
class Obj:
    data: bytes
    vendor: str = "local"
    ignore_if_match: bool = False
    extra_headers: dict = field(default_factory=dict)
    etag: str | None = None  # response ETag override (None = the true one)

    @property
    def true_etag(self) -> str:
        return etag_of(self.data)


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, Obj] = {}
        self.requests: list[tuple[str, str]] = []
        self.signed_base = ""
        self.signed_override: dict[str, str] = {}  # id -> signedUrl to answer
        self.signed_pad = 0  # extra bytes in the signed-URL answer
        self.mode: dict[str, str] = {}  # id -> 500 | reset | truncated | slow
        # Awaited with the id when a download arrives, before it is answered: the caller is then mid-task.
        self.on_download: Callable[[str], Awaitable[None]] | None = None
        self.runner: web.AppRunner | None = None
        self.url = ""

    async def start(self) -> FakeStorage:
        app = web.Application()
        app.router.add_get("/api/v1/document/internal/{id}/download", self.download)
        app.router.add_get("/obj/{id}", self.signed)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        self.signed_base = self.url
        return self

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()

    def add(self, doc_id: str, data: bytes, **kw) -> Obj:
        self.objects[doc_id] = Obj(data, **kw)
        return self.objects[doc_id]

    def count(self, kind: str) -> int:
        return sum(1 for k, _ in self.requests if k == kind)

    async def download(self, request: web.Request) -> web.StreamResponse:
        doc_id = request.match_info["id"]
        self.requests.append(("download", doc_id))
        if self.on_download is not None:
            await self.on_download(doc_id)
        obj = self.objects.get(doc_id)
        if obj is None:
            return web.json_response({"error": "not found"}, status=404)
        mode = self.mode.get(doc_id)
        if mode == "500":
            return web.Response(status=500)
        if mode == "reset":
            request.transport.abort()
            return web.Response()
        if mode == "slow":
            await asyncio.sleep(3)
        if mode == "truncated":
            resp = web.StreamResponse(headers={"ETag": f'"{obj.true_etag}"', "Content-Length": str(len(obj.data))})
            await resp.prepare(request)
            await resp.write(obj.data[: len(obj.data) // 2])
            request.transport.abort()
            return resp
        if obj.vendor != "local":
            url = self.signed_override.get(doc_id) or f"{self.signed_base}/obj/{doc_id}"
            # A pad is trailing whitespace after a complete answer: the first 8 KiB alone parse, so only the size check refuses it.
            return web.Response(text=json.dumps({"signedUrl": url}) + " " * self.signed_pad, content_type="application/json")
        if_match = request.query.get("ifMatch")
        if if_match is not None and if_match.strip('"') != obj.true_etag:
            return web.json_response({"error": "The file is not the one that was verified; read the document again"}, status=412)
        return web.Response(body=obj.data, headers={"ETag": f'"{obj.etag or obj.true_etag}"', **obj.extra_headers})

    async def signed(self, request: web.Request) -> web.StreamResponse:
        doc_id = request.match_info["id"]
        self.requests.append(("signed", doc_id))
        self.last_signed_headers = dict(request.headers)
        obj = self.objects.get(doc_id)
        if obj is None:
            return web.Response(status=404)
        header = request.headers.get("If-Match")
        if header is not None and not obj.ignore_if_match and header.strip('"') != obj.true_etag:
            return web.Response(status=412)
        return web.Response(body=obj.data, headers={"ETag": f'"{obj.etag or obj.true_etag}"', **obj.extra_headers})
