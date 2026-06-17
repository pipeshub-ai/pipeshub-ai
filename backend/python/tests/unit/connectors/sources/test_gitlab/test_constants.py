"""Unit tests for gitlab constants.

Covers:
- UPLOAD_PATTERN regex: valid/invalid GitLab upload markdown
- GITLAB_COMPARE_DIFF_LIMIT: expected value
- GITLAB_SEARCH_MIN_PARTIAL_CHARS: expected value
- _AUTH_ERROR_MARKERS: coverage of expected strings
- _should_skip_dotfile_repo_path: dot-file detection logic via repos module
- IMAGE_EXTENSIONS / PREVIEW_RENDERABLE_EXTENSIONS: membership checks
"""
from __future__ import annotations

import re

import pytest

from app.connectors.sources.gitlab.constants import (
    GITLAB_COMPARE_DIFF_LIMIT,
    GITLAB_SEARCH_MIN_PARTIAL_CHARS,
    IMAGE_EXTENSIONS,
    PREVIEW_RENDERABLE_EXTENSIONS,
    UPLOAD_PATTERN,
    _AUTH_ERROR_MARKERS,
)
from app.connectors.sources.gitlab.repos import _should_skip_dotfile_repo_path


# ===========================================================================
# UPLOAD_PATTERN regex
# ===========================================================================


class TestUploadPattern:
    def test_image_upload_full_match(self) -> None:
        text = "See ![screen](/uploads/a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4/screen.png) here"
        matches = list(UPLOAD_PATTERN.finditer(text))
        assert len(matches) == 1
        m = matches[0]
        assert m.group("filename") == "screen.png"
        assert "/uploads/" in m.group("href")

    def test_file_link_full_match(self) -> None:
        text = "[doc](/uploads/a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4/report.pdf)"
        matches = list(UPLOAD_PATTERN.finditer(text))
        assert len(matches) == 1

    def test_hash_must_be_32_chars(self) -> None:
        text = "![img](/uploads/abc/img.png)"  # hash too short
        matches = list(UPLOAD_PATTERN.finditer(text))
        assert len(matches) == 0

    def test_external_link_not_matched(self) -> None:
        text = "[click](https://example.com/file.pdf)"
        matches = list(UPLOAD_PATTERN.finditer(text))
        assert len(matches) == 0

    def test_multiple_uploads_in_text(self) -> None:
        hash_val = "a" * 32
        text = (
            f"See ![img1](/uploads/{hash_val}/1.png) "
            f"and [doc](/uploads/{hash_val}/file.pdf)"
        )
        matches = list(UPLOAD_PATTERN.finditer(text))
        assert len(matches) == 2

    def test_filename_with_spaces_not_matched(self) -> None:
        hash_val = "b" * 32
        text = f"![img](/uploads/{hash_val}/file with spaces.png)"
        matches = list(UPLOAD_PATTERN.finditer(text))
        assert len(matches) == 0

    def test_case_insensitive_uploads_path(self) -> None:
        hash_val = "c" * 32
        text = f"![img](/UPLOADS/{hash_val}/img.PNG)"
        # The regex uses re.IGNORECASE
        matches = list(UPLOAD_PATTERN.finditer(text))
        assert len(matches) == 1


# ===========================================================================
# _should_skip_dotfile_repo_path
# ===========================================================================


class TestShouldSkipDotfileRepoPath:
    def test_root_dotfile_skipped(self) -> None:
        assert _should_skip_dotfile_repo_path(".env") is True

    def test_nested_dotfile_skipped(self) -> None:
        assert _should_skip_dotfile_repo_path("src/.hidden") is True

    def test_deep_nested_dotfile_skipped(self) -> None:
        assert _should_skip_dotfile_repo_path("src/sub/.cache") is True

    def test_normal_file_not_skipped(self) -> None:
        assert _should_skip_dotfile_repo_path("src/main.py") is False

    def test_dotfile_in_component_not_skipped(self) -> None:
        # The component has a dot in its directory name (e.g., a.b/file.py)
        # Only the *filename* (last component) matters
        assert _should_skip_dotfile_repo_path("my.lib/file.py") is False

    def test_empty_string(self) -> None:
        # Empty path should not raise
        result = _should_skip_dotfile_repo_path("")
        assert isinstance(result, bool)


# ===========================================================================
# Numeric constants
# ===========================================================================


class TestNumericConstants:
    def test_compare_diff_limit_is_1000(self) -> None:
        assert GITLAB_COMPARE_DIFF_LIMIT == 1000

    def test_search_min_partial_chars_is_3(self) -> None:
        assert GITLAB_SEARCH_MIN_PARTIAL_CHARS == 3


# ===========================================================================
# _AUTH_ERROR_MARKERS
# ===========================================================================


class TestAuthErrorMarkers:
    def test_contains_401(self) -> None:
        assert "401" in _AUTH_ERROR_MARKERS

    def test_contains_unauthorized(self) -> None:
        assert "unauthorized" in _AUTH_ERROR_MARKERS

    def test_all_lowercase(self) -> None:
        for marker in _AUTH_ERROR_MARKERS:
            assert marker == marker.lower()


# ===========================================================================
# IMAGE_EXTENSIONS / PREVIEW_RENDERABLE_EXTENSIONS
# ===========================================================================


class TestExtensionSets:
    def test_common_image_extensions_present(self) -> None:
        for ext in ("png", "jpg", "jpeg", "gif", "svg"):
            assert ext in IMAGE_EXTENSIONS

    def test_image_extensions_are_lowercase(self) -> None:
        for ext in IMAGE_EXTENSIONS:
            assert ext == ext.lower()

    def test_preview_renderable_not_empty(self) -> None:
        assert len(PREVIEW_RENDERABLE_EXTENSIONS) > 0
