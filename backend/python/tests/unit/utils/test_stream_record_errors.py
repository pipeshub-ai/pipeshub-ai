"""Tests for stream-record error mapping.

Covers the two guarantees this change exists for:
  1. a source failure reaches the client as its own status, not a blanket 500
     or a hardcoded 404;
  2. that status is still settable when the connector calls the source lazily
     inside the stream body.
"""

import asyncio
from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.connectors.core.base.error.stream_errors import (
    connector_not_ready,
    extract_source_status,
    map_source_status,
    not_downloadable,
    not_found_at_source,
    to_stream_error,
)
from app.utils.streaming import create_stream_record_response, start_streaming_response


class TestMapSourceStatus:
    @pytest.mark.parametrize(
        ("source_status", "expected"),
        [
            (401, 409),  # reconnect — deliberately NOT 401, see below
            (403, 403),
            (404, 404),
            (410, 404),
            (429, 429),
            (400, 422),
            (500, 502),
            (503, 502),
            (504, 504),
        ],
    )
    def test_status_mapping(self, source_status: int, expected: int) -> None:
        assert map_source_status(source_status, connector="Acme").status_code == expected

    def test_source_401_never_surfaces_as_401(self) -> None:
        """A 401 from us makes the frontend interceptor refresh-then-logout, so a
        dead *connector* token must not be reported as our session expiring."""
        err = map_source_status(401, connector="Google Drive")
        assert err.status_code == 409
        assert "Reconnect" in err.detail

    def test_rate_limit_forwards_retry_after(self) -> None:
        assert map_source_status(429, connector="Slack", retry_after="30").headers == {
            "Retry-After": "30"
        }

    def test_retry_after_ignored_for_other_statuses(self) -> None:
        assert not map_source_status(404, connector="Slack", retry_after="30").headers

    @pytest.mark.parametrize("status", [None, "403", object(), True])
    def test_unusable_status_falls_back_to_500(self, status: object) -> None:
        """SDKs hand back all sorts of things; the error path must not itself raise."""
        err = map_source_status(status, connector="Acme")  # type: ignore[arg-type]
        assert err.status_code == 500

    def test_message_reads_without_a_connector_name(self) -> None:
        assert "the source" in map_source_status(404).detail


class TestToStreamError:
    def test_refresh_token_invalid_means_reconnect(self) -> None:
        class RefreshTokenInvalidError(Exception):
            pass

        err = to_stream_error(RefreshTokenInvalidError("dead"), connector="Dropbox")
        assert err.status_code == 409

    def test_connector_init_error_keeps_its_user_facing_message(self) -> None:
        class ConnectorInitError(Exception):
            pass

        err = to_stream_error(ConnectorInitError("Missing client secret"))
        assert err.status_code == 409
        assert err.detail == "Missing client secret"

    def test_api_call_error_uses_its_status(self) -> None:
        from app.utils.api_call import ApiCallError

        assert to_stream_error(ApiCallError("nope", status_code=404)).status_code == 404

    def test_timeout_maps_to_gateway_timeout(self) -> None:
        assert to_stream_error(asyncio.TimeoutError()).status_code == 504

    def test_existing_http_exception_passes_through(self) -> None:
        original = HTTPException(status_code=418, detail="teapot")
        assert to_stream_error(original) is original

    def test_unknown_error_does_not_leak_the_message(self) -> None:
        """Exception text can carry tokens, signed URLs and internal hostnames."""
        err = to_stream_error(RuntimeError("token=sk-secret host=internal.local"))
        assert err.status_code == 500
        assert "sk-secret" not in err.detail
        assert "internal.local" not in err.detail


class TestExtractSourceStatus:
    """Each SDK hides the status somewhere different; connectors rely on this
    so they don't each need their own translation."""

    def test_googleapiclient_shape(self) -> None:
        class Resp:
            status = 403

        class HttpError(Exception):
            resp = Resp()

        assert extract_source_status(HttpError()) == 403

    def test_msgraph_shape(self) -> None:
        class ODataError(Exception):
            response_status_code = 401

        assert to_stream_error(ODataError()).status_code == 409

    def test_box_shape(self) -> None:
        class Info:
            status_code = 404

        class BoxAPIError(Exception):
            response_info = Info()

        assert extract_source_status(BoxAPIError()) == 404

    def test_botocore_shape(self) -> None:
        class BotoError(Exception):
            response = {"ResponseMetadata": {"HTTPStatusCode": 429}}

        assert extract_source_status(BotoError()) == 429

    def test_no_status_available(self) -> None:
        assert extract_source_status(RuntimeError("boom")) is None

    def test_bogus_status_is_rejected(self) -> None:
        class Weird(Exception):
            status_code = 9999

        assert extract_source_status(Weird()) is None


class TestSmallHelpers:
    def test_connector_not_ready_is_409_not_404(self) -> None:
        """An uninitialised connector must not be reported as a missing file."""
        assert connector_not_ready("Box").status_code == 409

    def test_not_found_at_source(self) -> None:
        assert not_found_at_source("Box").status_code == 404

    def test_not_downloadable(self) -> None:
        assert not_downloadable("nope", connector="Drive").status_code == 422


def _app(make_response) -> FastAPI:
    app = FastAPI()

    @app.get("/stream")
    async def _stream():  # noqa: ANN202
        return await start_streaming_response(make_response())

    return app


class TestStartStreamingResponse:
    def test_failure_on_first_chunk_still_sets_the_status(self) -> None:
        """Starlette commits http.response.start before asking for a chunk, so
        without the prefetch this returns 200 + an empty body."""

        async def failing() -> AsyncGenerator[bytes, None]:
            raise map_source_status(403, connector="Slack")
            yield b""  # pragma: no cover - unreachable

        with TestClient(_app(lambda: create_stream_record_response(failing(), "f.txt"))) as c:
            resp = c.get("/stream")

        assert resp.status_code == 403
        assert "Slack" in resp.json()["detail"]

    def test_successful_stream_is_unchanged(self) -> None:
        async def ok() -> AsyncGenerator[bytes, None]:
            yield b"hello "
            yield b"world"

        with TestClient(_app(lambda: create_stream_record_response(ok(), "f.txt"))) as c:
            resp = c.get("/stream")

        assert resp.status_code == 200
        assert resp.content == b"hello world"
        assert 'filename="f.txt"' in resp.headers["content-disposition"]

    def test_empty_stream_is_unchanged(self) -> None:
        async def empty() -> AsyncGenerator[bytes, None]:
            return
            yield b""  # pragma: no cover - unreachable

        with TestClient(_app(lambda: create_stream_record_response(empty(), "f.txt"))) as c:
            resp = c.get("/stream")

        assert resp.status_code == 200
        assert resp.content == b""

    @pytest.mark.asyncio
    async def test_only_one_chunk_is_consumed_eagerly(self) -> None:
        pulled = []

        async def counting() -> AsyncGenerator[bytes, None]:
            for i in range(3):
                pulled.append(i)
                yield str(i).encode()

        response = StreamingResponse(counting())
        await start_streaming_response(response)
        assert pulled == [0], "the rest of the stream must stay lazy"

        collected = [chunk async for chunk in response.body_iterator]
        assert b"".join(collected) == b"012"
