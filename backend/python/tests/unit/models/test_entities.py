"""Tests for entities module: Record, TicketRecord, ProjectRecord, FileRecord, MailRecord, LinkRecord."""

from unittest.mock import patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.models.entities import (
    FileRecord,
    LinkPublicStatus,
    LinkRecord,
    MailRecord,
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
        assert rec.indexing_status == ProgressStatus.NOT_STARTED.value
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
