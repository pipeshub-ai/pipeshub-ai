"""Unit tests for app.schema.node_schema_registry module."""

import copy
from unittest.mock import patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.schema.node_schema_registry import (
    NODE_SCHEMA_REGISTRY,
    adapt_schema,
    get_node_schema,
    get_required_fields,
)


# ---------------------------------------------------------------------------
# adapt_schema
# ---------------------------------------------------------------------------
class TestAdaptSchema:
    """Tests for adapt_schema()."""

    def test_none_input_returns_none(self):
        assert adapt_schema(None) is None

    def test_no_rule_key_returns_as_is(self):
        raw = {"type": "object", "properties": {"name": {"type": "string"}}}
        result = adapt_schema(raw)
        assert result == raw

    def test_extracts_rule(self):
        raw = {
            "rule": {
                "type": "object",
                "properties": {"_key": {"type": "string"}, "name": {"type": "string"}},
            },
            "level": "strict",
            "message": "invalid",
        }
        result = adapt_schema(raw)
        # level and message should be stripped
        assert "level" not in result
        assert "message" not in result
        assert result["type"] == "object"

    def test_remaps_key_to_id(self):
        raw = {
            "rule": {
                "type": "object",
                "properties": {"_key": {"type": "string"}, "name": {"type": "string"}},
            },
        }
        result = adapt_schema(raw)
        assert "_key" not in result["properties"]
        assert "id" in result["properties"]
        assert result["properties"]["id"] == {"type": "string"}

    def test_adds_id_property_when_key_absent(self):
        raw = {
            "rule": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
            },
        }
        result = adapt_schema(raw)
        assert "id" in result["properties"]
        assert result["properties"]["id"] == {"type": "string"}

    def test_does_not_mutate_original(self):
        raw = {
            "rule": {
                "type": "object",
                "properties": {"_key": {"type": "string"}},
            },
        }
        original = copy.deepcopy(raw)
        adapt_schema(raw)
        assert raw == original

    def test_no_properties_key(self):
        raw = {
            "rule": {"type": "object"},
        }
        result = adapt_schema(raw)
        assert result == {"type": "object"}
        assert "properties" not in result


# ---------------------------------------------------------------------------
# NODE_SCHEMA_REGISTRY
# ---------------------------------------------------------------------------
class TestNodeSchemaRegistry:
    """Tests for NODE_SCHEMA_REGISTRY mapping."""

    def test_registry_is_dict(self):
        assert isinstance(NODE_SCHEMA_REGISTRY, dict)

    def test_records_has_schema(self):
        schema = NODE_SCHEMA_REGISTRY.get(CollectionNames.RECORDS.value)
        assert schema is not None
        assert "type" in schema

    def test_drives_has_no_schema(self):
        assert NODE_SCHEMA_REGISTRY.get(CollectionNames.DRIVES.value) is None

    def test_files_has_schema(self):
        schema = NODE_SCHEMA_REGISTRY.get(CollectionNames.FILES.value)
        assert schema is not None

    def test_files_schema_allows_local_fs_relative_path(self):
        schema = NODE_SCHEMA_REGISTRY.get(CollectionNames.FILES.value)
        assert schema is not None
        assert schema["properties"]["localFsRelativePath"] == {
            "type": ["string", "null"]
        }

    def test_groups_has_no_schema(self):
        assert NODE_SCHEMA_REGISTRY.get(CollectionNames.GROUPS.value) is None

    def test_anyone_has_no_schema(self):
        assert NODE_SCHEMA_REGISTRY.get(CollectionNames.ANYONE.value) is None

    def test_all_entries_are_none_or_dict(self):
        for name, schema in NODE_SCHEMA_REGISTRY.items():
            assert schema is None or isinstance(schema, dict), (
                f"Registry entry for '{name}' is neither None nor dict"
            )

    def test_registry_contains_expected_collections(self):
        """Verify key collections are present."""
        expected = [
            CollectionNames.RECORDS.value,
            CollectionNames.FILES.value,
            CollectionNames.USERS.value,
            CollectionNames.ORGS.value,
            CollectionNames.MAILS.value,
            CollectionNames.LINKS.value,
            CollectionNames.PEOPLE.value,
            CollectionNames.APPS.value,
            CollectionNames.DEPARTMENTS.value,
            CollectionNames.TEAMS.value,
            CollectionNames.TICKETS.value,
            CollectionNames.PROJECTS.value,
            CollectionNames.AGENT_INSTANCES.value,
            CollectionNames.AGENT_TEMPLATES.value,
        ]
        for col in expected:
            assert col in NODE_SCHEMA_REGISTRY

    def test_adapted_schemas_have_id_not_key(self):
        """All adapted schemas should have 'id' instead of '_key'."""
        for name, schema in NODE_SCHEMA_REGISTRY.items():
            if schema is not None and "properties" in schema:
                assert "_key" not in schema["properties"], (
                    f"'{name}' still has '_key' in properties"
                )


# ---------------------------------------------------------------------------
# get_node_schema
# ---------------------------------------------------------------------------
class TestGetNodeSchema:
    """Tests for get_node_schema()."""

    def test_existing_collection(self):
        schema = get_node_schema(CollectionNames.RECORDS.value)
        assert schema is not None
        assert isinstance(schema, dict)

    def test_collection_without_schema(self):
        assert get_node_schema(CollectionNames.DRIVES.value) is None

    def test_unknown_collection(self):
        assert get_node_schema("nonexistent_collection") is None

    def test_returns_same_reference_as_registry(self):
        schema = get_node_schema(CollectionNames.RECORDS.value)
        assert schema is NODE_SCHEMA_REGISTRY[CollectionNames.RECORDS.value]


# ---------------------------------------------------------------------------
# get_required_fields
# ---------------------------------------------------------------------------
class TestGetRequiredFields:
    """Tests for get_required_fields()."""

    def test_collection_with_required_fields(self):
        fields = get_required_fields(CollectionNames.USERS.value)
        assert isinstance(fields, list)
        assert len(fields) > 0
        assert "email" in fields

    def test_collection_without_schema(self):
        fields = get_required_fields(CollectionNames.DRIVES.value)
        assert fields == []

    def test_unknown_collection(self):
        fields = get_required_fields("nonexistent_collection")
        assert fields == []

    def test_collection_with_schema_but_no_required(self):
        """If a schema has no 'required' key, return empty list."""
        # Use a collection that has a schema; verify behavior handles missing 'required'
        # Create a controlled scenario
        with patch(
            "app.schema.node_schema_registry.NODE_SCHEMA_REGISTRY",
            {"test_col": {"type": "object", "properties": {"name": {"type": "string"}}}},
        ):
            from app.schema.node_schema_registry import get_required_fields as _grf

            fields = _grf("test_col")
            assert fields == []
