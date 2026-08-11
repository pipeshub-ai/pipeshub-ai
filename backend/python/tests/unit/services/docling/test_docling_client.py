"""
Tests for DoclingClient:
  - __init__ (URL, timeout, retry config)
  - parse_pdf (multipart POST /parse-pdf, response parsing, retry, size validation)
  - parse_pdf_batched (page counting, batching, concatenation)
  - health_check (GET /health)
  - _check_service_health (internal health check with existing client)
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
        mock_http.post.assert_awaited_once()
        call_args = mock_http.post.call_args
        assert "/parse-pdf" in call_args[0][0]
        assert call_args.kwargs["data"] == {"record_name": "doc.pdf"}
        assert call_args.kwargs["files"] == {
            "file": ("doc.pdf", small_pdf, "application/pdf")
        }

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

        mock_response = _make_response(status_code=503, text="Service Unavailable")
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
    async def test_parse_write_error_retries(self, client, small_pdf):
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

    @pytest.mark.asyncio
    async def test_parse_request_error_retries(self, client, small_pdf):
        client.max_retries = 2
        client.retry_delay = 0.001

        mock_http = MagicMock()
        mock_http.post = AsyncMock(
            side_effect=httpx.RequestError("request failed", request=MagicMock())
        )

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
            result = await client.parse_pdf("doc.pdf", small_pdf)

        assert result is None
        assert mock_http.post.await_count == 2

    @pytest.mark.asyncio
    async def test_parse_with_page_range(self, client, small_pdf):
        """page_range should be sent as start_page/end_page form fields."""
        response_json = {"success": True, "parse_result": "partial-doc"}
        mock_response = _make_response(status_code=200, json_data=response_json)

        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        async def fake_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with patch("app.services.docling.client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("asyncio.to_thread", side_effect=fake_to_thread):
                result = await client.parse_pdf(
                    "doc.pdf", small_pdf, page_range=(1, 10)
                )

        assert result == "partial-doc"
        call_args = mock_http.post.call_args
        form_data = call_args.kwargs["data"]
        assert form_data["start_page"] == "1"
        assert form_data["end_page"] == "10"


# ===========================================================================
# parse_pdf_batched
# ===========================================================================


class TestParsePdfBatched:
    """Test parse_pdf_batched method."""

    @pytest.mark.asyncio
    async def test_invalid_type_returns_none(self, client):
        result = await client.parse_pdf_batched("doc.pdf", "not bytes")  # type: ignore
        assert result is None

    @pytest.mark.asyncio
    async def test_too_large_returns_none(self, client):
        huge = b"x" * (101 * 1024 * 1024)
        result = await client.parse_pdf_batched("doc.pdf", huge)
        assert result is None

    @pytest.mark.asyncio
    async def test_single_batch_uploads_once(self, client, small_pdf):
        """When page_count <= batch_size, a single /parse-pdf call is made."""
        mock_doc = MagicMock()

        with patch(
            "app.services.docling.client.get_pdf_page_count", return_value=5
        ), patch.object(
            client, "parse_pdf", new=AsyncMock(return_value="serialized-doc")
        ) as mock_parse_pdf, patch(
            "app.services.docling.client.DoclingDocument"
        ) as MockDoc:
            MockDoc.model_validate_json.return_value = mock_doc
            result = await client.parse_pdf_batched("doc.pdf", small_pdf, batch_size=10)

        assert result is mock_doc
        mock_parse_pdf.assert_awaited_once_with("doc.pdf", small_pdf)

    @pytest.mark.asyncio
    async def test_single_batch_parse_failure_returns_none(self, client, small_pdf):
        with patch(
            "app.services.docling.client.get_pdf_page_count", return_value=5
        ), patch.object(client, "parse_pdf", new=AsyncMock(return_value=None)):
            result = await client.parse_pdf_batched("doc.pdf", small_pdf, batch_size=10)

        assert result is None

    @pytest.mark.asyncio
    async def test_multi_batch_concatenates(self, client, small_pdf):
        """When page_count > batch_size, multiple page-range calls are concatenated."""
        merged_doc = MagicMock()
        docs = [MagicMock(), MagicMock(), MagicMock()]

        async def fake_parse_pdf(record_name, pdf_binary, page_range=None):
            return f"doc-for-{page_range}"

        with patch(
            "app.services.docling.client.get_pdf_page_count", return_value=25
        ), patch.object(
            client, "parse_pdf", new=AsyncMock(side_effect=fake_parse_pdf)
        ) as mock_parse_pdf, patch(
            "app.services.docling.client.DoclingDocument"
        ) as MockDoc:
            MockDoc.model_validate_json.side_effect = docs
            MockDoc.concatenate.return_value = merged_doc
            result = await client.parse_pdf_batched("doc.pdf", small_pdf, batch_size=10)

        assert result is merged_doc
        assert merged_doc.name == "doc.pdf"
        assert mock_parse_pdf.await_count == 3
        page_ranges = [call.kwargs.get("page_range") for call in mock_parse_pdf.await_args_list]
        assert page_ranges == [(1, 10), (11, 20), (21, 25)]
        MockDoc.concatenate.assert_called_once_with(docs)

    @pytest.mark.asyncio
    async def test_multi_batch_failure_returns_none(self, client, small_pdf):
        """If any batch fails to parse, the whole call returns None."""
        with patch(
            "app.services.docling.client.get_pdf_page_count", return_value=25
        ), patch.object(
            client, "parse_pdf", new=AsyncMock(return_value=None)
        ):
            result = await client.parse_pdf_batched("doc.pdf", small_pdf, batch_size=10)

        assert result is None


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
