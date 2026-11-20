"""A local HTTP stand-in for the storage service's paid-result routes and a signed-URL blob store.

Real sockets on 127.0.0.1, so ``NodePaidResultStore`` is exercised through the same aiohttp session and request
building it uses against S3, Azure and local storage. The org comes from the bearer token (``Bearer org:<id>``),
as the real service takes it from its scoped token: a request cannot name another org's folder.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from email.utils import formatdate
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from aiohttp import web

from app.modules.transformers.blob_storage import BlobStorage, close_shared_session

BASE = "/api/v1/document/internal/paid-results"
SIGNATURE = "secret-signature-value"

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@dataclass
class FakePaidResultStorage:
    """``presign_status``: answer every presign with this status (404/405: an older storage service)."""

    presign_status: int = 200
    # What a signed GET of a missing key answers: S3 says 403 when the signer may not list the bucket.
    missing_status: int = 404
    #: What a conditional write (If-Match) of a missing key answers: S3 says 404, a store may say 412.
    gone_status: int = 404
    list_page: int = 1000
    # The batch purge route: absent (an older storage service) answers 404; ``batch_error`` answers that status instead;
    # ids in ``batch_failing`` come back as not purged.
    batch_present: bool = True
    batch_error: int | None = None
    batch_failing: set[str] = field(default_factory=set)
    batch_bodies: list[dict[str, Any]] = field(default_factory=list)
    presign_calls: list[dict[str, Any]] = field(default_factory=list)
    put_headers: list[dict[str, str]] = field(default_factory=list)
    calls: list[tuple[str, str]] = field(default_factory=list)
    objects: dict[str, tuple[bytes, int]] = field(default_factory=dict)
    delete_bodies: list[dict[str, Any]] = field(default_factory=list)
    #: Answers every delete with this body (a service that knows the route but not the newer fields).
    fixed_delete_reply: dict[str, Any] | None = None
    #: Whether the purge-cutoff route writes the refresh marker and says so (False: a storage service of an earlier release
    #: that has the route and not the marker).
    cutoff_marker: bool = True
    #: The storage could not tell what it honours (a failed probe): named apart from "none" in listings and presigns.
    capabilities_inconclusive: bool = False
    #: False: the storage service has no purge-cutoff route at all (404).
    cutoff_route: bool = True
    clock_ms: int = 1_700_000_000_000
    #: What the service names it honours in its replies (``conditionalDelete``, ``bump``); None: a service of an
    #: earlier release (no field, no ``etag`` in listings, ``bump`` refused by validation with a 400).
    capabilities: tuple[str, ...] | None = None
    #: How a write changes an object's ETag: ``md5`` (S3 and MinIO: the content hash) or ``unique`` (Azure, local).
    etag_mode: str = "md5"
    #: Awaited at ``list``, ``delete`` and ``bump_put`` after the request is read and before it is applied: a test
    #: runs a competing operation exactly there.
    hooks: dict[str, Callable[[], Awaitable[None]]] = field(default_factory=dict)
    etags: dict[str, str] = field(default_factory=dict)
    _writes: int = 0
    base: str = ""
    _runner: web.AppRunner | None = None

    async def start(self) -> str:
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_post(f"{BASE}/presign", self._presign)
        app.router.add_delete(f"{BASE}/org", self._purge_org)
        app.router.add_post(f"{BASE}/purge-batch", self._purge_batch)
        app.router.add_post(f"{BASE}/{{record}}/delete", self._delete)
        app.router.add_post(f"{BASE}/{{record}}/purge-cutoff", self._purge_cutoff)
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

    def _write(self, key: str, data: bytes) -> None:
        self._writes += 1
        self.objects[key] = (data, self.clock_ms)
        self.etags[key] = hashlib.md5(data).hexdigest() if self.etag_mode == "md5" else f"u{self._writes}"

    def etag_of(self, org: str, record: str, path: str) -> str | None:
        return self.etags.get(self.key(org, record, path))

    def begin_refresh(self, org: str, record: str) -> None:
        """The marker the storage service writes before it takes a purge cutoff."""
        self._write(self.key(org, record, "refreshed/marker.json"), b'{"startedMs":1}')

    async def _hook(self, name: str) -> None:
        hook = self.hooks.get(name)
        if hook is not None:
            await hook()

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
        if self.capabilities is None and any(f["mode"] == "bump" for f in body["files"]):
            return web.json_response({"error": {"message": "invalid mode"}}, status=400)
        for f in body["files"]:
            mode = f["mode"]
            headers = {"Content-Type": "application/octet-stream"} if mode != "get" else {}
            if mode == "put":
                headers["If-None-Match"] = "*"
            if mode == "bump":
                headers["If-Match"] = f'"{f["etag"]}"'
            files.append({
                "path": f["path"], "mode": mode, "method": "GET" if mode == "get" else "PUT",
                "url": f"{self.base}/blob/{self.key(org, record, f['path'])}?sv=2024&sig={SIGNATURE}%2F%2B",
                "headers": headers,
            })
        reply: dict[str, Any] = {"recordId": record, "expiresInSeconds": 900, "files": files}
        if self.capabilities is not None:
            reply["capabilities"] = list(self.capabilities)
            if self.capabilities_inconclusive:
                reply["capabilitiesInconclusive"] = True
        return web.json_response(reply)

    async def _put(self, request: web.Request) -> web.Response:
        key = request.match_info["tail"]
        self.put_headers.append(dict(request.headers))
        data = await request.read()
        if request.headers.get("If-None-Match") == "*" and key in self.objects:
            return web.Response(status=412)
        match = request.headers.get("If-Match")
        if match is not None:
            await self._hook("bump_put")
            if key not in self.objects:
                return web.Response(status=self.gone_status)
            if match.strip('"') != self.etags.get(key):
                return web.Response(status=412)
        self._write(key, data)
        return web.Response(status=200, headers={"ETag": f'"{self.etags[key]}"'})

    async def _get(self, request: web.Request) -> web.Response:
        key = request.match_info["tail"]
        entry = self.objects.get(key)
        if entry is None:
            return web.Response(status=self.missing_status)
        return web.Response(body=entry[0], headers={
            "ETag": f'"{self.etags[key]}"', "Last-Modified": formatdate(entry[1] // 1000, usegmt=True),
        })

    async def _list(self, request: web.Request) -> web.Response:
        org, record = self._org(request), request.match_info["record"]
        self.calls.append(("list", org))
        prefix = self.key(org, record, request.query.get("prefix", ""))
        await self._hook("list")
        keys = sorted(k for k in self.objects if k.startswith(prefix) and "/refreshed/" not in k)
        after = request.query.get("after")
        if after:
            keys = [k for k in keys if k > after]
        page = keys[: self.list_page]
        root = self.key(org, record, "")
        body: dict[str, Any] = {
            "objects": [
                {
                    "path": k[len(root):], "size": len(self.objects[k][0]), "lastModifiedMs": self.objects[k][1],
                    **({"etag": self.etags[k]} if self.capabilities is not None else {}),
                }
                for k in page
            ],
        }
        if self.capabilities is not None:
            body["capabilities"] = list(self.capabilities)
            if self.capabilities_inconclusive:
                body["capabilitiesInconclusive"] = True
        if len(keys) > self.list_page:
            body["next"] = page[-1]
        return web.json_response(body)

    async def _delete(self, request: web.Request) -> web.Response:
        org, record = self._org(request), request.match_info["record"]
        self.calls.append(("delete", org))
        body = await request.json()
        self.delete_bodies.append(body)
        await self._hook("delete")
        if self.fixed_delete_reply is not None:
            return web.json_response(self.fixed_delete_reply)
        etags = body.get("etags") if self.capabilities is not None else None
        changed: list[str] = []
        for p in body["paths"]:
            key = self.key(org, record, p)
            if etags and p in etags and key in self.objects and etags[p] != self.etags.get(key):
                changed.append(p)
                continue
            self.objects.pop(key, None)
        reply: dict[str, Any] = {"deleted": len(body["paths"]) - len(changed)}
        if self.capabilities is not None:
            reply["changed"] = changed
        return web.json_response(reply)

    async def _purge_batch(self, request: web.Request) -> web.Response:
        org = self._org(request)
        self.calls.append(("purge_batch", org))
        body = await request.json()
        self.batch_bodies.append(body)
        if not self.batch_present:
            return web.json_response({"error": {"message": "no route"}}, status=404)
        if self.batch_error is not None:
            return web.json_response({"error": {"message": "boom"}}, status=self.batch_error)
        results = []
        for record in body["recordIds"]:
            if record in self.batch_failing:
                results.append({"recordId": record, "purged": False, "error": "purge failed"})
                continue
            for k in [k for k in self.objects if k.startswith(self.key(org, record, ""))]:
                del self.objects[k]
            results.append({"recordId": record, "purged": True})
        return web.json_response({"results": results})

    async def _purge_cutoff(self, request: web.Request) -> web.Response:
        org, record = self._org(request), request.match_info["record"]
        self.calls.append(("purge_cutoff", org))
        if not self.cutoff_route:
            return web.json_response({"error": {"message": "not found"}}, status=404)
        if self.cutoff_marker:
            self.begin_refresh(org, record)
        reply: dict[str, Any] = {"cutoff": self.clock_ms - self.clock_ms % 1000}
        if self.cutoff_marker:
            reply["marker"] = True
        return web.json_response(reply)

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
