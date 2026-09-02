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
    raise_for_stream_fetch,
    to_internal_service_error,
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


class TestRaiseForStreamFetch:
    def test_success_with_payload_returns(self) -> None:
        raise_for_stream_fetch(
            success=True, has_payload=True, connector="Acme"
        )

    def test_success_empty_payload_is_404(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            raise_for_stream_fetch(
                success=True, has_payload=False, connector="Acme"
            )
        assert exc_info.value.status_code == 404
        assert "no longer exists" in exc_info.value.detail

    def test_failure_with_status_is_mapped(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            raise_for_stream_fetch(
                success=False,
                has_payload=False,
                connector="Acme",
                status=403,
            )
        assert exc_info.value.status_code == 403

    def test_failure_without_status_raises_runtime_error(self) -> None:
        """No proven status must not invent a 404 — to_stream_error maps this to 500."""
        with pytest.raises(RuntimeError, match="rate limited"):
            raise_for_stream_fetch(
                success=False,
                has_payload=False,
                connector="Acme",
                message="rate limited",
            )


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


class TestNonErrorStatusIsNoSignal:
    """A sub-400 status on an exception says nothing about the failure.

    Slack is the live case: `SlackApiError` carries the *200* response because
    the failure rides in the JSON body. Reading that 200 as the outcome used to
    classify every Slack error as 422 "cannot be downloaded".
    """

    @pytest.mark.parametrize("status", [200, 204, 302])
    def test_map_source_status_rejects_non_error(self, status) -> None:
        assert map_source_status(status).status_code == 500

    @pytest.mark.parametrize("status", [200, 204, 302])
    def test_extract_source_status_ignores_non_error(self, status) -> None:
        class Exc(Exception):
            status_code = status

        assert extract_source_status(Exc()) is None

    def test_slack_body_error_is_not_downloadable_claim(self) -> None:
        slack_sdk = pytest.importorskip("slack_sdk.errors")

        class Resp:
            status_code = 200

        assert to_stream_error(slack_sdk.SlackApiError("bad", Resp())).status_code == 500


class TestRequestTimeout:
    def test_source_408_is_a_timeout(self) -> None:
        assert map_source_status(408).status_code == 504


class TestSdkShapes:
    def test_google_refresh_error_is_reconnect(self) -> None:
        exceptions = pytest.importorskip("google.auth.exceptions")
        mapped = to_stream_error(exceptions.RefreshError("revoked"), connector="Drive")
        assert mapped.status_code == 409
        assert "Reconnect" in mapped.detail

    @pytest.mark.parametrize(("name", "expected"), [("NotFound", 404), ("Forbidden", 403)])
    def test_google_api_core_uses_code(self, name, expected) -> None:
        exceptions = pytest.importorskip("google.api_core.exceptions")
        exc = getattr(exceptions, name)("nope")
        assert to_stream_error(exc, connector="GCS").status_code == expected

    def test_dropbox_api_error_not_found(self) -> None:
        pytest.importorskip("dropbox")
        from dropbox.exceptions import ApiError
        from dropbox.files import GetTemporaryLinkError
        from dropbox.files import LookupError as DbxLookupError

        exc = ApiError("rid", GetTemporaryLinkError("path", DbxLookupError("not_found")), "m", None)
        assert to_stream_error(exc, connector="Dropbox").status_code == 404

    def test_retry_error_is_unwrapped(self) -> None:
        """tenacity wraps the last failure unless `reraise=True`, which
        `utils/api_call.py` does not pass — so a retried 429 arrives opaque."""
        tenacity = pytest.importorskip("tenacity")
        from app.utils.api_call import ApiCallError

        with pytest.raises(tenacity.RetryError) as caught:
            for attempt in tenacity.Retrying(stop=tenacity.stop_after_attempt(1)):
                with attempt:
                    raise ApiCallError("rate limited", 429)

        assert to_stream_error(caught.value, connector="Storage").status_code == 429

    def test_retry_after_is_carried_off_the_exception(self) -> None:
        httpx = pytest.importorskip("httpx")
        request = httpx.Request("GET", "https://example.test/f")
        response = httpx.Response(429, headers={"Retry-After": "30"}, request=request)
        mapped = to_stream_error(
            httpx.HTTPStatusError("429", request=request, response=response), connector="X"
        )
        assert mapped.status_code == 429
        assert mapped.headers["Retry-After"] == "30"


class TestInternalServiceError:
    """Our own storage service has no connector to reconnect."""

    @pytest.mark.parametrize(("status", "expected"), [(401, 502), (403, 502), (500, 502), (404, 404)])
    def test_auth_failure_is_not_a_reconnect_prompt(self, status, expected) -> None:
        from app.utils.api_call import ApiCallError

        mapped = to_internal_service_error(ApiCallError("boom", status))
        assert mapped.status_code == expected
        assert "Connector Settings" not in mapped.detail


class TestStartStreamingResponseReleasesSource:
    @pytest.mark.asyncio
    async def test_source_is_closed_when_download_is_abandoned(self) -> None:
        """Starlette never closes `body_iterator`, so an abandoned download
        would otherwise hold the connector's HTTP session until GC."""
        closed = []

        async def source() -> AsyncGenerator[bytes, None]:
            try:
                for i in range(3):
                    yield f"chunk{i}".encode()
            finally:
                closed.append(True)

        response = await start_streaming_response(StreamingResponse(source()))
        assert await response.body_iterator.__anext__() == b"chunk0"
        await response.body_iterator.aclose()
        assert closed == [True]

    @pytest.mark.asyncio
    async def test_source_is_closed_when_first_chunk_raises(self) -> None:
        closed = []

        async def source() -> AsyncGenerator[bytes, None]:
            try:
                raise RuntimeError("boom")
                yield b""  # noqa: unreachable — marks this an async generator
            finally:
                closed.append(True)

        with pytest.raises(RuntimeError):
            await start_streaming_response(StreamingResponse(source()))
        assert closed == [True]

    @pytest.mark.asyncio
    async def test_sync_iterable_body_has_no_aclose(self) -> None:
        """Starlette wraps a sync iterable in `iterate_in_threadpool`, which
        has no `aclose` — priming must not assume one."""
        response = await start_streaming_response(StreamingResponse(iter([b"a", b"b"])))
        assert [chunk async for chunk in response.body_iterator] == [b"a", b"b"]

    @pytest.mark.asyncio
    async def test_str_chunks_survive_priming(self) -> None:
        async def source() -> AsyncGenerator[str, None]:
            yield "hello"

        response = await start_streaming_response(StreamingResponse(source()))
        assert [chunk async for chunk in response.body_iterator] == ["hello"]


class TestTransportFailures:
    """DNS/TLS/refused/reset carry no status, and used to collapse into 500 —
    which is the dominant failure mode for self-hosted sources."""

    @pytest.mark.parametrize(
        "exc",
        [
            ConnectionRefusedError("refused"),
            ConnectionResetError("reset"),
            __import__("socket").gaierror("dns"),
            __import__("ssl").SSLError("handshake"),
        ],
        ids=["refused", "reset", "dns", "tls"],
    )
    def test_unreachable_source_is_502(self, exc) -> None:
        mapped = to_stream_error(exc, connector="Nextcloud")
        assert mapped.status_code == 502
        assert "Could not reach Nextcloud" in mapped.detail

    def test_aiohttp_disconnect_is_502(self) -> None:
        aiohttp = pytest.importorskip("aiohttp")
        assert to_stream_error(aiohttp.ServerDisconnectedError()).status_code == 502

    def test_httpx_connect_error_is_502(self) -> None:
        httpx = pytest.importorskip("httpx")
        assert to_stream_error(httpx.ConnectError("refused")).status_code == 502


class TestOutOfRangeStatus:
    @pytest.mark.parametrize("status", [600, 999, 10**9, -1, 0])
    def test_garbage_status_does_not_claim_the_source_is_down(self, status) -> None:
        assert map_source_status(status, connector="X").status_code == 500


# A CR/LF inside the header value, built at runtime so the escape cannot be
# mangled by an editor or a tool rewriting line endings.
_CRLF_INJECTION = "5" + chr(13) + chr(10) + "X-Injected: 1"


class TestRetryAfterIsSanitized:
    """The value is upstream-controlled and goes straight onto a response
    header; several sources here are tenant-configured self-hosted endpoints."""

    @staticmethod
    def _exc(headers):
        class _Response:
            def __init__(self, h) -> None:
                self.headers = h

        class _Exc(Exception):
            def __init__(self, h) -> None:
                self.response = _Response(h)
                self.status_code = 429

        return _Exc(headers)

    def test_plain_delta_seconds_is_relayed(self) -> None:
        mapped = to_stream_error(self._exc({"Retry-After": "30"}), connector="X")
        assert mapped.status_code == 429
        assert mapped.headers["Retry-After"] == "30"

    @pytest.mark.parametrize(
        "value",
        [_CRLF_INJECTION, ["5", "10"], "soon", "Wed, 21 Oct 2026 07:28:00 GMT"],
        ids=["crlf", "list", "words", "http-date"],
    )
    def test_non_numeric_values_are_dropped(self, value) -> None:
        mapped = to_stream_error(self._exc({"Retry-After": value}), connector="X")
        assert mapped.status_code == 429
        assert not mapped.headers or "Retry-After" not in mapped.headers


class TestRaiseForStreamFetchOrdering:
    """An error status outranks a `success` flag.

    A GraphQL client sets `success` from the body's `errors` key alone, so a
    401 whose body has no `errors` arrives as success=True with no payload.
    Reporting that as "deleted" is the lie this module exists to stop.
    """

    def test_error_status_beats_a_lying_success_flag(self) -> None:
        with pytest.raises(HTTPException) as caught:
            raise_for_stream_fetch(
                success=True, has_payload=False, connector="Linear", status=401
            )
        assert caught.value.status_code == 409

    def test_genuine_empty_payload_is_still_404(self) -> None:
        with pytest.raises(HTTPException) as caught:
            raise_for_stream_fetch(success=True, has_payload=False, connector="Linear")
        assert caught.value.status_code == 404

    def test_statusless_failure_stays_unclassified(self) -> None:
        with pytest.raises(RuntimeError):
            raise_for_stream_fetch(success=False, has_payload=False, connector="Linear")
