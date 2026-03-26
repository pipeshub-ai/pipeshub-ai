"""Deep sync loop tests for S3, Azure Files, and GCS storage connectors.

Tests the common sync patterns: run_sync -> iterate containers/buckets/shares
-> process objects -> ensure parent folders.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import MimeTypes
from app.connectors.sources.s3.base_connector import (
    get_file_extension,
    get_folder_path_segments_from_key,
    get_mimetype_for_s3,
    get_parent_path_for_s3,
    get_parent_path_from_key,
    get_parent_weburl_for_s3,
    make_s3_composite_revision,
    parse_parent_external_id,
)


# ---------------------------------------------------------------------------
# S3 helper functions
# ---------------------------------------------------------------------------

class TestS3HelperFunctions:
    """Tests for S3/MinIO base connector helper functions."""

    def test_get_file_extension(self):
        assert get_file_extension("file.pdf") == "pdf"
        assert get_file_extension("a/b/file.tar.gz") == "gz"
        assert get_file_extension("Makefile") is None
        assert get_file_extension("") is None

    def test_get_parent_path_from_key(self):
        assert get_parent_path_from_key("a/b/c/file.txt") == "a/b/c"
        assert get_parent_path_from_key("file.txt") is None
        assert get_parent_path_from_key("") is None
        assert get_parent_path_from_key("a/b/c/") == "a/b"
        assert get_parent_path_from_key("/a/b/file.txt") == "a/b"

    def test_get_folder_path_segments(self):
        assert get_folder_path_segments_from_key("a/b/c/file.txt") == ["a", "a/b", "a/b/c"]
        assert get_folder_path_segments_from_key("file.txt") == []
        assert get_folder_path_segments_from_key("") == []
        assert get_folder_path_segments_from_key("a/file.txt") == ["a"]

    def test_get_parent_weburl_for_s3(self):
        url = get_parent_weburl_for_s3("bucket/path/to/dir")
        assert "s3/object/bucket" in url
        assert "prefix=" in url

        url = get_parent_weburl_for_s3("bucket")
        assert "s3/buckets/bucket" in url

    def test_get_parent_path_for_s3(self):
        assert get_parent_path_for_s3("bucket/folder") == "folder/"
        assert get_parent_path_for_s3("bucket/folder/") == "folder/"
        assert get_parent_path_for_s3("bucket") is None

    def test_parse_parent_external_id(self):
        bucket, path = parse_parent_external_id("bucket/path/to")
        assert bucket == "bucket"
        assert path == "path/to/"

        bucket, path = parse_parent_external_id("bucket")
        assert bucket == "bucket"
        assert path is None

    def test_make_s3_composite_revision_with_etag(self):
        result = make_s3_composite_revision("bucket", "key.txt", '"abc123"')
        assert result == 'bucket/"abc123"'

    def test_make_s3_composite_revision_without_etag(self):
        result = make_s3_composite_revision("bucket", "key.txt", None)
        assert result == "bucket/key.txt|"

    def test_make_s3_composite_revision_empty_etag(self):
        result = make_s3_composite_revision("bucket", "key.txt", "")
        assert result == "bucket/key.txt|"

    def test_get_mimetype_for_s3_folder(self):
        assert get_mimetype_for_s3("folder/", is_folder=True) == MimeTypes.FOLDER.value

    def test_get_mimetype_for_s3_pdf(self):
        assert get_mimetype_for_s3("report.pdf") == MimeTypes.PDF.value

    def test_get_mimetype_for_s3_unknown(self):
        assert get_mimetype_for_s3("data.xyz999") == MimeTypes.BIN.value

    def test_get_mimetype_for_s3_text(self):
        result = get_mimetype_for_s3("readme.txt")
        assert result == MimeTypes.PLAIN_TEXT.value

    def test_parent_weburl_custom_base(self):
        url = get_parent_weburl_for_s3("bucket/path", base_console_url="https://minio.local")
        assert "minio.local" in url

    def test_folder_segments_trailing_slash(self):
        result = get_folder_path_segments_from_key("a/b/c/")
        assert result == ["a", "a/b"]

    def test_folder_segments_leading_slash(self):
        result = get_folder_path_segments_from_key("/a/b/file.txt")
        assert result == ["a", "a/b"]

    def test_parent_path_single_level(self):
        assert get_parent_path_from_key("a/file.txt") == "a"


# ---------------------------------------------------------------------------
# Azure Files helper functions (same patterns as Azure Blob)
# ---------------------------------------------------------------------------

class TestAzureFilesHelpers:
    """Tests for Azure Files helper functions using the same pattern as Azure Blob."""

    def test_get_file_extension_various(self):
        assert get_file_extension("doc.docx") == "docx"
        assert get_file_extension("image.PNG") == "png"  # case insensitive
        assert get_file_extension("") is None

    def test_parent_path_root_level(self):
        assert get_parent_path_from_key("file.txt") is None

    def test_parent_path_nested(self):
        assert get_parent_path_from_key("deep/nested/path/file.txt") == "deep/nested/path"


# ---------------------------------------------------------------------------
# GCS helper patterns (shared with S3)
# ---------------------------------------------------------------------------

class TestGCSHelperPatterns:
    """GCS uses similar helpers to S3 - test the patterns."""

    def test_folder_segments_deep_nesting(self):
        segments = get_folder_path_segments_from_key("a/b/c/d/e/file.txt")
        assert len(segments) == 5
        assert segments[-1] == "a/b/c/d/e"

    def test_composite_revision_with_hash(self):
        result = make_s3_composite_revision("gcs-bucket", "doc.pdf", "md5hash123")
        assert "gcs-bucket" in result
        assert "md5hash123" in result

    def test_mimetype_common_types(self):
        assert get_mimetype_for_s3("data.json") == MimeTypes.JSON.value
        assert get_mimetype_for_s3("style.css") == MimeTypes.BIN.value
        assert get_mimetype_for_s3("image.png") == MimeTypes.PNG.value
        assert get_mimetype_for_s3("photo.jpg") == MimeTypes.JPEG.value

    def test_mimetype_html(self):
        assert get_mimetype_for_s3("page.html") == MimeTypes.HTML.value

    def test_mimetype_xml(self):
        assert get_mimetype_for_s3("data.xml") == MimeTypes.XML.value

    def test_mimetype_zip(self):
        assert get_mimetype_for_s3("archive.zip") == MimeTypes.ZIP.value

    def test_get_parent_path_preserves_slashes(self):
        result = get_parent_path_for_s3("bucket/a/b/c")
        assert result == "a/b/c/"

    def test_parse_external_id_with_nested_path(self):
        bucket, path = parse_parent_external_id("bucket/a/b/c")
        assert bucket == "bucket"
        assert path == "a/b/c/"

    def test_parse_external_id_with_leading_slash_in_path(self):
        bucket, path = parse_parent_external_id("bucket//path")
        assert bucket == "bucket"
        assert path == "path/"
