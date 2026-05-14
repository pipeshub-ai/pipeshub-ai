"""
Unit tests for app.config.constants.arangodb

Verifies enum members, values, uniqueness, and mapping correctness for
the ArangoDB/graph-layer constants used across the codebase.
"""

import pytest

from app.config.constants.arangodb import (
    AccountType,
    AppGroups,
    AppStatus,
    CollectionNames,
    Connectors,
    ConnectorScopes,
    DepartmentNames,
    EntityRelations,
    EventTypes,
    ExtensionTypes,
    GraphNames,
    LegacyCollectionNames,
    LegacyGraphNames,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
    QdrantCollectionNames,
    RECONCILIATION_ENABLED_EXTENSIONS,
    RECONCILIATION_ENABLED_MIME_TYPES,
    RECORD_TYPE_COLLECTION_MAPPING,
    RecordRelations,
    RecordTypes,
)


# ---------------------------------------------------------------------------
# DepartmentNames
# ---------------------------------------------------------------------------

class TestDepartmentNames:
    def test_has_expected_departments(self):
        names = {e.value for e in DepartmentNames}
        assert "Engineering/Technology" in names
        assert "Human Resources" in names
        assert "Finance" in names
        assert "Marketing" in names

    def test_others_member_exists(self):
        assert DepartmentNames.OTHERS.value == "Others"

    def test_all_values_are_nonempty_strings(self):
        for member in DepartmentNames:
            assert isinstance(member.value, str) and member.value


# ---------------------------------------------------------------------------
# Connectors
# ---------------------------------------------------------------------------

class TestConnectors:
    def test_google_workspace_connectors(self):
        assert Connectors.GOOGLE_DRIVE.value == "DRIVE"
        assert Connectors.GOOGLE_MAIL.value == "GMAIL"
        assert Connectors.GOOGLE_CALENDAR.value == "CALENDAR"

    def test_microsoft_connectors(self):
        assert Connectors.ONEDRIVE.value == "ONEDRIVE"
        assert Connectors.SHAREPOINT_ONLINE.value == "SHAREPOINT ONLINE"
        assert Connectors.OUTLOOK.value == "OUTLOOK"

    def test_knowledge_base_connector(self):
        assert Connectors.KNOWLEDGE_BASE.value == "KB"

    def test_coding_and_database_sandbox(self):
        assert Connectors.CODING_SANDBOX.value == "CODING_SANDBOX"
        assert Connectors.DATABASE_SANDBOX.value == "DATABASE_SANDBOX"

    def test_unknown_connector_exists(self):
        assert Connectors.UNKNOWN.value == "UNKNOWN"

    def test_sql_connectors(self):
        assert Connectors.SNOWFLAKE.value == "SNOWFLAKE"
        assert Connectors.POSTGRESQL.value == "POSTGRESQL"
        assert Connectors.MARIADB.value == "MARIADB"

    def test_no_duplicate_values(self):
        values = [e.value for e in Connectors]
        assert len(values) == len(set(values))


# ---------------------------------------------------------------------------
# AppGroups
# ---------------------------------------------------------------------------

class TestAppGroups:
    def test_key_groups_present(self):
        names = {e.name for e in AppGroups}
        assert "GOOGLE_WORKSPACE" in names
        assert "ATLASSIAN" in names
        assert "MICROSOFT" in names

    def test_values_are_nonempty_strings(self):
        for member in AppGroups:
            assert isinstance(member.value, str) and member.value


# ---------------------------------------------------------------------------
# OriginTypes
# ---------------------------------------------------------------------------

class TestOriginTypes:
    def test_connector_and_upload(self):
        assert OriginTypes.CONNECTOR.value == "CONNECTOR"
        assert OriginTypes.UPLOAD.value == "UPLOAD"

    def test_exactly_two_members(self):
        assert len(list(OriginTypes)) == 2


# ---------------------------------------------------------------------------
# CollectionNames
# ---------------------------------------------------------------------------

class TestCollectionNames:
    def test_core_collections(self):
        assert CollectionNames.RECORDS.value == "records"
        assert CollectionNames.RECORD_GROUPS.value == "recordGroups"
        assert CollectionNames.BLOCKS.value == "blocks"

    def test_agent_collections(self):
        assert CollectionNames.AGENT_TEMPLATES.value == "agentTemplates"
        assert CollectionNames.AGENT_INSTANCES.value == "agentInstances"
        assert CollectionNames.AGENT_KNOWLEDGE.value == "agentKnowledge"
        assert CollectionNames.AGENT_TOOLSETS.value == "agentToolsets"
        assert CollectionNames.AGENT_TOOLS.value == "agentTools"

    def test_agent_edge_collections(self):
        assert CollectionNames.AGENT_HAS_KNOWLEDGE.value == "agentHasKnowledge"
        assert CollectionNames.AGENT_HAS_TOOLSET.value == "agentHasToolset"
        assert CollectionNames.TOOLSET_HAS_TOOL.value == "toolsetHasTool"

    def test_sql_collections(self):
        assert CollectionNames.SQL_TABLES.value == "sqlTables"
        assert CollectionNames.SQL_VIEWS.value == "sqlViews"

    def test_crm_collections(self):
        assert CollectionNames.PRODUCTS.value == "products"
        assert CollectionNames.DEALS.value == "deals"

    def test_no_duplicate_values(self):
        values = [e.value for e in CollectionNames]
        assert len(values) == len(set(values))


# ---------------------------------------------------------------------------
# LegacyCollectionNames / LegacyGraphNames / GraphNames
# ---------------------------------------------------------------------------

class TestLegacyNames:
    def test_legacy_collection_names(self):
        assert LegacyCollectionNames.KNOWLEDGE_BASE.value == "knowledgeBase"
        assert LegacyCollectionNames.PERMISSIONS.value == "permissions"

    def test_legacy_graph_names(self):
        assert LegacyGraphNames.FILE_ACCESS_GRAPH.value == "fileAccessGraph"

    def test_graph_names(self):
        assert GraphNames.KNOWLEDGE_GRAPH.value == "knowledgeGraph"


# ---------------------------------------------------------------------------
# ExtensionTypes
# ---------------------------------------------------------------------------

class TestExtensionTypes:
    def test_common_document_extensions(self):
        assert ExtensionTypes.PDF.value == "pdf"
        assert ExtensionTypes.DOCX.value == "docx"
        assert ExtensionTypes.XLSX.value == "xlsx"
        assert ExtensionTypes.PPTX.value == "pptx"

    def test_image_extensions(self):
        assert ExtensionTypes.PNG.value == "png"
        assert ExtensionTypes.JPG.value == "jpg"
        assert ExtensionTypes.JPEG.value == "jpeg"

    def test_sql_extensions(self):
        assert ExtensionTypes.SQL_TABLE.value == "sql_table"
        assert ExtensionTypes.SQL_VIEW.value == "sql_view"

    def test_no_duplicate_values(self):
        values = [e.value for e in ExtensionTypes]
        assert len(values) == len(set(values))


# ---------------------------------------------------------------------------
# MimeTypes
# ---------------------------------------------------------------------------

class TestMimeTypes:
    def test_common_mime_types(self):
        assert MimeTypes.PDF.value == "application/pdf"
        assert MimeTypes.DOCX.value == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    def test_google_workspace_mime_types(self):
        assert MimeTypes.GOOGLE_DOCS.value == "application/vnd.google-apps.document"
        assert MimeTypes.GOOGLE_SHEETS.value == "application/vnd.google-apps.spreadsheet"
        assert MimeTypes.GOOGLE_SLIDES.value == "application/vnd.google-apps.presentation"

    def test_image_mime_types(self):
        assert MimeTypes.PNG.value == "image/png"
        assert MimeTypes.JPEG.value == "image/jpeg"
        assert MimeTypes.WEBP.value == "image/webp"

    def test_sql_mime_types(self):
        assert MimeTypes.SQL_TABLE.value == "application/vnd.sql.table"
        assert MimeTypes.SQL_VIEW.value == "application/vnd.sql.view"

    def test_blocks_mime_type(self):
        assert MimeTypes.BLOCKS.value == "application/blocks"

    def test_no_duplicate_values(self):
        values = [e.value for e in MimeTypes]
        assert len(values) == len(set(values))


# ---------------------------------------------------------------------------
# RECONCILIATION_ENABLED_MIME_TYPES and EXTENSIONS
# ---------------------------------------------------------------------------

class TestReconciliationSets:
    def test_reconciliation_mime_types_is_set(self):
        assert isinstance(RECONCILIATION_ENABLED_MIME_TYPES, (set, frozenset))

    def test_reconciliation_mime_types_contains_common_types(self):
        assert MimeTypes.PDF.value in RECONCILIATION_ENABLED_MIME_TYPES
        assert MimeTypes.DOCX.value in RECONCILIATION_ENABLED_MIME_TYPES
        assert MimeTypes.SQL_TABLE.value in RECONCILIATION_ENABLED_MIME_TYPES
        assert MimeTypes.SQL_VIEW.value in RECONCILIATION_ENABLED_MIME_TYPES

    def test_reconciliation_extensions_is_set(self):
        assert isinstance(RECONCILIATION_ENABLED_EXTENSIONS, (set, frozenset))

    def test_reconciliation_extensions_contains_common_extensions(self):
        assert ExtensionTypes.PDF.value in RECONCILIATION_ENABLED_EXTENSIONS
        assert ExtensionTypes.DOCX.value in RECONCILIATION_ENABLED_EXTENSIONS
        assert ExtensionTypes.SQL_TABLE.value in RECONCILIATION_ENABLED_EXTENSIONS
        assert ExtensionTypes.SQL_VIEW.value in RECONCILIATION_ENABLED_EXTENSIONS

    def test_reconciliation_mime_types_does_not_include_image_types(self):
        # Images should not trigger reconciliation
        assert MimeTypes.PNG.value not in RECONCILIATION_ENABLED_MIME_TYPES
        assert MimeTypes.JPEG.value not in RECONCILIATION_ENABLED_MIME_TYPES


# ---------------------------------------------------------------------------
# RECORD_TYPE_COLLECTION_MAPPING
# ---------------------------------------------------------------------------

class TestRecordTypeCollectionMapping:
    def test_is_dict(self):
        assert isinstance(RECORD_TYPE_COLLECTION_MAPPING, dict)

    def test_file_maps_to_files_collection(self):
        assert RECORD_TYPE_COLLECTION_MAPPING["FILE"] == CollectionNames.FILES.value

    def test_mail_maps_to_mails_collection(self):
        assert RECORD_TYPE_COLLECTION_MAPPING["MAIL"] == CollectionNames.MAILS.value
        assert RECORD_TYPE_COLLECTION_MAPPING["GROUP_MAIL"] == CollectionNames.MAILS.value

    def test_ticket_maps_to_tickets_collection(self):
        assert RECORD_TYPE_COLLECTION_MAPPING["TICKET"] == CollectionNames.TICKETS.value
        assert RECORD_TYPE_COLLECTION_MAPPING["CASE"] == CollectionNames.TICKETS.value
        assert RECORD_TYPE_COLLECTION_MAPPING["TASK"] == CollectionNames.TICKETS.value

    def test_sql_maps_to_sql_collections(self):
        assert RECORD_TYPE_COLLECTION_MAPPING["SQL_TABLE"] == CollectionNames.SQL_TABLES.value
        assert RECORD_TYPE_COLLECTION_MAPPING["SQL_VIEW"] == CollectionNames.SQL_VIEWS.value

    def test_webpage_types_map_to_webpages(self):
        webpages_collection = CollectionNames.WEBPAGES.value
        assert RECORD_TYPE_COLLECTION_MAPPING["WEBPAGE"] == webpages_collection
        assert RECORD_TYPE_COLLECTION_MAPPING["DATABASE"] == webpages_collection

    def test_pull_request_maps_to_prs(self):
        assert RECORD_TYPE_COLLECTION_MAPPING["PULL_REQUEST"] == CollectionNames.PULLREQUESTS.value

    def test_all_values_are_valid_collection_names(self):
        valid_collection_values = {e.value for e in CollectionNames}
        for key, value in RECORD_TYPE_COLLECTION_MAPPING.items():
            assert value in valid_collection_values, (
                f"'{key}' maps to '{value}' which is not a known CollectionNames value"
            )


# ---------------------------------------------------------------------------
# RecordTypes, RecordRelations, EntityRelations
# ---------------------------------------------------------------------------

class TestRecordTypes:
    def test_common_record_types(self):
        assert RecordTypes.FILE.value == "FILE"
        assert RecordTypes.MAIL.value == "MAIL"
        assert RecordTypes.TICKET.value == "TICKET"
        assert RecordTypes.CODE_FILE.value == "CODE_FILE"

    def test_all_values_nonempty_strings(self):
        for member in RecordTypes:
            assert isinstance(member.value, str) and member.value


class TestRecordRelations:
    def test_common_relations(self):
        assert RecordRelations.PARENT_CHILD.value == "PARENT_CHILD"
        assert RecordRelations.ATTACHMENT.value == "ATTACHMENT"
        assert RecordRelations.FOREIGN_KEY.value == "FOREIGN_KEY"

    def test_no_duplicate_values(self):
        values = [e.value for e in RecordRelations]
        assert len(values) == len(set(values))


class TestEntityRelations:
    def test_standard_relations(self):
        assert EntityRelations.ASSIGNED_TO.value == "ASSIGNED_TO"
        assert EntityRelations.CREATED_BY.value == "CREATED_BY"
        assert EntityRelations.REPORTED_BY.value == "REPORTED_BY"


# ---------------------------------------------------------------------------
# EventTypes
# ---------------------------------------------------------------------------

class TestEventTypes:
    def test_crud_events(self):
        assert EventTypes.NEW_RECORD.value == "newRecord"
        assert EventTypes.UPDATE_RECORD.value == "updateRecord"
        assert EventTypes.DELETE_RECORD.value == "deleteRecord"

    def test_reindex_events(self):
        assert EventTypes.REINDEX_RECORD.value == "reindexRecord"
        assert EventTypes.REINDEX_FAILED.value == "reindexFailed"

    def test_bulk_delete_event(self):
        assert EventTypes.BULK_DELETE_RECORDS.value == "bulkDeleteRecords"


# ---------------------------------------------------------------------------
# AccountType, ConnectorScopes, AppStatus, ProgressStatus, QdrantCollectionNames
# ---------------------------------------------------------------------------

class TestAccountType:
    def test_values(self):
        assert AccountType.INDIVIDUAL.value == "individual"
        assert AccountType.ENTERPRISE.value == "enterprise"
        assert AccountType.ADMIN.value == "admin"


class TestConnectorScopes:
    def test_personal_and_team(self):
        assert ConnectorScopes.PERSONAL.value == "personal"
        assert ConnectorScopes.TEAM.value == "team"


class TestAppStatus:
    def test_statuses(self):
        assert AppStatus.IDLE.value == "IDLE"
        assert AppStatus.FULL_SYNCING.value == "FULL_SYNCING"
        assert AppStatus.SYNCING.value == "SYNCING"


class TestProgressStatus:
    def test_terminal_states(self):
        assert ProgressStatus.COMPLETED.value == "COMPLETED"
        assert ProgressStatus.FAILED.value == "FAILED"
        assert ProgressStatus.NOT_STARTED.value == "NOT_STARTED"

    def test_queued_state(self):
        assert ProgressStatus.QUEUED.value == "QUEUED"

    def test_multimodal_enable_state(self):
        assert ProgressStatus.ENABLE_MULTIMODAL_MODELS.value == "ENABLE_MULTIMODAL_MODELS"


class TestQdrantCollectionNames:
    def test_records_collection(self):
        assert QdrantCollectionNames.RECORDS.value == "records"
