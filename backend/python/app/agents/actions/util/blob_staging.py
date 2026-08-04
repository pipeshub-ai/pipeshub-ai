"""
Fetch raw bytes back from PipesHub blob storage from agent actions.

:func:`fetch_blob_bytes` is the primary export used by the record-content
resolution layer (``BlobBackedContentStrategy``) and artifact registry. It
downloads raw bytes for a storage document using a scoped JWT and the internal
Node.js download route (or follows a signedUrl hop for S3-backed storage).

Tenancy: the download route's ``getDocumentInfo`` middleware filters by
``{_id, orgId}``, so a STORAGE_TOKEN-scoped JWT issued for org A cannot read
org B's documents. Callers must pass a consistent ``org_id``.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiohttp
import jwt
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
    version: int | None = None,
    session: aiohttp.ClientSession | None = None,
) -> bytes:
    """Download bytes for a previously staged document.

    The Node.js download route (``getDocumentInfo``) enforces
    ``{_id, orgId}`` matching, so a request scoped to ``org_id`` cannot read a
    document owned by a different org.

    Pass ``version`` (a storage-layer ``versionHistory`` index, NOT a
    registry version number — callers must map that first, see
    ``ArtifactRegistryService._resolve_storage_version``) to pin the fetch
    to a specific historical version; omit it for the current content.

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
    if version is not None:
        download_url = f"{download_url}?version={version}"

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


