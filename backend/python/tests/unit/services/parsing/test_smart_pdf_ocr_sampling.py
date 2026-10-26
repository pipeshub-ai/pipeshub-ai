"""The OCR decision samples fixed pages, so a retry of one file decides the same way."""

from app.services.parsing.providers.smart_pdf_parser import _sample_page_indices


def test_samples_are_evenly_spaced_and_include_both_ends() -> None:
    assert _sample_page_indices(100, 5) == [0, 25, 50, 74, 99]


def test_a_short_document_samples_every_page() -> None:
    assert _sample_page_indices(3, 3) == [0, 1, 2]


def test_a_single_page_document() -> None:
    assert _sample_page_indices(1, 1) == [0]


def test_the_sample_is_deterministic() -> None:
    assert len({tuple(_sample_page_indices(517, 5)) for _ in range(20)}) == 1
