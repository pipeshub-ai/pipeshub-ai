"""Unit tests for app.schema.node_validator module."""

from unittest.mock import patch

import pytest

from app.schema.node_validator import NodeSchemaValidator, SchemaValidationError


# ---------------------------------------------------------------------------
# SchemaValidationError
# ---------------------------------------------------------------------------
class TestSchemaValidationError:
    """Tests for SchemaValidationError exception."""

    def test_message_includes_collection(self):
        err = SchemaValidationError("myCollection", "field X is invalid")
        assert "myCollection" in str(err)
        assert "field X is invalid" in str(err)

    def test_collection_attribute(self):
        err = SchemaValidationError("col1", "msg")
        assert err.collection == "col1"

    def test_original_error_stored(self):
        original = ValueError("orig")
        err = SchemaValidationError("col1", "msg", original_error=original)
        assert err.original_error is original

    def test_original_error_none_by_default(self):
        err = SchemaValidationError("col1", "msg")
        assert err.original_error is None

    def test_is_exception(self):
        err = SchemaValidationError("col1", "msg")
        assert isinstance(err, Exception)


# ---------------------------------------------------------------------------
# NodeSchemaValidator.__init__
# ---------------------------------------------------------------------------
class TestNodeSchemaValidatorInit:
    """Tests for NodeSchemaValidator initialization."""

    def test_init(self):
        validator = NodeSchemaValidator()
        assert validator is not None


# ---------------------------------------------------------------------------
# NodeSchemaValidator.validate_node
# ---------------------------------------------------------------------------
class TestValidateNode:
    """Tests for validate_node() - full validation."""

    def _make_validator(self):
        return NodeSchemaValidator()

    def test_collection_without_schema_passes(self):
        """Collections with no schema should pass validation silently."""
        validator = self._make_validator()
        with patch(
            "app.schema.node_validator.get_node_schema", return_value=None
        ):
            validator.validate_node("noSchemaCol", {"any": "data"})

    def test_valid_node_passes(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
            "required": ["name"],
            "additionalProperties": False,
        }
        validator = self._make_validator()
        with patch(
            "app.schema.node_validator.get_node_schema", return_value=schema
        ):
            validator.validate_node("testCol", {"name": "Alice", "age": 30})

    def test_missing_required_field_raises(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
            "additionalProperties": True,
        }
        validator = self._make_validator()
        with patch(
            "app.schema.node_validator.get_node_schema", return_value=schema
        ):
            with pytest.raises(SchemaValidationError) as exc_info:
                validator.validate_node("testCol", {"age": 30})
            assert "testCol" in str(exc_info.value)

    def test_wrong_type_raises(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
            "additionalProperties": True,
        }
        validator = self._make_validator()
        with patch(
            "app.schema.node_validator.get_node_schema", return_value=schema
        ):
            with pytest.raises(SchemaValidationError):
                validator.validate_node("testCol", {"name": 123})

    def test_additional_properties_rejected(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
            "additionalProperties": False,
        }
        validator = self._make_validator()
        with patch(
            "app.schema.node_validator.get_node_schema", return_value=schema
        ):
            with pytest.raises(SchemaValidationError):
                validator.validate_node("testCol", {"name": "Alice", "extra": "nope"})

    def test_strips_id_field(self):
        """_id field should be stripped before validation."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
            "additionalProperties": False,
        }
        validator = self._make_validator()
        with patch(
            "app.schema.node_validator.get_node_schema", return_value=schema
        ):
            # Should not raise even though _id is extra, because it's stripped
            validator.validate_node("testCol", {"name": "Alice", "_id": "some/id"})

    def test_does_not_mutate_original_node(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
            "additionalProperties": False,
        }
        validator = self._make_validator()
        original = {"name": "Alice", "_id": "some/id"}
        original_copy = original.copy()

        with patch(
            "app.schema.node_validator.get_node_schema", return_value=schema
        ):
            validator.validate_node("testCol", original)

        assert original == original_copy

    def test_error_path_in_message(self):
        schema = {
            "type": "object",
            "properties": {
                "nested": {
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                }
            },
            "additionalProperties": True,
        }
        validator = self._make_validator()
        with patch(
            "app.schema.node_validator.get_node_schema", return_value=schema
        ):
            with pytest.raises(SchemaValidationError) as exc_info:
                validator.validate_node("testCol", {"nested": {"value": "not-int"}})
            assert "nested" in str(exc_info.value)

    def test_enum_validation(self):
        schema = {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["active", "inactive"]},
            },
            "required": ["status"],
            "additionalProperties": True,
        }
        validator = self._make_validator()
        with patch(
            "app.schema.node_validator.get_node_schema", return_value=schema
        ):
            # Valid enum value
            validator.validate_node("testCol", {"status": "active"})

            # Invalid enum value
            with pytest.raises(SchemaValidationError):
                validator.validate_node("testCol", {"status": "unknown"})


# ---------------------------------------------------------------------------
# NodeSchemaValidator.validate_node_update
# ---------------------------------------------------------------------------
class TestValidateNodeUpdate:
    """Tests for validate_node_update() - partial validation."""

    def _make_validator(self):
        return NodeSchemaValidator()

    def test_collection_without_schema_passes(self):
        validator = self._make_validator()
        with patch(
            "app.schema.node_validator.get_node_schema", return_value=None
        ):
            validator.validate_node_update("noSchemaCol", {"any": "data"})

    def test_partial_update_skips_required(self):
        """Update should not enforce 'required' constraint."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
            "required": ["name", "age"],
            "additionalProperties": False,
        }
        validator = self._make_validator()
        with patch(
            "app.schema.node_validator.get_node_schema", return_value=schema
        ):
            # Only updating 'age', missing 'name' should not raise
            validator.validate_node_update("testCol", {"age": 25})

    def test_type_validation_still_applies(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
            "additionalProperties": False,
        }
        validator = self._make_validator()
        with patch(
            "app.schema.node_validator.get_node_schema", return_value=schema
        ):
            with pytest.raises(SchemaValidationError):
                validator.validate_node_update("testCol", {"name": 123})

    def test_allows_additional_properties(self):
        """Updates should allow extra fields even if schema forbids them."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
            "additionalProperties": False,
        }
        validator = self._make_validator()
        with patch(
            "app.schema.node_validator.get_node_schema", return_value=schema
        ):
            # Should not raise - additional properties allowed in updates
            validator.validate_node_update("testCol", {"name": "Bob", "extra": "ok"})

    def test_strips_id_field(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "additionalProperties": True,
        }
        validator = self._make_validator()
        with patch(
            "app.schema.node_validator.get_node_schema", return_value=schema
        ):
            validator.validate_node_update(
                "testCol", {"name": "Alice", "_id": "col/key"}
            )

    def test_does_not_mutate_original(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "additionalProperties": True,
        }
        validator = self._make_validator()
        original = {"name": "Alice", "_id": "col/key"}
        original_copy = original.copy()

        with patch(
            "app.schema.node_validator.get_node_schema", return_value=schema
        ):
            validator.validate_node_update("testCol", original)

        assert original == original_copy

    def test_error_path_in_message(self):
        schema = {
            "type": "object",
            "properties": {
                "count": {"type": "integer"},
            },
            "additionalProperties": True,
        }
        validator = self._make_validator()
        with patch(
            "app.schema.node_validator.get_node_schema", return_value=schema
        ):
            with pytest.raises(SchemaValidationError) as exc_info:
                validator.validate_node_update("testCol", {"count": "not-int"})
            assert "count" in str(exc_info.value)

    def test_does_not_modify_original_schema(self):
        """The original schema from registry should not be mutated."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
            "additionalProperties": False,
        }
        import copy
        original_schema = copy.deepcopy(schema)

        validator = self._make_validator()
        with patch(
            "app.schema.node_validator.get_node_schema", return_value=schema
        ):
            validator.validate_node_update("testCol", {"name": "Bob"})

        # Original schema should be unchanged
        assert schema == original_schema

    def test_enum_validation_on_update(self):
        schema = {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["active", "inactive"]},
            },
            "additionalProperties": True,
        }
        validator = self._make_validator()
        with patch(
            "app.schema.node_validator.get_node_schema", return_value=schema
        ):
            validator.validate_node_update("testCol", {"status": "active"})

            with pytest.raises(SchemaValidationError):
                validator.validate_node_update("testCol", {"status": "unknown"})

    def test_empty_update(self):
        """Empty update dict should pass."""
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        }
        validator = self._make_validator()
        with patch(
            "app.schema.node_validator.get_node_schema", return_value=schema
        ):
            validator.validate_node_update("testCol", {})
