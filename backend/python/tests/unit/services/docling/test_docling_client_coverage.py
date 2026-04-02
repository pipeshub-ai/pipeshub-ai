"""Additional unit tests for DoclingClient to push coverage above 97%.

Targets missing lines/branches:
- Lines 170-171: process_pdf loop fallthrough (all retries exhausted via loop exit)
- Line 260->262: parse_pdf non-503 HTTP error (non-retryable status codes)
- Lines 287-288: parse_pdf loop fallthrough (all retries exhausted)
- Lines 353->355: create_blocks non-503 HTTP error
- Lines 380-381: create_blocks loop fallthrough (all retries exhausted)
"""

import base64
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
# process_pdf - loop fallthrough (all retries exhausted without exception)
# ===========================================================================


class TestProcessPdfLoopFallthrough:
    """Cover lines 170-171: after all retries, the loop exits and returns None."""

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_loop_exit(self, client, small_pdf):
        """When all retries are used up via HTTP errors, the loop ends and returns None.

        The key is to get max_retries=1 so there's only attempt 0.
        On attempt 0, it's the last attempt (attempt < max_retries - 1 is False),
        so it returns None inside the loop. We need max_retries=3 (default) and
        every attempt to succeed in entering retry branch but the last to also return None.
        Actually, the fallthrough on line 170 can only be reached if the loop exits normally
        (i.e., all iterations complete without any return/break/continue). This happens
        when every iteration enters a continue branch - which means HTTP errors with retries.
        But on the last attempt, the code returns None.

        Actually, for process_pdf, every error path either returns None or continues.
        The fallthrough on line 170 is actually unreachable in practice because the last
        attempt always hits a return None. But it's there as a safety net.
        Let's check: for an HTTP error on the last attempt, attempt < self.max_retries - 1
        is False, so it returns None (line 120). So line 170 should not be reachable.

        But wait - there IS a scenario: if max_retries = 0, the loop body never runs at all.
        """
        client.max_retries = 0
        client.retry_delay = 0.001

        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=_make_response(status_code=500, text="Error"))

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.process_pdf("doc.pdf", small_pdf)

        assert result is None
        mock_http.post.assert_not_awaited()


# ===========================================================================
# parse_pdf - non-503 HTTP error + loop fallthrough
# ===========================================================================


class TestParsePdfNon503Error:
    """Cover line 260->262: parse_pdf HTTP error not in [502, 503, 504]."""

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
    """Cover lines 287-288: parse_pdf loop fallthrough."""

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


# ===========================================================================
# create_blocks - non-503 HTTP error + loop fallthrough
# ===========================================================================


class TestCreateBlocksNon503Error:
    """Cover line 353->355: create_blocks HTTP error not in [502, 503, 504]."""

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
    """Cover lines 380-381: create_blocks loop fallthrough."""

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


# ===========================================================================
# process_pdf - retryable HTTP 502 status
# ===========================================================================


class TestProcessPdf502:
    """Cover the 502 status code path for process_pdf."""

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
            result = await client.process_pdf("doc.pdf", small_pdf)

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
            result = await client.process_pdf("doc.pdf", small_pdf)

        assert result is None
        assert mock_http.post.await_count == 2
