"""Artifact sets through the storage service's ``/internal/artifacts`` routes.

The storage service signs one URL per file; the bytes then go straight to
S3 or Azure (or, on local storage, to a storage-service route). A signed URL
is a bearer credential: nothing here logs one or puts one in an error.
"""

import json
import logging
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import aiohttp
from yarl import URL

from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import Routes
from app.exceptions.storage_exceptions import StorageRequestRejectedError
from app.modules.artifacts.models import RevisionUploadPlan, SignedFileUrl
from app.modules.transformers.blob_storage import (
    _storage_error_detail,
    _storage_status_error,
    get_shared_session,
)

if TYPE_CHECKING:
    from app.modules.transformers.blob_storage import BlobStorage

#: The storage service's cap on files in one presign or read-urls call.
MAX_FILES_PER_CALL = 1000

_ROUTE_MISSING = frozenset({HttpStatusCode.NOT_FOUND.value, HttpStatusCode.METHOD_NOT_ALLOWED.value})
# S3 answers a signed GET of a missing key with 403 when the signer may not list the bucket.
_FILE_MISSING = frozenset({HttpStatusCode.NOT_FOUND.value, HttpStatusCode.FORBIDDEN.value})


class ArtifactRoutesUnavailable(Exception):
    """The storage service predates the artifact routes (404/405), e.g. mid-upgrade."""

    def __init__(self, status: int) -> None:
        super().__init__(f"storage has no artifact routes (HTTP {status})")
        self.status = status


class ArtifactStoreError(Exception):
    """A storage reply this client cannot use. Never carries a signed URL."""


class _Availability:
    """Process-wide: once the storage service is seen without the routes, stop asking.

    Every record would otherwise pay a failing presign and log it. A restart
    (normally the upgrade that brings the routes) asks again.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._unavailable_status: int | None = None

    @property
    def available(self) -> bool:
        return self._unavailable_status is None

    def mark_unavailable(self, status: int) -> bool:
        """True the first time only, so the caller warns once."""
        with self._lock:
            if self._unavailable_status is not None:
                return False
            self._unavailable_status = status
            return True

    def reset(self) -> None:
        with self._lock:
            self._unavailable_status = None


ARTIFACT_ROUTES = _Availability()
#: Whether the storage service takes ``properties.json``; one older than the file refuses it.
PROPERTIES_FILE = _Availability()


def artifact_writes_available() -> bool:
    return ARTIFACT_ROUTES.available


def _is_success(status: int) -> bool:
    # S3 answers a PUT with 200, Azure with 201.
    return 200 <= status < 300


def _signed_file(entry: Any, expected_method: str) -> tuple[str, SignedFileUrl]:  # noqa: ANN401 - storage reply
    if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not entry.get("url"):
        raise ArtifactStoreError("storage answered a malformed artifact URL entry")
    method = entry.get("method") or expected_method
    if method != expected_method:
        raise ArtifactStoreError(f"storage signed {method} where {expected_method} was asked")
    headers = entry.get("headers") or {}
    if not isinstance(headers, dict):
        raise ArtifactStoreError("storage answered artifact headers that are not a map")
    return entry["path"], SignedFileUrl(
        url=str(entry["url"]),
        method=method,
        headers={str(k): str(v) for k, v in headers.items()},
    )


class NodeArtifactStore:
    """Presign, upload, read, list and delete the files of content revisions."""

    def __init__(self, blob_storage: "BlobStorage", logger: logging.Logger | None = None) -> None:
        self._blob_storage = blob_storage
        self.logger = logger or logging.getLogger(__name__)

    async def _post_json(
        self, org_id: str, route: str, body: dict[str, Any], *, missing_route: bool = False
    ) -> dict[str, Any]:
        headers, endpoint, _ = await self._blob_storage._get_auth_and_config(org_id)

        async def attempt() -> dict[str, Any]:
            async with get_shared_session().post(
                f"{endpoint}{route}", json=body, headers=headers
            ) as response:
                return await self._json_reply(response, route, missing_route=missing_route)

        return await self._blob_storage._with_storage_retry(f"artifact request {route}", attempt)

    async def _json_reply(
        self, response: aiohttp.ClientResponse, route: str, *, missing_route: bool = False
    ) -> dict[str, Any]:
        """``missing_route``: on a route that never answers 404 itself, a 404 means
        the storage service does not have it."""
        if missing_route and response.status in _ROUTE_MISSING:
            raise ArtifactRoutesUnavailable(response.status)
        if response.status != HttpStatusCode.SUCCESS.value:
            # Error bodies come from the storage service itself, never with a signed URL.
            text = await response.text()
            error = _storage_status_error(
                response.status, f"artifact request {route} failed (HTTP {response.status}): {text[:300]}"
            )
            if isinstance(error, StorageRequestRejectedError):
                try:
                    error.detail = _storage_error_detail(json.loads(text))
                except ValueError:
                    error.detail = None
            raise error
        body = await response.json()
        if not isinstance(body, dict):
            raise ArtifactStoreError(f"artifact request {route} answered a non-object body")
        return body

    async def presign_revision(
        self,
        org_id: str,
        virtual_record_id: str,
        rev: str,
        files: Sequence[tuple[str, str]],
    ) -> RevisionUploadPlan:
        """Upload URLs for ``(path, content_type)`` files of one revision.

        More files than one call takes are asked for in several calls; the
        storage service files them all under the revision's one document.
        Raises ``ArtifactRoutesUnavailable`` (and stops asking for the rest of
        the process) when the storage service has no artifact routes.
        """
        if not files:
            raise ValueError("a revision needs at least one file")
        document_id: str | None = None
        root: str | None = None
        expires: int | None = None
        signed: dict[str, SignedFileUrl] = {}
        for start in range(0, len(files), MAX_FILES_PER_CALL):
            chunk = files[start:start + MAX_FILES_PER_CALL]
            try:
                body = await self._post_json(
                    org_id,
                    Routes.STORAGE_ARTIFACTS_PRESIGN.value,
                    {
                        "virtualRecordId": virtual_record_id,
                        "rev": rev,
                        "files": [{"path": p, "contentType": ct} for p, ct in chunk],
                    },
                    missing_route=True,
                )
            except ArtifactRoutesUnavailable as exc:
                if ARTIFACT_ROUTES.mark_unavailable(exc.status):
                    self.logger.warning(
                        "Storage has no artifact routes (HTTP %d); artifact writes are off "
                        "until this service restarts", exc.status,
                    )
                raise
            chunk_doc = body.get("documentId")
            if not isinstance(chunk_doc, str) or not chunk_doc:
                raise ArtifactStoreError("presign answered no document id")
            if document_id is not None and chunk_doc != document_id:
                raise ArtifactStoreError("presign calls for one revision answered different documents")
            document_id = chunk_doc
            root = body.get("root") if isinstance(body.get("root"), str) else root
            if isinstance(body.get("expiresInSeconds"), int):
                expires = body["expiresInSeconds"]
            answered = body.get("files")
            if not isinstance(answered, list):
                raise ArtifactStoreError("presign answered no files")
            for entry in answered:
                path, url = _signed_file(entry, "PUT")
                signed[path] = url
            missing = {p for p, _ in chunk} - signed.keys()
            if missing:
                raise ArtifactStoreError(f"presign left {len(missing)} file(s) unsigned")
        return RevisionUploadPlan(
            document_id=str(document_id), root=root or "", expires_in_seconds=expires, files=signed,
        )

    async def put(self, entry: SignedFileUrl, data: bytes, content_type: str | None = None) -> None:
        """PUT ``data`` with exactly the headers the storage service signed.

        ``content_type`` is sent only when the signed headers name none. Any
        2xx is success. Retried on transient failures: a PUT to the same key
        is idempotent.
        """
        if entry.method != "PUT":
            raise ValueError("not an upload URL")
        headers = dict(entry.headers)
        if content_type and not any(k.lower() == "content-type" for k in headers):
            headers["Content-Type"] = content_type

        async def attempt() -> None:
            async with get_shared_session().put(
                # encoded=True: re-quoting the query would break the signature.
                URL(entry.url, encoded=True),
                data=data,
                headers=headers,
                skip_auto_headers={"Content-Type"},
            ) as response:
                if not _is_success(response.status):
                    await response.read()
                    raise _storage_status_error(
                        response.status, f"artifact upload failed (HTTP {response.status})"
                    )

        await self._blob_storage._with_storage_retry("artifact upload", attempt)

    async def read_urls(
        self, org_id: str, document_id: str, paths: Sequence[str]
    ) -> dict[str, SignedFileUrl]:
        """Download URLs for files of a stored revision, by path."""
        signed: dict[str, SignedFileUrl] = {}
        route = Routes.STORAGE_ARTIFACT_READ_URLS.value.format(documentId=document_id)
        for start in range(0, len(paths), MAX_FILES_PER_CALL):
            body = await self._post_json(
                org_id, route, {"paths": list(paths[start:start + MAX_FILES_PER_CALL])}
            )
            answered = body.get("files")
            if not isinstance(answered, list):
                raise ArtifactStoreError("read-urls answered no files")
            for entry in answered:
                path, url = _signed_file(entry, "GET")
                signed[path] = url
        return signed

    async def get(self, entry: SignedFileUrl, byte_range: tuple[int, int] | None = None) -> bytes:
        """The file's bytes; ``byte_range`` is inclusive ``(start, end)``."""
        data = await self._get(entry, byte_range, missing_ok=False)
        if data is None:
            raise ArtifactStoreError("artifact download answered no body")
        return data

    async def get_if_present(self, entry: SignedFileUrl) -> bytes | None:
        """The file's bytes, or None when the file does not exist."""
        return await self._get(entry, None, missing_ok=True)

    async def _get(
        self, entry: SignedFileUrl, byte_range: tuple[int, int] | None, *, missing_ok: bool
    ) -> bytes | None:
        if entry.method != "GET":
            raise ValueError("not a download URL")
        headers = dict(entry.headers)
        if byte_range is not None:
            headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"

        async def attempt() -> bytes | None:
            async with get_shared_session().get(
                URL(entry.url, encoded=True), headers=headers
            ) as response:
                if missing_ok and response.status in _FILE_MISSING:
                    await response.read()
                    return None
                if not _is_success(response.status):
                    await response.read()
                    raise _storage_status_error(
                        response.status, f"artifact download failed (HTTP {response.status})"
                    )
                return await response.read()

        return await self._blob_storage._with_storage_retry("artifact download", attempt)

    async def list_revisions(self, org_id: str, virtual_record_id: str) -> list[dict[str, Any]]:
        headers, endpoint, _ = await self._blob_storage._get_auth_and_config(org_id)
        route = Routes.STORAGE_ARTIFACTS.value

        async def attempt() -> dict[str, Any]:
            async with get_shared_session().get(
                f"{endpoint}{route}", params={"virtualRecordId": virtual_record_id}, headers=headers
            ) as response:
                return await self._json_reply(response, route, missing_route=True)

        body = await self._blob_storage._with_storage_retry("artifact listing", attempt)
        revisions = body.get("revisions")
        if not isinstance(revisions, list):
            raise ArtifactStoreError("artifact listing answered no revisions")
        return [r for r in revisions if isinstance(r, dict)]

    async def delete_revision(self, org_id: str, document_id: str) -> int:
        """Remove a revision's files and its document. Returns how many were removed."""
        headers, endpoint, _ = await self._blob_storage._get_auth_and_config(org_id)
        route = Routes.STORAGE_ARTIFACT_SET.value.format(documentId=document_id)

        async def attempt() -> dict[str, Any]:
            async with get_shared_session().delete(f"{endpoint}{route}", headers=headers) as response:
                return await self._json_reply(response, route)

        body = await self._blob_storage._with_storage_retry("artifact delete", attempt)
        return int(body.get("purged", 0))
