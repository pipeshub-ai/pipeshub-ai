"""A scan longer than the page cap is refused before any model is called."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.modules.parsers.pdf import vlm_ocr_strategy
from app.modules.parsers.pdf.vlm_ocr_strategy import MAX_PAGES_ENV, VLMOCRStrategy
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)
from app.utils.user_errors import FILE_TOO_LARGE, to_user_reason
from tests.support.pdf_samples import blank_pdf


def _strategy() -> VLMOCRStrategy:
    strategy = VLMOCRStrategy(logging.getLogger("test-vlm-page-cap"), MagicMock())
    strategy._get_multimodal_llm = AsyncMock(return_value=MagicMock())
    strategy._preprocess_document = AsyncMock(return_value={})
    return strategy


class TestPageCap:
    async def test_a_scan_over_the_cap_is_refused_before_the_model_is_fetched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(MAX_PAGES_ENV, "2")
        strategy = _strategy()

        with pytest.raises(DocumentProcessingError) as raised:
            await strategy.load_document(blank_pdf(pages=3))

        strategy._get_multimodal_llm.assert_not_awaited()
        assert to_user_reason(raised.value) == FILE_TOO_LARGE
        assert MessageErrorClassifier.classify_by_exception(raised.value) == MessageErrorType.TERMINAL

    async def test_a_scan_at_the_cap_is_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(MAX_PAGES_ENV, "3")
        strategy = _strategy()

        await strategy.load_document(blank_pdf(pages=3))

        strategy._get_multimodal_llm.assert_awaited_once()

    @pytest.mark.parametrize(("raw", "expected"), [(None, 500), ("40", 40), ("0", 1), ("-2", 1), ("lots", 500)])
    def test_the_cap_setting(self, monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: int) -> None:
        if raw is None:
            monkeypatch.delenv(MAX_PAGES_ENV, raising=False)
        else:
            monkeypatch.setenv(MAX_PAGES_ENV, raw)

        assert vlm_ocr_strategy.vlm_ocr_max_pages() == expected
