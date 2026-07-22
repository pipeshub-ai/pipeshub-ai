"""Tests for DoclingClient.

DoclingClient extends BaseServiceClient, which owns retry/circuit-breaker/
backoff behaviour (see tests/unit/services/test_base_client.py). These tests
focus on DoclingClient-specific concerns: payload construction, response
parsing, and raising typed errors instead of returning None.
"""
from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.base_client import ServiceCallError, ServiceUnavailableError
from app.services.docling.client import DoclingClient, DoclingClientError


@pytest.fixture
def client() -> DoclingClient:
    return DoclingClient(service_url="http://test-docling:8081", timeout=60.0)


@pytest.fixture
def small_pdf() -> bytes:
    return b"%PDF-1.4 fake content"


def _make_response(status_code: int = 200, json_data: dict | None = None) -> httpx.Response:
    content = json.dumps(json_data or {}).encode()
    return httpx.Response(status_code, content=content)


def _patch_client(client: DoclingClient, request_side_effect) -> AsyncMock:
    mock_httpx = AsyncMock()
    mock_httpx.is_closed = False
    if callable(request_side_effect) and not isinstance(request_side_effect, AsyncMock):
        mock_httpx.request = request_side_effect
    else:
        mock_httpx.request = AsyncMock(side_effect=request_side_effect)
    client._client = mock_httpx
    return mock_httpx


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_default_url_from_param(self) -> None:
        c = DoclingClient(service_url="http://my-service:9000")
        assert c.service_url == "http://my-service:9000"

    def test_trailing_slash_stripped(self) -> None:
        c = DoclingClient(service_url="http://my-service:9000/")
        assert c.service_url == "http://my-service:9000"

    def test_default_timeout(self) -> None:
        c = DoclingClient(service_url="http://x:1")
        assert c.timeout == 1200.0

    def test_custom_timeout(self) -> None:
        c = DoclingClient(service_url="http://x:1", timeout=120.0)
        assert c.timeout == 120.0

    def test_retry_config(self) -> None:
        c = DoclingClient(service_url="http://x:1")
        assert c.max_retries == 3
        assert c.retry_delay == 1.0

    @patch.dict("os.environ", {"DOCLING_SERVICE_URL": "http://env-url:5000"})
    def test_default_url_from_env(self) -> None:
        c = DoclingClient()
        assert c.service_url == "http://env-url:5000"

    @patch.dict("os.environ", {}, clear=True)
    def test_default_url_fallback(self) -> None:
        c = DoclingClient()
        assert c.service_url == "http://localhost:8081"


# ---------------------------------------------------------------------------
# process_pdf
# ---------------------------------------------------------------------------


class TestProcessPdf:
    @pytest.mark.asyncio
    async def test_invalid_pdf_type_raises(self, client: DoclingClient) -> None:
        with pytest.raises(DoclingClientError, match="expected bytes"):
            await client.process_pdf("doc.pdf", "not bytes")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_pdf_too_large_raises(self, client: DoclingClient) -> None:
        huge = b"x" * (101 * 1024 * 1024)
        with pytest.raises(DoclingClientError, match="too large"):
            await client.process_pdf("doc.pdf", huge)

    @pytest.mark.asyncio
    async def test_successful_processing(self, client: DoclingClient, small_pdf: bytes) -> None:
        blocks_data = {"blocks": [], "block_groups": []}
        response_json = {"success": True, "block_containers": blocks_data}
        _patch_client(client, AsyncMock(return_value=_make_response(200, response_json)))

        result = await client.process_pdf("doc.pdf", small_pdf, org_id="org-1")

        assert result.blocks == []

    @pytest.mark.asyncio
    async def test_service_returns_error_raises(self, client: DoclingClient, small_pdf: bytes) -> None:
        response_json = {"success": False, "error": "parse failure"}
        _patch_client(client, AsyncMock(return_value=_make_response(200, response_json)))

        with pytest.raises(DoclingClientError, match="parse failure"):
            await client.process_pdf("doc.pdf", small_pdf)

    @pytest.mark.asyncio
    async def test_persistent_5xx_raises_service_call_error(
        self, client: DoclingClient, small_pdf: bytes
    ) -> None:
        client.max_retries = 2
        client.retry_delay = 0.0
        _patch_client(client, AsyncMock(return_value=_make_response(503)))

        with pytest.raises(ServiceCallError):
            await client.process_pdf("doc.pdf", small_pdf)

    @pytest.mark.asyncio
    async def test_connect_error_raises_service_unavailable(
        self, client: DoclingClient, small_pdf: bytes
    ) -> None:
        client.max_retries = 2
        client.retry_delay = 0.0
        _patch_client(client, httpx.ConnectError("refused"))

        with pytest.raises(ServiceUnavailableError):
            await client.process_pdf("doc.pdf", small_pdf)

    @pytest.mark.asyncio
    async def test_base64_encoding_in_payload(self, client: DoclingClient, small_pdf: bytes) -> None:
        mock_httpx = _patch_client(
            client, AsyncMock(return_value=_make_response(200, {"success": False, "error": "x"}))
        )

        with pytest.raises(DoclingClientError):
            await client.process_pdf("doc.pdf", small_pdf, org_id="org-1")

        call_kwargs = mock_httpx.request.call_args.kwargs
        payload = json.loads(call_kwargs["content"])
        assert payload["record_name"] == "doc.pdf"
        assert payload["org_id"] == "org-1"
        assert base64.b64decode(payload["pdf_binary"]) == small_pdf


# ---------------------------------------------------------------------------
# _parse_blocks_container
# ---------------------------------------------------------------------------


class TestParseBlocksContainer:
    def test_dict_input(self, client: DoclingClient) -> None:
        result = client._parse_blocks_container({"blocks": [], "block_groups": []})
        assert result.blocks == []

    def test_string_input(self, client: DoclingClient) -> None:
        result = client._parse_blocks_container(json.dumps({"blocks": [], "block_groups": []}))
        assert result.blocks == []

    def test_invalid_string_raises(self, client: DoclingClient) -> None:
        with pytest.raises(Exception):  # noqa: B017 - json.JSONDecodeError
            client._parse_blocks_container("not-json")


# ---------------------------------------------------------------------------
# parse_pdf
# ---------------------------------------------------------------------------


class TestParsePdf:
    @pytest.mark.asyncio
    async def test_invalid_type_raises(self, client: DoclingClient) -> None:
        with pytest.raises(DoclingClientError):
            await client.parse_pdf("doc.pdf", "not bytes")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_too_large_raises(self, client: DoclingClient) -> None:
        huge = b"x" * (101 * 1024 * 1024)
        with pytest.raises(DoclingClientError):
            await client.parse_pdf("doc.pdf", huge)

    @pytest.mark.asyncio
    async def test_successful_parse(self, client: DoclingClient, small_pdf: bytes) -> None:
        response_json = {"success": True, "parse_result": "serialized-doc"}
        _patch_client(client, AsyncMock(return_value=_make_response(200, response_json)))

        result = await client.parse_pdf("doc.pdf", small_pdf)

        assert result == "serialized-doc"

    @pytest.mark.asyncio
    async def test_parse_error_response_raises(self, client: DoclingClient, small_pdf: bytes) -> None:
        response_json = {"success": False, "error": "parse fail"}
        _patch_client(client, AsyncMock(return_value=_make_response(200, response_json)))

        with pytest.raises(DoclingClientError, match="parse fail"):
            await client.parse_pdf("doc.pdf", small_pdf)

    @pytest.mark.asyncio
    async def test_remote_protocol_error_retried_then_succeeds(
        self, client: DoclingClient, small_pdf: bytes
    ) -> None:
        """Regression test: RemoteProtocolError must be retried, not fail immediately."""
        client.max_retries = 3
        client.retry_delay = 0.0
        call_count = 0

        async def _fake_request(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
            return _make_response(200, {"success": True, "parse_result": "ok"})

        _patch_client(client, _fake_request)

        result = await client.parse_pdf("doc.pdf", small_pdf)

        assert result == "ok"
        assert call_count == 2


# ---------------------------------------------------------------------------
# create_blocks
# ---------------------------------------------------------------------------


class TestCreateBlocks:
    @pytest.mark.asyncio
    async def test_successful_create(self, client: DoclingClient) -> None:
        blocks_data = {"blocks": [], "block_groups": []}
        response_json = {"success": True, "block_containers": blocks_data}
        mock_httpx = _patch_client(client, AsyncMock(return_value=_make_response(200, response_json)))

        result = await client.create_blocks("serialized-parse-result", page_number=1)

        assert result.blocks == []
        call_kwargs = mock_httpx.request.call_args.kwargs
        payload = json.loads(call_kwargs["content"])
        assert payload["parse_result"] == "serialized-parse-result"
        assert payload["page_number"] == 1

    @pytest.mark.asyncio
    async def test_create_blocks_error_response_raises(self, client: DoclingClient) -> None:
        response_json = {"success": False, "error": "create fail"}
        _patch_client(client, AsyncMock(return_value=_make_response(200, response_json)))

        with pytest.raises(DoclingClientError, match="create fail"):
            await client.create_blocks("parse-result")


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy_service(self, client: DoclingClient) -> None:
        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_instance.get = AsyncMock(return_value=httpx.Response(200))
            mock_cls.return_value = mock_instance

            result = await client.health_check()

        assert result is True

    @pytest.mark.asyncio
    async def test_unhealthy_service(self, client: DoclingClient) -> None:
        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_instance.get = AsyncMock(return_value=httpx.Response(503))
            mock_cls.return_value = mock_instance

            result = await client.health_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_health_connect_error(self, client: DoclingClient) -> None:
        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_instance.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_cls.return_value = mock_instance

            result = await client.health_check()

        assert result is False
