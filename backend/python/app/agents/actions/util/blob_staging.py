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
from collections.abc import Mapping
from typing import Any

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


class BlobStagingError(Exception):
    """Raised when blob fetching fails."""


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
) -> bytes:
    """Download bytes for a previously staged document.

    The Node.js download route (``getDocumentInfo``) enforces
    ``{_id, orgId}`` matching, so a request scoped to ``org_id`` cannot read a
    document owned by a different org.
    """
    if not storage_document_id:
        raise BlobStagingError("storage_document_id is required")

    headers, nodejs_endpoint = await _get_storage_auth(org_id, config_service)
    download_url = (
        f"{nodejs_endpoint}"
        f"{Routes.STORAGE_DOWNLOAD.value.format(documentId=storage_document_id)}"
    )

    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(download_url, headers=headers) as resp:
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
                    async with session.get(
                        URL(signed_url, encoded=True)
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
    entry: Mapping[str, Any],
    org_id: str,
    config_service: ConfigurationService,
) -> bytes:
    """Resolve a ``document_id_to_url`` registry entry to raw bytes.

    When ``entry['storage_type'] == 's3'``, ``download_url`` is treated as a
    pre-signed object URL (GET without auth). Otherwise bytes are loaded via
    :func:`fetch_blob_bytes` (scoped storage JWT + internal download route).

    Raises ``RuntimeError`` on direct-URL HTTP failure; :exc:`BlobStagingError`
    on the scoped path. Size limits are enforced by callers after return.
    """
    storage_type = entry.get("storage_type")
    if storage_type == "s3":
        url = entry.get("download_url")
        if not isinstance(url, str) or not url:
            raise RuntimeError("registry entry missing download_url")
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(URL(url, encoded=True)) as resp:
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
    )


def conversation_upload_to_registry_entry(
    upload_info: Mapping[str, Any],
    *,
    filename: str,
    mime_type: str,
    size_bytes: int,
    source: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """Turn ``BlobStorage.save_conversation_file_to_storage`` JSON into a registry row.

    Returns ``(document_id, entry)`` for ``chat_state['document_id_to_url']``,
    or ``None`` if ``documentId`` / download URL are missing.
    """
    document_id = upload_info.get("documentId")
    signed_url = upload_info.get("signedUrl")
    download_url = signed_url or upload_info.get("downloadUrl")
    if not document_id or not download_url:
        return None
    storage_type = "s3" if signed_url else "external"
    row: dict[str, Any] = {
        "download_url": download_url,
        "filename": filename,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "storage_type": storage_type,
    }
    if source is not None:
        row["source"] = source
    return str(document_id), row
