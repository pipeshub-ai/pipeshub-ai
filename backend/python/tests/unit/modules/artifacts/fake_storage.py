"""A local HTTP stand-in for the storage service's artifact routes and a signed-URL store.

Real sockets on 127.0.0.1, so the store is exercised through the same aiohttp
session and request building it uses against S3, Azure or local storage.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from aiohttp import web

from app.modules.transformers.blob_storage import BlobStorage, close_shared_session

PRESIGN = "/api/v1/document/internal/artifacts/presign"
SIGNATURE = "secret-signature-value"


@dataclass
class StoredPut:
    data: bytes
    headers: dict[str, str]


@dataclass
class FakeStorage:
    """``vendor``: which signed headers presign hands out (azure, s3 or local).
    ``missing_status``: the answer to a GET of a missing file (S3 may say 403).
    ``refused_paths``: paths presign does not take, refusing the whole call as
    the storage service's validation does (an older one, without a newer file).
    ``refuse_other_with_properties``: a 400 with this message for any set that names
    properties.json, whatever the cause, and accepted without it."""

    vendor: str = "azure"
    missing_status: int = 404
    put_status: int = 201
    presign_status: int = 200
    fail_paths: set[str] = field(default_factory=set)
    refused_paths: set[str] = field(default_factory=set)
    refuse_other_with_properties: str = ""
    presign_calls: list[dict[str, Any]] = field(default_factory=list)
    puts: dict[str, StoredPut] = field(default_factory=dict)
    put_order: list[str] = field(default_factory=list)
    roots: dict[str, str] = field(default_factory=dict)
    base: str = ""
    _runner: web.AppRunner | None = None

    async def start(self) -> str:
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_post(PRESIGN, self._presign)
        app.router.add_get("/api/v1/document/internal/artifacts", self._list)
        app.router.add_post("/api/v1/document/internal/artifacts/{doc}/read-urls", self._read_urls)
        app.router.add_delete("/api/v1/document/internal/artifacts/{doc}", self._delete)
        app.router.add_put("/blob/{tail:.*}", self._put)
        app.router.add_get("/blob/{tail:.*}", self._get)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        self.base = f"http://127.0.0.1:{port}"
        return self.base

    async def stop(self) -> None:
        await close_shared_session()
        if self._runner is not None:
            await self._runner.cleanup()

    def blob_storage(self) -> BlobStorage:
        storage = BlobStorage(logger=logging.getLogger("fake-storage"), config_service=MagicMock())
        storage._get_auth_and_config = AsyncMock(  # type: ignore[method-assign]
            return_value=({"Authorization": "Bearer service-token"}, self.base, self.vendor)
        )
        return storage

    @staticmethod
    def document_id(vrid: str, rev: str) -> str:
        return hashlib.sha256(f"{vrid}/{rev}".encode()).hexdigest()[:24]

    @staticmethod
    def root(vrid: str, rev: str) -> str:
        return f"org-1/PipesHub/record-revisions/{vrid}/{rev}"

    def _signed(self, root: str, path: str, method: str, content_type: str | None) -> dict[str, Any]:
        url = f"{self.base}/blob/{root}/{path}?sv=2024-08-04&se=2099&sp=cw&sig={SIGNATURE}%2F%2B"
        headers: dict[str, str] = {}
        if method == "PUT":
            if self.vendor == "azure":
                headers = {"x-ms-blob-type": "BlockBlob", "Content-Type": content_type or "application/octet-stream"}
            elif self.vendor == "s3":
                headers = {"Content-Type": content_type or "application/octet-stream"}
            else:
                headers = {"Authorization": "Bearer local-token", "Content-Type": "application/octet-stream"}
        return {"path": path, "method": method, "url": url, "headers": headers}

    async def _presign(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.presign_calls.append(body)
        if self.presign_status != 200:
            return web.json_response({"error": {"message": "no"}}, status=self.presign_status)
        files = body["files"]
        paths = [f["path"] for f in files]
        if self.refuse_other_with_properties and "properties.json" in paths:
            return web.json_response({"error": {"message": self.refuse_other_with_properties}}, status=400)
        if refused := self.refused_paths.intersection(paths):
            # One line per path, as the storage service's validation reports them.
            message = "\n".join(["Not a file an artifact set can hold"] * len(refused))
            return web.json_response({"error": {"code": "VALIDATION_ERROR", "message": message}}, status=400)
        if not 1 <= len(files) <= 1000 or len(set(paths)) != len(paths):
            return web.json_response({"error": {"message": "bad files"}}, status=400)
        vrid, rev = body["virtualRecordId"], body["rev"]
        root = self.root(vrid, rev)
        self.roots[self.document_id(vrid, rev)] = root
        return web.json_response({
            "documentId": self.document_id(vrid, rev),
            "virtualRecordId": vrid,
            "rev": rev,
            "root": root,
            "expiresInSeconds": 900,
            "files": [self._signed(root, f["path"], "PUT", f["contentType"]) for f in files],
        })

    async def _read_urls(self, request: web.Request) -> web.Response:
        body = await request.json()
        doc = request.match_info["doc"]
        root = self.roots.get(doc)
        if root is None:
            return web.json_response({"error": {"message": "Artifact set not found"}}, status=404)
        return web.json_response({
            "documentId": doc,
            "files": [self._signed(root, p, "GET", None) for p in body["paths"]],
        })

    async def _list(self, request: web.Request) -> web.Response:
        vrid = request.query["virtualRecordId"]
        revs = sorted({k.split("/")[4] for k in self.puts if k.split("/")[3] == vrid})
        return web.json_response({"revisions": [
            {"documentId": self.document_id(vrid, r), "virtualRecordId": vrid, "rev": r} for r in revs
        ]})

    async def _delete(self, request: web.Request) -> web.Response:
        doc = request.match_info["doc"]
        gone = [k for k in self.puts if self.document_id(*k.split("/")[3:5]) == doc]
        for key in gone:
            del self.puts[key]
        return web.json_response({"purged": 1 if gone else 0})

    async def _put(self, request: web.Request) -> web.Response:
        if request.query.get("sig") != f"{SIGNATURE}/+":
            return web.Response(status=403, text="signature mismatch")
        key = request.match_info["tail"]
        path = key.split("/", 5)[5]
        data = await request.read()
        if path in self.fail_paths:
            return web.Response(status=500, text="injected failure")
        self.puts[key] = StoredPut(data, {k.lower(): v for k, v in request.headers.items()})
        self.put_order.append(path)
        return web.Response(status=self.put_status)

    async def _get(self, request: web.Request) -> web.Response:
        stored = self.puts.get(request.match_info["tail"])
        if stored is None:
            return web.Response(status=self.missing_status)
        range_header = request.headers.get("Range")
        if range_header:
            start, end = (int(x) for x in range_header.removeprefix("bytes=").split("-"))
            return web.Response(status=206, body=stored.data[start:end + 1])
        return web.Response(status=200, body=stored.data)
