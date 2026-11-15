"""A .log/.cfg with no stored size is held to the 256 MB ceiling while it downloads: the download
stops past it, the file is not supported with the size reason, and nothing else downloads it."""
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web

from app.config import file_types
from app.utils.api_call import ResponseTooLargeError, make_api_call
from app.utils.user_errors import file_too_large_to_read
from tests.unit.modules.parsers.real_files.new_type_harness import handler_run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_LIMIT = 1024


@pytest.fixture
async def big_file_url(unused_tcp_port: int) -> AsyncIterator[tuple[str, list[int]]]:
    sent: list[int] = []

    async def chunked(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse()
        await response.prepare(request)
        for _ in range(64):
            await response.write(b"x" * 512)
            sent.append(512)
        await response.write_eof()
        return response

    async def sized(_request: web.Request) -> web.Response:
        return web.Response(body=b"x" * (_LIMIT * 4))

    app = web.Application()
    app.router.add_get("/chunked", chunked)
    app.router.add_get("/sized", sized)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", unused_tcp_port)
    await site.start()
    yield f"http://127.0.0.1:{unused_tcp_port}", sent
    await runner.cleanup()


@pytest.mark.parametrize("path", ["/chunked", "/sized"])
async def test_the_signed_url_download_stops_past_the_limit(big_file_url, path: str) -> None:  # type: ignore[no-untyped-def]
    from tests.unit.services.messaging.test_record_handler import _make_handler

    base, _sent = big_file_url
    handler = _make_handler()
    with pytest.raises(ResponseTooLargeError):
        await handler._download_from_signed_url(f"{base}{path}", "r1", {}, max_bytes=_LIMIT)
    assert await handler._download_from_signed_url(f"{base}{path}", "r1", {}) is not None


@pytest.mark.parametrize("path", ["/chunked", "/sized"])
async def test_the_connector_stream_stops_past_the_limit(big_file_url, path: str) -> None:  # type: ignore[no-untyped-def]
    base, _sent = big_file_url
    with pytest.raises(ResponseTooLargeError):
        await make_api_call(f"{base}{path}", "token", max_bytes=_LIMIT)
    assert len((await make_api_call(f"{base}{path}", "token"))["data"]) >= _LIMIT * 4


@pytest.mark.parametrize("ext", ["log", "cfg"])
async def test_an_unsized_log_past_the_ceiling_is_not_supported_and_not_streamed_again(ext: str) -> None:
    with patch("app.services.messaging.kafka.handlers.record.make_api_call", new_callable=AsyncMock) as stream:
        record, downloaded = await handler_run(
            ext, "application/octet-stream", b"", too_large=True,
        )
    assert downloaded
    assert record["indexingStatus"] == "FILE_TYPE_NOT_SUPPORTED"
    assert record["reason"] == file_too_large_to_read(ext)
    stream.assert_not_awaited()


async def test_the_limit_is_passed_only_for_capped_text() -> None:
    seen: dict[str, object] = {}
    record, _ = await handler_run("pdf", "application/pdf", b"%PDF-1.4", download_kwargs=seen)
    assert seen.get("max_bytes") is None
    await handler_run("log", None, b"line\n", download_kwargs=seen)
    assert seen.get("max_bytes") == file_types.TEXT_LOG_HARD_LIMIT_BYTES


_UNLABELLED = ["", "<none>", "unknown"]


@pytest.mark.parametrize("ext", _UNLABELLED)
async def test_a_log_without_an_extension_is_held_to_the_limit_on_the_signed_url(ext: str) -> None:
    seen: dict[str, object] = {}
    await handler_run(ext, "text/plain", b"line\n", name="build.log", download_kwargs=seen)
    assert seen.get("max_bytes") == file_types.TEXT_LOG_HARD_LIMIT_BYTES


@pytest.mark.parametrize("ext", _UNLABELLED)
async def test_a_log_without_an_extension_is_held_to_the_limit_on_the_connector_stream(ext: str) -> None:
    with patch("app.services.messaging.kafka.handlers.record.make_api_call", new_callable=AsyncMock) as stream, \
         patch("app.services.messaging.kafka.handlers.record.generate_jwt", new_callable=AsyncMock, return_value="t"):
        stream.return_value = {"is_json": False, "data": b"line\n"}
        await handler_run(ext, "text/plain", b"line\n", name="build.log", signed_url_fails=True)
    stream.assert_awaited()
    assert stream.await_args.kwargs.get("max_bytes") == file_types.TEXT_LOG_HARD_LIMIT_BYTES


@pytest.mark.parametrize("ext", _UNLABELLED)
async def test_a_log_without_an_extension_too_large_by_its_size_is_refused_before_download(ext: str) -> None:
    record, downloaded = await handler_run(
        ext, "text/plain", b"", name="build.log", size=file_types.TEXT_LOG_HARD_LIMIT_BYTES + 1,
    )
    assert not downloaded
    assert record["indexingStatus"] == "FILE_TYPE_NOT_SUPPORTED"
    assert record["reason"] == file_too_large_to_read("log")


@pytest.mark.parametrize("ext", _UNLABELLED)
async def test_another_file_without_an_extension_has_no_limit(ext: str) -> None:
    seen: dict[str, object] = {}
    await handler_run(ext, "text/plain", b"line\n", name="notes.txt", download_kwargs=seen)
    assert seen and seen.get("max_bytes") is None
