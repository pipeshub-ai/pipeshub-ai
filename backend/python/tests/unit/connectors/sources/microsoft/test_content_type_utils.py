from app.connectors.sources.microsoft.common.content_type_utils import (
    attachment_metadata_from_graph,
    derive_attachment_extension,
    normalize_content_type,
)


class TestNormalizeContentType:
    def test_strips_charset(self):
        assert normalize_content_type("text/html; charset=utf-8") == "text/html"

    def test_lowercases(self):
        assert normalize_content_type("IMAGE/PNG") == "image/png"


class TestDeriveAttachmentExtension:
    def test_prefers_filename_extension(self):
        assert derive_attachment_extension("report.pdf", "image/png") == "pdf"

    def test_uses_mime_when_filename_has_no_extension(self):
        assert derive_attachment_extension("Unnamed Attachment", "image/png") == "png"

    def test_returns_none_when_unresolvable(self):
        assert derive_attachment_extension("Unnamed Attachment", "application/x-custom") is None


class TestAttachmentMetadataFromGraph:
    def test_preserves_api_mime_type(self):
        file_name, mime_type, extension = attachment_metadata_from_graph(
            "logo.png",
            "image/png",
            "Unnamed Attachment",
        )

        assert file_name == "logo.png"
        assert mime_type == "image/png"
        assert extension == "png"

    def test_uses_default_name(self):
        file_name, mime_type, extension = attachment_metadata_from_graph(
            None,
            "application/pdf",
            "Unnamed Attachment",
        )

        assert file_name == "Unnamed Attachment"
        assert mime_type == "application/pdf"
        assert extension == "pdf"
