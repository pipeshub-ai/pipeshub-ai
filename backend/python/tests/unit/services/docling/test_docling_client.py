"""Tests for DoclingClient.

DoclingClient extends BaseServiceClient, so retry/circuit-breaker/backpressure
mechanics are covered by tests/unit/services/test_base_client.py and
test_base_client_backpressure.py. These tests focus on Docling-specific
behaviour:
  - __init__ (URL, timeout, retry config)
  - _validate_pdf_binary (type / size guards)
  - parse_pdf / parse_pdf_batched: request shape, response translation,
    and failure-to-None mapping
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.exceptions.indexing_exceptions import IndexingError
from app.services.base_client import ServiceCallError, ServiceUnavailableError
from app.services.docling.client import (
    MAX_PDF_BYTES,
    DoclingClient,
    DoclingUnavailableError,
    within_docling_size_limit,
)
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)

# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def client():
    return DoclingClient(service_url="http://test-docling:8081", timeout=60.0)


@pytest.fixture
def small_pdf():
    """A small fake PDF binary."""
    return b"%PDF-1.4 fake content"


def _make_response(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body)


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
        assert c.timeout == 2450.0

    def test_custom_timeout(self):
        c = DoclingClient(service_url="http://x:1", timeout=120.0)
        assert c.timeout == 120.0

    def test_retry_config(self):
        c = DoclingClient(service_url="http://x:1")
        assert c.max_retries == 3
        assert c.retry_delay == 1.0

    def test_service_name(self):
        c = DoclingClient(service_url="http://x:1")
        assert c.service_name == "DoclingService"

    def test_backpressure_coordinator_passthrough(self):
        coordinator = MagicMock()
        c = DoclingClient(service_url="http://x:1", backpressure_coordinator=coordinator)
        assert c._backpressure_coordinator is coordinator

    @patch.dict("os.environ", {"DOCLING_SERVICE_URL": "http://env-url:5000"})
    def test_default_url_from_env(self):
        c = DoclingClient()
        assert c.service_url == "http://env-url:5000"

    @patch.dict("os.environ", {}, clear=True)
    def test_default_url_fallback(self):
        c = DoclingClient()
        assert c.service_url == "http://localhost:8081"


# ===========================================================================
# _validate_pdf_binary
# ===========================================================================


class TestValidatePdfBinary:
    def test_rejects_non_bytes(self, client):
        assert client._validate_pdf_binary("not bytes") is False  # type: ignore

    def test_rejects_too_large(self, client):
        huge = b"x" * (101 * 1024 * 1024)
        assert client._validate_pdf_binary(huge) is False

    def test_accepts_valid_binary(self, client, small_pdf):
        assert client._validate_pdf_binary(small_pdf) is True


# ===========================================================================
# parse_pdf_batched
# ===========================================================================


class TestParsePdfBatched:
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
    async def test_single_batch_skips_concatenate(self, client, small_pdf):
        """A document that fits in one batch is parsed once and not merged."""
        mock_doc = MagicMock()
        with (
            patch("app.services.docling.client.get_pdf_page_count", return_value=3),
            patch.object(
                client, "_post_multipart",
                new=AsyncMock(return_value=_make_response(200, {"success": True, "parse_result": "{}"})),
            ) as mock_post,
            patch("app.services.docling.client.DoclingDocument") as MockDoc,
        ):
            MockDoc.model_validate_json.return_value = mock_doc
            result = await client.parse_pdf_batched("doc.pdf", small_pdf, batch_size=10)

        assert result is mock_doc
        mock_post.assert_awaited_once()
        assert mock_post.call_args.args[0] == "/parse-pdf"
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["data"] == {"record_name": "doc.pdf"}
        assert call_kwargs["files"] == {"file": ("doc.pdf", small_pdf, "application/pdf")}
        MockDoc.concatenate.assert_not_called()

    @pytest.mark.asyncio
    async def test_each_batch_is_parsed_before_concatenate(self, client, small_pdf):
        """Page-range batches are parsed sequentially, then concatenated."""
        call_ranges = []

        async def fake_parse(record_name, pdf_binary, page_range=None):
            call_ranges.append(page_range)
            return f'{{"range": "{page_range}"}}'

        docs = [MagicMock(), MagicMock(), MagicMock()]
        merged = MagicMock()

        with (
            patch("app.services.docling.client.get_pdf_page_count", return_value=5),
            patch.object(client, "parse_pdf", side_effect=fake_parse),
            patch("app.services.docling.client.DoclingDocument") as MockDoc,
        ):
            MockDoc.model_validate_json.side_effect = docs
            MockDoc.concatenate.return_value = merged
            result = await client.parse_pdf_batched("doc.pdf", small_pdf, batch_size=2)

        assert call_ranges == [(1, 2), (3, 4), (5, 5)]
        MockDoc.concatenate.assert_called_once_with(docs)
        assert result is merged
        assert merged.name == "doc.pdf"

    @pytest.mark.asyncio
    async def test_service_error_response_returns_none(self, client, small_pdf):
        with (
            patch("app.services.docling.client.get_pdf_page_count", return_value=1),
            patch.object(
                client, "_post_multipart",
                new=AsyncMock(return_value=_make_response(200, {"success": False, "error": "process fail"})),
            ),
        ):
            result = await client.parse_pdf_batched("doc.pdf", small_pdf)

        assert result is None

    @pytest.mark.asyncio
    async def test_an_unreachable_service_is_raised_so_the_record_is_retried(self, client, small_pdf) -> None:
        """Read as a failed parse, an outage sent every PDF to the per-page OCR fallback."""
        with (
            patch("app.services.docling.client.get_pdf_page_count", return_value=1),
            patch.object(
                client, "_post_multipart",
                new=AsyncMock(side_effect=ServiceUnavailableError("down", service_name="DoclingService")),
            ),
            pytest.raises(DoclingUnavailableError) as raised,
        ):
            await client.parse_pdf_batched("doc.pdf", small_pdf)

        assert isinstance(raised.value, IndexingError)
        assert MessageErrorClassifier.classify_by_exception(raised.value) == MessageErrorType.TRANSIENT


# ===========================================================================
# parse_pdf
# ===========================================================================


class TestParsePdf:
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
        response_body = {"success": True, "parse_result": "serialized-doc"}

        with patch.object(
            client, "_post_multipart", new=AsyncMock(return_value=_make_response(200, response_body)),
        ) as mock_post:
            result = await client.parse_pdf("doc.pdf", small_pdf)

        assert result == "serialized-doc"
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["data"] == {"record_name": "doc.pdf"}

    @pytest.mark.asyncio
    async def test_parse_error_response(self, client, small_pdf):
        response_body = {"success": False, "error": "parse fail"}

        with patch.object(
            client, "_post_multipart", new=AsyncMock(return_value=_make_response(200, response_body)),
        ):
            result = await client.parse_pdf("doc.pdf", small_pdf)

        assert result is None

    @pytest.mark.asyncio
    async def test_a_service_shedding_load_is_raised_so_the_record_is_retried(self, client, small_pdf) -> None:
        from app.services.base_client import ServiceBackpressureError

        with patch.object(
            client, "_post_multipart",
            new=AsyncMock(
                side_effect=ServiceBackpressureError(
                    "backpressured", retry_after=5.0, service_name="DoclingService",
                )
            ),
        ), pytest.raises(DoclingUnavailableError):
            await client.parse_pdf("doc.pdf", small_pdf)

    @pytest.mark.asyncio
    async def test_a_refused_service_token_is_raised_so_the_record_is_retried(self, client, small_pdf) -> None:
        from app.services.base_client import ServiceAuthRefusedError

        with patch.object(
            client, "_post_multipart",
            new=AsyncMock(side_effect=ServiceAuthRefusedError("refused", status_code=401)),
        ), pytest.raises(DoclingUnavailableError):
            await client.parse_pdf("doc.pdf", small_pdf)

    @pytest.mark.parametrize("status", [502, 503, 504])
    @pytest.mark.asyncio
    async def test_a_gateway_or_unavailable_status_is_raised(self, client, small_pdf, status: int) -> None:
        with patch.object(
            client, "_post_multipart",
            new=AsyncMock(side_effect=ServiceCallError("failed", status_code=status)),
        ), pytest.raises(DoclingUnavailableError):
            await client.parse_pdf("doc.pdf", small_pdf)

    @pytest.mark.parametrize("status", [400, 413, 422, 500])
    @pytest.mark.asyncio
    async def test_a_failure_on_this_file_returns_none(self, client, small_pdf, status: int) -> None:
        """Docling answered and could not parse it: the caller reads it another way."""
        with patch.object(
            client, "_post_multipart",
            new=AsyncMock(side_effect=ServiceCallError("failed", status_code=status)),
        ):
            assert await client.parse_pdf("doc.pdf", small_pdf) is None

    def test_the_size_limit_is_inclusive(self) -> None:
        assert within_docling_size_limit(MAX_PDF_BYTES) is True
        assert within_docling_size_limit(MAX_PDF_BYTES + 1) is False

    @pytest.mark.asyncio
    async def test_parse_with_page_range(self, client, small_pdf):
        """page_range should be sent as start_page/end_page form fields."""
        response_body = {"success": True, "parse_result": "partial-doc"}

        with patch.object(
            client, "_post_multipart", new=AsyncMock(return_value=_make_response(200, response_body)),
        ) as mock_post:
            result = await client.parse_pdf("doc.pdf", small_pdf, page_range=(1, 10))

        assert result == "partial-doc"
        form_data = mock_post.call_args.kwargs["data"]
        assert form_data["start_page"] == "1"
        assert form_data["end_page"] == "10"


# ===========================================================================
# health_check (inherited from BaseServiceClient)
# ===========================================================================


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy_service(self, client):
        mock_response = MagicMock(status_code=200)
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch("app.services.base_client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.health_check()

        assert result is True

    @pytest.mark.asyncio
    async def test_unhealthy_service(self, client):
        mock_response = MagicMock(status_code=503)
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch("app.services.base_client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await client.health_check()

        assert result is False
