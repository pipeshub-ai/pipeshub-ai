"""Unit tests for app.utils.attachment_mime_types."""

import pytest

from app.utils.attachment_mime_types import (
    DELIMITED_MIME_TYPES,
    DOC_ATTACHMENT_MIME_TYPES,
    DOCX_MIME_TYPE,
    DOCX_MIME_TYPES,
    IMAGE_UPLOAD_MIME_TYPES,
    PDF_MIME_TYPE,
    SPREADSHEET_MIME_TYPES,
    SUPPORTED_ATTACHMENT_MIME_TYPES,
    TEXT_ATTACHMENT_MIME_TYPES,
    XLSX_MIME_TYPE,
    is_doc_attachment_mime,
    is_image_attachment_mime,
)


class TestConstants:
    def test_pdf_mime_type(self):
        assert PDF_MIME_TYPE == "application/pdf"

    def test_docx_mime_type(self):
        assert DOCX_MIME_TYPE == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    def test_xlsx_mime_type(self):
        assert XLSX_MIME_TYPE == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    def test_text_types_are_frozenset(self):
        assert isinstance(TEXT_ATTACHMENT_MIME_TYPES, frozenset)
        assert "text/plain" in TEXT_ATTACHMENT_MIME_TYPES
        assert "text/markdown" in TEXT_ATTACHMENT_MIME_TYPES

    def test_delimited_types(self):
        assert "text/csv" in DELIMITED_MIME_TYPES
        assert "text/tab-separated-values" in DELIMITED_MIME_TYPES

    def test_doc_is_superset(self):
        assert TEXT_ATTACHMENT_MIME_TYPES.issubset(DOC_ATTACHMENT_MIME_TYPES)
        assert DOCX_MIME_TYPES.issubset(DOC_ATTACHMENT_MIME_TYPES)
        assert SPREADSHEET_MIME_TYPES.issubset(DOC_ATTACHMENT_MIME_TYPES)
        assert DELIMITED_MIME_TYPES.issubset(DOC_ATTACHMENT_MIME_TYPES)
        assert PDF_MIME_TYPE in DOC_ATTACHMENT_MIME_TYPES

    def test_supported_is_doc_union_image(self):
        assert SUPPORTED_ATTACHMENT_MIME_TYPES == DOC_ATTACHMENT_MIME_TYPES | IMAGE_UPLOAD_MIME_TYPES

    def test_image_upload_types(self):
        assert "image/jpeg" in IMAGE_UPLOAD_MIME_TYPES
        assert "image/png" in IMAGE_UPLOAD_MIME_TYPES


class TestIsImageAttachmentMime:
    @pytest.mark.parametrize("mime", [
        "image/jpeg", "image/jpg", "image/png", "image/gif", "image/webp", "image/svg+xml",
    ])
    def test_image_types_return_true(self, mime):
        assert is_image_attachment_mime(mime) is True

    @pytest.mark.parametrize("mime", [
        "application/pdf", "text/plain", "application/json", "video/mp4",
    ])
    def test_non_image_types_return_false(self, mime):
        assert is_image_attachment_mime(mime) is False

    def test_case_insensitive(self):
        assert is_image_attachment_mime("Image/PNG") is True
        assert is_image_attachment_mime("IMAGE/JPEG") is True


class TestIsDocAttachmentMime:
    @pytest.mark.parametrize("mime", list(DOC_ATTACHMENT_MIME_TYPES))
    def test_all_doc_types_return_true(self, mime):
        assert is_doc_attachment_mime(mime) is True

    @pytest.mark.parametrize("mime", [
        "image/jpeg", "image/png", "video/mp4", "application/octet-stream",
    ])
    def test_non_doc_types_return_false(self, mime):
        assert is_doc_attachment_mime(mime) is False

    def test_case_insensitive(self):
        assert is_doc_attachment_mime("TEXT/PLAIN") is True
        assert is_doc_attachment_mime("Application/PDF") is True
