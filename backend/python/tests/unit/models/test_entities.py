"""Tests for entities module: Record, TicketRecord, ProjectRecord, FileRecord, MailRecord, LinkRecord, ProductRecord, DealRecord."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.models.blocks import Block, BlockGroup, BlocksContainer, BlockType, GroupType, BlockGroupChildren, IndexRange
from app.models.entities import (
    DealRecord,
    FileRecord,
    LinkPublicStatus,
    LinkRecord,
    LlmTextContent,
    MailRecord,
    ProductRecord,
    ProjectRecord,
    Record,
    RecordType,
    TicketRecord,
)


def _record_kwargs(**overrides):
    """Provide default keyword args for creating a Record."""
    defaults = {
        "record_name": "Test Record",
        "record_type": RecordType.FILE,
        "external_record_id": "ext-123",
        "version": 1,
        "origin": OriginTypes.CONNECTOR,
        "connector_name": Connectors.GOOGLE_DRIVE,
        "connector_id": "conn-456",
    }
    defaults.update(overrides)
    return defaults


# ============================================================================
# Record tests
# ============================================================================


class TestRecord:
    def test_minimal_creation(self):
        rec = Record(**_record_kwargs())
        assert rec.record_name == "Test Record"
        assert rec.record_type == RecordType.FILE
        assert rec.external_record_id == "ext-123"
        assert rec.version == 1
        assert rec.origin == OriginTypes.CONNECTOR
        assert rec.connector_name == Connectors.GOOGLE_DRIVE
        assert rec.connector_id == "conn-456"

    def test_default_values(self):
        rec = Record(**_record_kwargs())
        assert rec.org_id == ""
        assert rec.record_status == ProgressStatus.NOT_STARTED
        assert rec.parent_record_type is None
        assert rec.record_group_type is None
        assert rec.external_revision_id is None
        assert rec.mime_type == MimeTypes.UNKNOWN.value
        assert rec.inherit_permissions is True
        assert rec.indexing_status == ProgressStatus.QUEUED.value
        assert rec.extraction_status == ProgressStatus.NOT_STARTED.value
        assert rec.reason is None
        assert rec.weburl is None
        assert rec.signed_url is None
        assert rec.preview_renderable is True
        assert rec.is_shared is False
        assert rec.is_internal is False
        assert rec.hide_weburl is False
        assert rec.is_vlm_ocr_processed is False
        assert rec.is_dependent_node is False
        assert rec.parent_node_id is None
        assert rec.child_record_ids == []
        assert rec.related_record_ids == []

    def test_id_auto_generated(self):
        rec1 = Record(**_record_kwargs())
        rec2 = Record(**_record_kwargs())
        assert rec1.id != rec2.id

    def test_id_explicit(self):
        rec = Record(**_record_kwargs(id="custom-id"))
        assert rec.id == "custom-id"

    def test_timestamps_set(self):
        rec = Record(**_record_kwargs())
        assert isinstance(rec.created_at, int)
        assert isinstance(rec.updated_at, int)
        assert rec.created_at > 0
        assert rec.updated_at > 0

    def test_format_timestamp_none(self):
        rec = Record(**_record_kwargs())
        assert rec._format_timestamp(None) == "N/A"

    def test_format_timestamp_valid(self):
        rec = Record(**_record_kwargs())
        # 2024-01-01 00:00:00 UTC = 1704067200000 ms
        result = rec._format_timestamp(1704067200000)
        assert "2024-01-01" in result
        assert "UTC" in result

    def test_format_person_name_and_email(self):
        rec = Record(**_record_kwargs())
        assert rec._format_person("John", "john@test.com") == "John (john@test.com)"

    def test_format_person_name_only(self):
        rec = Record(**_record_kwargs())
        assert rec._format_person("John", None) == "John"

    def test_format_person_email_only(self):
        rec = Record(**_record_kwargs())
        assert rec._format_person(None, "john@test.com") == "john@test.com"

    def test_format_person_neither(self):
        rec = Record(**_record_kwargs())
        assert rec._format_person(None, None) == "N/A"

    def test_to_llm_context(self):
        rec = Record(**_record_kwargs(
            id="rec-1",
            weburl="https://example.com/doc",
            source_created_at=1704067200000,
            source_updated_at=1704153600000,
        ))
        ctx = rec.to_llm_context()
        assert "rec-1" in ctx
        assert "Test Record" in ctx
        assert "DRIVE" in ctx  # connector_name.value
        assert "FILE" in ctx  # record_type.value
        assert "https://example.com/doc" in ctx

    def test_to_llm_context_with_frontend_url_prefix(self):
        rec = Record(**_record_kwargs(weburl="/internal/doc"))
        ctx = rec.to_llm_context(frontend_url="https://app.example.com")
        assert "https://app.example.com/internal/doc" in ctx

    def test_to_llm_context_with_semantic_metadata(self):
        from app.models.blocks import SemanticMetadata

        meta = SemanticMetadata(summary="Test summary")
        rec = Record(**_record_kwargs(semantic_metadata=meta))
        ctx = rec.to_llm_context()
        assert "Test summary" in ctx

    def test_to_arango_base_record(self):
        rec = Record(**_record_kwargs(
            id="rec-1",
            org_id="org-1",
            weburl="https://example.com",
        ))
        arango = rec.to_arango_base_record()
        assert arango["_key"] == "rec-1"
        assert arango["orgId"] == "org-1"
        assert arango["recordName"] == "Test Record"
        assert arango["recordType"] == "FILE"
        assert arango["externalRecordId"] == "ext-123"
        assert arango["version"] == 1
        assert arango["origin"] == "CONNECTOR"
        assert arango["connectorName"] == "DRIVE"
        assert arango["webUrl"] == "https://example.com"
        assert arango["isDeleted"] is False
        assert arango["isArchived"] is False

    def test_from_arango_base_record(self):
        arango_doc = {
            "_key": "rec-1",
            "orgId": "org-1",
            "recordName": "Test Record",
            "recordType": "FILE",
            "externalRecordId": "ext-123",
            "version": 1,
            "origin": "CONNECTOR",
            "connectorName": "DRIVE",
            "connectorId": "conn-1",
            "mimeType": "application/pdf",
            "webUrl": "https://example.com",
            "createdAtTimestamp": 1704067200000,
            "updatedAtTimestamp": 1704153600000,
            "sourceCreatedAtTimestamp": None,
            "sourceLastModifiedTimestamp": None,
            "indexingStatus": "QUEUED",
            "extractionStatus": "NOT_STARTED",
            "previewRenderable": True,
        }
        rec = Record.from_arango_base_record(arango_doc)
        assert rec.id == "rec-1"
        assert rec.org_id == "org-1"
        assert rec.record_name == "Test Record"
        assert rec.record_type == RecordType.FILE
        assert rec.connector_name == Connectors.GOOGLE_DRIVE

    def test_from_arango_base_record_unknown_connector(self):
        """Unknown connector name should fall back to KNOWLEDGE_BASE."""
        arango_doc = {
            "_key": "rec-1",
            "orgId": "org-1",
            "recordName": "Test",
            "recordType": "FILE",
            "externalRecordId": "ext-1",
            "version": 1,
            "origin": "CONNECTOR",
            "connectorName": "NONEXISTENT_CONNECTOR",
            "connectorId": "conn-1",
            "createdAtTimestamp": 1704067200000,
            "updatedAtTimestamp": 1704067200000,
        }
        rec = Record.from_arango_base_record(arango_doc)
        assert rec.connector_name == Connectors.KNOWLEDGE_BASE

    def test_from_arango_base_record_missing_connector(self):
        """Missing connectorName should fall back to KNOWLEDGE_BASE."""
        arango_doc = {
            "_key": "rec-1",
            "orgId": "org-1",
            "recordName": "Test",
            "recordType": "FILE",
            "externalRecordId": "ext-1",
            "version": 1,
            "origin": "UPLOAD",
            "connectorId": "conn-1",
            "createdAtTimestamp": 1704067200000,
            "updatedAtTimestamp": 1704067200000,
        }
        rec = Record.from_arango_base_record(arango_doc)
        assert rec.connector_name == Connectors.KNOWLEDGE_BASE

    def test_to_kafka_record_raises_not_implemented(self):
        rec = Record(**_record_kwargs())
        with pytest.raises(NotImplementedError):
            rec.to_kafka_record()


# ============================================================================
# FileRecord tests
# ============================================================================


class TestFileRecord:
    def test_creation(self):
        rec = FileRecord(**_record_kwargs(is_file=True, extension="pdf", path="/docs/test.pdf"))
        assert rec.is_file is True
        assert rec.extension == "pdf"
        assert rec.path == "/docs/test.pdf"

    def test_default_hash_fields(self):
        rec = FileRecord(**_record_kwargs(is_file=True))
        assert rec.etag is None
        assert rec.ctag is None
        assert rec.quick_xor_hash is None
        assert rec.crc32_hash is None
        assert rec.sha1_hash is None
        assert rec.sha256_hash is None

    def test_to_llm_context_with_extension(self):
        rec = FileRecord(**_record_kwargs(is_file=True, extension="pdf"))
        ctx = rec.to_llm_context()
        assert "Extension" in ctx
        assert "pdf" in ctx

    def test_to_llm_context_without_extension(self):
        rec = FileRecord(**_record_kwargs(is_file=True))
        ctx = rec.to_llm_context()
        assert "Extension" not in ctx

    def test_to_arango_record(self):
        rec = FileRecord(**_record_kwargs(
            id="file-1",
            org_id="org-1",
            is_file=True,
            extension="pdf",
            path="/docs/test.pdf",
        ))
        arango = rec.to_arango_record()
        assert arango["_key"] == "file-1"
        assert arango["isFile"] is True
        assert arango["extension"] == "pdf"
        assert arango["path"] == "/docs/test.pdf"

    def test_to_kafka_record(self):
        rec = FileRecord(**_record_kwargs(
            id="file-1",
            org_id="org-1",
            is_file=True,
            extension="pdf",
        ))
        kafka = rec.to_kafka_record()
        assert kafka["recordId"] == "file-1"
        assert kafka["extension"] == "pdf"
        assert kafka["isFile"] is True


# ============================================================================
# MailRecord tests
# ============================================================================


class TestMailRecord:
    def test_creation(self):
        rec = MailRecord(
            **_record_kwargs(
                record_type=RecordType.MAIL,
                connector_name=Connectors.GOOGLE_MAIL,
                subject="Test Email",
                from_email="sender@test.com",
                to_emails=["recip@test.com"],
            )
        )
        assert rec.subject == "Test Email"
        assert rec.from_email == "sender@test.com"
        assert rec.to_emails == ["recip@test.com"]

    def test_default_fields(self):
        rec = MailRecord(**_record_kwargs(
            record_type=RecordType.MAIL,
            connector_name=Connectors.GOOGLE_MAIL,
        ))
        assert rec.subject is None
        assert rec.from_email is None
        assert rec.to_emails is None
        assert rec.cc_emails is None
        assert rec.bcc_emails is None
        assert rec.thread_id is None
        assert rec.is_parent is False
        assert rec.internet_message_id is None
        assert rec.label_ids is None

    def test_to_llm_context_with_email_fields(self):
        rec = MailRecord(**_record_kwargs(
            record_type=RecordType.MAIL,
            connector_name=Connectors.GOOGLE_MAIL,
            subject="Important",
            from_email="sender@test.com",
            to_emails=["recip1@test.com", "recip2@test.com"],
            cc_emails=["cc@test.com"],
            bcc_emails=["bcc@test.com"],
        ))
        ctx = rec.to_llm_context()
        assert "Subject" in ctx
        assert "Important" in ctx
        assert "From" in ctx
        assert "sender@test.com" in ctx
        assert "To" in ctx
        assert "CC" in ctx
        assert "BCC" in ctx

    def test_to_llm_context_without_email_fields(self):
        rec = MailRecord(**_record_kwargs(
            record_type=RecordType.MAIL,
            connector_name=Connectors.GOOGLE_MAIL,
        ))
        ctx = rec.to_llm_context()
        assert "Subject" not in ctx
        assert "Email Information" not in ctx

    def test_to_arango_record(self):
        rec = MailRecord(**_record_kwargs(
            id="mail-1",
            record_type=RecordType.MAIL,
            connector_name=Connectors.GOOGLE_MAIL,
            subject="Test",
            from_email="sender@test.com",
            to_emails=["recip@test.com"],
            thread_id="thread-1",
            is_parent=True,
        ))
        arango = rec.to_arango_record()
        assert arango["_key"] == "mail-1"
        assert arango["subject"] == "Test"
        assert arango["from"] == "sender@test.com"
        assert arango["to"] == ["recip@test.com"]
        assert arango["threadId"] == "thread-1"
        assert arango["isParent"] is True

    def test_to_kafka_record(self):
        rec = MailRecord(**_record_kwargs(
            id="mail-1",
            org_id="org-1",
            record_type=RecordType.MAIL,
            connector_name=Connectors.GOOGLE_MAIL,
            subject="Test",
        ))
        kafka = rec.to_kafka_record()
        assert kafka["recordId"] == "mail-1"
        assert kafka["subject"] == "Test"


# ============================================================================
# TicketRecord tests
# ============================================================================


class TestTicketRecord:
    def test_creation(self):
        rec = TicketRecord(**_record_kwargs(
            record_type=RecordType.TICKET,
            connector_name=Connectors.JIRA,
            status="IN_PROGRESS",
            priority="HIGH",
            type="BUG",
            assignee="John Doe",
        ))
        assert rec.status == "IN_PROGRESS"
        assert rec.priority == "HIGH"
        assert rec.type == "BUG"
        assert rec.assignee == "John Doe"

    def test_default_fields(self):
        rec = TicketRecord(**_record_kwargs(
            record_type=RecordType.TICKET,
            connector_name=Connectors.JIRA,
        ))
        assert rec.status is None
        assert rec.priority is None
        assert rec.type is None
        assert rec.delivery_status is None
        assert rec.assignee is None
        assert rec.reporter_email is None
        assert rec.assignee_email is None
        assert rec.reporter_name is None
        assert rec.labels == []
        assert rec.is_email_hidden is False

    def test_to_llm_context_with_fields(self):
        rec = TicketRecord(**_record_kwargs(
            record_type=RecordType.TICKET,
            connector_name=Connectors.JIRA,
            status="IN_PROGRESS",
            priority="HIGH",
            type="BUG",
            assignee="John",
            assignee_email="john@test.com",
            reporter_name="Jane",
            reporter_email="jane@test.com",
            creator_name="Admin",
            creator_email="admin@test.com",
            delivery_status="ON_TRACK",
        ))
        ctx = rec.to_llm_context()
        assert "Status" in ctx
        assert "Priority" in ctx
        assert "Type" in ctx
        assert "Assignee" in ctx
        assert "Reporter" in ctx
        assert "Creator" in ctx
        assert "Delivery Status" in ctx

    def test_to_arango_record(self):
        rec = TicketRecord(**_record_kwargs(
            id="ticket-1",
            org_id="org-1",
            record_type=RecordType.TICKET,
            connector_name=Connectors.JIRA,
            status="OPEN",
            priority="MEDIUM",
            labels=["bug", "critical"],
        ))
        arango = rec.to_arango_record()
        assert arango["_key"] == "ticket-1"
        assert arango["status"] == "OPEN"
        assert arango["priority"] == "MEDIUM"
        assert arango["labels"] == ["bug", "critical"]

    def test_to_kafka_record(self):
        rec = TicketRecord(**_record_kwargs(
            id="ticket-1",
            org_id="org-1",
            record_type=RecordType.TICKET,
            connector_name=Connectors.JIRA,
        ))
        kafka = rec.to_kafka_record()
        assert kafka["recordId"] == "ticket-1"
        assert kafka["recordType"] == "TICKET"

    def test_safe_enum_parse_valid(self):
        from app.models.entities import Status

        result = TicketRecord._safe_enum_parse("OPEN", Status)
        assert result == Status.OPEN

    def test_safe_enum_parse_case_insensitive(self):
        from app.models.entities import Priority

        result = TicketRecord._safe_enum_parse("high", Priority)
        assert result == Priority.HIGH

    def test_safe_enum_parse_unknown_returns_original_string(self):
        from app.models.entities import Status

        result = TicketRecord._safe_enum_parse("custom_status", Status)
        assert result == "custom_status"

    def test_safe_enum_parse_none_returns_none(self):
        from app.models.entities import Status

        result = TicketRecord._safe_enum_parse(None, Status)
        assert result is None

    def test_safe_enum_parse_empty_string_returns_none(self):
        from app.models.entities import Status

        result = TicketRecord._safe_enum_parse("", Status)
        assert result is None


# ============================================================================
# ProjectRecord tests
# ============================================================================


class TestProjectRecord:
    def test_creation(self):
        rec = ProjectRecord(**_record_kwargs(
            record_type=RecordType.PROJECT,
            connector_name=Connectors.JIRA,
            status="Active",
            priority="High",
            lead_name="Jane",
            lead_email="jane@test.com",
        ))
        assert rec.status == "Active"
        assert rec.priority == "High"
        assert rec.lead_name == "Jane"
        assert rec.lead_email == "jane@test.com"

    def test_default_fields(self):
        rec = ProjectRecord(**_record_kwargs(
            record_type=RecordType.PROJECT,
            connector_name=Connectors.JIRA,
        ))
        assert rec.status is None
        assert rec.priority is None
        assert rec.lead_id is None
        assert rec.lead_name is None
        assert rec.lead_email is None

    def test_to_llm_context_with_fields(self):
        rec = ProjectRecord(**_record_kwargs(
            record_type=RecordType.PROJECT,
            connector_name=Connectors.JIRA,
            status="Active",
            priority="High",
            lead_name="Jane",
            lead_email="jane@test.com",
        ))
        ctx = rec.to_llm_context()
        assert "Status" in ctx
        assert "Active" in ctx
        assert "Priority" in ctx
        assert "Lead" in ctx
        assert "Jane" in ctx

    def test_to_llm_context_no_fields(self):
        rec = ProjectRecord(**_record_kwargs(
            record_type=RecordType.PROJECT,
            connector_name=Connectors.JIRA,
        ))
        ctx = rec.to_llm_context()
        assert "Project Information" not in ctx

    def test_to_arango_record(self):
        rec = ProjectRecord(**_record_kwargs(
            id="proj-1",
            org_id="org-1",
            record_type=RecordType.PROJECT,
            connector_name=Connectors.JIRA,
            status="Active",
            lead_name="Jane",
        ))
        arango = rec.to_arango_record()
        assert arango["_key"] == "proj-1"
        assert arango["status"] == "Active"
        assert arango["leadName"] == "Jane"

    def test_to_kafka_record(self):
        rec = ProjectRecord(**_record_kwargs(
            id="proj-1",
            org_id="org-1",
            record_type=RecordType.PROJECT,
            connector_name=Connectors.JIRA,
        ))
        kafka = rec.to_kafka_record()
        assert kafka["recordId"] == "proj-1"
        assert kafka["recordType"] == "PROJECT"


# ============================================================================
# LinkRecord tests
# ============================================================================


class TestLinkRecord:
    def test_creation(self):
        rec = LinkRecord(**_record_kwargs(
            record_type=RecordType.LINK,
            url="https://example.com",
            is_public=LinkPublicStatus.TRUE,
        ))
        assert rec.url == "https://example.com"
        assert rec.is_public == LinkPublicStatus.TRUE

    def test_default_fields(self):
        rec = LinkRecord(**_record_kwargs(
            record_type=RecordType.LINK,
            url="https://example.com",
            is_public=LinkPublicStatus.UNKNOWN,
        ))
        assert rec.title is None
        assert rec.linked_record_id is None

    def test_to_llm_context_with_fields(self):
        rec = LinkRecord(**_record_kwargs(
            record_type=RecordType.LINK,
            url="https://example.com",
            title="Example Link",
            is_public=LinkPublicStatus.TRUE,
            linked_record_id="rec-linked-1",
        ))
        ctx = rec.to_llm_context()
        assert "URL" in ctx
        assert "https://example.com" in ctx
        assert "Title" in ctx
        assert "Example Link" in ctx
        assert "Public Access" in ctx
        assert "Linked Record ID" in ctx

    def test_to_arango_record(self):
        rec = LinkRecord(**_record_kwargs(
            id="link-1",
            org_id="org-1",
            record_type=RecordType.LINK,
            url="https://example.com",
            title="Test Link",
            is_public=LinkPublicStatus.FALSE,
            linked_record_id="linked-1",
        ))
        arango = rec.to_arango_record()
        assert arango["_key"] == "link-1"
        assert arango["url"] == "https://example.com"
        assert arango["title"] == "Test Link"
        assert arango["isPublic"] == "false"
        assert arango["linkedRecordId"] == "linked-1"

    def test_to_kafka_record(self):
        rec = LinkRecord(**_record_kwargs(
            id="link-1",
            org_id="org-1",
            record_type=RecordType.LINK,
            url="https://example.com",
            is_public=LinkPublicStatus.UNKNOWN,
        ))
        kafka = rec.to_kafka_record()
        assert kafka["recordId"] == "link-1"
        assert kafka["recordType"] == "LINK"


# ============================================================================
# LinkPublicStatus enum tests
# ============================================================================


class TestLinkPublicStatus:
    def test_true_value(self):
        assert LinkPublicStatus.TRUE.value == "true"

    def test_false_value(self):
        assert LinkPublicStatus.FALSE.value == "false"

    def test_unknown_value(self):
        assert LinkPublicStatus.UNKNOWN.value == "unknown"


# ============================================================================
# FileRecord.from_arango_record tests (lines 384-390)
# ============================================================================


class TestFileRecordFromArango:
    def _arango_base(self, **overrides):
        defaults = {
            "_key": "file-1",
            "orgId": "org-1",
            "recordName": "Test File",
            "recordType": "FILE",
            "externalRecordId": "ext-1",
            "version": 1,
            "origin": "CONNECTOR",
            "connectorName": "DRIVE",
            "connectorId": "conn-1",
            "mimeType": "application/pdf",
            "webUrl": "https://example.com/file",
            "createdAtTimestamp": 1704067200000,
            "updatedAtTimestamp": 1704153600000,
            "sourceCreatedAtTimestamp": 1704067200000,
            "sourceLastModifiedTimestamp": 1704153600000,
        }
        defaults.update(overrides)
        return defaults

    def _arango_file(self, **overrides):
        defaults = {
            "isFile": True,
            "sizeInBytes": 1024,
            "extension": "pdf",
            "path": "/docs/test.pdf",
        }
        defaults.update(overrides)
        return defaults

    def test_from_arango_record_basic(self):
        rec = FileRecord.from_arango_record(self._arango_file(), self._arango_base())
        assert rec.id == "file-1"
        assert rec.org_id == "org-1"
        assert rec.is_file is True
        assert rec.extension == "pdf"
        assert rec.path == "/docs/test.pdf"
        assert rec.connector_name == Connectors.GOOGLE_DRIVE

    def test_from_arango_record_unknown_connector(self):
        rec = FileRecord.from_arango_record(
            self._arango_file(),
            self._arango_base(connectorName="NONEXISTENT"),
        )
        assert rec.connector_name == Connectors.KNOWLEDGE_BASE

    def test_from_arango_record_missing_connector(self):
        base = self._arango_base()
        del base["connectorName"]
        rec = FileRecord.from_arango_record(self._arango_file(), base)
        assert rec.connector_name == Connectors.KNOWLEDGE_BASE

    def test_from_arango_record_with_hashes(self):
        rec = FileRecord.from_arango_record(
            self._arango_file(
                etag="etag-val",
                ctag="ctag-val",
                quickXorHash="qxor",
                crc32Hash="crc",
                sha1Hash="sha1",
                sha256Hash="sha256",
            ),
            self._arango_base(),
        )
        assert rec.etag == "etag-val"
        assert rec.ctag == "ctag-val"
        assert rec.quick_xor_hash == "qxor"
        assert rec.crc32_hash == "crc"
        assert rec.sha1_hash == "sha1"
        assert rec.sha256_hash == "sha256"

    def test_from_arango_record_size_from_base_record(self):
        """sizeInBytes from base record should take precedence."""
        rec = FileRecord.from_arango_record(
            self._arango_file(sizeInBytes=500),
            self._arango_base(sizeInBytes=2048),
        )
        assert rec.size_in_bytes == 2048


# ============================================================================
# MailRecord.from_arango_record tests (lines 539-545)
# ============================================================================


class TestMailRecordFromArango:
    def _record_doc(self, **overrides):
        defaults = {
            "_key": "mail-1",
            "orgId": "org-1",
            "recordName": "Test Mail",
            "recordType": "MAIL",
            "externalRecordId": "ext-mail-1",
            "version": 1,
            "origin": "CONNECTOR",
            "connectorName": "GMAIL",
            "connectorId": "conn-1",
            "mimeType": "message/rfc822",
            "createdAtTimestamp": 1704067200000,
            "updatedAtTimestamp": 1704153600000,
            "sourceCreatedAtTimestamp": 1704067200000,
            "sourceLastModifiedTimestamp": 1704153600000,
        }
        defaults.update(overrides)
        return defaults

    def _mail_doc(self, **overrides):
        defaults = {
            "subject": "Test Subject",
            "from": "sender@test.com",
            "to": ["recip@test.com"],
            "cc": ["cc@test.com"],
            "bcc": [],
            "threadId": "thread-1",
            "isParent": True,
            "messageIdHeader": "msg-id-1",
            "labelIds": ["INBOX"],
        }
        defaults.update(overrides)
        return defaults

    def test_from_arango_record_basic(self):
        rec = MailRecord.from_arango_record(self._mail_doc(), self._record_doc())
        assert rec.id == "mail-1"
        assert rec.subject == "Test Subject"
        assert rec.from_email == "sender@test.com"
        assert rec.to_emails == ["recip@test.com"]
        assert rec.cc_emails == ["cc@test.com"]
        assert rec.thread_id == "thread-1"
        assert rec.is_parent is True
        assert rec.internet_message_id == "msg-id-1"
        assert rec.label_ids == ["INBOX"]

    def test_from_arango_record_unknown_connector(self):
        rec = MailRecord.from_arango_record(
            self._mail_doc(),
            self._record_doc(connectorName="NONEXISTENT"),
        )
        assert rec.connector_name == Connectors.KNOWLEDGE_BASE

    def test_from_arango_record_missing_connector(self):
        doc = self._record_doc()
        del doc["connectorName"]
        rec = MailRecord.from_arango_record(self._mail_doc(), doc)
        assert rec.connector_name == Connectors.KNOWLEDGE_BASE


# ============================================================================
# WebpageRecord tests (lines 580, 596, 604-610)
# ============================================================================


class TestWebpageRecord:
    def _record_doc(self, **overrides):
        defaults = {
            "_key": "web-1",
            "orgId": "org-1",
            "recordName": "Test Webpage",
            "recordType": "WEBPAGE",
            "externalRecordId": "ext-web-1",
            "version": 1,
            "origin": "CONNECTOR",
            "connectorName": "WEB",
            "connectorId": "conn-1",
            "mimeType": "text/html",
            "webUrl": "https://example.com",
            "createdAtTimestamp": 1704067200000,
            "updatedAtTimestamp": 1704153600000,
            "sourceCreatedAtTimestamp": 1704067200000,
            "sourceLastModifiedTimestamp": 1704153600000,
        }
        defaults.update(overrides)
        return defaults

    def test_to_kafka_record(self):
        from app.models.entities import WebpageRecord
        rec = WebpageRecord(**_record_kwargs(
            id="web-1",
            org_id="org-1",
            record_type=RecordType.WEBPAGE,
        ))
        kafka = rec.to_kafka_record()
        assert kafka["recordId"] == "web-1"
        assert kafka["orgId"] == "org-1"
        assert kafka["recordType"] == "WEBPAGE"
        assert "signedUrl" in kafka

    def test_to_arango_record(self):
        from app.models.entities import WebpageRecord
        rec = WebpageRecord(**_record_kwargs(
            id="web-1",
            org_id="org-1",
            record_type=RecordType.WEBPAGE,
        ))
        arango = rec.to_arango_record()
        assert arango["_key"] == "web-1"
        assert arango["orgId"] == "org-1"

    def test_from_arango_record(self):
        from app.models.entities import WebpageRecord
        rec = WebpageRecord.from_arango_record({}, self._record_doc())
        assert rec.id == "web-1"
        assert rec.record_type == RecordType.WEBPAGE

    def test_from_arango_record_unknown_connector(self):
        from app.models.entities import WebpageRecord
        rec = WebpageRecord.from_arango_record(
            {},
            self._record_doc(connectorName="NONEXISTENT"),
        )
        assert rec.connector_name == Connectors.KNOWLEDGE_BASE


# ============================================================================
# LinkRecord.from_arango_record tests (lines 703-709)
# ============================================================================


class TestLinkRecordFromArango:
    def _record_doc(self, **overrides):
        defaults = {
            "_key": "link-1",
            "orgId": "org-1",
            "recordName": "Test Link",
            "recordType": "LINK",
            "externalRecordId": "ext-link-1",
            "version": 1,
            "origin": "CONNECTOR",
            "connectorName": "CONFLUENCE",
            "connectorId": "conn-1",
            "mimeType": "text/html",
            "webUrl": "https://example.com",
            "createdAtTimestamp": 1704067200000,
            "updatedAtTimestamp": 1704153600000,
            "sourceCreatedAtTimestamp": 1704067200000,
            "sourceLastModifiedTimestamp": 1704153600000,
        }
        defaults.update(overrides)
        return defaults

    def _link_doc(self, **overrides):
        defaults = {
            "url": "https://linked.example.com",
            "title": "Linked Page",
            "isPublic": "true",
            "linkedRecordId": "linked-rec-1",
        }
        defaults.update(overrides)
        return defaults

    def test_from_arango_record(self):
        rec = LinkRecord.from_arango_record(self._link_doc(), self._record_doc())
        assert rec.id == "link-1"
        assert rec.url == "https://linked.example.com"
        assert rec.title == "Linked Page"
        assert rec.is_public == LinkPublicStatus.TRUE
        assert rec.linked_record_id == "linked-rec-1"

    def test_from_arango_record_unknown_connector(self):
        rec = LinkRecord.from_arango_record(
            self._link_doc(),
            self._record_doc(connectorName="NONEXISTENT"),
        )
        assert rec.connector_name == Connectors.KNOWLEDGE_BASE

    def test_from_arango_record_default_public_status(self):
        link_doc = self._link_doc()
        del link_doc["isPublic"]
        rec = LinkRecord.from_arango_record(link_doc, self._record_doc())
        assert rec.is_public == LinkPublicStatus.UNKNOWN


# ============================================================================
# CommentRecord tests (lines 750-761, 764, 779, 789-795)
# ============================================================================


class TestCommentRecord:
    def _record_doc(self, **overrides):
        defaults = {
            "_key": "comment-1",
            "orgId": "org-1",
            "recordName": "Test Comment",
            "recordType": "COMMENT",
            "externalRecordId": "ext-comment-1",
            "version": 1,
            "origin": "CONNECTOR",
            "connectorName": "CONFLUENCE",
            "connectorId": "conn-1",
            "mimeType": "text/html",
            "createdAtTimestamp": 1704067200000,
            "updatedAtTimestamp": 1704153600000,
            "sourceCreatedAtTimestamp": 1704067200000,
            "sourceLastModifiedTimestamp": 1704153600000,
        }
        defaults.update(overrides)
        return defaults

    def _comment_doc(self, **overrides):
        defaults = {
            "authorSourceId": "author-1",
            "resolutionStatus": "resolved",
            "commentSelection": "selected text",
        }
        defaults.update(overrides)
        return defaults

    def test_creation(self):
        from app.models.entities import CommentRecord
        rec = CommentRecord(**_record_kwargs(
            record_type=RecordType.COMMENT,
            author_source_id="author-1",
            resolution_status="resolved",
        ))
        assert rec.author_source_id == "author-1"
        assert rec.resolution_status == "resolved"

    def test_to_llm_context_with_resolution(self):
        from app.models.entities import CommentRecord
        rec = CommentRecord(**_record_kwargs(
            record_type=RecordType.COMMENT,
            author_source_id="author-1",
            resolution_status="resolved",
        ))
        ctx = rec.to_llm_context()
        assert "Resolution Status" in ctx
        assert "resolved" in ctx

    def test_to_llm_context_without_resolution(self):
        from app.models.entities import CommentRecord
        rec = CommentRecord(**_record_kwargs(
            record_type=RecordType.COMMENT,
            author_source_id="author-1",
        ))
        ctx = rec.to_llm_context()
        assert "Comment Information" not in ctx

    def test_to_kafka_record(self):
        from app.models.entities import CommentRecord
        rec = CommentRecord(**_record_kwargs(
            id="comment-1",
            org_id="org-1",
            record_type=RecordType.COMMENT,
            author_source_id="author-1",
        ))
        kafka = rec.to_kafka_record()
        assert kafka["recordId"] == "comment-1"
        assert kafka["recordType"] == "COMMENT"

    def test_to_arango_record(self):
        from app.models.entities import CommentRecord
        rec = CommentRecord(**_record_kwargs(
            id="comment-1",
            record_type=RecordType.COMMENT,
            author_source_id="author-1",
            resolution_status="resolved",
            comment_selection="some text",
        ))
        arango = rec.to_arango_record()
        assert arango["_key"] == "comment-1"
        assert arango["authorSourceId"] == "author-1"
        assert arango["resolutionStatus"] == "resolved"
        assert arango["commentSelection"] == "some text"

    def test_from_arango_record(self):
        from app.models.entities import CommentRecord
        rec = CommentRecord.from_arango_record(self._comment_doc(), self._record_doc())
        assert rec.id == "comment-1"
        assert rec.author_source_id == "author-1"
        assert rec.resolution_status == "resolved"
        assert rec.comment_selection == "selected text"

    def test_from_arango_record_unknown_connector(self):
        from app.models.entities import CommentRecord
        rec = CommentRecord.from_arango_record(
            self._comment_doc(),
            self._record_doc(connectorName="NONEXISTENT"),
        )
        assert rec.connector_name == Connectors.KNOWLEDGE_BASE

    def test_from_arango_record_fallback_author(self):
        """When authorSourceId is missing, should fall back to authorId then 'unknown'."""
        from app.models.entities import CommentRecord
        comment = {"authorId": "fallback-author"}
        rec = CommentRecord.from_arango_record(comment, self._record_doc())
        assert rec.author_source_id == "fallback-author"


# ============================================================================
# TicketRecord.from_arango_record tests (lines 931-937)
# ============================================================================


class TestTicketRecordFromArango:
    def _record_doc(self, **overrides):
        defaults = {
            "_key": "ticket-1",
            "orgId": "org-1",
            "recordName": "Test Ticket",
            "recordType": "TICKET",
            "externalRecordId": "ext-ticket-1",
            "version": 1,
            "origin": "CONNECTOR",
            "connectorName": "JIRA",
            "connectorId": "conn-1",
            "mimeType": "text/plain",
            "createdAtTimestamp": 1704067200000,
            "updatedAtTimestamp": 1704153600000,
            "sourceCreatedAtTimestamp": 1704067200000,
            "sourceLastModifiedTimestamp": 1704153600000,
        }
        defaults.update(overrides)
        return defaults

    def _ticket_doc(self, **overrides):
        defaults = {
            "status": "OPEN",
            "priority": "HIGH",
            "type": "BUG",
            "deliveryStatus": "ON_TRACK",
            "assignee": "John",
            "reporterEmail": "jane@test.com",
            "assigneeEmail": "john@test.com",
            "reporterName": "Jane",
            "creatorEmail": "admin@test.com",
            "creatorName": "Admin",
            "labels": ["critical"],
        }
        defaults.update(overrides)
        return defaults

    def test_from_arango_record(self):
        rec = TicketRecord.from_arango_record(self._ticket_doc(), self._record_doc())
        assert rec.id == "ticket-1"
        assert rec.assignee == "John"
        assert rec.reporter_email == "jane@test.com"
        assert rec.labels == ["critical"]

    def test_from_arango_record_unknown_connector(self):
        rec = TicketRecord.from_arango_record(
            self._ticket_doc(),
            self._record_doc(connectorName="NONEXISTENT"),
        )
        assert rec.connector_name == Connectors.KNOWLEDGE_BASE


# ============================================================================
# ProjectRecord.from_arango_record tests (lines 1039-1045)
# ============================================================================


class TestProjectRecordFromArango:
    def _record_doc(self, **overrides):
        defaults = {
            "_key": "proj-1",
            "orgId": "org-1",
            "recordName": "Test Project",
            "recordType": "PROJECT",
            "externalRecordId": "ext-proj-1",
            "version": 1,
            "origin": "CONNECTOR",
            "connectorName": "JIRA",
            "connectorId": "conn-1",
            "mimeType": "text/plain",
            "createdAtTimestamp": 1704067200000,
            "updatedAtTimestamp": 1704153600000,
            "sourceCreatedAtTimestamp": 1704067200000,
            "sourceLastModifiedTimestamp": 1704153600000,
        }
        defaults.update(overrides)
        return defaults

    def _project_doc(self, **overrides):
        defaults = {
            "status": "Active",
            "priority": "High",
            "leadId": "lead-1",
            "leadName": "Jane",
            "leadEmail": "jane@test.com",
        }
        defaults.update(overrides)
        return defaults

    def test_from_arango_record(self):
        rec = ProjectRecord.from_arango_record(self._project_doc(), self._record_doc())
        assert rec.id == "proj-1"
        assert rec.status == "Active"
        assert rec.lead_name == "Jane"
        assert rec.lead_email == "jane@test.com"

    def test_from_arango_record_unknown_connector(self):
        rec = ProjectRecord.from_arango_record(
            self._project_doc(),
            self._record_doc(connectorName="NONEXISTENT"),
        )
        assert rec.connector_name == Connectors.KNOWLEDGE_BASE


# ============================================================================
# SharePoint record tests (lines 1097, 1122, 1147, 1172, 1178)
# ============================================================================


class TestSharePointRecords:
    def _base_kwargs(self, record_type, **overrides):
        defaults = _record_kwargs(
            id="sp-1",
            org_id="org-1",
            record_type=record_type,
            connector_name=Connectors.SHAREPOINT_ONLINE,
            external_revision_id="rev-1",
            external_record_group_id="grp-1",
            parent_external_record_id="parent-1",
            weburl="https://sp.example.com",
        )
        defaults.update(overrides)
        return defaults

    def test_sharepoint_list_to_kafka_record(self):
        from app.models.entities import SharePointListRecord
        rec = SharePointListRecord(**self._base_kwargs(RecordType.SHAREPOINT_LIST))
        kafka = rec.to_kafka_record()
        assert kafka["recordId"] == "sp-1"
        assert kafka["recordType"] == "SHAREPOINT_LIST"
        assert kafka["externalRevisionId"] == "rev-1"

    def test_sharepoint_list_item_to_kafka_record(self):
        from app.models.entities import SharePointListItemRecord
        rec = SharePointListItemRecord(**self._base_kwargs(RecordType.SHAREPOINT_LIST_ITEM))
        kafka = rec.to_kafka_record()
        assert kafka["recordId"] == "sp-1"
        assert kafka["recordType"] == "SHAREPOINT_LIST_ITEM"

    def test_sharepoint_document_library_to_kafka_record(self):
        from app.models.entities import SharePointDocumentLibraryRecord
        rec = SharePointDocumentLibraryRecord(**self._base_kwargs(RecordType.SHAREPOINT_DOCUMENT_LIBRARY))
        kafka = rec.to_kafka_record()
        assert kafka["recordId"] == "sp-1"
        assert kafka["recordType"] == "SHAREPOINT_DOCUMENT_LIBRARY"

    def test_sharepoint_page_to_arango_record(self):
        from app.models.entities import SharePointPageRecord
        rec = SharePointPageRecord(**self._base_kwargs(RecordType.SHAREPOINT_PAGE))
        arango = rec.to_arango_record()
        assert arango["_key"] == "sp-1"
        assert arango["orgId"] == "org-1"

    def test_sharepoint_page_to_kafka_record(self):
        from app.models.entities import SharePointPageRecord
        rec = SharePointPageRecord(**self._base_kwargs(RecordType.SHAREPOINT_PAGE))
        kafka = rec.to_kafka_record()
        assert kafka["recordId"] == "sp-1"
        assert kafka["recordType"] == "SHAREPOINT_PAGE"


# ============================================================================
# PullRequestRecord tests (lines 1213, 1229, 1265, 1286)
# ============================================================================


class TestPullRequestRecord:
    def test_creation(self):
        from app.models.entities import PullRequestRecord
        rec = PullRequestRecord(**_record_kwargs(
            record_type=RecordType.PULL_REQUEST,
            connector_name=Connectors.GITHUB,
            status="open",
            creator_email="dev@test.com",
            creator_name="Dev",
            labels=["enhancement"],
        ))
        assert rec.status == "open"
        assert rec.creator_email == "dev@test.com"
        assert rec.labels == ["enhancement"]

    def test_to_kafka_record(self):
        from app.models.entities import PullRequestRecord
        rec = PullRequestRecord(**_record_kwargs(
            id="pr-1",
            org_id="org-1",
            record_type=RecordType.PULL_REQUEST,
            connector_name=Connectors.GITHUB,
        ))
        kafka = rec.to_kafka_record()
        assert kafka["recordId"] == "pr-1"
        assert kafka["recordType"] == "PULL_REQUEST"

    def test_to_arango_record(self):
        from app.models.entities import PullRequestRecord
        rec = PullRequestRecord(**_record_kwargs(
            id="pr-1",
            org_id="org-1",
            record_type=RecordType.PULL_REQUEST,
            connector_name=Connectors.GITHUB,
            status="open",
            assignee=["dev1"],
            labels=["bug"],
            mergeable="true",
            merged_by="admin",
        ))
        arango = rec.to_arango_record()
        assert arango["_key"] == "pr-1"
        assert arango["status"] == "open"
        assert arango["assignee"] == ["dev1"]
        assert arango["labels"] == ["bug"]
        assert arango["mergeable"] == "true"
        assert arango["mergedBy"] == "admin"


# ============================================================================
# RecordGroup tests (lines 1265, 1286)
# ============================================================================


class TestRecordGroup:
    def test_creation(self):
        from app.models.entities import RecordGroup, RecordGroupType
        rg = RecordGroup(
            name="Test Group",
            external_group_id="ext-grp-1",
            connector_name=Connectors.GOOGLE_DRIVE,
            connector_id="conn-1",
            group_type=RecordGroupType.DRIVE,
        )
        assert rg.name == "Test Group"
        assert rg.group_type == RecordGroupType.DRIVE

    def test_to_arango_base_record_group(self):
        from app.models.entities import RecordGroup, RecordGroupType
        rg = RecordGroup(
            id="rg-1",
            org_id="org-1",
            name="Test Group",
            external_group_id="ext-grp-1",
            connector_name=Connectors.GOOGLE_DRIVE,
            connector_id="conn-1",
            group_type=RecordGroupType.DRIVE,
            web_url="https://drive.example.com",
        )
        arango = rg.to_arango_base_record_group()
        assert arango["_key"] == "rg-1"
        assert arango["groupName"] == "Test Group"
        assert arango["connectorName"] == "DRIVE"
        assert arango["groupType"] == "DRIVE"

    def test_from_arango_base_record_group(self):
        from app.models.entities import RecordGroup, RecordGroupType
        doc = {
            "_key": "rg-1",
            "orgId": "org-1",
            "groupName": "Test Group",
            "externalGroupId": "ext-1",
            "connectorName": "DRIVE",
            "connectorId": "conn-1",
            "groupType": "DRIVE",
            "webUrl": "https://drive.example.com",
            "createdAtTimestamp": 1704067200000,
            "updatedAtTimestamp": 1704153600000,
        }
        rg = RecordGroup.from_arango_base_record_group(doc)
        assert rg.id == "rg-1"
        assert rg.name == "Test Group"


# ============================================================================
# User tests (lines 1375, 1382, 1385, 1389)
# ============================================================================


class TestUser:
    def test_to_arango_base_record(self):
        from app.models.entities import User
        user = User(email="test@test.com", full_name="Test User", is_active=True)
        arango = user.to_arango_base_record()
        assert arango["email"] == "test@test.com"
        assert arango["fullName"] == "Test User"
        assert arango["isActive"] is True

    def test_validate_with_email(self):
        from app.models.entities import User
        user = User(email="test@test.com")
        assert user.validate() is True

    def test_validate_empty_email(self):
        from app.models.entities import User
        user = User(email="")
        assert user.validate() is False

    def test_key(self):
        from app.models.entities import User
        user = User(email="test@test.com")
        assert user.key() == "test@test.com"

    def test_from_arango_user(self):
        from app.models.entities import User
        data = {
            "_key": "user-1",
            "email": "test@test.com",
            "orgId": "org-1",
            "userId": "uid-1",
            "isActive": True,
            "firstName": "Test",
            "lastName": "User",
            "fullName": "Test User",
        }
        user = User.from_arango_user(data)
        assert user.id == "user-1"
        assert user.email == "test@test.com"
        assert user.org_id == "org-1"
        assert user.first_name == "Test"
        assert user.full_name == "Test User"


# ============================================================================
# UserGroup tests (lines 1416, 1427, 1430)
# ============================================================================


class TestUserGroup:
    def test_to_dict(self):
        from app.models.entities import UserGroup
        ug = UserGroup(
            source_user_group_id="src-1",
            name="Test Group",
            description="A group",
        )
        d = ug.to_dict()
        assert d["name"] == "Test Group"
        assert d["description"] == "A group"

    def test_validate(self):
        from app.models.entities import UserGroup
        ug = UserGroup(source_user_group_id="src-1", name="Test")
        assert ug.validate() is True

    def test_key(self):
        from app.models.entities import UserGroup
        ug = UserGroup(source_user_group_id="src-1", name="Test", id="ug-1")
        assert ug.key() == "ug-1"


# ============================================================================
# Person tests (lines 1441, 1450)
# ============================================================================


class TestPerson:
    def test_to_arango_person(self):
        from app.models.entities import Person
        p = Person(id="p-1", email="person@test.com", created_at=1000, updated_at=2000)
        arango = p.to_arango_person()
        assert arango["_key"] == "p-1"
        assert arango["email"] == "person@test.com"
        assert arango["createdAtTimestamp"] == 1000

    def test_from_arango_person(self):
        from app.models.entities import Person
        data = {
            "_key": "p-1",
            "email": "person@test.com",
            "createdAtTimestamp": 1000,
            "updatedAtTimestamp": 2000,
        }
        p = Person.from_arango_person(data)
        assert p.id == "p-1"
        assert p.email == "person@test.com"
        assert p.created_at == 1000


# ============================================================================
# AppUser tests (lines 1474, 1487)
# ============================================================================


class TestAppUser:
    def test_to_arango_base_user(self):
        from app.models.entities import AppUser
        au = AppUser(
            app_name=Connectors.GOOGLE_DRIVE,
            connector_id="conn-1",
            source_user_id="src-1",
            email="user@test.com",
            full_name="App User",
        )
        arango = au.to_arango_base_user()
        assert arango["email"] == "user@test.com"
        assert arango["fullName"] == "App User"
        assert arango["userId"] == "src-1"

    def test_from_arango_user(self):
        from app.models.entities import AppUser
        data = {
            "_key": "au-1",
            "email": "user@test.com",
            "orgId": "org-1",
            "isActive": True,
            "fullName": "App User",
            "sourceUserId": "src-1",
            "appName": "DRIVE",
            "connectorId": "conn-1",
        }
        au = AppUser.from_arango_user(data)
        assert au.id == "au-1"
        assert au.email == "user@test.com"
        assert au.full_name == "App User"


# ============================================================================
# AppUserGroup tests (lines 1516, 1533)
# ============================================================================


class TestAppUserGroup:
    def test_to_arango_base_user_group(self):
        from app.models.entities import AppUserGroup
        aug = AppUserGroup(
            app_name=Connectors.GOOGLE_DRIVE,
            connector_id="conn-1",
            source_user_group_id="src-grp-1",
            name="Test Group",
        )
        arango = aug.to_arango_base_user_group()
        assert arango["name"] == "Test Group"
        assert arango["externalGroupId"] == "src-grp-1"
        assert arango["connectorName"] == "DRIVE"

    def test_from_arango_base_user_group(self):
        from app.models.entities import AppUserGroup
        data = {
            "_key": "aug-1",
            "orgId": "org-1",
            "name": "Test Group",
            "externalGroupId": "src-grp-1",
            "connectorName": "DRIVE",
            "connectorId": "conn-1",
            "createdAtTimestamp": 1000,
            "updatedAtTimestamp": 2000,
        }
        aug = AppUserGroup.from_arango_base_user_group(data)
        assert aug.id == "aug-1"
        assert aug.name == "Test Group"
        assert aug.source_user_group_id == "src-grp-1"


# ============================================================================
# AppRole tests (lines 1562, 1578)
# ============================================================================


class TestAppRole:
    def test_to_arango_base_role(self):
        from app.models.entities import AppRole
        ar = AppRole(
            app_name=Connectors.GOOGLE_DRIVE,
            connector_id="conn-1",
            source_role_id="role-1",
            name="Admin",
        )
        arango = ar.to_arango_base_role()
        assert arango["name"] == "Admin"
        assert arango["externalRoleId"] == "role-1"
        assert arango["connectorName"] == "DRIVE"

    def test_from_arango_base_role(self):
        from app.models.entities import AppRole
        data = {
            "_key": "ar-1",
            "orgId": "org-1",
            "name": "Admin",
            "externalRoleId": "role-1",
            "connectorName": "DRIVE",
            "connectorId": "conn-1",
            "createdAtTimestamp": 1000,
            "updatedAtTimestamp": 2000,
        }
        ar = AppRole.from_arango_base_role(data)
        assert ar.id == "ar-1"
        assert ar.name == "Admin"
        assert ar.source_role_id == "role-1"


# ============================================================================
# Record.to_llm_context edge cases (lines 232->235 branch, mime_type)
# ============================================================================


class TestRecordToLlmContextEdgeCases:
    def test_to_llm_context_with_mime_type(self):
        """When mime_type is set, should appear in context."""
        rec = Record(**_record_kwargs(mime_type="application/pdf"))
        ctx = rec.to_llm_context()
        assert "MIME Type" in ctx
        assert "application/pdf" in ctx

    def test_to_llm_context_without_mime_type(self):
        """When mime_type is default/unknown, should still render (not None)."""
        rec = Record(**_record_kwargs())
        ctx = rec.to_llm_context()
        # Default mime_type is MimeTypes.UNKNOWN.value which is truthy
        # The to_llm_context checks 'if self.mime_type' so unknown value will appear
        assert "Record ID" in ctx

    def test_to_llm_context_weburl_without_http(self):
        """Weburl not starting with http should be prefixed by frontend_url."""
        rec = Record(**_record_kwargs(weburl="/path/to/doc"))
        ctx = rec.to_llm_context(frontend_url="https://app.example.com")
        assert "https://app.example.com/path/to/doc" in ctx

    def test_to_llm_context_weburl_without_http_no_frontend(self):
        """Weburl not starting with http without frontend_url uses raw."""
        rec = Record(**_record_kwargs(weburl="/path/to/doc"))
        ctx = rec.to_llm_context(frontend_url=None)
        assert "/path/to/doc" in ctx


# ============================================================================
# TicketRecord.to_llm_context with Enum values (lines 850-879)
# ============================================================================


class TestTicketRecordToLlmContextEnum:
    def test_with_enum_status_and_priority(self):
        """Enum values should have .value extracted."""
        from app.models.entities import DeliveryStatus, ItemType, Priority, Status
        rec = TicketRecord(**_record_kwargs(
            record_type=RecordType.TICKET,
            connector_name=Connectors.JIRA,
            status=Status.IN_PROGRESS,
            priority=Priority.HIGH,
            type=ItemType.BUG,
            delivery_status=DeliveryStatus.ON_TRACK,
            assignee="John",
            assignee_email="john@test.com",
            reporter_name="Jane",
            reporter_email="jane@test.com",
            creator_name="Admin",
            creator_email="admin@test.com",
        ))
        ctx = rec.to_llm_context()
        assert "IN_PROGRESS" in ctx
        assert "HIGH" in ctx
        assert "BUG" in ctx
        assert "ON_TRACK" in ctx
        assert "Assignee" in ctx
        assert "Reporter" in ctx
        assert "Creator" in ctx


# ============================================================================
# LinkRecord.to_llm_context all branches (lines 654-671)
# ============================================================================


class TestLinkRecordToLlmContextBranches:
    def test_with_all_fields(self):
        """All link-specific fields should appear."""
        rec = LinkRecord(**_record_kwargs(
            record_type=RecordType.LINK,
            url="https://example.com",
            title="Example",
            is_public=LinkPublicStatus.TRUE,
            linked_record_id="rec-linked-1",
        ))
        ctx = rec.to_llm_context()
        assert "URL" in ctx
        assert "Title" in ctx
        assert "Public Access" in ctx
        assert "Linked Record ID" in ctx

    def test_without_optional_fields(self):
        """Without title, linked_record_id -- those should not appear."""
        rec = LinkRecord(**_record_kwargs(
            record_type=RecordType.LINK,
            url="https://example.com",
            is_public=LinkPublicStatus.FALSE,
        ))
        ctx = rec.to_llm_context()
        assert "URL" in ctx
        assert "Title" not in ctx
        assert "Linked Record ID" not in ctx

# ============================================================================
# ProductRecord tests
# ============================================================================


class TestProductRecord:
    def test_creation(self):
        rec = ProductRecord(**_record_kwargs(
            record_type=RecordType.PRODUCT,
            product_code="PROD-001",
            product_family="Software",
        ))
        assert rec.product_code == "PROD-001"
        assert rec.product_family == "Software"
        assert rec.record_type == RecordType.PRODUCT

    def test_default_fields(self):
        rec = ProductRecord(**_record_kwargs(record_type=RecordType.PRODUCT))
        assert rec.product_code is None
        assert rec.product_family is None

    def test_to_llm_context_with_fields(self):
        rec = ProductRecord(**_record_kwargs(
            record_type=RecordType.PRODUCT,
            product_code="PROD-001",
            product_family="Hardware",
        ))
        ctx = rec.to_llm_context()
        assert "Product Code" in ctx
        assert "PROD-001" in ctx
        assert "Product Family" in ctx
        assert "Hardware" in ctx
        assert "Product Information" in ctx

    def test_to_llm_context_no_fields(self):
        rec = ProductRecord(**_record_kwargs(record_type=RecordType.PRODUCT))
        ctx = rec.to_llm_context()
        assert "Product Information" not in ctx

    def test_to_arango_record(self):
        rec = ProductRecord(**_record_kwargs(
            id="prod-1",
            org_id="org-1",
            record_type=RecordType.PRODUCT,
            product_code="PROD-001",
            product_family="Software",
        ))
        arango = rec.to_arango_record()
        assert arango["_key"] == "prod-1"
        assert arango["orgId"] == "org-1"
        assert arango["productCode"] == "PROD-001"
        assert arango["productFamily"] == "Software"

    def test_to_kafka_record(self):
        rec = ProductRecord(**_record_kwargs(
            id="prod-1",
            org_id="org-1",
            record_type=RecordType.PRODUCT,
            product_code="PROD-001",
        ))
        kafka = rec.to_kafka_record()
        assert kafka["recordId"] == "prod-1"
        assert kafka["recordType"] == "PRODUCT"
        assert kafka["orgId"] == "org-1"

    def test_from_arango_record(self):
        product_doc = {
            "productCode": "PROD-002",
            "productFamily": "Cloud",
        }
        record_doc = {
            "_key": "prod-2",
            "orgId": "org-1",
            "recordName": "My Product",
            "recordType": "PRODUCT",
            "externalRecordId": "ext-prod-2",
            "version": 1,
            "origin": "CONNECTOR",
            "connectorName": "DRIVE",
            "connectorId": "conn-1",
            "createdAtTimestamp": 1704067200000,
            "updatedAtTimestamp": 1704153600000,
        }
        rec = ProductRecord.from_arango_record(product_doc, record_doc)
        assert rec.id == "prod-2"
        assert rec.record_name == "My Product"
        assert rec.product_code == "PROD-002"
        assert rec.product_family == "Cloud"
        assert rec.record_type == RecordType.PRODUCT


# ============================================================================
# DealRecord tests
# ============================================================================


class TestDealRecord:
    def test_creation(self):
        rec = DealRecord(**_record_kwargs(
            record_type=RecordType.DEAL,
            name="Big Enterprise Deal",
            amount=50000.0,
            expected_revenue=45000.0,
            expected_close_date="2024-06-30",
            conversion_probability=0.75,
            type="New Business",
            owner_id="user-001",
            is_won=False,
            is_closed=False,
        ))
        assert rec.name == "Big Enterprise Deal"
        assert rec.amount == 50000.0
        assert rec.expected_revenue == 45000.0
        assert rec.expected_close_date == "2024-06-30"
        assert rec.conversion_probability == 0.75
        assert rec.type == "New Business"
        assert rec.owner_id == "user-001"
        assert rec.is_won is False
        assert rec.is_closed is False
        assert rec.record_type == RecordType.DEAL

    def test_default_fields(self):
        rec = DealRecord(**_record_kwargs(record_type=RecordType.DEAL))
        assert rec.name is None
        assert rec.amount is None
        assert rec.expected_revenue is None
        assert rec.expected_close_date is None
        assert rec.conversion_probability is None
        assert rec.type is None
        assert rec.owner_id is None
        assert rec.is_won is None
        assert rec.is_closed is None
        assert rec.created_date is None
        assert rec.close_date is None

    def test_to_llm_context_with_fields(self):
        rec = DealRecord(**_record_kwargs(
            record_type=RecordType.DEAL,
            name="Enterprise Deal",
            amount=100000.0,
            expected_revenue=90000.0,
            expected_close_date="2024-12-31",
            conversion_probability=0.8,
            type="Renewal",
            owner_id="user-42",
            is_won=True,
            is_closed=True,
            created_date="2024-01-01",
            close_date="2024-12-31",
        ))
        ctx = rec.to_llm_context()
        assert "Deal Information" in ctx
        assert "Enterprise Deal" in ctx
        assert "100000.0" in ctx
        assert "90000.0" in ctx
        assert "2024-12-31" in ctx
        assert "0.8" in ctx
        assert "Renewal" in ctx
        assert "user-42" in ctx
        assert "Won" in ctx
        assert "Closed" in ctx

    def test_to_llm_context_no_fields(self):
        rec = DealRecord(**_record_kwargs(record_type=RecordType.DEAL))
        ctx = rec.to_llm_context()
        assert "Deal Information" not in ctx

    def test_to_arango_record(self):
        rec = DealRecord(**_record_kwargs(
            id="deal-1",
            org_id="org-1",
            record_type=RecordType.DEAL,
            name="Test Deal",
            amount=20000.0,
            expected_revenue=18000.0,
            expected_close_date="2024-09-30",
            conversion_probability=0.6,
            type="Upsell",
            owner_id="user-10",
            is_won=False,
            is_closed=False,
            created_date="2024-03-01",
            close_date="2024-09-30",
        ))
        arango = rec.to_arango_record()
        assert arango["_key"] == "deal-1"
        assert arango["orgId"] == "org-1"
        assert arango["name"] == "Test Deal"
        assert arango["amount"] == 20000.0
        assert arango["expectedRevenue"] == 18000.0
        assert arango["expectedCloseDate"] == "2024-09-30"
        assert arango["conversionProbability"] == 0.6
        assert arango["type"] == "Upsell"
        assert arango["ownerId"] == "user-10"
        assert arango["isWon"] is False
        assert arango["isClosed"] is False
        assert arango["createdDate"] == "2024-03-01"
        assert arango["closeDate"] == "2024-09-30"

    def test_to_kafka_record(self):
        rec = DealRecord(**_record_kwargs(
            id="deal-1",
            org_id="org-1",
            record_type=RecordType.DEAL,
        ))
        kafka = rec.to_kafka_record()
        assert kafka["recordId"] == "deal-1"
        assert kafka["recordType"] == "DEAL"
        assert kafka["orgId"] == "org-1"

    def test_from_arango_record(self):
        deal_doc = {
            "name": "Arango Deal",
            "amount": 75000.0,
            "expectedRevenue": 70000.0,
            "expectedCloseDate": "2024-11-01",
            "conversionProbability": 0.9,
            "type": "New Business",
            "ownerId": "user-99",
            "isWon": False,
            "isClosed": False,
            "createdDate": "2024-02-15",
            "closeDate": "2024-11-01",
        }
        record_doc = {
            "_key": "deal-2",
            "orgId": "org-1",
            "recordName": "Arango Deal",
            "recordType": "DEAL",
            "externalRecordId": "ext-deal-2",
            "version": 1,
            "origin": "CONNECTOR",
            "connectorName": "DRIVE",
            "connectorId": "conn-1",
            "createdAtTimestamp": 1704067200000,
            "updatedAtTimestamp": 1704153600000,
        }
        rec = DealRecord.from_arango_record(deal_doc, record_doc)
        assert rec.id == "deal-2"
        assert rec.record_name == "Arango Deal"
        assert rec.name == "Arango Deal"
        assert rec.amount == 75000.0
        assert rec.expected_revenue == 70000.0
        assert rec.conversion_probability == 0.9
        assert rec.owner_id == "user-99"
        assert rec.record_type == RecordType.DEAL

    def test_deal_info_edges_to_llm_lines_empty(self):
        rec = DealRecord(**_record_kwargs(record_type=RecordType.DEAL))
        lines = rec._deal_info_edges_to_llm_lines([])
        assert any("No incoming dealInfo edges" in line for line in lines)

    def test_deal_info_edges_to_llm_lines_with_edges(self):
        rec = DealRecord(**_record_kwargs(record_type=RecordType.DEAL))
        edges = [
            {
                "_from": "orgs/org-1",
                "stage": "Prospecting",
                "createdAtTimestamp": 1704067200000,
                "updatedAtTimestamp": 1704153600000,
            }
        ]
        lines = rec._deal_info_edges_to_llm_lines(edges)
        assert any("orgs/org-1" in line for line in lines)
        assert any("Prospecting" in line for line in lines)

    def test_sold_in_edges_products_to_llm_lines_empty(self):
        rec = DealRecord(**_record_kwargs(record_type=RecordType.DEAL))
        lines = rec._sold_in_edges_products_to_llm_lines([])
        assert any("No products in this deal" in line for line in lines)

    def test_sold_in_edges_products_to_llm_lines_with_data(self):
        rec = DealRecord(**_record_kwargs(record_type=RecordType.DEAL))
        relations = [
            {
                "edge": {
                    "_from": "records/prod-1",
                    "quantities": [2],
                    "unitPrices": [500.0],
                    "totalPrices": [1000.0],
                    "isDeletedFlags": [False],
                    "createdAtTimestamp": 1704067200000,
                    "updatedAtTimestamp": 1704153600000,
                },
                "product": {"recordName": "Widget Pro"},
            }
        ]
        lines = rec._sold_in_edges_products_to_llm_lines(relations)
        assert any("Widget Pro" in line for line in lines)
        assert any("qty: 2" in line for line in lines)
        assert any("unitPrice: 500.0" in line for line in lines)
        assert any("totalPrice: 1000.0" in line for line in lines)

    def test_to_llm_context_with_graph_provider(self):
        rec = DealRecord(**_record_kwargs(
            id="deal-gp",
            record_type=RecordType.DEAL,
            name="Graph Deal",
            amount=5000.0,
        ))
        mock_provider = MagicMock()
        mock_provider.get_edges_to_node = AsyncMock(return_value=[])
        ctx = asyncio.run(rec.to_llm_context_with_graph(graph_provider=mock_provider))
        assert "Graph Deal" in ctx
        assert "DealInfo relations" in ctx
        assert "Products in this deal" in ctx


# ============================================================================
# FileRecord.to_llm_full_context
# ============================================================================


def _make_file_record_with_blocks(blocks=None, block_groups=None, record_id="rec-1"):
    container = BlocksContainer(
        blocks=blocks or [],
        block_groups=block_groups or [],
    )
    rec = FileRecord(**_record_kwargs(
        id=record_id,
        org_id="org-1",
        is_file=True,
        extension="txt",
    ))
    rec.block_containers = container
    return rec


class TestFileRecordToLlmFullContext:
    def test_returns_list_of_llm_text_content(self):
        rec = _make_file_record_with_blocks()
        with patch("app.utils.chat_helpers.valid_group_labels", []):
            items = rec.to_llm_full_context()
        assert isinstance(items, list)
        assert all(isinstance(i, LlmTextContent) for i in items)

    def test_header_item_contains_record_id(self):
        rec = _make_file_record_with_blocks(record_id="rec-42")
        with patch("app.utils.chat_helpers.valid_group_labels", []):
            items = rec.to_llm_full_context()
        assert any("rec-42" in item.text for item in items)

    def test_text_block_produces_content_item(self):
        block = Block(type=BlockType.TEXT, data="Hello world", parent_index=None)
        rec = _make_file_record_with_blocks(blocks=[block])
        with patch("app.utils.chat_helpers.valid_group_labels", []):
            items = rec.to_llm_full_context()
        texts = [i.text for i in items]
        assert any("Hello world" in t for t in texts)

    def test_image_block_is_skipped(self):
        image_block = Block(type=BlockType.IMAGE, data="img_data", parent_index=None)
        text_block = Block(type=BlockType.TEXT, data="visible", parent_index=None)
        rec = _make_file_record_with_blocks(blocks=[image_block, text_block])
        with patch("app.utils.chat_helpers.valid_group_labels", []):
            items = rec.to_llm_full_context()
        texts = " ".join(i.text for i in items)
        assert "img_data" not in texts
        assert "visible" in texts

    def test_table_block_with_children_none_produces_no_row_content(self):
        """TABLE group with children=None → rows_to_be_included_list stays empty."""
        row_block = Block(type=BlockType.TABLE_ROW, data={"row_natural_language_text": "row A"}, parent_index=0)
        group = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            children=None,
        )
        rec = _make_file_record_with_blocks(blocks=[row_block], block_groups=[group])
        with patch("app.utils.chat_helpers.valid_group_labels", [GroupType.TABLE.value]):
            items = rec.to_llm_full_context()
        texts = " ".join(i.text for i in items)
        assert "row A" not in texts

    def test_table_block_with_single_range_renders_rows(self):
        """TABLE group with block_ranges=[0..0] → the row block text appears."""
        row_block = Block(
            type=BlockType.TABLE_ROW,
            data={"row_natural_language_text": "row content"},
            parent_index=0,
        )
        children = BlockGroupChildren(block_ranges=[IndexRange(start=0, end=0)])
        group = BlockGroup(index=0, type=GroupType.TABLE, children=children)
        rec = _make_file_record_with_blocks(blocks=[row_block], block_groups=[group])

        with patch("app.utils.chat_helpers.valid_group_labels", [GroupType.TABLE.value]), \
             patch("app.agents.actions.util.parse_file.LlmTextContent", LlmTextContent):
            from jinja2 import Template
            with patch("app.models.entities.Template") as mock_tpl:
                mock_tpl.return_value.render = MagicMock(return_value="TABLE_RENDERED")
                items = rec.to_llm_full_context()

        texts = " ".join(i.text for i in items)
        assert "TABLE_RENDERED" in texts

    def test_table_block_multi_range_expands_correctly(self):
        """block_ranges=[0..1] expands to indices [0, 1]."""
        row0 = Block(type=BlockType.TABLE_ROW, data={"row_natural_language_text": "r0"}, parent_index=0)
        row1 = Block(type=BlockType.TABLE_ROW, data={"row_natural_language_text": "r1"}, parent_index=0)
        children = BlockGroupChildren(block_ranges=[IndexRange(start=0, end=1)])
        group = BlockGroup(index=0, type=GroupType.TABLE, children=children)
        rec = _make_file_record_with_blocks(blocks=[row0, row1], block_groups=[group])

        with patch("app.utils.chat_helpers.valid_group_labels", [GroupType.TABLE.value]):
            from jinja2 import Template
            with patch("app.models.entities.Template") as mock_tpl:
                captured = {}

                def _render(**kwargs):
                    captured.update(kwargs)
                    return "RENDERED"

                mock_tpl.return_value.render = MagicMock(side_effect=_render)
                rec.to_llm_full_context()

        blocks_passed = captured.get("blocks", [])
        contents = [b["content"] for b in blocks_passed]
        assert "r0" in contents
        assert "r1" in contents
