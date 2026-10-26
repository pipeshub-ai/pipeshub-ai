"""The Node storage gateway's record routes, on a real HTTP server in its own thread.

Enough of ``modules/storage`` for ``BlobStorage`` to store and read records
end to end:

- ``local``: multipart upload with the platform upload limit, refused the way
  Node's file processor refuses it (400, "This file is larger than the N MB
  limit"); download serves the stored envelope itself.
- ``s3``: placeholder, direct-upload URL, and a signed URL that takes the PUT
  and serves ranged GETs, standing in for S3/Azure (no size limit).

Runs on its own thread and event loop, as Node runs in its own process, so
the server's work never stalls the client loop a test or benchmark measures.
"""

from __future__ import annotations

import asyncio
import itertools
import socket
import threading

from aiohttp import web

from app.config.constants.service import Routes

_SIGNED_PATH = "/signed/{documentId}"


class FakeStorageGateway:
    def __init__(self, storage_type: str = "local", *, upload_limit_bytes: int = 30 * 1024 * 1024) -> None:
        assert storage_type in ("local", "s3")
        self.storage_type = storage_type
        self.upload_limit_bytes = upload_limit_bytes
        self.documents: dict[str, bytes] = {}
        self.downloads = 0
        self.rejected_uploads = 0
        self.base_url = ""
        self._ids = itertools.count(1)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=lambda: asyncio.run(self._serve()), daemon=True)

    def _new_id(self) -> str:
        return f"doc-{next(self._ids)}"

    def _signed_url(self, document_id: str) -> str:
        return self.base_url + _SIGNED_PATH.format(documentId=document_id)

    async def _upload(self, request: web.Request) -> web.Response:
        body = b""
        async for part in await request.multipart():
            data = await part.read()
            if part.name == "file":
                body = bytes(data)
        if len(body) > self.upload_limit_bytes:
            self.rejected_uploads += 1
            mb = round(self.upload_limit_bytes / (1024 * 1024))
            return web.json_response(
                {"error": {"message": f"This file is larger than the {mb} MB limit. "
                           "Make it smaller or split it, then upload it again."}},
                status=400,
            )
        document_id = self._new_id()
        self.documents[document_id] = body
        return web.json_response({"_id": document_id})

    async def _placeholder(self, _request: web.Request) -> web.Response:
        document_id = self._new_id()
        self.documents[document_id] = b""
        return web.json_response({"_id": document_id})

    async def _direct_upload(self, request: web.Request) -> web.Response:
        return web.json_response({"signedUrl": self._signed_url(request.match_info["documentId"])})

    async def _download(self, request: web.Request) -> web.Response:
        document_id = request.match_info["documentId"]
        if document_id not in self.documents:
            return web.json_response({"error": {"message": "Document not found"}}, status=404)
        if self.storage_type == "s3":
            return web.json_response({"signedUrl": self._signed_url(document_id)})
        self.downloads += 1
        return web.Response(body=self.documents[document_id], content_type="application/json")

    async def _signed_put(self, request: web.Request) -> web.Response:
        self.documents[request.match_info["documentId"]] = await request.read()
        return web.Response(status=200)

    async def _signed_get(self, request: web.Request) -> web.Response:
        body = self.documents.get(request.match_info["documentId"])
        if body is None:
            return web.Response(status=404)
        ranged = request.headers.get("Range", "")
        if not ranged.startswith("bytes="):
            self.downloads += 1
            return web.Response(body=body, content_type="application/json")
        start_text, _, end_text = ranged[len("bytes="):].partition("-")
        start = int(start_text)
        end = min(int(end_text) if end_text else len(body) - 1, len(body) - 1)
        if start == 0 and end > 0:  # bytes=0-0 is BlobStorage probing the size
            self.downloads += 1
        return web.Response(
            status=206,
            body=body[start:end + 1],
            headers={"Content-Range": f"bytes {start}-{end}/{len(body)}"},
        )

    async def _serve(self) -> None:
        app = web.Application(client_max_size=1024 * 1024 * 1024)
        app.router.add_post(Routes.STORAGE_UPLOAD.value, self._upload)
        app.router.add_post(Routes.STORAGE_PLACEHOLDER.value, self._placeholder)
        app.router.add_post(Routes.STORAGE_DIRECT_UPLOAD.value, self._direct_upload)
        app.router.add_get(Routes.STORAGE_DOWNLOAD.value, self._download)
        app.router.add_put(_SIGNED_PATH, self._signed_put)
        app.router.add_get(_SIGNED_PATH, self._signed_get)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        await web.SockSite(runner, sock).start()
        self.base_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        self._ready.set()
        await self._stop.wait()
        await runner.cleanup()

    def __enter__(self) -> FakeStorageGateway:
        self._thread.start()
        assert self._ready.wait(10), "fake storage gateway did not start"
        return self

    def __exit__(self, *_exc: object) -> None:
        assert self._loop is not None and self._stop is not None
        self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(10)
