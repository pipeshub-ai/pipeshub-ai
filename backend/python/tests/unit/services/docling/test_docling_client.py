"""
Tests for DoclingClient:
  - __init__ (URL, timeout, retry config)
  - process_pdf (base64 encoding, HTTP POST, response parsing, retry, size validation)
  - _parse_blocks_container (dict and string input)
  - parse_pdf (similar to process_pdf but returns parse_result string)
  - create_blocks (POST /create-blocks)
  - health_check (GET /health)
  - _check_service_health (internal health check with existing client)
"""

import asyncio
import base64
import json
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.docling.client import DoclingClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    return DoclingClient(service_url="http://test-docling:8081", timeout=60.0)


@pytest.fixture
def small_pdf():
    """A small fake PDF binary."""
    return b"%PDF-1.4 fake content"


def _make_response(status_code=200, json_data=None, text=""):
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json = lambda: json_data
    return resp


@asynccontextmanager
async def _mock_async_client(mock_client_instance):
    """Helper context manager that yields the mock client instance."""
    yield mock_client_instance


# ===========================================================================
# __init__
# ===========================================================================


class TestInit:
    """Test constructor defaults and overrides."""

    def test_default_url_from_param(self):
        c = DoclingClient(service_url="http://my-service:9000")
        assert c.service_url == "http://my-service:9000"

    def test_trailing_slash_stripped(self):
        c = DoclingClient(service_url="http://my-service:9000/")
        assert c.service_url == "http://my-service:9000"

    def test_default_timeout(self):
        c = DoclingClient(service_url="http://x:1")
        assert c.timeout == 2400.0

    def test_custom_timeout(self):
        c = DoclingClient(service_url="http://x:1", timeout=120.0)
        assert c.timeout == 120.0

    def test_retry_config(self):
        c = DoclingClient(service_url="http://x:1")
        assert c.max_retries == 3
        assert c.retry_delay == 1.0

    @patch.dict("os.environ", {"DOCLING_SERVICE_URL": "http://env-url:5000"})
    def test_default_url_from_env(self):
        c = DoclingClient()
        assert c.service_url == "http://env-url:5000"

    @patch.dict("os.environ", {}, clear=True)
    def test_default_url_fallback(self):
        c = DoclingClient()
        assert c.service_url == "http://localhost:8081"


# ===========================================================================
# process_pdf
# ===========================================================================


class TestProcessPdf:
    """Test process_pdf method."""

    @pytest.mark.asyncio
    async def test_invalid_pdf_type_returns_none(self, client):
        result = await client.process_pdf("doc.pdf", "not bytes")  # type: ignore
        assert result is None

    @pytest.mark.asyncio
    async def test_pdf_too_large_returns_none(self, client):
        huge = b"x" * (101 * 1024 * 1024)  # 101 MB
        result = await client.process_pdf("doc.pdf", huge)
        assert result is None

    @pytest.mark.asyncio
    async def test_successful_processing(self, client, small_pdf):
        """Successful POST returns a parsed BlocksContainer."""
        blocks_data = {"blocks": [], "block_groups": []}
        response_json = {"success": True, "block_containers": blocks_data}
        mock_blocks = MagicMock()

        mock_response = _make_response(status_code=200, json_data=response_json)

        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        async def fake_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("asyncio.to_thread", side_effect=fake_to_thread):
                with patch.object(client, "_parse_blocks_container", return_value=mock_blocks):
                    result = await client.process_pdf("doc.pdf", small_pdf, org_id="org-1")

        assert result is mock_blocks
        mock_http.post.assert_awaited_once()
        call_args = mock_http.post.call_args
        assert "/process-pdf" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_service_returns_error(self, client, small_pdf):
        """When service returns success=False, should return None."""
        response_json = {"success": False, "error": "parse failure"}
        mock_response = _make_response(status_code=200, json_data=response_json)

        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        async def fake_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("asyncio.to_thread", side_effect=fake_to_thread):
                result = await client.process_pdf("doc.pdf", small_pdf)

        assert result is None

    @pytest.mark.asyncio
    async def test_http_error_retries_and_fails(self, client, small_pdf):
        """Non-200 response should retry and eventually return None."""
        client.max_retries = 2
        client.retry_delay = 0.001

        mock_response = _make_response(status_code=503, text="Service Unavailable")

        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.process_pdf("doc.pdf", small_pdf)

        assert result is None
        assert mock_http.post.await_count == 2

    @pytest.mark.asyncio
    async def test_timeout_retries(self, client, small_pdf):
        """TimeoutException should trigger retries."""
        client.max_retries = 2
        client.retry_delay = 0.001

        mock_http = MagicMock()
        mock_http.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.process_pdf("doc.pdf", small_pdf)

        assert result is None
        assert mock_http.post.await_count == 2

    @pytest.mark.asyncio
    async def test_connect_error_retries(self, client, small_pdf):
        """ConnectError should trigger retries."""
        client.max_retries = 2
        client.retry_delay = 0.001

        mock_http = MagicMock()
        mock_http.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.process_pdf("doc.pdf", small_pdf)

        assert result is None
        assert mock_http.post.await_count == 2

    @pytest.mark.asyncio
    async def test_write_error_blocking_retries(self, client, small_pdf):
        """WriteError with 'write could not complete without blocking' should retry."""
        client.max_retries = 2
        client.retry_delay = 0.001

        mock_http = MagicMock()
        mock_http.post = AsyncMock(
            side_effect=httpx.WriteError("write could not complete without blocking")
        )

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.process_pdf("doc.pdf", small_pdf)

        assert result is None
        assert mock_http.post.await_count == 2

    @pytest.mark.asyncio
    async def test_write_error_non_blocking_no_retry(self, client, small_pdf):
        """WriteError without 'blocking' message should not retry."""
        client.max_retries = 2
        client.retry_delay = 0.001

        mock_http = MagicMock()
        mock_http.post = AsyncMock(side_effect=httpx.WriteError("other error"))

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.process_pdf("doc.pdf", small_pdf)

        assert result is None
        assert mock_http.post.await_count == 1

    @pytest.mark.asyncio
    async def test_request_error_retries(self, client, small_pdf):
        """Generic RequestError should retry."""
        client.max_retries = 2
        client.retry_delay = 0.001

        mock_http = MagicMock()
        mock_http.post = AsyncMock(
            side_effect=httpx.RequestError("request failed", request=MagicMock())
        )

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.process_pdf("doc.pdf", small_pdf)

        assert result is None
        assert mock_http.post.await_count == 2

    @pytest.mark.asyncio
    async def test_unexpected_error_retries(self, client, small_pdf):
        """Unexpected exceptions should also retry."""
        client.max_retries = 2
        client.retry_delay = 0.001

        mock_http = MagicMock()
        mock_http.post = AsyncMock(side_effect=RuntimeError("unexpected"))

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.process_pdf("doc.pdf", small_pdf)

        assert result is None
        assert mock_http.post.await_count == 2

    @pytest.mark.asyncio
    async def test_base64_encoding_in_payload(self, client, small_pdf):
        """Verify that pdf_binary is base64-encoded in the request payload."""
        response_json = {"success": False, "error": "test"}
        mock_response = _make_response(status_code=200, json_data=response_json)

        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        async def fake_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("asyncio.to_thread", side_effect=fake_to_thread):
                await client.process_pdf("doc.pdf", small_pdf, org_id="org-1")

        # Check the payload sent
        call_kwargs = mock_http.post.call_args.kwargs
        payload = call_kwargs["json"]
        assert payload["record_name"] == "doc.pdf"
        assert payload["org_id"] == "org-1"
        # Verify it's valid base64
        decoded = base64.b64decode(payload["pdf_binary"])
        assert decoded == small_pdf

    @pytest.mark.asyncio
    async def test_non_retryable_http_error(self, client, small_pdf):
        """HTTP 400 should not match 502/503/504 branch but still retries."""
        client.max_retries = 2
        client.retry_delay = 0.001

        mock_response = _make_response(status_code=400, text="Bad Request")

        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.process_pdf("doc.pdf", small_pdf)

        assert result is None


# ===========================================================================
# _parse_blocks_container
# ===========================================================================


class TestParseBlocksContainer:
    """Test _parse_blocks_container method."""

    def test_dict_input(self, client):
        with patch("app.services.docling.client.BlocksContainer") as MockBC:
            mock_instance = MagicMock()
            MockBC.return_value = mock_instance

            result = client._parse_blocks_container({"blocks": []})

            MockBC.assert_called_once_with(blocks=[])
            assert result is mock_instance

    def test_string_input(self, client):
        with patch("app.services.docling.client.BlocksContainer") as MockBC:
            mock_instance = MagicMock()
            MockBC.return_value = mock_instance

            result = client._parse_blocks_container('{"blocks": []}')

            MockBC.assert_called_once_with(blocks=[])
            assert result is mock_instance

    def test_invalid_string_raises(self, client):
        with pytest.raises(Exception):
            client._parse_blocks_container("not-json")

    def test_invalid_data_raises(self, client):
        with patch("app.services.docling.client.BlocksContainer", side_effect=TypeError("bad")):
            with pytest.raises(TypeError):
                client._parse_blocks_container({"invalid": True})


# ===========================================================================
# parse_pdf
# ===========================================================================


class TestParsePdf:
    """Test parse_pdf method."""

    @pytest.mark.asyncio
    async def test_invalid_type_returns_none(self, client):
        result = await client.parse_pdf("doc.pdf", "not bytes")  # type: ignore
        assert result is None

    @pytest.mark.asyncio
    async def test_too_large_returns_none(self, client):
        huge = b"x" * (101 * 1024 * 1024)
        result = await client.parse_pdf("doc.pdf", huge)
        assert result is None

    @pytest.mark.asyncio
    async def test_successful_parse(self, client, small_pdf):
        response_json = {"success": True, "parse_result": "serialized-doc"}
        mock_response = _make_response(status_code=200, json_data=response_json)

        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        async def fake_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("asyncio.to_thread", side_effect=fake_to_thread):
                result = await client.parse_pdf("doc.pdf", small_pdf)

        assert result == "serialized-doc"

    @pytest.mark.asyncio
    async def test_parse_error_response(self, client, small_pdf):
        response_json = {"success": False, "error": "parse fail"}
        mock_response = _make_response(status_code=200, json_data=response_json)

        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        async def fake_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("asyncio.to_thread", side_effect=fake_to_thread):
                result = await client.parse_pdf("doc.pdf", small_pdf)

        assert result is None

    @pytest.mark.asyncio
    async def test_parse_http_error_retries(self, client, small_pdf):
        client.max_retries = 2
        client.retry_delay = 0.001

        mock_response = _make_response(status_code=502, text="Bad Gateway")
        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.parse_pdf("doc.pdf", small_pdf)

        assert result is None
        assert mock_http.post.await_count == 2

    @pytest.mark.asyncio
    async def test_parse_timeout_retries(self, client, small_pdf):
        client.max_retries = 2
        client.retry_delay = 0.001

        mock_http = MagicMock()
        mock_http.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.parse_pdf("doc.pdf", small_pdf)

        assert result is None
        assert mock_http.post.await_count == 2

    @pytest.mark.asyncio
    async def test_parse_connect_error_retries(self, client, small_pdf):
        client.max_retries = 2
        client.retry_delay = 0.001

        mock_http = MagicMock()
        mock_http.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.parse_pdf("doc.pdf", small_pdf)

        assert result is None
        assert mock_http.post.await_count == 2

    @pytest.mark.asyncio
    async def test_parse_unexpected_error_retries(self, client, small_pdf):
        client.max_retries = 2
        client.retry_delay = 0.001

        mock_http = MagicMock()
        mock_http.post = AsyncMock(side_effect=RuntimeError("unexpected"))

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.parse_pdf("doc.pdf", small_pdf)

        assert result is None
        assert mock_http.post.await_count == 2


# ===========================================================================
# create_blocks
# ===========================================================================


class TestCreateBlocks:
    """Test create_blocks method."""

    @pytest.mark.asyncio
    async def test_successful_create(self, client):
        blocks_data = {"blocks": [], "block_groups": []}
        response_json = {"success": True, "block_containers": blocks_data}
        mock_blocks = MagicMock()
        mock_response = _make_response(status_code=200, json_data=response_json)

        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        async def fake_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("asyncio.to_thread", side_effect=fake_to_thread):
                with patch.object(client, "_parse_blocks_container", return_value=mock_blocks):
                    result = await client.create_blocks("serialized-parse-result", page_number=1)

        assert result is mock_blocks
        call_kwargs = mock_http.post.call_args.kwargs
        payload = call_kwargs["json"]
        assert payload["parse_result"] == "serialized-parse-result"
        assert payload["page_number"] == 1

    @pytest.mark.asyncio
    async def test_create_blocks_error_response(self, client):
        response_json = {"success": False, "error": "create fail"}
        mock_response = _make_response(status_code=200, json_data=response_json)

        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        async def fake_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("asyncio.to_thread", side_effect=fake_to_thread):
                result = await client.create_blocks("parse-result")

        assert result is None

    @pytest.mark.asyncio
    async def test_create_blocks_http_error_retries(self, client):
        client.max_retries = 2
        client.retry_delay = 0.001

        mock_response = _make_response(status_code=504, text="Gateway Timeout")
        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.create_blocks("parse-result")

        assert result is None
        assert mock_http.post.await_count == 2

    @pytest.mark.asyncio
    async def test_create_blocks_timeout_retries(self, client):
        client.max_retries = 2
        client.retry_delay = 0.001

        mock_http = MagicMock()
        mock_http.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.create_blocks("parse-result")

        assert result is None
        assert mock_http.post.await_count == 2

    @pytest.mark.asyncio
    async def test_create_blocks_connect_error_retries(self, client):
        client.max_retries = 2
        client.retry_delay = 0.001

        mock_http = MagicMock()
        mock_http.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.create_blocks("parse-result")

        assert result is None
        assert mock_http.post.await_count == 2

    @pytest.mark.asyncio
    async def test_create_blocks_unexpected_error_retries(self, client):
        client.max_retries = 2
        client.retry_delay = 0.001

        mock_http = MagicMock()
        mock_http.post = AsyncMock(side_effect=RuntimeError("unexpected"))

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.create_blocks("parse-result")

        assert result is None
        assert mock_http.post.await_count == 2


# ===========================================================================
# health_check / _check_service_health
# ===========================================================================


class TestHealthCheck:
    """Test health check methods."""

    @pytest.mark.asyncio
    async def test_healthy_service(self, client):
        mock_response = _make_response(status_code=200)

        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.health_check()

        assert result is True
        mock_http.get.assert_awaited_once()
        call_args = mock_http.get.call_args
        assert "/health" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_unhealthy_service(self, client):
        mock_response = _make_response(status_code=503)

        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.health_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_health_connect_error(self, client):
        mock_http = MagicMock()
        mock_http.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.health_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_health_unexpected_error(self, client):
        mock_http = MagicMock()
        mock_http.get = AsyncMock(side_effect=RuntimeError("unexpected"))

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.health_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_outer_exception(self, client):
        """Exception during AsyncClient creation should return False."""
        with patch(
            "app.services.docling.client.httpx.AsyncClient",
            side_effect=Exception("client creation failed"),
        ):
            result = await client.health_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_check_service_health_directly(self, client):
        """Test the internal _check_service_health with an existing client."""
        mock_response = _make_response(status_code=200)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await client._check_service_health(mock_client)
        assert result is True

    @pytest.mark.asyncio
    async def test_check_service_health_connect_error(self, client):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("down"))

        result = await client._check_service_health(mock_client)
        assert result is False

    @pytest.mark.asyncio
    async def test_check_service_health_generic_error(self, client):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("generic"))

        result = await client._check_service_health(mock_client)
        assert result is False
