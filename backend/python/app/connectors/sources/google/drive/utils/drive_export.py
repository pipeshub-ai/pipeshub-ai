"""Stream a Google-native file, including files larger than the export API allows.

``files.export`` refuses documents over 10 MB with ``exportSizeLimitExceeded``.
The file's ``exportLinks`` URL is not subject to that cap. Those URLs are only
fetched when they point at Google; the bearer token is never sent anywhere else
and neither the URL nor the token is logged.
"""

from __future__ import annotations

import io
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

import aiohttp
from fastapi import HTTPException
from googleapiclient.errors import HttpError

from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.error.stream_errors import (
    map_source_status,
    to_stream_error,
)

if TYPE_CHECKING:
    from logging import Logger

EXPORT_SIZE_LIMIT_REASON = "exportSizeLimitExceeded"
_EXPORT_CHUNK_BYTES = 4 * 1024 * 1024
_MAX_REDIRECTS = 5

_ALLOWED_EXPORT_HOSTS = frozenset(
    {
        "docs.google.com",
        "drive.google.com",
        "www.googleapis.com",
    }
)
_GOOGLEUSERCONTENT_SUFFIX = ".googleusercontent.com"

Execute = Callable[[Callable[[], Any]], Awaitable[Any]]
TokenProvider = Callable[[], Awaitable[str]]


class ExportSizeLimitExceeded(Exception):
    """Drive refused ``files.export`` because the file is over 10 MB."""


def drive_http_reasons(error: HttpError) -> set[str]:
    details = getattr(error, "error_details", None) or []
    if not isinstance(details, list):
        return set()
    return {
        reason
        for detail in details
        if isinstance(detail, dict)
        and (reason := detail.get("reason"))
    }


def is_allowed_export_url(url: str | None) -> bool:
    """True for an https URL on a Google host that serves Drive exports."""
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if host in _ALLOWED_EXPORT_HOSTS:
        return True
    return host.endswith(_GOOGLEUSERCONTENT_SUFFIX)


async def stream_media_request(
    execute: Execute,
    request: object,
    *,
    downloader_cls: type,
    chunk_size: int,
    logger: Logger,
    connector_name: str,
    error_context: str,
) -> AsyncGenerator[bytes, None]:
    """Stream a Drive media request (``get_media`` or ``export_media``).

    ``downloader_cls`` is passed in so callers can keep ``MediaIoBaseDownload``
    in their own module, where existing tests patch it.
    """
    buffer = io.BytesIO()
    try:
        downloader = downloader_cls(buffer, request, chunksize=chunk_size)
        done = False
        while not done:
            try:
                _status, done = await execute(downloader.next_chunk)
            except HttpError as http_error:
                logger.error("HTTP error during %s: %s", error_context, http_error)
                if EXPORT_SIZE_LIMIT_REASON in drive_http_reasons(http_error):
                    raise ExportSizeLimitExceeded(error_context) from http_error
                raise map_source_status(
                    http_error.resp.status, connector=connector_name
                ) from http_error
            except Exception as chunk_error:
                logger.error("Error during %s: %s", error_context, chunk_error)
                raise to_stream_error(chunk_error, connector=connector_name) from chunk_error

            buffer.seek(0)
            content = buffer.read()
            if content:
                yield content
            buffer.seek(0)
            buffer.truncate(0)
    except HTTPException:
        raise
    except ExportSizeLimitExceeded:
        raise
    except Exception as stream_error:
        logger.error("Error in %s stream: %s", error_context, stream_error)
        raise to_stream_error(stream_error, connector=connector_name) from stream_error
    finally:
        buffer.close()


class GoogleExportStreamer:
    """Export a Google-native file, falling back to ``exportLinks`` past 10 MB."""

    def __init__(
        self,
        *,
        drive_service: object,
        execute: Execute,
        get_access_token: TokenProvider,
        downloader_cls: type,
        logger: Logger,
        connector_name: str,
        chunk_size: int = _EXPORT_CHUNK_BYTES,
        files_get: Callable[..., Awaitable[dict]] | None = None,
    ) -> None:
        self._drive_service = drive_service
        self._execute = execute
        self._get_access_token = get_access_token
        self._downloader_cls = downloader_cls
        self._logger = logger
        self._connector_name = connector_name
        self._chunk_size = chunk_size
        self._files_get = files_get

    async def iter_export(
        self, file_id: str, export_mime: str
    ) -> AsyncGenerator[bytes, None]:
        request = self._drive_service.files().export_media(
            fileId=file_id, mimeType=export_mime
        )
        try:
            async for chunk in stream_media_request(
                self._execute,
                request,
                downloader_cls=self._downloader_cls,
                chunk_size=self._chunk_size,
                logger=self._logger,
                connector_name=self._connector_name,
                error_context="Google Workspace file export",
            ):
                yield chunk
        except ExportSizeLimitExceeded:
            self._logger.info(
                "Drive export exceeded the 10 MB API limit for %s; using exportLinks",
                file_id,
            )
            async for chunk in self._iter_export_link(file_id, export_mime):
                yield chunk

    async def _iter_export_link(
        self, file_id: str, export_mime: str
    ) -> AsyncGenerator[bytes, None]:
        metadata = await self._load_export_links(file_id)
        link = (metadata.get("exportLinks") or {}).get(export_mime)
        if not is_allowed_export_url(link):
            raise HTTPException(
                status_code=HttpStatusCode.UNPROCESSABLE_ENTITY.value,
                detail="This file is too large to export from Google Drive",
            )
        token = await self._get_access_token()
        async for chunk in _stream_authorized_get(
            link, token, self._logger, self._connector_name
        ):
            yield chunk

    async def _load_export_links(self, file_id: str) -> dict:
        if self._files_get is not None:
            return await self._files_get(file_id) or {}
        request = self._drive_service.files().get(
            fileId=file_id,
            fields="exportLinks",
            supportsAllDrives=True,
        )
        return await self._execute(request.execute) or {}


async def _stream_authorized_get(
    url: str,
    token: str,
    logger: Logger,
    connector_name: str,
) -> AsyncGenerator[bytes, None]:
    """GET an export link, following redirects only while the host stays allowed.

    The token is attached to the first request and to each allowed redirect.
    It is not written to the log.
    """
    current = url
    timeout = aiohttp.ClientTimeout(total=300)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for _ in range(_MAX_REDIRECTS + 1):
            if not is_allowed_export_url(current):
                logger.error(
                    "Refusing Drive export URL for %s: host is not an allowed Google host",
                    connector_name,
                )
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail="Refusing to fetch a Google Drive export from this host",
                )
            try:
                response = await session.get(
                    current,
                    headers={"Authorization": f"Bearer {token}"},
                    allow_redirects=False,
                )
            except aiohttp.ClientError as error:
                logger.error("Drive export download failed: %s", type(error).__name__)
                raise to_stream_error(error, connector=connector_name) from error

            try:
                if response.status in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        raise HTTPException(
                            status_code=HttpStatusCode.BAD_GATEWAY.value,
                            detail="Google Drive export redirect had no destination",
                        )
                    current = urljoin(current, location)
                    continue
                if response.status >= 400:
                    raise map_source_status(response.status, connector=connector_name)
                async for chunk in response.content.iter_chunked(_EXPORT_CHUNK_BYTES):
                    if chunk:
                        yield chunk
                return
            finally:
                if not response.closed:
                    await response.release()
    raise HTTPException(
        status_code=HttpStatusCode.BAD_GATEWAY.value,
        detail="Google Drive export redirected too many times",
    )
