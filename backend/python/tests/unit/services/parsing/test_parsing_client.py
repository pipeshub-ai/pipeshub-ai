"""Tests for ParsingClient HTTP client."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import jwt
import pytest

from app.config.constants.service import TokenScopes
from app.models.blocks import BlocksContainer
from app.services.base_client import (
    ServiceBackpressureError,
    ServiceCallError,
    ServiceUnavailableError,
)
from app.services.parsing.client import ParsingClient, ParsingClientError
from app.services.parsing.interface import ParseErrorCode, ParseResult, ParserProvider


def _bc_dict() -> dict:
    return {"blocks": [], "block_groups": []}


def _success_response() -> dict:
    return {
        "success": True,
        "block_container": _bc_dict(),
        "provider_used": "default",
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# Mock transport helper
# ---------------------------------------------------------------------------


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = iter(responses)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return next(self._responses)


def _make_response(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body)


SCOPED_SECRET = "scoped-secret-for-tests"


class _SecretsConfigService:
    async def get_config(self, key, **kwargs):
        return {"scopedJwtSecret": SCOPED_SECRET}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_success() -> None:
    client = ParsingClient(service_url="http://fake-parsing:8092", max_retries=1)

    with patch.object(
        client,
        "_post_multipart",
        new=AsyncMock(return_value=_make_response(200, _success_response())),
    ):
        result = await client.parse(
            file_content=b"data",
            record_name="test.csv",
            mime_type="text/csv",
            extension="csv",
        )

    assert isinstance(result, ParseResult)
    assert result.provider_used == ParserProvider.DEFAULT


@pytest.mark.asyncio
async def test_parse_with_raw_document_builds_blocks_locally() -> None:
    """Docling-backed providers defer block construction; the client must finish it."""
    client = ParsingClient(service_url="http://fake-parsing:8092", max_retries=1)

    raw_doc_body = {
        "success": True,
        "block_container": None,
        "raw_document": '{"schema_name": "DoclingDocument"}',
        "provider_used": "docling",
        "metadata": {"record_name": "test.pdf"},
    }

    mock_block_container = BlocksContainer(**_bc_dict())
    mock_processor = MagicMock()
    mock_processor.create_blocks = AsyncMock(return_value=mock_block_container)

    with patch.object(
        client,
        "_post_multipart",
        new=AsyncMock(return_value=_make_response(200, raw_doc_body)),
    ), patch.object(
        client, "_get_docling_processor", return_value=mock_processor
    ), patch(
        "app.services.parsing.client.DoclingDocument"
    ) as MockDoc:
        MockDoc.model_validate_json.return_value = MagicMock()
        result = await client.parse(
            file_content=b"%PDF-1.4",
            record_name="test.pdf",
            mime_type="application/pdf",
            provider=ParserProvider.DOCLING,
        )

    assert result.block_container is mock_block_container
    assert result.provider_used == ParserProvider.DOCLING
    mock_processor.create_blocks.assert_awaited_once()


@pytest.mark.asyncio
async def test_parse_with_explicit_provider() -> None:
    client = ParsingClient(service_url="http://fake-parsing:8092", max_retries=1)

    success_body = {**_success_response(), "provider_used": "docling"}

    with patch.object(
        client,
        "_post_multipart",
        new=AsyncMock(return_value=_make_response(200, success_body)),
    ):
        result = await client.parse(
            file_content=b"data",
            record_name="test.pdf",
            mime_type="application/pdf",
            provider=ParserProvider.DOCLING,
        )

    assert result.provider_used == ParserProvider.DOCLING


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_raises_parsing_client_error_on_service_failure() -> None:
    client = ParsingClient(service_url="http://fake-parsing:8092", max_retries=1)

    error_body = {
        "success": False,
        "error": {
            "code": "PARSE_FAILED",
            "message": "Docling crashed",
            "details": {},
        },
    }

    with patch.object(
        client,
        "_post_multipart",
        new=AsyncMock(return_value=_make_response(500, error_body)),
    ):
        with pytest.raises(ParsingClientError) as exc_info:
            await client.parse(file_content=b"data", record_name="test.pdf")

    assert exc_info.value.code == ParseErrorCode.PARSE_FAILED


@pytest.mark.asyncio
async def test_parse_surfaces_exhausted_backpressure_as_retryable_error() -> None:
    """When base_client exhausts its backpressure budget, parse() must
    surface a retryable ParsingClientError(PARSE_BACKPRESSURE), not let the
    raw ServiceBackpressureError propagate unclassified."""
    client = ParsingClient(service_url="http://fake-parsing:8092", max_retries=1)

    with patch.object(
        client,
        "_post_multipart",
        new=AsyncMock(
            side_effect=ServiceBackpressureError(
                "ParsingService parse is backpressured",
                retry_after=5.0,
                service_name="ParsingService",
            )
        ),
    ):
        with pytest.raises(ParsingClientError) as exc_info:
            await client.parse(file_content=b"data", record_name="test.pdf")

    assert exc_info.value.code == ParseErrorCode.PARSE_BACKPRESSURE
    assert exc_info.value.details["retry_after"] == 5.0


@pytest.mark.asyncio
async def test_parse_raises_service_unavailable_on_connection_error() -> None:
    client = ParsingClient(service_url="http://fake-parsing:8092", max_retries=1, retry_delay=0.0)

    with patch.object(
        client,
        "_post_multipart",
        new=AsyncMock(side_effect=ServiceUnavailableError("Connection refused", service_name="ParsingService")),
    ):
        with pytest.raises(ServiceUnavailableError):
            await client.parse(file_content=b"data", record_name="test.pdf")


# ---------------------------------------------------------------------------
# list_providers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_providers_success() -> None:
    client = ParsingClient(service_url="http://fake-parsing:8092", max_retries=1)
    providers_body = {"pdf": ["docling", "default"], "csv": ["default"]}

    with patch.object(
        client,
        "_get_json",
        new=AsyncMock(return_value=_make_response(200, providers_body)),
    ):
        result = await client.list_providers()

    assert "pdf" in result
    assert "default" in result["csv"]


@pytest.mark.asyncio
async def test_health_check_returns_true_on_ok() -> None:
    client = ParsingClient(service_url="http://fake-parsing:8092", max_retries=1)

    with patch("httpx.AsyncClient") as mock_cls:
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        mock_instance.get = AsyncMock(return_value=httpx.Response(200, json={"status": "ok"}))
        mock_cls.return_value = mock_instance

        result = await client.health_check()

    assert result is True


@pytest.mark.asyncio
async def test_an_unparseable_document_is_not_retried_and_leaves_the_breaker_closed() -> None:
    """A 422 PARSE_FAILED is a fact about the document. Reported as a 500 it
    was retried, counted against the circuit breaker, and five in a row
    failed every other record fast for the breaker's cooldown."""
    client = ParsingClient(
        service_url="http://fake-parsing:8092", max_retries=3, retry_delay=0.0,
        config_service=_SecretsConfigService(),
    )
    calls = 0

    async def _request(method, url, **kwargs) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _make_response(
            422,
            {"success": False, "error": {"code": "PARSE_FAILED", "message": "cannot parse", "details": {}}},
        )

    fake_httpx = AsyncMock()
    fake_httpx.__aenter__ = AsyncMock(return_value=fake_httpx)
    fake_httpx.__aexit__ = AsyncMock(return_value=False)
    fake_httpx.request = _request

    with patch.object(client, "_make_client", return_value=fake_httpx):
        with pytest.raises(ParsingClientError) as exc_info:
            await client.parse(file_content=b"data", record_name="broken.yaml")

    assert exc_info.value.code == ParseErrorCode.PARSE_FAILED
    assert calls == 1, "a document error must not be retried"
    assert not client.circuit_breaker.is_open
    assert client.circuit_breaker._consecutive_failures == 0


@pytest.mark.asyncio
async def test_a_password_required_answer_becomes_the_typed_error_and_is_not_retried() -> None:
    from app.exceptions.indexing_exceptions import PasswordProtectedError
    from app.services.messaging.error_classifier import (
        MessageErrorClassifier,
        MessageErrorType,
    )
    from app.utils.user_errors import (
        PASSWORD_PROTECTED,
        is_password_protected,
        to_user_reason,
    )

    client = ParsingClient(
        service_url="http://fake-parsing:8092", max_retries=3, retry_delay=0.0,
        config_service=_SecretsConfigService(),
    )
    calls = 0

    async def _request(method, url, **kwargs) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _make_response(
            422,
            {"success": False, "error": {
                "code": "PASSWORD_REQUIRED", "message": "needs a password", "details": {"format": "pdf"},
            }},
        )

    fake_httpx = AsyncMock()
    fake_httpx.__aenter__ = AsyncMock(return_value=fake_httpx)
    fake_httpx.__aexit__ = AsyncMock(return_value=False)
    fake_httpx.request = _request

    with patch.object(client, "_make_client", return_value=fake_httpx):
        with pytest.raises(PasswordProtectedError) as exc_info:
            await client.parse(file_content=b"data", record_name="locked.pdf")

    assert calls == 1
    assert not client.circuit_breaker.is_open
    assert client.circuit_breaker._consecutive_failures == 0
    assert is_password_protected(exc_info.value)
    assert to_user_reason(exc_info.value) == PASSWORD_PROTECTED
    assert MessageErrorClassifier.classify_by_exception(exc_info.value) == MessageErrorType.TERMINAL


@pytest.mark.asyncio
async def test_no_model_that_reads_images_is_not_retried_and_tells_what_to_fix() -> None:
    """The parse route's answer when OCR has no multimodal model: one call,
    terminal, and the record stores the reason the route sent."""
    from app.config.constants.arangodb import OriginTypes, RecordTypes
    from app.services.messaging.error_classifier import (
        MessageErrorClassifier,
        MessageErrorType,
    )
    from app.utils.user_errors import SCANNED_DOCUMENT_NEEDS_OCR, to_user_reason

    client = ParsingClient(
        service_url="http://fake-parsing:8092", max_retries=3, retry_delay=0.0,
        config_service=_SecretsConfigService(),
    )
    calls = 0

    async def _request(method, url, **kwargs) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _make_response(
            422,
            {"success": False, "error": {
                "code": "PARSE_FAILED",
                "message": SCANNED_DOCUMENT_NEEDS_OCR,
                "details": {"required": "multimodal LLM"},
            }},
        )

    fake_httpx = AsyncMock()
    fake_httpx.__aenter__ = AsyncMock(return_value=fake_httpx)
    fake_httpx.__aexit__ = AsyncMock(return_value=False)
    fake_httpx.request = _request

    with patch.object(client, "_make_client", return_value=fake_httpx):
        with pytest.raises(ParsingClientError) as exc_info:
            await client.parse(file_content=b"data", record_name="scan.pdf")

    assert calls == 1
    assert not client.circuit_breaker.is_open
    assert client.circuit_breaker._consecutive_failures == 0
    # How the record handler sees it (record.py wraps unknown errors this way).
    try:
        raise Exception(str(exc_info.value)) from exc_info.value
    except Exception as wrapped:
        handled = wrapped
    assert MessageErrorClassifier.classify_by_exception(handled) == MessageErrorType.TERMINAL
    assert to_user_reason(handled) == SCANNED_DOCUMENT_NEEDS_OCR
    uploaded = {"origin": OriginTypes.UPLOAD.value, "recordType": RecordTypes.FILE.value}
    synced = {"origin": OriginTypes.CONNECTOR.value, "recordType": RecordTypes.FILE.value}
    assert to_user_reason(handled, record=uploaded) == SCANNED_DOCUMENT_NEEDS_OCR
    assert to_user_reason(handled, record=synced, source="Drive") == SCANNED_DOCUMENT_NEEDS_OCR


@pytest.mark.asyncio
async def test_an_unexpected_parser_failure_is_retried_and_stored_as_temporary() -> None:
    """The parsing service answers an unexpected exception with a 500 and a
    generic body. The client retries it as transient, and the record is told
    of a temporary problem, as before the body stopped carrying the text."""
    from app.utils.user_errors import TEMPORARY_PROBLEM, to_user_reason

    client = ParsingClient(
        service_url="http://fake-parsing:8092", max_retries=2, retry_delay=0.0,
        config_service=_SecretsConfigService(),
    )
    body = {
        "success": False,
        "error": {
            "code": "PARSE_FAILED",
            "message": "Unexpected error while parsing the document",
            "details": {"requestId": "req-1"},
        },
    }
    calls = 0

    async def _request(method, url, **kwargs) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _make_response(500, body)

    fake_httpx = AsyncMock()
    fake_httpx.__aenter__ = AsyncMock(return_value=fake_httpx)
    fake_httpx.__aexit__ = AsyncMock(return_value=False)
    fake_httpx.request = _request

    with patch.object(client, "_make_client", return_value=fake_httpx):
        with pytest.raises(ServiceCallError) as exc_info:
            await client.parse(file_content=b"data", record_name="doc.pdf")

    assert calls == 2
    assert exc_info.value.status_code == 500
    assert "Unexpected error while parsing the document" in str(exc_info.value)
    assert to_user_reason(exc_info.value) == TEMPORARY_PROBLEM


# ---------------------------------------------------------------------------
# Service token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_passes_org_for_token() -> None:
    client = ParsingClient(service_url="http://fake-parsing:8092", max_retries=1)
    mock_post = AsyncMock(return_value=_make_response(200, _success_response()))

    with patch.object(client, "_post_multipart", new=mock_post):
        await client.parse(file_content=b"data", record_name="test.csv", org_id="org-123")

    assert mock_post.await_args.kwargs["org_id"] == "org-123"


@pytest.mark.asyncio
async def test_list_providers_requests_token_without_org() -> None:
    client = ParsingClient(
        service_url="http://fake-parsing:8092", max_retries=1, config_service=_SecretsConfigService()
    )
    mock_request = AsyncMock(return_value=_make_response(200, {"csv": ["default"]}))

    with patch.object(client, "_request_with_retry", new=mock_request):
        await client.list_providers()

    authorization = mock_request.await_args.kwargs["headers"]["Authorization"]
    claims = jwt.decode(authorization.removeprefix("Bearer "), SCOPED_SECRET, algorithms=["HS256"])
    assert claims["scopes"] == ["document:parse"]
    assert "orgId" not in claims


def test_parsing_client_uses_document_parse_scope() -> None:
    client = ParsingClient(service_url="http://fake-parsing:8092")

    assert client._service_scope is TokenScopes.DOCUMENT_PARSE


@pytest.mark.asyncio
async def test_parse_forwards_file_path_when_given() -> None:
    client = ParsingClient(service_url="http://fake-parsing:8092", max_retries=1)

    with patch.object(
        client,
        "_post_multipart",
        new=AsyncMock(return_value=_make_response(200, _success_response())),
    ) as post:
        await client.parse(
            file_content=b"x = 1\n",
            record_name="a.py",
            extension="py",
            file_path="src/pkg/a.py",
        )
        assert post.await_args.kwargs["data"]["file_path"] == "src/pkg/a.py"

        await client.parse(file_content=b"x = 1\n", record_name="a.py", extension="py")
        assert "file_path" not in post.await_args.kwargs["data"]


@pytest.mark.asyncio
async def test_a_pdf_gets_a_read_timeout_from_its_own_budget() -> None:
    """The client-wide read timeout is sized from the generic record budget
    and capped at one Docling batch, so a long filing died there whatever its
    own budget said. With USE_PARSING_SERVICE=true every page batch runs
    inside one /api/v1/parse response, so the deadline has to come from the
    record."""
    client = ParsingClient(service_url="http://fake-parsing:8092", max_retries=1)
    mock_post = AsyncMock(return_value=_make_response(200, _success_response()))

    with patch.object(client, "_post_multipart", new=mock_post):
        await client.parse(
            file_content=b"%PDF-1.7", record_name="filing.pdf",
            mime_type="application/pdf", extension="pdf",
        )

    kwargs = mock_post.await_args.kwargs
    # 5400 * 0.8, above both the 1440s default and the 2400s per-batch cap.
    assert kwargs["read_timeout"] == pytest.approx(4320.0)
    assert kwargs["budget_seconds"] == pytest.approx(5400.0)
    # One attempt can now use the budget, so a second cannot be squeezed in.
    assert kwargs["read_timeout"] * 2 > kwargs["budget_seconds"]


@pytest.mark.asyncio
async def test_a_non_pdf_keeps_the_client_read_timeout() -> None:
    client = ParsingClient(service_url="http://fake-parsing:8092", max_retries=1)
    mock_post = AsyncMock(return_value=_make_response(200, _success_response()))

    with patch.object(client, "_post_multipart", new=mock_post):
        await client.parse(
            file_content=b"a,b", record_name="rows.csv",
            mime_type="text/csv", extension="csv",
        )

    kwargs = mock_post.await_args.kwargs
    assert kwargs["read_timeout"] == pytest.approx(1440.0)
    assert kwargs["budget_seconds"] == pytest.approx(1800.0)


@pytest.mark.asyncio
async def test_the_per_call_read_timeout_reaches_the_request() -> None:
    """`_request_with_retry` must apply it, not just accept it: the override
    is the whole point, and the retry reserve has to match what one attempt
    is actually allowed."""
    client = ParsingClient(service_url="http://fake-parsing:8092", max_retries=1)
    seen: dict = {}

    class _Recorder(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            seen["timeout"] = request.extensions.get("timeout")
            return httpx.Response(200, json=_success_response())

    with patch.object(client, "_make_client",
                      lambda: httpx.AsyncClient(transport=_Recorder())):
        await client._request_with_retry(
            "POST", "http://fake-parsing:8092/api/v1/parse",
            operation="parse", read_timeout=4320.0,
        )

    assert seen["timeout"]["read"] == pytest.approx(4320.0)
