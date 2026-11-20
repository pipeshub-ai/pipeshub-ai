"""A deliberate stop (``NonRetryableError``) raised while the Parsing Service builds blocks ends the
parse as a 422 the client does not retry, and is never taken as a reason to OCR the PDF."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.exceptions.non_retryable import NonRetryableError
from app.services.parsing.interface import ParseError
from app.services.parsing.providers.pdfplumber_parser import PdfPlumberParser
from app.services.parsing.providers.smart_pdf_parser import SmartPDFParser
from app.services.parsing.registry import ParserRegistry
from tests.unit.api.routes.test_parsing_routes import _build_app, _ok_result


def _registry_with(parser: object) -> MagicMock:
    registry = MagicMock(spec=ParserRegistry)
    registry.resolve = MagicMock(return_value=parser)
    return registry


def test_a_non_retryable_error_from_a_parser_is_a_422_not_a_500() -> None:
    """As a 500 the client retried the parse and counted it against the parsing circuit breaker."""
    parser = MagicMock()
    parser.parse = AsyncMock(side_effect=NonRetryableError("refused for good"))
    client = TestClient(_build_app(_registry_with(parser)))

    response = client.post(
        "/api/v1/parse",
        files={"file": ("rows.csv", b"a,b\n1,2\n", "text/csv")},
        data={"mime_type": "text/csv", "extension": "csv", "provider": "default"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "PARSE_FAILED"
    assert body["error"]["details"]["error_type"] == "NonRetryableError"
    assert "refused for good" not in response.text, "the exception text is logged by type only"


def _plumber_refusing(stop: BaseException) -> PdfPlumberParser:
    processor = MagicMock()
    processor.parse_document = AsyncMock(return_value={"pages": []})
    processor.create_blocks = AsyncMock(side_effect=stop)
    return PdfPlumberParser(processor)


@pytest.mark.asyncio
async def test_the_pdfplumber_parser_lets_a_non_retryable_error_through_unwrapped() -> None:
    stop = NonRetryableError("refused for good")

    with pytest.raises(NonRetryableError) as raised:
        await _plumber_refusing(stop).parse(b"%PDF-1.7", "report.pdf")

    assert raised.value is stop
    assert not isinstance(raised.value, ParseError)


@pytest.mark.asyncio
async def test_a_pdf_whose_table_call_is_refused_is_not_sent_to_ocr() -> None:
    stop = NonRetryableError("refused for good")
    ocr = MagicMock()
    ocr.parse = AsyncMock(return_value=_ok_result())
    smart = SmartPDFParser(_plumber_refusing(stop), ocr)

    with pytest.raises(NonRetryableError) as raised:
        await smart._parse_text(b"%PDF-1.7", "report.pdf", None, 3)

    assert raised.value is stop
    ocr.parse.assert_not_called()


@pytest.mark.asyncio
async def test_an_ordinary_pdfplumber_failure_still_falls_back_to_ocr() -> None:
    ocr = MagicMock()
    ocr.parse = AsyncMock(return_value=_ok_result())
    smart = SmartPDFParser(_plumber_refusing(RuntimeError("layout crashed")), ocr)

    result = await smart._parse_text(b"%PDF-1.7", "report.pdf", None, 3)

    assert result.provider_used == _ok_result().provider_used
    ocr.parse.assert_awaited_once()


async def test_a_record_deleted_mid_parse_passes_through_the_pdfplumber_wrap_and_is_never_a_parse_error() -> None:
    """The stop must reach the route as RecordGone (a 410 the client confirms), not as PARSE_FAILED, and must not trigger OCR."""
    from app.services.paid_cache.orphan import RecordGone

    processor = MagicMock()
    processor.parse_document = AsyncMock(return_value=object())
    processor.create_blocks = AsyncMock(side_effect=RecordGone("rec-1"))
    with pytest.raises(RecordGone):
        await PdfPlumberParser(processor).parse(b"%PDF", "a.pdf")
