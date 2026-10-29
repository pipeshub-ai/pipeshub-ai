"""Create one immutable document in the storage service from a file on disk.

The document is never overwritten: a new revision of anything is a new document.
Uploads go through the internal route like the other connectors' uploads, so
local, S3 and Azure storage all work; for S3 and Azure the service answers with a
redirect to a presigned URL, which is followed as a PUT of the raw bytes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import aiohttp
from yarl import URL

from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.modules.transformers.blob_storage import (
    CustomMetadataEntry,
    _add_custom_metadata_to_form,
    _signed_upload_headers,
    _upload_succeeded,
)
from app.utils.jwt import generate_jwt

if TYPE_CHECKING:
    import logging
    from collections.abc import Mapping

    from app.config.configuration_service import ConfigurationService

_REDIRECTS = frozenset({301, 302, 307, 308})
_TIMEOUT = aiohttp.ClientTimeout(total=90)


class StorageUploadError(Exception):
    """The storage service did not store the document."""


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


def parse_document_info(body: object, *, verify: bool) -> StorageDocumentInfo | None:
    """Parse the body of ``GET /internal/:id``; None when it holds no document."""
    document: Any = body.get("document", body) if isinstance(body, dict) else None
    if not isinstance(document, dict):
        return None
    doc_id = document.get("_id") or document.get("id")
    if not doc_id:
        return None
    fp = body.get("storageFingerprint", document.get("storageFingerprint")) if isinstance(body, dict) else None
    fp = fp if isinstance(fp, dict) and fp.get("version") == _FINGERPRINT_VERSION else None
    obj = fp.get("object") if fp else None
    obj = obj if isinstance(obj, dict) else None
    vendor = document.get("storageVendor")
    trusted = bool(verify and fp and fp.get("verified") is True and fp.get("trusted") is True and obj)
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
                        raise StorageUploadError(f"the storage service answered {resp.status}")
        except StorageUploadError:
            raise
        except (aiohttp.ClientError, TimeoutError, OSError) as e:
            raise StorageUploadError(f"the storage service could not be reached: {type(e).__name__}") from e

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
