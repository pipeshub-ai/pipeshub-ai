"""Parsers that call a model (CSV/Excel table summaries, VLM OCR) log its failure without the provider's text.

A provider's error message is its response body, which can echo the rows or the page it was sent.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.modules.parsers.pdf.vlm_ocr_strategy as vlm_module
from app.config.constants.ai_models import OCRProvider
from app.modules.parsers.csv.csv_parser import CSVParser
from app.modules.parsers.excel.excel_parser import ExcelParser
from app.modules.parsers.pdf.ocr_handler import OCRHandler
from app.modules.parsers.pdf.vlm_ocr_strategy import VLMOCRStrategy
from app.services.llm_gateway.gateway import ProviderUnavailableError
from app.utils.table_enrichment import fallback_table_summary
from tests.support.provider_errors import (
    assert_text_free,
    failure_line,
    httpx_error,
    openai_error,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_ERRORS = [
    (lambda: openai_error(400), "bad_request", logging.WARNING),
    (lambda: openai_error(401), "misconfigured", logging.ERROR),
    (lambda: httpx_error(400), "bad_request", logging.WARNING),
]


def _outage() -> ProviderUnavailableError:
    cause = openai_error(500)
    # The gateway's own wording: the body rides in the wrapper's message.
    error = ProviderUnavailableError(f"p could not serve the call: InternalServerError: {cause}", provider="p")
    error.__cause__ = cause
    return error


@pytest.mark.parametrize(("error", "error_class", "level"), [*_ERRORS, (_outage, "upstream_5xx", logging.WARNING)])
async def test_a_failed_csv_table_summary_falls_back_with_one_text_free_line(caplog, error, error_class, level) -> None:
    caplog.set_level(logging.DEBUG)
    parser = CSVParser(config_service=None)
    parser._call_llm = AsyncMock(side_effect=error())

    summary = await parser.get_table_summary(MagicMock(), [{"name": "a"}])

    assert summary == fallback_table_summary(["name"], 1)
    assert failure_line(caplog.records, "table_summary.failed", error_class, level)["parser"] == "csv"
    assert_text_free(caplog.records)


@pytest.mark.parametrize(("error", "error_class", "level"), [*_ERRORS, (_outage, "upstream_5xx", logging.WARNING)])
async def test_a_failed_excel_table_summary_falls_back_with_one_text_free_line(caplog, error, error_class, level) -> None:
    caplog.set_level(logging.DEBUG)
    parser = ExcelParser(logging.getLogger("test.excel"), None)
    parser._call_llm = AsyncMock(side_effect=error())

    summary = await parser.get_table_summary({"headers": ["name"], "data": [[{"header": "name", "value": "a"}]]})

    assert summary == fallback_table_summary(["name"], 1)
    assert failure_line(caplog.records, "table_summary.failed", error_class, level)["parser"] == "excel"
    assert_text_free(caplog.records)


def _vlm(monkeypatch: pytest.MonkeyPatch, invoke: AsyncMock, pages: int = 1) -> VLMOCRStrategy:
    monkeypatch.setattr(vlm_module, "get_llm_gateway", lambda: SimpleNamespace(invoke=invoke))
    strategy = VLMOCRStrategy(logging.getLogger("test.vlm"), MagicMock())
    strategy.llm, strategy.llm_config = MagicMock(), None
    strategy.doc = SimpleNamespace(pages=[SimpleNamespace(page_number=n, width=1, height=1) for n in range(1, pages + 1)])

    async def _render(numbers: list[int] | None = None) -> None:
        strategy._page_images.update(dict.fromkeys(numbers or [], "data:image/png;base64,iVBORw0KGgo="))

    strategy._preload_page_images = _render
    return strategy


async def test_a_page_the_model_keeps_refusing_is_retried_then_given_up_once(caplog, monkeypatch) -> None:
    caplog.set_level(logging.DEBUG)
    strategy = _vlm(monkeypatch, AsyncMock(side_effect=openai_error(400)), pages=2)
    ok = SimpleNamespace(content="# page", response_metadata={})
    strategy._call_llm_for_markdown = _only_page_two_fails(strategy._call_llm_for_markdown, ok)

    result = await strategy._preprocess_document()

    assert [p["markdown"] for p in result["pages"]] == ["# page", ""]
    retries = [r for r in caplog.records if "retrying" in r.getMessage()]
    assert len(retries) == 2
    assert {r.error_type for r in retries} == {"BadRequestError"}
    fields = failure_line(caplog.records, "vlm_ocr.page_failed", "bad_request", logging.WARNING)
    assert (fields["page"], fields["attempts"]) == (2, 3)
    assert_text_free(caplog.records)


def _only_page_two_fails(call: Callable[[str, int], Awaitable[str]], ok: SimpleNamespace) -> Callable[[str, int], Awaitable[str]]:
    async def _call(image: str, page_number: int) -> str:
        if page_number == 1:
            return ok.content
        return await call(image, page_number)
    return _call


async def test_every_page_failing_raises_the_last_error_with_one_line_per_page(caplog, monkeypatch) -> None:
    caplog.set_level(logging.DEBUG)
    error = openai_error(401)
    strategy = _vlm(monkeypatch, AsyncMock(side_effect=error))
    strategy._open_pdf_off_loop = AsyncMock(return_value=("", strategy.doc, 1))
    strategy._get_multimodal_llm = AsyncMock(return_value=strategy.llm)
    monkeypatch.setattr(VLMOCRStrategy, "_release_pdf_sync", staticmethod(lambda *_a: None))

    with pytest.raises(type(error)):
        await strategy.load_document(b"%PDF")

    failure_line(caplog.records, "vlm_ocr.page_failed", "misconfigured", logging.ERROR)
    assert_text_free(caplog.records)


async def test_the_ocr_handler_re_raises_without_a_line_of_its_own(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    error = _outage()
    handler = OCRHandler.__new__(OCRHandler)
    handler.logger = logging.getLogger("test.ocr_handler")
    handler.provider = OCRProvider.VLM_OCR.value
    handler._strategy_kwargs = {}
    handler._create_strategy = MagicMock(return_value=SimpleNamespace(load_document=AsyncMock(side_effect=error)))

    with pytest.raises(ProviderUnavailableError) as raised:
        await handler.process_document(b"%PDF")

    assert raised.value is error
    assert not [r for r in caplog.records if r.name == "test.ocr_handler" and r.levelno >= logging.WARNING]
    assert_text_free(caplog.records)
