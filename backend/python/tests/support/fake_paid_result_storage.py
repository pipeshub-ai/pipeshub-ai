"""A local HTTP stand-in for the storage service's paid-result routes and a signed-URL blob store.

Real sockets on 127.0.0.1, so ``NodePaidResultStore`` is exercised through the same aiohttp session and request
building it uses against S3, Azure and local storage. The org comes from the bearer token (``Bearer org:<id>``),
as the real service takes it from its scoped token: a request cannot name another org's folder.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from aiohttp import web

from app.modules.transformers.blob_storage import BlobStorage, close_shared_session

BASE = "/api/v1/document/internal/paid-results"
SIGNATURE = "secret-signature-value"


@dataclass
class FakePaidResultStorage:
    """``presign_status``: answer every presign with this status (404/405: an older storage service)."""

    presign_status: int = 200
    # What a signed GET of a missing key answers: S3 says 403 when the signer may not list the bucket.
    missing_status: int = 404
    list_page: int = 1000
    presign_calls: list[dict[str, Any]] = field(default_factory=list)
    put_headers: list[dict[str, str]] = field(default_factory=list)
    calls: list[tuple[str, str]] = field(default_factory=list)
    objects: dict[str, tuple[bytes, int]] = field(default_factory=dict)
    clock_ms: int = 1_700_000_000_000
    base: str = ""
    _runner: web.AppRunner | None = None

    async def start(self) -> str:
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_post(f"{BASE}/presign", self._presign)
        app.router.add_delete(f"{BASE}/org", self._purge_org)
        app.router.add_post(f"{BASE}/{{record}}/delete", self._delete)
        app.router.add_get(f"{BASE}/{{record}}", self._list)
        app.router.add_delete(f"{BASE}/{{record}}", self._purge_record)
        app.router.add_put("/blob/{tail:.*}", self._put)
        app.router.add_get("/blob/{tail:.*}", self._get)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"  # type: ignore[union-attr]
        return self.base

    async def stop(self) -> None:
        await close_shared_session()
        if self._runner is not None:
            await self._runner.cleanup()

    def blob_storage(self) -> BlobStorage:
        storage = BlobStorage(logger=logging.getLogger("fake-paid-storage"), config_service=MagicMock())

        async def auth(org_id: str) -> tuple[dict[str, str], str, str]:
            return {"Authorization": f"Bearer org:{org_id}"}, self.base, "local"

        storage._get_auth_and_config = AsyncMock(side_effect=auth)  # type: ignore[method-assign]
        return storage

    # -- helpers --------------------------------------------------------------------------------

    @staticmethod
    def _org(request: web.Request) -> str:
        return request.headers["Authorization"].removeprefix("Bearer org:")

    @staticmethod
    def key(org: str, record: str, path: str) -> str:
        return f"{org}/PipesHub/paid-results/{record}/{path}"

    def stored(self, org: str, record: str) -> list[str]:
        prefix = f"{org}/PipesHub/paid-results/{record}/"
        return sorted(k[len(prefix):] for k in self.objects if k.startswith(prefix))

    # -- routes ---------------------------------------------------------------------------------

    async def _presign(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.presign_calls.append({"org": self._org(request), **body})
        self.calls.append(("presign", self._org(request)))
        if self.presign_status != 200:
            return web.json_response({"error": {"message": "no"}}, status=self.presign_status)
        org, record = self._org(request), body["recordId"]
        files = []
        for f in body["files"]:
            mode = f["mode"]
            headers = {"Content-Type": "application/octet-stream"} if mode != "get" else {}
            if mode == "put":
                headers["If-None-Match"] = "*"
            files.append({
                "path": f["path"], "mode": mode, "method": "GET" if mode == "get" else "PUT",
                "url": f"{self.base}/blob/{self.key(org, record, f['path'])}?sv=2024&sig={SIGNATURE}%2F%2B",
                "headers": headers,
            })
        return web.json_response({"recordId": record, "expiresInSeconds": 900, "files": files})

    async def _put(self, request: web.Request) -> web.Response:
        key = request.match_info["tail"]
        self.put_headers.append(dict(request.headers))
        data = await request.read()
        if request.headers.get("If-None-Match") == "*" and key in self.objects:
            return web.Response(status=412)
        self.objects[key] = (data, self.clock_ms)
        return web.Response(status=200)

    async def _get(self, request: web.Request) -> web.Response:
        entry = self.objects.get(request.match_info["tail"])
        return web.Response(status=self.missing_status) if entry is None else web.Response(body=entry[0])

    async def _list(self, request: web.Request) -> web.Response:
        org, record = self._org(request), request.match_info["record"]
        self.calls.append(("list", org))
        prefix = self.key(org, record, request.query.get("prefix", ""))
        keys = sorted(k for k in self.objects if k.startswith(prefix))
        after = request.query.get("after")
        if after:
            keys = [k for k in keys if k > after]
        page = keys[: self.list_page]
        root = self.key(org, record, "")
        body: dict[str, Any] = {
            "objects": [
                {"path": k[len(root):], "size": len(self.objects[k][0]), "lastModifiedMs": self.objects[k][1]}
                for k in page
            ],
        }
        if len(keys) > self.list_page:
            body["next"] = page[-1]
        return web.json_response(body)

    async def _delete(self, request: web.Request) -> web.Response:
        org, record = self._org(request), request.match_info["record"]
        self.calls.append(("delete", org))
        for p in (await request.json())["paths"]:
            self.objects.pop(self.key(org, record, p), None)
        return web.json_response({"deleted": 1})

    async def _purge_record(self, request: web.Request) -> web.Response:
        org, record = self._org(request), request.match_info["record"]
        self.calls.append(("purge_record", org))
        for k in [k for k in self.objects if k.startswith(self.key(org, record, ""))]:
            del self.objects[k]
        return web.json_response({"purged": True})

    async def _purge_org(self, request: web.Request) -> web.Response:
        org = self._org(request)
        self.calls.append(("purge_org", org))
        for k in [k for k in self.objects if k.startswith(f"{org}/PipesHub/paid-results/")]:
            del self.objects[k]
        return web.json_response({"purged": True})
