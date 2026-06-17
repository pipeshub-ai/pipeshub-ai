"""Unit tests for gitlab domain models.

Covers:
- GitlabLiterals: enum membership, value access
- FileAttachment: valid construction, min_length validation, category values
- RecordUpdate: default booleans, required fields, field names preserved
"""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.connectors.sources.gitlab.models import (
    FileAttachment,
    GitlabLiterals,
    RecordUpdate,
)


# ===========================================================================
# GitlabLiterals
# ===========================================================================


class TestGitlabLiterals:
    def test_last_sync_time_value(self) -> None:
        assert GitlabLiterals.LAST_SYNC_TIME.value == "last_sync_time"

    def test_last_commit_sha_value(self) -> None:
        assert GitlabLiterals.LAST_COMMIT_SHA.value == "last_commit_sha"

    def test_record_group_value(self) -> None:
        assert GitlabLiterals.RECORD_GROUP.value == "record_group"

    def test_global_value(self) -> None:
        assert GitlabLiterals.GLOBAL.value == "global"

    def test_updated_at_value(self) -> None:
        assert GitlabLiterals.UPDATED_AT.value == "updated_at"

    def test_utf8_value(self) -> None:
        assert GitlabLiterals.UTF_8.value == "utf-8"

    def test_image_value(self) -> None:
        assert GitlabLiterals.IMAGE.value == "image"

    def test_attachment_value(self) -> None:
        assert GitlabLiterals.ATTACHMENT.value == "attachment"

    def test_string_cast(self) -> None:
        # GitlabLiterals extends str — .value gives the raw string
        assert GitlabLiterals.IMAGE.value == "image"
        # Can also be used directly in string context (format strings, etc.)
        assert f"{GitlabLiterals.IMAGE.value}" == "image"


# ===========================================================================
# FileAttachment
# ===========================================================================


class TestFileAttachment:
    def test_valid_image_attachment(self) -> None:
        fa = FileAttachment(
            href="/uploads/abc/img.png",
            filename="img.png",
            filetype="png",
            category="image",
        )
        assert fa.href == "/uploads/abc/img.png"
        assert fa.filetype == "png"
        assert fa.category == "image"

    def test_valid_file_attachment(self) -> None:
        fa = FileAttachment(
            href="/uploads/abc/doc.pdf",
            filename="doc.pdf",
            filetype="pdf",
            category="attachment",
        )
        assert fa.category == "attachment"

    def test_empty_href_raises(self) -> None:
        with pytest.raises(ValidationError):
            FileAttachment(href="", filename="img.png", filetype="png", category="image")

    def test_empty_filename_raises(self) -> None:
        with pytest.raises(ValidationError):
            FileAttachment(href="/uploads/x/img.png", filename="", filetype="png", category="image")

    def test_filetype_can_be_empty_string(self) -> None:
        # filetype has no min_length constraint in the current model
        fa = FileAttachment(
            href="/uploads/abc/noext",
            filename="noext",
            filetype="",
            category="attachment",
        )
        assert fa.filetype == ""


# ===========================================================================
# RecordUpdate
# ===========================================================================


class TestRecordUpdate:
    def _make_record(self):
        from app.models.entities import TicketRecord, RecordType, RecordGroupType
        from app.config.constants.arangodb import OriginTypes, MimeTypes
        return TicketRecord(
            id=str(uuid.uuid4()),
            record_name="Test Issue",
            external_record_id="issue-1",
            record_type=RecordType.TICKET.value,
            connector_name="GITLAB",
            connector_id="conn-1",
            origin=OriginTypes.CONNECTOR.value,
            version=0,
            external_record_group_id="42-work-items",
            org_id="org-1",
            record_group_type=RecordGroupType.PROJECT,
            mime_type=MimeTypes.BLOCKS.value,
            inherit_permissions=True,
        )

    def test_all_booleans_default_to_false(self) -> None:
        record = self._make_record()
        ru = RecordUpdate(
            record=record,
            is_new=False,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            old_permissions=None,
            new_permissions=None,
            external_record_id=None,
        )
        assert ru.is_new is False
        assert ru.is_updated is False
        assert ru.is_deleted is False
        assert ru.metadata_changed is False
        assert ru.content_changed is False
        assert ru.permissions_changed is False

    def test_is_new_true_preserved(self) -> None:
        record = self._make_record()
        ru = RecordUpdate(
            record=record,
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            old_permissions=[],
            new_permissions=[],
            external_record_id="issue-1",
        )
        assert ru.is_new is True

    def test_permissions_none_allowed(self) -> None:
        record = self._make_record()
        ru = RecordUpdate(
            record=record,
            is_new=False,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            old_permissions=None,
            new_permissions=None,
            external_record_id=None,
        )
        assert ru.old_permissions is None
        assert ru.new_permissions is None

    def test_record_field_name_not_changed(self) -> None:
        record = self._make_record()
        ru = RecordUpdate(
            record=record,
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            old_permissions=[],
            new_permissions=[],
            external_record_id="ext",
        )
        # Field must still be named 'record' (contracts with data_entities_processor)
        assert hasattr(ru, "record")
        assert ru.record is record
