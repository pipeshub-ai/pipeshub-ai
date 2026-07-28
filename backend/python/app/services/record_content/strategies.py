"""`BlobBackedContentStrategy` and `ConnectorBackedContentStrategy`.

Two fetch backends, dispatched by `RecordContentResolver` based on
`record.origin`:

- `UPLOAD` → `BlobBackedContentStrategy`: uses the STORAGE_TOKEN path that
  already exists in `blob_staging.fetch_blob_bytes`. No cross-service hop —
  bytes are pulled in-process from the Node.js blob store.

- `CONNECTOR` → `ConnectorBackedContentStrategy`: bytes only exist in the
  external system; calls the new ACL-enforcing internal endpoint on the
  connectors service with a `record:content`-scoped JWT.

Both strategies enforce `max_bytes` before accumulating the full response.
"""

from __future__ import annotations

import logging
import mimetypes
from typing import Any

import aiohttp
import jwt

from app.agents.actions.util.blob_staging import fetch_blob_bytes
from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import OriginTypes
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import (
    DefaultEndpoints,
    TokenScopes,
    config_node_constants,
)
from app.services.artifact_registry.versioning import resolve_storage_version

from .models import (
    RecordContentUnavailableError,
    RecordTooLargeError,
    ResolvedRecordContent,
)

logger = logging.getLogger(__name__)

__all__ = ["BlobBackedContentStrategy", "ConnectorBackedContentStrategy"]

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=120)

# Internal endpoint template for connector-backed records.
_CONNECTOR_CONTENT_PATH = "/api/v1/internal/records/{record_id}/content"


def _guess_mime(record: Any, fallback: str = "application/octet-stream") -> str:
    mime = (
        getattr(record, "mime_type", None)
        or (record.get("mimeType") if isinstance(record, dict) else None)
    )
    if mime:
        return mime
    filename = (
        getattr(record, "record_name", None)
        or (record.get("recordName") if isinstance(record, dict) else None)
    )
    if filename:
        guessed, _ = mimetypes.guess_type(filename)
        if guessed:
            return guessed
    return fallback


def _record_attr(record: Any, attr_camel: str, attr_snake: str) -> Any:
    """Get an attribute from either a Pydantic model or a plain dict."""
    if isinstance(record, dict):
        return record.get(attr_camel) or record.get(attr_snake)
    return getattr(record, attr_snake, None) or getattr(record, attr_camel, None)


class BlobBackedContentStrategy:
    """Fetch content from PipesHub blob storage (origin=UPLOAD).

    Delegates to `fetch_blob_bytes`, which handles the local-streams-binary
    vs S3-returns-signedUrl split. Uses `resolve_storage_version` to map
    registry version numbers to storage layer indices.
    """

    def supports(self, record: Any) -> bool:
        origin = _record_attr(record, "origin", "origin")
        if origin is None:
            return False
        if isinstance(origin, OriginTypes):
            return origin == OriginTypes.UPLOAD
        return str(origin).upper() == "UPLOAD"

    async def fetch(
        self,
        record: Any,
        *,
        actor: Any,
        version: int | None,
        max_bytes: int,
        session: aiohttp.ClientSession | None,
        config_service: ConfigurationService,
    ) -> bytes:
        external_record_id = _record_attr(record, "externalRecordId", "external_record_id")
        if not external_record_id:
            raise RecordContentUnavailableError(
                f"Record has no externalRecordId: {_record_attr(record, '_key', 'id')}"
            )

        storage_version: int | None = None
        if version is not None:
            current_version = _record_attr(record, "version", "version") or 1
            raw_versions = _record_attr(record, "versions", "versions") or []
            try:
                storage_version = resolve_storage_version(current_version, raw_versions, version)
            except Exception as exc:
                raise RecordContentUnavailableError(
                    f"Cannot resolve version {version}: {exc}"
                ) from exc

        try:
            data = await fetch_blob_bytes(
                org_id=actor.org_id,
                config_service=config_service,
                storage_document_id=external_record_id,
                version=storage_version,
                session=session,
            )
        except Exception as exc:
            raise RecordContentUnavailableError(
                f"Blob fetch failed: {exc}"
            ) from exc

        if len(data) > max_bytes:
            raise RecordTooLargeError(
                record_id=_record_attr(record, "_key", "id") or "",
                size_bytes=len(data),
                max_bytes=max_bytes,
            )
        return data


class ConnectorBackedContentStrategy:
    """Fetch content via the connectors service's ACL-enforcing internal
    endpoint (origin=CONNECTOR).

    Uses a `record:content`-scoped JWT that carries `userId` so the endpoint
    can enforce the ACL independently of the caller.
    """

    def supports(self, record: Any) -> bool:
        origin = _record_attr(record, "origin", "origin")
        if origin is None:
            return False
        if isinstance(origin, OriginTypes):
            return origin == OriginTypes.CONNECTOR
        return str(origin).upper() == "CONNECTOR"

    async def fetch(
        self,
        record: Any,
        *,
        actor: Any,
        version: int | None,
        max_bytes: int,
        session: aiohttp.ClientSession | None,
        config_service: ConfigurationService,
    ) -> bytes:
        record_id = _record_attr(record, "_key", "id")
        if not record_id:
            raise RecordContentUnavailableError("Record has no id")

        try:
            secret_keys = await config_service.get_config(
                config_node_constants.SECRET_KEYS.value
            )
            scoped_jwt_secret = (secret_keys or {}).get("scopedJwtSecret")
            if not scoped_jwt_secret:
                raise RecordContentUnavailableError("Missing scopedJwtSecret in configuration")

            token = jwt.encode(
                {
                    "orgId": actor.org_id,
                    "userId": actor.user_id,
                    "scopes": [TokenScopes.RECORD_CONTENT.value],
                },
                scoped_jwt_secret,
                algorithm="HS256",
            )

            endpoints = await config_service.get_config(
                config_node_constants.ENDPOINTS.value
            )
            connector_endpoint = (endpoints or {}).get("connectors", {}).get(
                "endpoint", DefaultEndpoints.CONNECTOR_ENDPOINT.value
            )
            connector_endpoint = connector_endpoint.rstrip("/")
        except RecordContentUnavailableError:
            raise
        except Exception as exc:
            raise RecordContentUnavailableError(
                f"Failed to build connector content request: {exc}"
            ) from exc

        url = f"{connector_endpoint}{_CONNECTOR_CONTENT_PATH.format(record_id=record_id)}"
        if version is not None:
            url = f"{url}?version={version}"

        headers = {"Authorization": f"Bearer {token}"}

        async def _do_fetch(http: aiohttp.ClientSession) -> bytes:
            async with http.get(url, headers=headers, timeout=_REQUEST_TIMEOUT) as resp:
                if resp.status == 404:
                    raise RecordContentUnavailableError(
                        f"Connector content not found for record {record_id}"
                    )
                if resp.status == 403:
                    from .models import RecordAccessDeniedError
                    raise RecordAccessDeniedError(
                        f"Connector content endpoint denied access to record {record_id}"
                    )
                if resp.status != HttpStatusCode.SUCCESS.value:
                    detail = (await resp.text())[:300]
                    raise RecordContentUnavailableError(
                        f"Connector content endpoint returned {resp.status}: {detail}"
                    )

                # Check Content-Length pre-flight before reading the full body.
                content_length = resp.headers.get("Content-Length")
                if content_length:
                    try:
                        declared_size = int(content_length)
                        if declared_size > max_bytes:
                            raise RecordTooLargeError(
                                record_id=record_id,
                                size_bytes=declared_size,
                                max_bytes=max_bytes,
                            )
                    except ValueError:
                        pass

                data = await resp.read()
                if len(data) > max_bytes:
                    raise RecordTooLargeError(
                        record_id=record_id,
                        size_bytes=len(data),
                        max_bytes=max_bytes,
                    )
                return data

        try:
            if session is not None:
                return await _do_fetch(session)
            async with aiohttp.ClientSession() as http:
                return await _do_fetch(http)
        except (RecordContentUnavailableError, RecordTooLargeError):
            raise
        except Exception as exc:
            raise RecordContentUnavailableError(
                f"Connector content fetch failed: {exc}"
            ) from exc


def build_resolved_content(
    record: Any,
    content: bytes,
    version: int | None,
    source: str,
) -> ResolvedRecordContent:
    """Assemble a `ResolvedRecordContent` from graph record + fetched bytes."""
    record_id = _record_attr(record, "_key", "id") or ""
    filename = (
        _record_attr(record, "recordName", "record_name")
        or _record_attr(record, "name", "name")
        or record_id
    )
    mime_type = _guess_mime(record)
    return ResolvedRecordContent(
        record_id=record_id,
        filename=filename,
        mime_type=mime_type,
        size_bytes=len(content),
        content=content,
        version=version,
        source=source,
    )
