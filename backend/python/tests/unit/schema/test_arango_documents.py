"""Unit tests for app.schema.arango.documents ArangoDB collection schemas."""

from __future__ import annotations

import app.schema.arango.documents as documents
import pytest
from jsonschema import Draft4Validator

from app.config.constants.arangodb import CollectionNames
from app.schema.node_schema_registry import adapt_schema

# Every *_schema defined in documents.py
ALL_DOCUMENT_SCHEMAS: dict[str, dict] = {
    name: getattr(documents, name)
    for name in dir(documents)
    if name.endswith("_schema") and not name.startswith("_")
}

# Schemas wired to Arango node collections (non-null entries from NODE_COLLECTIONS)
NODE_COLLECTION_SCHEMAS: dict[str, dict] = {
    CollectionNames.RECORDS.value: documents.record_schema,
    CollectionNames.FILES.value: documents.file_record_schema,
    CollectionNames.LINKS.value: documents.link_record_schema,
    CollectionNames.MAILS.value: documents.mail_record_schema,
    CollectionNames.WEBPAGES.value: documents.webpage_record_schema,
    CollectionNames.COMMENTS.value: documents.comment_record_schema,
    CollectionNames.PEOPLE.value: documents.people_schema,
    CollectionNames.USERS.value: documents.user_schema,
    CollectionNames.ROLES.value: documents.app_role_schema,
    CollectionNames.ORGS.value: documents.orgs_schema,
    CollectionNames.APPS.value: documents.app_schema,
    CollectionNames.DEPARTMENTS.value: documents.department_schema,
    CollectionNames.RECORD_GROUPS.value: documents.record_group_schema,
    CollectionNames.AGENT_INSTANCES.value: documents.agent_schema,
    CollectionNames.AGENT_TEMPLATES.value: documents.agent_template_schema,
    CollectionNames.AGENT_KNOWLEDGE.value: documents.knowledge_schema,
    CollectionNames.AGENT_TOOLSETS.value: documents.toolset_schema,
    CollectionNames.AGENT_TOOLS.value: documents.tool_schema,
    CollectionNames.TICKETS.value: documents.ticket_record_schema,
    CollectionNames.MEETINGS.value: documents.meeting_record_schema,
    CollectionNames.PROJECTS.value: documents.project_record_schema,
    CollectionNames.PULLREQUESTS.value: documents.pull_request_record_schema,
    CollectionNames.TEAMS.value: documents.team_schema,
    CollectionNames.PRODUCTS.value: documents.product_record_schema,
    CollectionNames.DEALS.value: documents.deal_record_schema,
    CollectionNames.ARTIFACTS.value: documents.artifact_record_schema,
    CollectionNames.SQL_TABLES.value: documents.sql_table_record_schema,
    CollectionNames.SQL_VIEWS.value: documents.sql_view_record_schema,
    CollectionNames.CODE_FILES.value: documents.code_file_record_schema,
    CollectionNames.MESSAGES.value: documents.message_record_schema,
}


def _collect_format_email_paths(obj: object, prefix: str = "") -> list[str]:
    """Return JSON paths where format is set to email."""
    paths: list[str] = []
    if isinstance(obj, dict):
        if obj.get("format") == "email":
            paths.append(prefix or "$")
        for key, value in obj.items():
            child_prefix = f"{prefix}.{key}" if prefix else key
            paths.extend(_collect_format_email_paths(value, child_prefix))
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            paths.extend(_collect_format_email_paths(item, f"{prefix}[{index}]"))
    return paths


# ---------------------------------------------------------------------------
# Coverage / inventory
# ---------------------------------------------------------------------------
class TestDocumentSchemaInventory:
    def test_all_expected_schemas_are_defined(self):
        expected = {
            "orgs_schema",
            "user_schema",
            "user_group_schema",
            "app_role_schema",
            "app_schema",
            "record_schema",
            "file_record_schema",
            "artifact_record_schema",
            "drive_record_schema",
            "mail_record_schema",
            "webpage_record_schema",
            "comment_record_schema",
            "link_record_schema",
            "ticket_record_schema",
            "project_record_schema",
            "meeting_record_schema",
            "message_record_schema",
            "pull_request_record_schema",
            "sql_table_record_schema",
            "sql_view_record_schema",
            "product_record_schema",
            "deal_record_schema",
            "record_group_schema",
            "department_schema",
            "agent_template_schema",
            "agent_schema",
            "team_schema",
            "people_schema",
            "knowledge_schema",
            "toolset_schema",
            "tool_schema",
            "code_file_record_schema",
        }
        assert expected == set(ALL_DOCUMENT_SCHEMAS.keys())

    @pytest.mark.parametrize("schema_name", sorted(ALL_DOCUMENT_SCHEMAS.keys()))
    def test_each_schema_has_rule_object(self, schema_name: str):
        schema = ALL_DOCUMENT_SCHEMAS[schema_name]
        assert isinstance(schema, dict)
        assert "rule" in schema
        rule = schema["rule"]
        assert isinstance(rule, dict)
        assert rule.get("type") == "object"
        assert "properties" in rule
        assert isinstance(rule["properties"], dict)
        assert len(rule["properties"]) > 0


# ---------------------------------------------------------------------------
# Arango wrapper shape (level / message)
# ---------------------------------------------------------------------------
class TestDocumentSchemaWrapper:
    @pytest.mark.parametrize(
        ("collection_name", "schema"),
        sorted(NODE_COLLECTION_SCHEMAS.items()),
    )
    def test_node_collection_schemas_use_strict_validation_when_wrapped(
        self, collection_name: str, schema: dict
    ):
        if "level" not in schema:
            pytest.skip(f"{collection_name} schema is rule-only (no Arango wrapper)")
        assert schema["level"] == "strict"
        assert isinstance(schema.get("message"), str)
        assert schema["message"]


# ---------------------------------------------------------------------------
# Email field policy (Zammad / RFC quoted local-part regression)
# ---------------------------------------------------------------------------
class TestEmailFieldPolicy:
    def test_user_schema_email_is_string_without_format(self):
        email_prop = documents.user_schema["rule"]["properties"]["email"]
        assert email_prop == {"type": "string"}
        assert "format" not in email_prop

    def test_people_schema_email_is_string_without_format(self):
        email_prop = documents.people_schema["rule"]["properties"]["email"]
        assert email_prop == {"type": "string"}
        assert "format" not in email_prop

    @pytest.mark.parametrize("schema_name,schema", sorted(ALL_DOCUMENT_SCHEMAS.items()))
    def test_no_format_email_anywhere_in_document_schemas(self, schema_name: str, schema: dict):
        paths = _collect_format_email_paths(schema)
        assert paths == [], f"{schema_name} still uses format:email at {paths}"

    def test_user_schema_accepts_quoted_rfc_email_via_jsonschema(self):
        validator = Draft4Validator(adapt_schema(documents.user_schema))
        doc = {
            "id": "test-user-key",
            "email": '"john..doe"@example.com',
        }
        validator.validate(doc)

    def test_people_schema_accepts_quoted_rfc_email_via_jsonschema(self):
        validator = Draft4Validator(adapt_schema(documents.people_schema))
        doc = {
            "id": "test-people-key",
            "email": '"john..doe"@example.com',
            "createdAtTimestamp": 1,
            "updatedAtTimestamp": 1,
        }
        validator.validate(doc)


# ---------------------------------------------------------------------------
# Required fields on key collections
# ---------------------------------------------------------------------------
class TestRequiredFields:
    @pytest.mark.parametrize(
        ("schema_name", "required_field"),
        [
            ("user_schema", "email"),
            ("people_schema", "email"),
            ("orgs_schema", "accountType"),
            ("app_schema", "name"),
            ("record_schema", "recordName"),
            ("team_schema", "name"),
        ],
    )
    def test_required_fields_present(self, schema_name: str, required_field: str):
        rule = ALL_DOCUMENT_SCHEMAS[schema_name]["rule"]
        assert required_field in rule.get("required", [])

    def test_user_schema_disallows_extra_properties(self):
        rule = documents.user_schema["rule"]
        assert rule.get("additionalProperties") is False


# ---------------------------------------------------------------------------
# Node collection wiring
# ---------------------------------------------------------------------------
class TestNodeCollectionSchemaWiring:
    @pytest.mark.parametrize(
        ("collection_name", "schema"),
        sorted(NODE_COLLECTION_SCHEMAS.items()),
    )
    def test_node_collection_schema_is_documents_module_object(
        self, collection_name: str, schema: dict
    ):
        assert isinstance(schema, dict)
        assert "rule" in schema
        assert schema is ALL_DOCUMENT_SCHEMAS[
            next(name for name, value in ALL_DOCUMENT_SCHEMAS.items() if value is schema)
        ]
