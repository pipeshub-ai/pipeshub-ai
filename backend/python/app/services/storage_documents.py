"""Create one immutable document in the storage service from a file on disk.

The document is never overwritten: a new revision of anything is a new document.
Uploads go through the internal route like the other connectors' uploads, so
local, S3 and Azure storage all work; for S3 and Azure the service answers with a
redirect to a presigned URL, which is followed as a PUT of the raw bytes.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import aiohttp
from yarl import URL

from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.core.org_context import spawn_in_request
from app.modules.transformers.blob_storage import (
    CustomMetadataEntry,
    _add_custom_metadata_to_form,
    _signed_upload_headers,
    _upload_succeeded,
)
from app.services.featureflag.platform_settings import PLATFORM_SETTINGS_KEY
from app.utils.jwt import generate_jwt

if TYPE_CHECKING:
    import logging
    from collections.abc import Mapping

    from app.config.configuration_service import ConfigurationService

_REDIRECTS = frozenset({301, 302, 307, 308})
_TIMEOUT = aiohttp.ClientTimeout(total=90)


#: The platform's per-file upload limit when the setting is absent or unusable
#: (``KB_UPLOAD_LIMITS.defaultMaxFileSizeBytes``, ``knowledge_base/constants/kb.constants.ts``).
DEFAULT_UPLOAD_LIMIT_BYTES = 30 << 20


class StorageUploadError(Exception):
    """The storage service did not store the document.

    ``status`` is the service's HTTP status when it answered (413 is set by ``create`` for a 400 that is
    an oversize file); ``limit_bytes`` is the upload limit read at that moment for a 413.
    """

    def __init__(self, message: str, *, status: int | None = None, limit_bytes: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.limit_bytes = limit_bytes


def parse_upload_limit(settings: object) -> int:
    """The upload limit in a platform settings blob, by the rule of ``getPlatformSettingsFromStore``
    (``storage/routes/storage.routes.ts:61-67``, ``configuration_manager/utils/util.ts:54-58``): a number
    greater than 0, else the default. ``True`` is not a number there, nor is a numeric string.
    """
    value = settings.get("fileUploadMaxSizeBytes") if isinstance(settings, dict) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 and value != float("inf"):
        return int(value)
    return DEFAULT_UPLOAD_LIMIT_BYTES


_FINGERPRINT_VERSION = 1


def _strip_etag(raw: str | None) -> str:
    """An ETag without its weak marker and outer quotes (the form the storage service reports)."""
    tag = (raw or "").strip()
    if tag.startswith("W/"):
        tag = tag[2:]
    if len(tag) >= 2 and tag.startswith('"') and tag.endswith('"'):
        tag = tag[1:-1]
    return tag


@dataclass(frozen=True)
class StorageDocumentInfo:
    """What the storage service says about one document.

    ``trusted`` is the only flag that may justify skipping a re-read: it is True only for a verified answer
    (``verify=True``) whose fingerprint the service marked trusted. ``fingerprint`` is comparable only between
    trusted answers. Without ``storageFingerprint`` in the answer (an older service) nothing is trusted.
    """

    document_id: str
    is_deleted: bool
    awaiting_direct_upload: bool
    declared_size: int | None
    fingerprint: str | None
    trusted: bool
    object_size: int | None
    object_etag: str | None
    object_unverifiable: bool
    reason: str | None
    is_artifact_set: bool
    storage_vendor: str | None = None
    initiator_user_id: str | None = None
    mime_type: str | None = None
    retired: bool = False
    #: The object's own URL as stored (``s3.url`` / ``azureBlob.url``); the signed-URL hop is pinned to it.
    object_url: str | None = None
    #: False when a writer window is open or reads disagreed (the counter state is not settled); None without a fingerprint.
    settled: bool | None = None
    #: Why a fingerprint cannot be trusted: no_fingerprint, version, unverifiable, clock, open_writer, other; None when trusted.
    untrusted_reason: str | None = None

    @property
    def if_match_header(self) -> str | None:
        """The ``If-Match`` value for a signed GET of an S3 or Azure object (None for local files and unverified answers).

        A local document is checked through the ``ifMatch`` query parameter of the download route instead, which
        carries the bare digest (``object_etag``).
        """
        if self.object_etag is None or self.storage_vendor == "local":
            return None
        return f'"{self.object_etag}"'

    def matches_download(self, etag_header: str | None) -> bool:
        """Whether the ``ETag`` of a download response is the object this answer was verified against."""
        if self.object_etag is None or not etag_header or etag_header.strip().startswith("W/"):
            return False
        return _strip_etag(etag_header) == self.object_etag


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


#: The image rows of Node's ``extensionToMimeType`` (``storage/mimetypes/mimetypes.ts``); the push admits only images, so
#: any other extension maps to None. Node lower-cases the extension and drops its first dot (``getMimeType``).
_IMAGE_MIME_BY_EXTENSION = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "svg": "image/svg+xml", "webp": "image/webp",
    "bmp": "image/bmp", "ico": "image/x-icon", "tiff": "image/tiff", "heic": "image/heic", "heif": "image/heif", "avif": "image/avif",
    "jp2": "image/jp2", "jpx": "image/jpx", "jpm": "image/jpm", "jxl": "image/jxl", "jxs": "image/jxs", "jxr": "image/jxr",
}


def _document_mime(document: dict[str, Any]) -> str | None:
    """The document's type. Node's upload route stores only ``extension`` (``".png"``, ``storage.controller.ts:483``) and
    never sets ``mimeType`` (``document.schema.ts:114``), so the extension decides; a stored ``mimeType`` is read only when
    there is no extension."""
    extension = document.get("extension")
    if isinstance(extension, str) and extension:
        return _IMAGE_MIME_BY_EXTENSION.get(extension.lower().replace(".", "", 1))
    mime = document.get("mimeType")
    return mime if isinstance(mime, str) else None


def parse_document_info(body: object, *, verify: bool) -> StorageDocumentInfo | None:
    """Parse the body of ``GET /internal/:id``; None when it holds no document."""
    document: Any = body.get("document", body) if isinstance(body, dict) else None
    if not isinstance(document, dict):
        return None
    doc_id = document.get("_id") or document.get("id")
    if not doc_id:
        return None
    fp = body.get("storageFingerprint", document.get("storageFingerprint")) if isinstance(body, dict) else None
    raw_fp = fp if isinstance(fp, dict) else None
    fp = raw_fp if raw_fp is not None and raw_fp.get("version") == _FINGERPRINT_VERSION else None
    obj = fp.get("object") if fp else None
    obj = obj if isinstance(obj, dict) else None
    vendor = document.get("storageVendor")
    initiator = document.get("initiatorUserId")
    remote = document.get("s3") if vendor == "s3" else document.get("azureBlob") if vendor == "azureBlob" else None
    object_url = remote.get("url") if isinstance(remote, dict) and isinstance(remote.get("url"), str) else None
    trusted = bool(verify and fp and fp.get("verified") is True and fp.get("trusted") is True and obj)
    if trusted:
        why = None
    elif raw_fp is None:
        why = "no_fingerprint"
    elif fp is None:
        why = "version"
    elif fp.get("objectUnverifiable") is True:
        why = "unverifiable"
    elif isinstance(fp.get("reason"), str):
        why = "clock"
    elif fp.get("settled") is False:
        why = "open_writer"
    else:
        why = "other"
    return StorageDocumentInfo(
        document_id=str(doc_id),
        is_deleted=document.get("isDeleted") is True,
        awaiting_direct_upload=document.get("awaitingDirectUpload") is True,
        declared_size=_as_int(document.get("sizeInBytes")),
        fingerprint=fp.get("value") if fp and isinstance(fp.get("value"), str) else None,
        trusted=trusted,
        object_size=_as_int(obj.get("size")) if obj else None,
        object_etag=_strip_etag(obj.get("etag")) if obj and isinstance(obj.get("etag"), str) else None,
        object_unverifiable=bool(fp and fp.get("objectUnverifiable") is True),
        reason=fp.get("reason") if fp and isinstance(fp.get("reason"), str) else None,
        is_artifact_set=bool(document.get("artifactSet")),
        storage_vendor=vendor if isinstance(vendor, str) else None,
        initiator_user_id=str(initiator) if isinstance(initiator, (str, int)) and not isinstance(initiator, bool) and initiator else None,
        mime_type=_document_mime(document),
        retired=bool(document.get("retiredAt")),
        object_url=object_url,
        settled=fp.get("settled") if fp and isinstance(fp.get("settled"), bool) else None,
        untrusted_reason=why,
    )


def document_id_from_upload(headers: Mapping[str, str], data: object) -> str | None:
    """The id of a created document, from the response headers or JSON body."""
    header = headers.get("x-document-id") or headers.get("X-Document-Id")
    if isinstance(header, str) and header.strip():
        return header.strip()
    if not isinstance(data, dict):
        return None
    for source in (data, data.get("document")):
        if isinstance(source, dict):
            found = source.get("_id") or source.get("id")
            if found:
                return str(found)
    return None


class StorageDocuments:
    def __init__(self, config_service: ConfigurationService, logger: logging.Logger) -> None:
        self.config_service = config_service
        self.logger = logger

    async def _storage_url(self) -> str:
        endpoints = await self.config_service.get_config(config_node_constants.ENDPOINTS.value)
        return (endpoints or {}).get("storage", {}).get("endpoint", DefaultEndpoints.STORAGE_ENDPOINT.value)

    async def create(
        self,
        *,
        org_id: str,
        name: str,
        document_path: str,
        file_path: str,
        extension: str,
        content_type: str,
        tags: dict[str, str],
    ) -> str:
        """Upload ``file_path`` as a new document and return its id. Raises ``StorageUploadError``."""
        token = await generate_jwt(self.config_service, {"orgId": org_id, "scopes": ["storage:token"]})
        base = await self._storage_url()
        size = os.path.getsize(file_path)
        tag_list: list[CustomMetadataEntry] = [{"key": k, "value": v} for k, v in tags.items()]
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                with open(file_path, "rb") as source:
                    form = aiohttp.FormData()
                    form.add_field("file", source, filename=f"{name}.{extension}", content_type=content_type)
                    form.add_field("documentName", name)
                    form.add_field("documentPath", document_path)
                    form.add_field("isVersionedFile", "false")
                    _add_custom_metadata_to_form(form, tag_list)
                    async with session.post(
                        f"{base}/api/v1/document/internal/upload",
                        data=form,
                        headers={"Authorization": f"Bearer {token}"},
                        allow_redirects=False,
                    ) as resp:
                        if resp.status in _REDIRECTS:
                            return await self._put_presigned(session, resp, file_path, size)
                        if resp.status == 200:
                            doc_id = document_id_from_upload(resp.headers, await resp.json())
                            if doc_id:
                                return doc_id
                            raise StorageUploadError("the storage service did not return a document id")
                        if resp.status == 400:
                            # The route answers 400 with free text for a file over the platform limit (multer's
                            # LIMIT_FILE_SIZE, ``fp.service.ts:146-151,226-229``), never 413.
                            await self._explain_400(size)
                        raise StorageUploadError(f"the storage service answered {resp.status}", status=resp.status)
        except StorageUploadError:
            raise
        except (aiohttp.ClientError, TimeoutError, OSError) as e:
            raise StorageUploadError(f"the storage service could not be reached: {type(e).__name__}") from e

    async def upload_limit(self, *, use_cache: bool = False) -> int:
        """The platform's per-file upload limit in bytes. Raises ``StorageUploadError`` when the setting cannot be read:
        a limit is never guessed, so an outage is a retry, not a refusal or a pass."""
        try:
            settings = await self.config_service.get_config(PLATFORM_SETTINGS_KEY, default={}, use_cache=use_cache, raise_on_error=True)
        except Exception as e:
            raise StorageUploadError(f"the upload limit could not be read: {type(e).__name__}") from e
        return parse_upload_limit(settings)

    async def _explain_400(self, size: int) -> None:
        """Raise the 413 a 400 means when the file is larger than the limit now (read fresh); else return."""
        limit = await self.upload_limit(use_cache=False)
        if size > limit:  # multer's check is ``>``: a file of exactly the limit is stored
            raise StorageUploadError("the file is larger than the platform upload limit", status=413, limit_bytes=limit)

    async def exists(self, org_id: str, document_id: str) -> bool:
        """Whether the storage service still has the document (absent or soft-deleted is False).

        Raises ``StorageUploadError`` when the answer is unknown.
        """
        token = await generate_jwt(self.config_service, {"orgId": org_id, "scopes": ["storage:token"]})
        base = await self._storage_url()
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session, session.get(
                f"{base}/api/v1/document/internal/{document_id}", headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                if resp.status in (404, 410):
                    return False
                if resp.status != 200:
                    raise StorageUploadError(f"the storage service answered {resp.status}")
                body = await resp.json()
        except (aiohttp.ClientError, TimeoutError, OSError) as e:
            raise StorageUploadError(f"the storage service could not be reached: {type(e).__name__}") from e
        document = body.get("document", body) if isinstance(body, dict) else {}
        return not (isinstance(document, dict) and document.get("isDeleted") is True)

    async def info(self, org_id: str, document_id: str, *, verify: bool = False) -> StorageDocumentInfo | None:
        """The document as the storage service sees it; None when it is absent (404/410).

        With ``verify=True`` the service stats the stored object (real size and ETag) and answers a fingerprint whose
        ``trusted`` flag is the only thing that may justify skipping a re-read. Raises ``StorageUploadError`` for any
        other answer or a transport failure.
        """
        token = await generate_jwt(self.config_service, {"orgId": org_id, "scopes": ["storage:token"]})
        base = await self._storage_url()
        params = {"verify": "1"} if verify else None
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session, session.get(
                f"{base}/api/v1/document/internal/{document_id}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                if resp.status in (404, 410):
                    return None
                if resp.status != 200:
                    raise StorageUploadError(f"the storage service answered {resp.status}")
                body = await resp.json()
        except (aiohttp.ClientError, TimeoutError, OSError) as e:
            raise StorageUploadError(f"the storage service could not be reached: {type(e).__name__}") from e
        info = parse_document_info(body, verify=verify)
        if info is None:
            raise StorageUploadError("the storage service answered without a document")
        return info

    async def mint_token(self, org_id: str, *, ttl_s: int) -> str:
        """An org-scoped storage token that expires in ``ttl_s`` seconds (never logged)."""
        return await generate_jwt(self.config_service, {"orgId": org_id, "scopes": ["storage:token"]}, timedelta(seconds=ttl_s))

    async def info_many(
        self, org_id: str, document_ids: list[str], *, verify: bool = False, concurrency: int = 8,
    ) -> dict[str, StorageDocumentInfo | None]:
        """``info`` of each distinct id, sharing one token and one session, ``concurrency`` at a time.

        Raises ``StorageUploadError`` for the first failure (an unknown answer is not "absent").
        """
        token = await generate_jwt(self.config_service, {"orgId": org_id, "scopes": ["storage:token"]})
        base = await self._storage_url()
        gate = asyncio.Semaphore(concurrency)
        params = {"verify": "1"} if verify else None

        async def one(session: aiohttp.ClientSession, doc_id: str) -> StorageDocumentInfo | None:
            async with gate:
                try:
                    async with session.get(f"{base}/api/v1/document/internal/{doc_id}", params=params, headers={"Authorization": f"Bearer {token}"}) as resp:
                        if resp.status in (404, 410):
                            return None
                        if resp.status != 200:
                            raise StorageUploadError(f"the storage service answered {resp.status}", status=resp.status)
                        body = await resp.json()
                except (aiohttp.ClientError, TimeoutError, OSError) as e:
                    raise StorageUploadError(f"the storage service could not be reached: {type(e).__name__}") from e
            parsed = parse_document_info(body, verify=verify)
            if parsed is None:
                raise StorageUploadError("the storage service answered without a document")
            return parsed

        ids = list(dict.fromkeys(document_ids))
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            tasks = [spawn_in_request(one(session, i)) for i in ids]
            try:
                results = await asyncio.gather(*tasks)
            except BaseException:
                # gather leaves its siblings running: stop them and wait, so none outlives the session it uses.
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
        return dict(zip(ids, results, strict=True))

    async def _put_presigned(
        self, session: aiohttp.ClientSession, resp: aiohttp.ClientResponse, file_path: str, size: int
    ) -> str:
        location = resp.headers.get("Location") or resp.headers.get("location")
        if not location:
            raise StorageUploadError("the storage redirect has no Location")
        try:
            data = await resp.json()
        except Exception:
            data = None
        doc_id = document_id_from_upload(resp.headers, data)
        if not doc_id:
            raise StorageUploadError("the storage redirect did not return a document id")
        # The URL is signed with an empty Content-Type; aiohttp would add one and break the signature.
        headers = {"Content-Length": str(size), **_signed_upload_headers(location)}
        with open(file_path, "rb") as source:
            async with session.put(
                URL(location, encoded=True), data=source, headers=headers,
                skip_auto_headers={"Content-Type"}, allow_redirects=False,
            ) as put:
                if not _upload_succeeded(put.status):
                    raise StorageUploadError(f"the presigned upload answered {put.status}")
        return doc_id
