"""
Fetch raw bytes back from PipesHub blob storage from agent actions.

Cross-toolset file transfers (Outlook attachment -> Salesforce ContentVersion,
Drive -> Box, etc.) cannot ship binary content through the agent's
``(bool, str)`` tool boundary. The source action uploads bytes via
``BlobStorage.save_conversation_file_to_storage`` and registers a small
``documentId`` handle on ``chat_state.document_id_to_url`` (see
:func:`conversation_upload_to_registry_entry`). The destination
action calls :func:`fetch_staged_document_bytes` (or :func:`fetch_blob_bytes`
for the scoped path alone) to pull bytes back in-process via the internal
download route or a pre-signed URL, depending on ``storage_type``.

Tenancy: the download route's ``getDocumentInfo`` middleware filters by
``{_id, orgId}``, so a STORAGE_TOKEN-scoped JWT issued for org A cannot read
org B's documents. This helper just needs to pass the caller's ``org_id``
consistently.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, Literal

import aiohttp
import jwt
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from yarl import URL

from app.config.configuration_service import ConfigurationService
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import (
    DefaultEndpoints,
    Routes,
    TokenScopes,
    config_node_constants,
)

logger = logging.getLogger(__name__)

# Soft cap for staged blobs. Salesforce ContentVersion via REST tops out at
# ~37.5 MB after base64 expansion (50 MB string limit), so a 25 MB raw cap is
# safe for the current callers. Larger files would need multipart upload.
DEFAULT_MAX_STAGE_BYTES = 25 * 1024 * 1024

# Per-request budget. Applied via ``session.get(..., timeout=...)`` so the
# 120 s ceiling holds whether the helper creates its own session or borrows
# a caller-owned one (an injected session may have a different default).
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=120)


class BlobStagingError(Exception):
    """Raised when blob fetching fails."""


# ---------------------------------------------------------------------------
# Wire models
# ---------------------------------------------------------------------------


class BlobUploadInfo(BaseModel):
    """JSON returned by ``BlobStorage.save_conversation_file_to_storage``.

    The Node.js storage route returns either ``signedUrl`` (S3 path) or
    ``downloadUrl`` (local path); ``documentId`` may come back as int or
    str depending on the storage backend. We treat all fields as optional
    so callers can decide what counts as "complete enough" (see
    :func:`conversation_upload_to_registry_entry`).
    """

    model_config = ConfigDict(extra="ignore")

    documentId: str | int | None = None
    fileName: str | None = None
    signedUrl: str | None = None
    downloadUrl: str | None = None


class StagedDocumentSource(BaseModel):
    """Producer-side breadcrumb so consumers can trace bytes back to origin.

    ``platform`` is the only required field; everything else is producer-
    specific (Outlook supplies ``message_id``/``attachment_id``; Drive will
    supply ``file_id``/``revision_id``; etc.) so we accept extras rather
    than discriminate per platform — the consumer never reads these.
    """

    model_config = ConfigDict(extra="allow")

    platform: str
    message_id: str | None = None
    attachment_id: str | None = None


class StagedDocumentEntry(BaseModel):
    """One row in ``chat_state['document_id_to_url']``.

    ``storage_type`` controls which fetch path
    :func:`fetch_staged_document_bytes` takes:

    - ``"s3"``: ``download_url`` is a pre-signed URL; GET it directly.
    - ``"external"``: route through the scoped-token internal download.
    """

    model_config = ConfigDict(extra="ignore")

    download_url: str
    filename: str
    mime_type: str
    size_bytes: int = Field(ge=0)
    storage_type: Literal["s3", "external"]
    source: StagedDocumentSource | None = None


@asynccontextmanager
async def _session_or_default(
    session: aiohttp.ClientSession | None,
) -> AsyncIterator[aiohttp.ClientSession]:
    """Yield ``session`` if injected, else create+close a one-shot session.

    Lets batch callers (e.g. ``upload_file_to_salesforce``) share a single
    ``aiohttp.ClientSession`` across N parallel fetches — HTTP keep-alive
    and the connection pool then survive across the batch — without
    breaking one-shot callers that don't care. The injected session's
    lifecycle stays with the caller; we only borrow it.
    """
    if session is not None:
        yield session
        return
    async with aiohttp.ClientSession() as new_session:
        yield new_session


async def _get_storage_auth(
    org_id: str,
    config_service: ConfigurationService,
) -> tuple[dict[str, str], str]:
    """Mint a STORAGE_TOKEN-scoped JWT for ``org_id`` and resolve the cm endpoint.

    Mirrors ``BlobStorage._get_auth_and_config`` so we hit the same internal
    routes the indexing service uses.
    """
    if not org_id:
        raise BlobStagingError("org_id is required for blob staging")

    secret_keys = await config_service.get_config(
        config_node_constants.SECRET_KEYS.value
    )
    scoped_jwt_secret = (secret_keys or {}).get("scopedJwtSecret")
    if not scoped_jwt_secret:
        raise BlobStagingError("Missing scopedJwtSecret in configuration")

    token = jwt.encode(
        {"orgId": org_id, "scopes": [TokenScopes.STORAGE_TOKEN.value]},
        scoped_jwt_secret,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}

    endpoints = await config_service.get_config(
        config_node_constants.ENDPOINTS.value
    )
    nodejs_endpoint = (endpoints or {}).get("cm", {}).get(
        "endpoint", DefaultEndpoints.NODEJS_ENDPOINT.value
    )
    if not nodejs_endpoint:
        raise BlobStagingError("Missing cm endpoint in configuration")

    return headers, nodejs_endpoint.rstrip("/")


async def fetch_blob_bytes(
    *,
    org_id: str,
    config_service: ConfigurationService,
    storage_document_id: str,
    session: aiohttp.ClientSession | None = None,
) -> bytes:
    """Download bytes for a previously staged document.

    The Node.js download route (``getDocumentInfo``) enforces
    ``{_id, orgId}`` matching, so a request scoped to ``org_id`` cannot read a
    document owned by a different org.

    Pass ``session`` to reuse an open ``aiohttp.ClientSession`` across many
    fetches in a batch (HTTP keep-alive + pooled connections to the cm
    endpoint). When ``None``, a single-use session is created and torn down
    for backward compatibility with 1-shot callers.
    """
    if not storage_document_id:
        raise BlobStagingError("storage_document_id is required")

    headers, nodejs_endpoint = await _get_storage_auth(org_id, config_service)
    download_url = (
        f"{nodejs_endpoint}"
        f"{Routes.STORAGE_DOWNLOAD.value.format(documentId=storage_document_id)}"
    )

    async with _session_or_default(session) as http:
        async with http.get(
            download_url, headers=headers, timeout=_REQUEST_TIMEOUT,
        ) as resp:
            if resp.status != HttpStatusCode.SUCCESS.value:
                detail = (await resp.text())[:300]
                raise BlobStagingError(
                    f"Storage download failed [{resp.status}]: {detail}"
                )
            content_type = (resp.headers.get("Content-Type") or "").lower()
            if "application/json" in content_type:
                payload = await resp.json()
                signed_url = (
                    payload.get("signedUrl")
                    if isinstance(payload, dict)
                    else None
                )
                if signed_url:
                    async with http.get(
                        URL(signed_url, encoded=True),
                        timeout=_REQUEST_TIMEOUT,
                    ) as signed_resp:
                        if signed_resp.status != HttpStatusCode.SUCCESS.value:
                            detail = (await signed_resp.text())[:300]
                            raise BlobStagingError(
                                f"Signed URL fetch failed "
                                f"[{signed_resp.status}]: {detail}"
                            )
                        return await signed_resp.read()
                # Local storage path returns inline JSON / base64 fallback.
                if isinstance(payload, dict) and payload.get("base64"):
                    return base64.b64decode(payload["base64"])
                raise BlobStagingError(
                    "Storage download returned JSON without signedUrl/base64"
                )
            return await resp.read()


async def fetch_staged_document_bytes(
    *,
    document_id: str,
    entry: StagedDocumentEntry,
    org_id: str,
    config_service: ConfigurationService,
    session: aiohttp.ClientSession | None = None,
) -> bytes:
    """Resolve a ``document_id_to_url`` registry entry to raw bytes.

    When ``entry.storage_type == 's3'``, ``download_url`` is treated as a
    pre-signed object URL (GET without auth). Otherwise bytes are loaded via
    :func:`fetch_blob_bytes` (scoped storage JWT + internal download route).

    Accepts a raw ``Mapping`` in addition to :class:`StagedDocumentEntry`
    purely so a LangGraph checkpointer that round-trips ``chat_state``
    through JSON (entries come back as plain dicts) doesn't break consumers
    — the boundary coercion is one line and keeps the scoped path strict.

    Pass ``session`` to share one ``aiohttp.ClientSession`` across a batch
    fetch — the same session is forwarded to :func:`fetch_blob_bytes` on the
    scoped path, so keep-alive holds across both code paths. When ``None``,
    each call creates and tears down its own one-shot session.

    Raises ``RuntimeError`` on direct-URL HTTP failure; :exc:`BlobStagingError`
    on the scoped path. Size limits are enforced by callers after return.
    """
    validated = (
        entry
        if isinstance(entry, StagedDocumentEntry)
        else StagedDocumentEntry.model_validate(entry)
    )
    if validated.storage_type == "s3":
        async with _session_or_default(session) as http:
            async with http.get(
                URL(validated.download_url, encoded=True),
                timeout=_REQUEST_TIMEOUT,
            ) as resp:
                if resp.status != HttpStatusCode.SUCCESS.value:
                    detail = (await resp.text())[:300]
                    raise RuntimeError(
                        f"Download URL returned HTTP {resp.status}: {detail}"
                    )
                return await resp.read()

    return await fetch_blob_bytes(
        org_id=org_id,
        config_service=config_service,
        storage_document_id=document_id,
        session=session,
    )


def conversation_upload_to_registry_entry(
    upload_info: BlobUploadInfo,
    *,
    filename: str,
    mime_type: str,
    size_bytes: int,
    source: StagedDocumentSource | None = None,
) -> tuple[str, StagedDocumentEntry] | None:
    """Turn ``BlobStorage.save_conversation_file_to_storage`` JSON into a registry row.

    Returns ``(document_id, entry)`` for ``chat_state['document_id_to_url']``,
    or ``None`` if ``documentId`` / download URL are missing.

    Accepts either a validated :class:`BlobUploadInfo` or the raw ``dict``
    that the storage route returns today; the dict path goes through
    ``model_validate`` so extra keys are ignored and ``documentId`` int/str
    polymorphism is handled at the boundary.
    """
    info = (
        upload_info
        if isinstance(upload_info, BlobUploadInfo)
        else BlobUploadInfo.model_validate(upload_info)
    )
    download_url = info.signedUrl or info.downloadUrl
    if not info.documentId or not download_url:
        return None

    validated_source = (
        source
        if source is None or isinstance(source, StagedDocumentSource)
        else StagedDocumentSource.model_validate(source)
    )
    entry = StagedDocumentEntry(
        download_url=download_url,
        filename=filename,
        mime_type=mime_type,
        size_bytes=size_bytes,
        storage_type="s3" if info.signedUrl else "external",
        source=validated_source,
    )
    return str(info.documentId), entry
