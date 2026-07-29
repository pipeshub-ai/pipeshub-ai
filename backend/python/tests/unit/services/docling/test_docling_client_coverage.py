"""Additional unit tests for DoclingClient to push coverage above 97%.

Targets missing lines/branches:
- parse_pdf non-503 HTTP error (non-retryable status codes)
- parse_pdf loop fallthrough (all retries exhausted)
- parse_pdf retryable HTTP 502/504 status
- create_blocks non-503 HTTP error
- create_blocks loop fallthrough (all retries exhausted)
"""

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
    return b"%PDF-1.4 fake content"


def _make_response(status_code=200, json_data=None, text=""):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json = lambda: json_data
    return resp


# ===========================================================================
# parse_pdf - non-503 HTTP error + loop fallthrough
# ===========================================================================


class TestParsePdfNon503Error:
    """Cover parse_pdf HTTP error not in [502, 503, 504]."""

    @pytest.mark.asyncio
    async def test_non_retryable_http_error(self, client, small_pdf):
        """HTTP 400 should not match 502/503/504 warning branch but still retries."""
        client.max_retries = 2
        client.retry_delay = 0.001

        mock_response = _make_response(status_code=400, text="Bad Request")
        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.parse_pdf("doc.pdf", small_pdf)

        assert result is None
        assert mock_http.post.await_count == 2

    @pytest.mark.asyncio
    async def test_parse_pdf_write_error_retries(self, client, small_pdf):
        """WriteError in parse_pdf should retry."""
        client.max_retries = 2
        client.retry_delay = 0.001

        mock_http = MagicMock()
        mock_http.post = AsyncMock(
            side_effect=httpx.WriteError("write could not complete without blocking")
        )

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.parse_pdf("doc.pdf", small_pdf)

        assert result is None
        assert mock_http.post.await_count == 2


class TestParsePdfLoopFallthrough:
    """Cover parse_pdf loop fallthrough."""

    @pytest.mark.asyncio
    async def test_parse_pdf_zero_retries(self, client, small_pdf):
        """When max_retries=0, loop doesn't run and returns None."""
        client.max_retries = 0

        mock_http = MagicMock()
        mock_http.post = AsyncMock()

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.parse_pdf("doc.pdf", small_pdf)

        assert result is None
        mock_http.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_loop_exit(self, client, small_pdf):
        """When max_retries=0, the loop body never runs and fallthrough returns None."""
        client.max_retries = 0
        client.retry_delay = 0.001

        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=_make_response(status_code=500, text="Error"))

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.parse_pdf("doc.pdf", small_pdf)

        assert result is None
        mock_http.post.assert_not_awaited()


# ===========================================================================
# parse_pdf - retryable HTTP 502/504 status
# ===========================================================================


class TestParsePdf502:
    """Cover the 502/504 status code paths for parse_pdf."""

    @pytest.mark.asyncio
    async def test_http_502_retries_and_logs_warning(self, client, small_pdf):
        """HTTP 502 triggers both the warning and the retry."""
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
    async def test_http_504_retries_and_logs_warning(self, client, small_pdf):
        """HTTP 504 triggers both the warning and the retry."""
        client.max_retries = 2
        client.retry_delay = 0.001

        mock_response = _make_response(status_code=504, text="Gateway Timeout")
        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.parse_pdf("doc.pdf", small_pdf)

        assert result is None
        assert mock_http.post.await_count == 2


# ===========================================================================
# create_blocks - non-503 HTTP error + loop fallthrough
# ===========================================================================


class TestCreateBlocksNon503Error:
    """Cover create_blocks HTTP error not in [502, 503, 504]."""

    @pytest.mark.asyncio
    async def test_non_retryable_http_error(self, client):
        """HTTP 400 should not match 502/503/504 warning but still retries."""
        client.max_retries = 2
        client.retry_delay = 0.001

        mock_response = _make_response(status_code=400, text="Bad Request")
        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.create_blocks("parse-result")

        assert result is None
        assert mock_http.post.await_count == 2

    @pytest.mark.asyncio
    async def test_create_blocks_write_error_retries(self, client):
        """WriteError in create_blocks should retry."""
        client.max_retries = 2
        client.retry_delay = 0.001

        mock_http = MagicMock()
        mock_http.post = AsyncMock(
            side_effect=httpx.WriteError("write could not complete without blocking")
        )

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.create_blocks("parse-result")

        assert result is None
        assert mock_http.post.await_count == 2


class TestCreateBlocksLoopFallthrough:
    """Cover create_blocks loop fallthrough."""

    @pytest.mark.asyncio
    async def test_create_blocks_zero_retries(self, client):
        """When max_retries=0, loop doesn't run and returns None."""
        client.max_retries = 0

        mock_http = MagicMock()
        mock_http.post = AsyncMock()

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.create_blocks("parse-result")

        assert result is None
        mock_http.post.assert_not_awaited()
