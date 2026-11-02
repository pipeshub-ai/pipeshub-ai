"""The PDF facts read in a parse worker, on real files: sound, damaged, protected and not PDFs."""
from __future__ import annotations

import io
import time
from unittest.mock import patch

import pdfplumber
import pytest
from pypdfium2 import PdfiumError

from app.exceptions.indexing_exceptions import PasswordProtectedError
from app.modules.parsers.pdf.ocr_handler import OCRStrategy
from app.modules.parsers.pdf.pdf_inspection import (
    OCR_SAMPLE_PAGES,
    detect_ocr,
    needs_ocr_and_page_count,
    open_pdf,
    pages_need_ocr,
    repair_pdf,
    sample_page_indices,
    text_layer_char_counts,
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

    @pytest.mark.parametrize("page_count", [31, 88, 117, 1_000])
    def test_the_sample_is_deterministic(self, page_count: int) -> None:
        assert sample_page_indices(page_count, 30) == sample_page_indices(page_count, 30)

    @pytest.mark.parametrize(
        "page_count,period,needing_ocr",
        [(88, 3, {0, 1}), (117, 4, {0, 1, 2}), (60, 3, {0, 1}), (200, 5, {0, 1, 2}), (301, 6, {0, 1, 2, 3})],
    )
    def test_a_layout_that_repeats_is_not_seen_as_one_kind_of_page(
        self, page_count: int, period: int, needing_ocr: set[int]
    ) -> None:
        """A fixed stride landed every sampled page on one position of a repeating
        layout: 2 scanned pages in 3 (88 pages) read as 0 %, 3 in 4 (117) as 0 %."""
        true_share = sum(i % period in needing_ocr for i in range(page_count)) / page_count
        indices = sample_page_indices(page_count, OCR_SAMPLE_PAGES)
        sampled_share = sum(i % period in needing_ocr for i in indices) / len(indices)

        assert (sampled_share >= 0.5) == (true_share >= 0.5)

    def test_no_repeating_layout_moves_the_sampled_share_by_more_than_a_third(self) -> None:
        """Every layout repeating every 2 to 6 pages, over 31 to 400 pages. A fixed
        stride was off by up to 83 points, and the golden-ratio sequence by 72:
        both line up with some period. A page drawn from each run stays within 34."""
        worst = 0.0
        for period in range(2, 7):
            for pattern in range(1, 2**period - 1):
                needs_ocr = [(pattern >> (i % period)) & 1 for i in range(400)]
                for page_count in range(31, 401, 3):
                    indices = sample_page_indices(page_count, OCR_SAMPLE_PAGES)
                    sampled = sum(needs_ocr[i] for i in indices) / len(indices)
                    true = sum(needs_ocr[:page_count]) / page_count
                    worst = max(worst, abs(sampled - true))

        assert worst <= 0.34

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


class TestTextLayerCharCounts:
    def test_text_and_shapes_pages(self) -> None:
        pytest.importorskip("reportlab")
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.drawString(72, 700, "hello")
        c.showPage()
        c.rect(72, 600, 200, 100, fill=1)
        c.showPage()
        c.save()

        text_page, shapes_page = text_layer_char_counts(buf.getvalue())
        assert text_page >= len("hello")
        assert shapes_page == 0

    @pytest.mark.parametrize("content", list(NOT_PDFS.values()), ids=list(NOT_PDFS))
    def test_a_file_pdfium_cannot_open_has_no_counts(self, content: bytes) -> None:
        assert text_layer_char_counts(content) is None


class TestNeedsOcrAndPageCount:
    """pdfplumber spent over a minute deciding that a report whose glyphs were
    drawn as vector paths had no text. pdfium's per-page character counts settle
    textless pages first; pdfplumber's heuristic sees only pages with text."""

    @staticmethod
    def _decide(content: bytes, char_counts: list[int] | None) -> tuple[bool, int]:
        with patch(
            "app.modules.parsers.pdf.pdf_inspection.text_layer_char_counts", return_value=char_counts
        ):
            return needs_ocr_and_page_count(content)

    def test_a_text_pdf_needs_no_ocr(self) -> None:
        assert needs_ocr_and_page_count(text_pdf(pages=3)) == (False, 3)

    def test_a_pdf_without_text_reads_as_scanned(self) -> None:
        assert needs_ocr_and_page_count(blank_pdf(pages=3)) == (True, 3)

    def test_pages_without_a_text_layer_never_reach_pdfplumber(self) -> None:
        with patch.object(OCRStrategy, "needs_ocr") as needs_ocr:
            assert needs_ocr_and_page_count(blank_pdf(pages=3)) == (True, 3)

        needs_ocr.assert_not_called()

    def test_enough_textless_pages_settle_it_without_pdfplumber(self) -> None:
        with patch.object(OCRStrategy, "needs_ocr") as needs_ocr:
            assert self._decide(text_pdf(pages=4), [0, 0, 300, 12]) == (True, 4)

        needs_ocr.assert_not_called()

    @pytest.mark.parametrize("pages_needing_ocr,expect", [(0, False), (1, True)])
    def test_pages_with_text_keep_the_existing_heuristic(
        self, pages_needing_ocr: int, expect: bool
    ) -> None:
        """One textless page of four plus the heuristic's verdict on the other three."""
        verdicts = [True] * pages_needing_ocr + [False] * (3 - pages_needing_ocr)
        with patch.object(OCRStrategy, "needs_ocr", side_effect=verdicts) as needs_ocr:
            assert self._decide(text_pdf(pages=4), [0, 40, 300, 12]) == (expect, 4)

        assert needs_ocr.call_count == 3

    @pytest.mark.parametrize("char_counts", [None, [0]], ids=["pdfium-error", "page-count-mismatch"])
    def test_every_page_gets_the_heuristic_when_pdfium_cannot_help(
        self, char_counts: list[int] | None
    ) -> None:
        with patch.object(OCRStrategy, "needs_ocr", return_value=False) as needs_ocr:
            assert self._decide(text_pdf(pages=3), char_counts) == (False, 3)

        assert needs_ocr.call_count == 3

    def test_a_pdf_with_no_pages_needs_no_ocr(self) -> None:
        assert needs_ocr_and_page_count(broken_xref_pdf(1_000)) == (False, 0)


class TestPageLimit:
    """Counting text-layer characters walks every page (about 11 s for 4,000 pages):
    a document past the caller's page limit is answered from the page count alone,
    so the caller can refuse it before its deadline."""

    def test_past_the_limit_only_the_page_count_is_read(self) -> None:
        with patch(
            "app.modules.parsers.pdf.pdf_inspection.text_layer_char_counts"
        ) as char_counts, patch.object(OCRStrategy, "needs_ocr") as needs_ocr:
            assert needs_ocr_and_page_count(text_pdf(pages=6), max_pages=5) == (False, 6)

        char_counts.assert_not_called()
        needs_ocr.assert_not_called()

    def test_at_the_limit_the_document_is_checked(self) -> None:
        with patch(
            "app.modules.parsers.pdf.pdf_inspection.text_layer_char_counts", return_value=[0] * 5
        ) as char_counts:
            assert needs_ocr_and_page_count(text_pdf(pages=5), max_pages=5) == (True, 5)

        char_counts.assert_called_once()


def test_the_sample_is_thirty_pages() -> None:
    """The sample size is the memory and time bound of the check; changing it is a decision."""
    assert OCR_SAMPLE_PAGES == 30


class TestNeedsOcrAndPageCountOnLongDocuments:
    """pdfplumber keeps every parsed page until it is closed, so walking all of a
    1,000-page text PDF held 5.2 GiB in the parse worker. The check now looks at a
    bounded sample of the pages with text and closes each as soon as it is read."""

    PAGES = 100

    @staticmethod
    def _decide(char_counts: list[int], verdict) -> tuple[tuple[bool, int], list[int]]:
        """The decision, and the (0-based) pages the heuristic was asked about."""
        asked: list[int] = []

        def needs_ocr(page, _logger) -> bool:
            asked.append(page.page_number - 1)
            return verdict(len(asked) - 1)

        with patch.object(OCRStrategy, "needs_ocr", side_effect=needs_ocr), patch(
            "app.modules.parsers.pdf.pdf_inspection.text_layer_char_counts", return_value=char_counts
        ):
            return needs_ocr_and_page_count(text_pdf(pages=TestNeedsOcrAndPageCountOnLongDocuments.PAGES)), asked

    def test_only_a_bounded_sample_of_the_pages_is_checked(self) -> None:
        _, asked = self._decide([300] * self.PAGES, lambda _: False)

        assert len(asked) == OCR_SAMPLE_PAGES
        assert asked[0] == 0 and asked[-1] == self.PAGES - 1

    def test_every_page_read_is_closed_before_the_next_is_read(self) -> None:
        from pdfplumber.page import Page

        events: list[str] = []
        real_close = Page.close

        def close(page) -> None:
            events.append("close")
            real_close(page)

        def needs_ocr(_page, _logger) -> bool:
            events.append("read")
            return False

        with patch.object(OCRStrategy, "needs_ocr", side_effect=needs_ocr), patch(
            "app.modules.parsers.pdf.pdf_inspection.text_layer_char_counts", return_value=[300] * self.PAGES
        ), patch.object(Page, "close", close):
            needs_ocr_and_page_count(text_pdf(pages=self.PAGES))

        # Closing the whole file afterwards closes the rest; what matters is the interleaving.
        assert events[: 2 * OCR_SAMPLE_PAGES] == ["read", "close"] * OCR_SAMPLE_PAGES

    @pytest.mark.parametrize("ocr_in_sample,expect", [(0, False), (14, False), (15, True), (30, True)])
    def test_the_sample_stands_for_all_pages_with_text(self, ocr_in_sample: int, expect: bool) -> None:
        """15 of 30 sampled is half of the 100 pages, not 15 of 100."""
        result, _ = self._decide([300] * self.PAGES, lambda n: n < ocr_in_sample)

        assert result == (expect, self.PAGES)

    def test_textless_pages_count_in_full_beside_the_sample(self) -> None:
        """40 textless pages are certain; half of the 60 with text brings it to 70."""
        counts = [0] * 40 + [300] * 60
        result, asked = self._decide(counts, lambda n: n < 15)

        assert result == (True, self.PAGES)
        assert all(counts[i] != 0 for i in asked)
        assert len(asked) == OCR_SAMPLE_PAGES

    def test_a_few_textless_pages_alone_do_not_decide_it(self) -> None:
        counts = [0] * 40 + [300] * 60
        result, _ = self._decide(counts, lambda _: False)

        assert result == (False, self.PAGES)

    @pytest.mark.parametrize("pages,period,ocr_positions", [(88, 3, {1, 2}), (117, 4, {1, 2, 3})])
    def test_a_repeating_mix_of_scanned_and_text_pages_is_called_scanned(
        self, pages: int, period: int, ocr_positions: set[int]
    ) -> None:
        """Mostly scanned pages in a repeating pattern: the sample must not miss them."""
        verdict_by_page = {i: i % period in ocr_positions for i in range(pages)}
        asked: list[int] = []

        def needs_ocr(page, _logger) -> bool:
            asked.append(page.page_number - 1)
            return verdict_by_page[page.page_number - 1]

        with patch.object(OCRStrategy, "needs_ocr", side_effect=needs_ocr), patch(
            "app.modules.parsers.pdf.pdf_inspection.text_layer_char_counts", return_value=[300] * pages
        ):
            assert needs_ocr_and_page_count(text_pdf(pages=pages)) == (True, pages)

    def test_a_document_within_the_sample_is_checked_in_full(self) -> None:
        pages = OCR_SAMPLE_PAGES
        with patch.object(OCRStrategy, "needs_ocr", return_value=False) as needs_ocr, patch(
            "app.modules.parsers.pdf.pdf_inspection.text_layer_char_counts", return_value=[300] * pages
        ):
            assert needs_ocr_and_page_count(text_pdf(pages=pages)) == (False, pages)

        assert needs_ocr.call_count == pages

    def test_an_unreadable_text_layer_samples_all_pages(self) -> None:
        result, asked = self._decide(None, lambda _: False)

        assert result == (False, self.PAGES)
        assert len(asked) == OCR_SAMPLE_PAGES


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
        with pytest.raises(PasswordProtectedError):
            repair_pdf(password_protected_pdf())

    @pytest.mark.parametrize(
        "content", [*NOT_PDFS.values(), truncated_pdf()], ids=[*NOT_PDFS, "truncated"]
    )
    def test_a_file_pdfium_cannot_open_raises(self, content: bytes) -> None:
        with pytest.raises(PdfiumError):
            repair_pdf(content)
