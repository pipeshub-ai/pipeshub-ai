"""Unit tests for app.utils.pdf_utils."""

from unittest.mock import MagicMock, patch

import pytest

from app.utils.pdf_utils import _get_page_batch_size, get_pdf_page_count


class TestGetPageBatchSize:
    def test_default_is_10(self, monkeypatch):
        monkeypatch.delenv("DOCLING_PAGE_BATCH_SIZE", raising=False)
        assert _get_page_batch_size() == 10

    def test_custom_env(self, monkeypatch):
        monkeypatch.setenv("DOCLING_PAGE_BATCH_SIZE", "20")
        assert _get_page_batch_size() == 20

    def test_invalid_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("DOCLING_PAGE_BATCH_SIZE", "abc")
        assert _get_page_batch_size() == 10

    def test_min_one(self, monkeypatch):
        monkeypatch.setenv("DOCLING_PAGE_BATCH_SIZE", "0")
        assert _get_page_batch_size() == 1

    def test_negative_becomes_one(self, monkeypatch):
        monkeypatch.setenv("DOCLING_PAGE_BATCH_SIZE", "-5")
        assert _get_page_batch_size() == 1


class TestGetPdfPageCount:
    def test_with_mock_pdf(self):
        mock_doc = MagicMock()
        mock_doc.__len__ = MagicMock(return_value=3)
        with patch("app.utils.pdf_utils.pdfium.PdfDocument", return_value=mock_doc):
            assert get_pdf_page_count(b"fake-pdf") == 3
        mock_doc.close.assert_called_once()

    def test_single_page(self):
        mock_doc = MagicMock()
        mock_doc.__len__ = MagicMock(return_value=1)
        with patch("app.utils.pdf_utils.pdfium.PdfDocument", return_value=mock_doc):
            assert get_pdf_page_count(b"fake-pdf") == 1

    def test_close_called_on_exception(self):
        mock_doc = MagicMock()
        mock_doc.__len__ = MagicMock(side_effect=RuntimeError("corrupt"))
        with patch("app.utils.pdf_utils.pdfium.PdfDocument", return_value=mock_doc):
            with pytest.raises(RuntimeError):
                get_pdf_page_count(b"bad-pdf")
        mock_doc.close.assert_called_once()
