"""Export streaming, including the 10 MB exportLinks fallback."""

import json
import logging
from collections.abc import AsyncIterator

import pytest
from fastapi import HTTPException
from googleapiclient.errors import HttpError
from httplib2 import Response

from app.connectors.sources.google.drive.utils.drive_export import (
    GoogleExportStreamer,
    is_allowed_export_url,
    stream_media_request,
)

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TOKEN = "export-token-should-not-be-logged"


def _size_limit_error() -> HttpError:
    body = json.dumps({
        "error": {
            "code": 403,
            "message": "too big",
            "errors": [{"reason": "exportSizeLimitExceeded", "domain": "global", "message": "too big"}],
        }
    }).encode()
    return HttpError(Response({"status": "403"}), body)


class _Downloader:
    def __init__(self, buffer: object, request: object, chunksize: int = 0) -> None:
        self.buffer = buffer
        self.request = request

    def next_chunk(self) -> tuple[None, bool]:
        if getattr(self.request, "fail", False):
            raise _size_limit_error()
        self.buffer.write(b"exported-bytes")
        return None, True


class _Request:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail


class _Files:
    def __init__(self, request: _Request) -> None:
        self.request = request

    def export_media(self, **_kwargs: object) -> _Request:
        return self.request


class _Drive:
    def __init__(self, request: _Request) -> None:
        self._files = _Files(request)

    def files(self) -> _Files:
        return self._files


def _logger() -> tuple[logging.Logger, list[str]]:
    logger = logging.getLogger("drive-export-test")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    messages: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    logger.addHandler(_Handler())
    return logger, messages


async def _execute(operation: object) -> object:
    if not callable(operation):
        raise TypeError("expected a callable")
    return operation()


async def test_a_file_under_the_limit_streams_from_export_media() -> None:
    logger, messages = _logger()
    streamer = GoogleExportStreamer(
        drive_service=_Drive(_Request()),
        execute=_execute,
        get_access_token=_token,
        downloader_cls=_Downloader,
        logger=logger,
        connector_name="Drive",
    )

    body = b"".join([chunk async for chunk in streamer.iter_export("file-1", DOCX)])

    assert body == b"exported-bytes"
    assert TOKEN not in "\n".join(messages)


async def test_export_size_limit_falls_back_to_an_allowed_export_link() -> None:
    logger, messages = _logger()
    seen: dict[str, str] = {}

    class _Content:
        async def iter_chunked(self, _size: int) -> AsyncIterator[bytes]:
            yield b"from-link"

    class _Response:
        status = 200
        headers: dict[str, str] = {}
        closed = False
        content = _Content()

        async def release(self) -> None:
            self.closed = True

    class _Session:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> "_Session":
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        async def get(self, url: str, headers: dict[str, str] | None = None, allow_redirects: bool = True) -> _Response:
            seen["url"] = url
            seen["auth"] = (headers or {}).get("Authorization", "")
            assert allow_redirects is False
            return _Response()

    async def files_get(_file_id: str) -> dict:
        return {"exportLinks": {DOCX: "https://docs.google.com/export?id=file-1"}}

    streamer = GoogleExportStreamer(
        drive_service=_Drive(_Request(fail=True)),
        execute=_execute,
        get_access_token=_token,
        downloader_cls=_Downloader,
        logger=logger,
        connector_name="Drive",
        files_get=files_get,
    )

    import app.connectors.sources.google.drive.utils.drive_export as drive_export

    original = drive_export.aiohttp.ClientSession
    drive_export.aiohttp.ClientSession = _Session
    try:
        body = b"".join([chunk async for chunk in streamer.iter_export("file-1", DOCX)])
    finally:
        drive_export.aiohttp.ClientSession = original

    assert body == b"from-link"
    assert seen["url"] == "https://docs.google.com/export?id=file-1"
    assert seen["auth"] == f"Bearer {TOKEN}"
    logged = "\n".join(messages)
    assert TOKEN not in logged
    assert "docs.google.com" not in logged


async def test_a_non_google_export_link_is_refused() -> None:
    logger, _messages = _logger()

    async def files_get(_file_id: str) -> dict:
        return {"exportLinks": {DOCX: "https://evil.example/export"}}

    streamer = GoogleExportStreamer(
        drive_service=_Drive(_Request(fail=True)),
        execute=_execute,
        get_access_token=_token,
        downloader_cls=_Downloader,
        logger=logger,
        connector_name="Drive",
        files_get=files_get,
    )

    with pytest.raises(HTTPException) as raised:
        _ = [chunk async for chunk in streamer.iter_export("file-1", DOCX)]

    assert raised.value.status_code == 422


async def test_a_redirect_off_google_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    logger, messages = _logger()
    calls: list[str] = []

    class _Redirect:
        status = 302
        headers = {"Location": "https://evil.example/steal"}
        closed = False

        async def release(self) -> None:
            self.closed = True

    class _Session:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> "_Session":
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        async def get(self, url: str, headers: dict[str, str] | None = None, allow_redirects: bool = True) -> _Redirect:
            calls.append(url)
            assert TOKEN not in url
            return _Redirect()

    import app.connectors.sources.google.drive.utils.drive_export as drive_export

    monkeypatch.setattr(drive_export.aiohttp, "ClientSession", _Session)

    async def files_get(_file_id: str) -> dict:
        return {"exportLinks": {DOCX: "https://docs.google.com/export?id=file-1"}}

    streamer = GoogleExportStreamer(
        drive_service=_Drive(_Request(fail=True)),
        execute=_execute,
        get_access_token=_token,
        downloader_cls=_Downloader,
        logger=logger,
        connector_name="Drive",
        files_get=files_get,
    )

    with pytest.raises(HTTPException) as raised:
        _ = [chunk async for chunk in streamer.iter_export("file-1", DOCX)]

    assert raised.value.status_code == 400
    assert calls == ["https://docs.google.com/export?id=file-1"]
    assert TOKEN not in "\n".join(messages)


def test_export_urls_are_limited_to_google_hosts() -> None:
    assert is_allowed_export_url("https://docs.google.com/export") is True
    assert is_allowed_export_url("https://lh3.googleusercontent.com/file") is True
    assert is_allowed_export_url("http://docs.google.com/export") is False
    assert is_allowed_export_url("https://evil.example/export") is False
    assert is_allowed_export_url("https://docs.google.com.evil.example/export") is False
    assert is_allowed_export_url("https://user:pass@docs.google.com/export") is False


async def _token() -> str:
    return TOKEN


async def test_stream_media_request_yields_download_chunks() -> None:
    logger, _messages = _logger()

    async def execute(operation: object) -> object:
        if not callable(operation):
            raise TypeError("expected a callable")
        return operation()

    chunks = [
        chunk
        async for chunk in stream_media_request(
            execute,
            _Request(),
            downloader_cls=_Downloader,
            chunk_size=1024,
            logger=logger,
            connector_name="Drive",
            error_context="download",
        )
    ]

    assert chunks == [b"exported-bytes"]
