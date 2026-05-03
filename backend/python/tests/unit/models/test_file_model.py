"""Tests for app.models.file — FileMetadata and FileContent dataclasses."""

import pytest


class TestFileMetadata:
    def test_required_fields(self):
        from app.models.file import FileMetadata

        fm = FileMetadata(
            file_id="f1",
            name="test.pdf",
            mime_type="application/pdf",
            parents=["root"],
            modified_time="2025-01-01T00:00:00Z",
        )
        assert fm.file_id == "f1"
        assert fm.name == "test.pdf"
        assert fm.mime_type == "application/pdf"
        assert fm.parents == ["root"]
        assert fm.modified_time == "2025-01-01T00:00:00Z"

    def test_optional_fields_defaults(self):
        from app.models.file import FileMetadata

        fm = FileMetadata(
            file_id="f1",
            name="test.pdf",
            mime_type="application/pdf",
            parents=[],
            modified_time="2025-01-01T00:00:00Z",
        )
        assert fm.created_time is None
        assert fm.md5_checksum is None
        assert fm.description is None
        assert fm.starred is False
        assert fm.trashed is False
        assert fm.owners is None
        assert fm.last_modifying_user is None
        assert fm.permissions is None
        assert fm.access_control is None
        assert fm.lastUpdatedTimestampAtSource is None

    def test_all_fields_set(self):
        from app.models.file import FileMetadata

        fm = FileMetadata(
            file_id="f1",
            name="test.pdf",
            mime_type="application/pdf",
            parents=["root", "folder1"],
            modified_time="2025-01-01T00:00:00Z",
            created_time="2024-12-01T00:00:00Z",
            md5_checksum="abc123",
            description="A test file",
            starred=True,
            trashed=True,
            owners=[{"email": "user@test.com"}],
            last_modifying_user={"email": "editor@test.com"},
            permissions=[],
            access_control=None,
            lastUpdatedTimestampAtSource="2025-06-01T00:00:00Z",
        )
        assert fm.starred is True
        assert fm.trashed is True
        assert fm.md5_checksum == "abc123"
        assert fm.description == "A test file"
        assert fm.owners == [{"email": "user@test.com"}]
        assert fm.last_modifying_user == {"email": "editor@test.com"}

    def test_multiple_parents(self):
        from app.models.file import FileMetadata

        fm = FileMetadata(
            file_id="f1",
            name="shared.doc",
            mime_type="application/msword",
            parents=["root", "folder_a", "folder_b"],
            modified_time="2025-01-01",
        )
        assert len(fm.parents) == 3

    def test_empty_name(self):
        from app.models.file import FileMetadata

        fm = FileMetadata(
            file_id="f1",
            name="",
            mime_type="text/plain",
            parents=[],
            modified_time="2025-01-01",
        )
        assert fm.name == ""


class TestFileContent:
    def test_creation(self):
        from app.models.file import FileContent

        fc = FileContent(
            file_id="f1",
            content=b"hello world",
            version_time="2025-01-01T00:00:00Z",
        )
        assert fc.file_id == "f1"
        assert fc.content == b"hello world"
        assert fc.version_time == "2025-01-01T00:00:00Z"

    def test_empty_content(self):
        from app.models.file import FileContent

        fc = FileContent(file_id="f1", content=b"", version_time="2025-01-01")
        assert fc.content == b""

    def test_binary_content(self):
        from app.models.file import FileContent

        binary_data = bytes(range(256))
        fc = FileContent(file_id="f1", content=binary_data, version_time="2025-01-01")
        assert len(fc.content) == 256

    def test_large_content(self):
        from app.models.file import FileContent

        large_data = b"x" * (1024 * 1024)  # 1MB
        fc = FileContent(file_id="f1", content=large_data, version_time="2025-01-01")
        assert len(fc.content) == 1024 * 1024
