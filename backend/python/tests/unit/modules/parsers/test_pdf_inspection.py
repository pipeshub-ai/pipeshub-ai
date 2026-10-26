"""The PDF facts read in a parse worker, on real files: sound, damaged, protected and not PDFs."""
from __future__ import annotations

import io
import time
from unittest.mock import patch

import pdfplumber
import pytest
from pypdfium2 import PdfiumError

from app.modules.parsers.pdf.ocr_handler import OCRStrategy
from app.modules.parsers.pdf.pdf_inspection import (
    detect_ocr,
    open_pdf,
    pages_need_ocr,
    repair_pdf,
    sample_page_indices,
)
from tests.support.pdf_samples import (
    MIB,
    NOT_PDFS,
    blank_pdf,
    broken_xref_pdf,
    garbage_tail_pdf,
    owner_password_pdf,
    password_protected_pdf,
    text_pdf,
    truncated_pdf,
)

UNREADABLE = {**NOT_PDFS, "truncated": truncated_pdf(), "password protected": password_protected_pdf()}


class TestSamplePageIndices:
    @pytest.mark.parametrize("page_count", [0, 1, 29, 30])
    def test_every_page_up_to_the_limit(self, page_count: int) -> None:
        assert sample_page_indices(page_count, 30) == list(range(page_count))

    @pytest.mark.parametrize("page_count", [31, 1_000, 250_000])
    def test_past_the_limit_the_sample_is_spread_from_first_to_last(self, page_count: int) -> None:
        indices = sample_page_indices(page_count, 30)

        assert len(indices) == 30
        assert indices[0] == 0
        assert indices[-1] == page_count - 1
        assert indices == sorted(set(indices))

    @pytest.mark.parametrize("limit", [1, 0, -5])
    def test_a_limit_below_two_samples_the_first_page(self, limit: int) -> None:
        assert sample_page_indices(10, limit) == [0]


class TestPagesNeedOcr:
    @staticmethod
    def _decide(answers: list[bool]) -> tuple[bool, int]:
        with patch.object(OCRStrategy, "needs_ocr", side_effect=answers) as checked:
            decision = pages_need_ocr([object()] * len(answers))
        return decision, checked.call_count

    def test_no_pages_need_no_ocr(self) -> None:
        assert pages_need_ocr([]) is False

    def test_half_the_pages_is_enough(self) -> None:
        assert self._decide([True, False, True, False])[0] is True

    def test_just_under_half_is_not(self) -> None:
        assert self._decide([True, False, False, False, False])[0] is False

    def test_it_stops_once_ocr_is_certain(self) -> None:
        assert self._decide([True, True, False, False]) == (True, 2)

    def test_it_stops_once_ocr_is_impossible(self) -> None:
        # Two of four needed: after three text pages one is left, so it cannot reach two.
        assert self._decide([False, False, False, True]) == (False, 3)


class TestOpenPdf:
    def test_a_sound_pdf_opens_quickly(self) -> None:
        structure = open_pdf(text_pdf(pages=2))

        assert structure.page_count == 2
        assert structure.open_seconds < 1.0

    def test_an_owner_password_does_not_stop_reading(self) -> None:
        assert open_pdf(owner_password_pdf()).page_count == 1

    @pytest.mark.parametrize("content", UNREADABLE.values(), ids=UNREADABLE.keys())
    def test_a_file_pdfminer_cannot_open_raises(self, content: bytes) -> None:
        with pytest.raises(Exception):  # noqa: B017 - pdfminer's errors are not one type
            open_pdf(content)

    def test_a_broken_cross_reference_table_reads_as_no_pages(self) -> None:
        """Why no pages counts as damage: pdfminer misses the page pdfium finds."""
        assert open_pdf(broken_xref_pdf(16 * 1024)).page_count == 0


class TestDetectOcr:
    def test_a_text_pdf_needs_no_ocr(self) -> None:
        assert detect_ocr(text_pdf(pages=2), 30) is False

    def test_a_pdf_without_text_reads_as_scanned(self) -> None:
        assert detect_ocr(blank_pdf(pages=3), 30) is True

    def test_an_owner_password_does_not_stop_the_decision(self) -> None:
        assert detect_ocr(owner_password_pdf(), 30) is False

    def test_only_the_sampled_pages_are_checked(self) -> None:
        with patch.object(OCRStrategy, "needs_ocr", wraps=OCRStrategy.needs_ocr) as checked:
            assert detect_ocr(text_pdf(pages=12), 4) is False

        assert checked.call_count <= 4

    def test_a_pdf_with_no_pages_needs_no_ocr(self) -> None:
        assert detect_ocr(broken_xref_pdf(1024), 30) is False


class TestRepairPdf:
    def test_a_broken_cross_reference_table_is_rebuilt(self) -> None:
        damaged = broken_xref_pdf(1 * MIB)

        repaired = repair_pdf(damaged)

        started = time.monotonic()
        with pdfplumber.open(io.BytesIO(repaired)) as pdf:
            assert len(pdf.pages) == 1
        assert time.monotonic() - started < 1.0

    def test_trailing_garbage_is_dropped(self) -> None:
        damaged = garbage_tail_pdf(4 * MIB)

        repaired = repair_pdf(damaged)

        assert len(repaired) < 64 * 1024
        assert open_pdf(repaired).page_count == 1

    def test_a_sound_file_keeps_its_pages_and_text(self) -> None:
        repaired = repair_pdf(text_pdf(pages=2))

        assert open_pdf(repaired).page_count == 2
        assert detect_ocr(repaired, 30) is False

    def test_a_password_protected_file_is_refused_as_such(self) -> None:
        with pytest.raises(PdfiumError, match="(?i)password"):
            repair_pdf(password_protected_pdf())

    @pytest.mark.parametrize(
        "content", [*NOT_PDFS.values(), truncated_pdf()], ids=[*NOT_PDFS, "truncated"]
    )
    def test_a_file_pdfium_cannot_open_raises(self, content: bytes) -> None:
        with pytest.raises(PdfiumError):
            repair_pdf(content)
