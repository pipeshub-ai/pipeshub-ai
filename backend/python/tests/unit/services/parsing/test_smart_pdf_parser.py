"""Tests for SmartPDFParser routing (scanned → VLM, digital → primary)."""

from unittest.mock import AsyncMock, patch

import pytest

from app.models.blocks import BlocksContainer
from app.services.parsing.interface import ParseError, ParseErrorCode, ParseResult
from app.services.parsing.providers.smart_pdf_parser import SmartPDFParser


def _result() -> ParseResult:
    return ParseResult(block_container=BlocksContainer(blocks=[], block_groups=[]))


@pytest.fixture
def primary():
    parser = AsyncMock()
    parser.parse = AsyncMock(return_value=_result())
    return parser


@pytest.fixture
def vlm():
    parser = AsyncMock()
    parser.parse = AsyncMock(return_value=_result())
    return parser


@pytest.mark.asyncio
async def test_digital_pdf_routes_to_primary(primary, vlm):
    parser = SmartPDFParser(primary, vlm)
    with patch(
        "app.services.parsing.providers.smart_pdf_parser.detect_scanned_pdf",
        new=AsyncMock(return_value=False),
    ):
        await parser.parse(b"%PDF", "digital.pdf")

    primary.parse.assert_awaited_once()
    vlm.parse.assert_not_awaited()


@pytest.mark.asyncio
async def test_scanned_pdf_routes_to_vlm(primary, vlm):
    parser = SmartPDFParser(primary, vlm)
    with patch(
        "app.services.parsing.providers.smart_pdf_parser.detect_scanned_pdf",
        new=AsyncMock(return_value=True),
    ):
        await parser.parse(b"%PDF", "scan.pdf")

    vlm.parse.assert_awaited_once()
    primary.parse.assert_not_awaited()


@pytest.mark.asyncio
async def test_detection_failure_defaults_to_primary(primary, vlm):
    parser = SmartPDFParser(primary, vlm)
    with patch(
        "app.services.parsing.providers.smart_pdf_parser.detect_scanned_pdf",
        new=AsyncMock(side_effect=RuntimeError("encrypted PDF")),
    ):
        await parser.parse(b"%PDF", "locked.pdf")

    primary.parse.assert_awaited_once()
    vlm.parse.assert_not_awaited()


@pytest.mark.asyncio
async def test_primary_failure_falls_back_to_vlm(primary, vlm):
    primary.parse = AsyncMock(
        side_effect=ParseError(ParseErrorCode.PARSE_FAILED, "boom")
    )
    parser = SmartPDFParser(primary, vlm)
    with patch(
        "app.services.parsing.providers.smart_pdf_parser.detect_scanned_pdf",
        new=AsyncMock(return_value=False),
    ):
        await parser.parse(b"%PDF", "flaky.pdf")

    vlm.parse.assert_awaited_once()


@pytest.mark.asyncio
async def test_client_errors_are_not_retried_via_vlm(primary, vlm):
    primary.parse = AsyncMock(
        side_effect=ParseError(ParseErrorCode.INVALID_INPUT, "bad input")
    )
    parser = SmartPDFParser(primary, vlm)
    with patch(
        "app.services.parsing.providers.smart_pdf_parser.detect_scanned_pdf",
        new=AsyncMock(return_value=False),
    ):
        with pytest.raises(ParseError):
            await parser.parse(b"%PDF", "bad.pdf")

    vlm.parse.assert_not_awaited()
