"""Unit tests for app.schema.node_validator module."""

from unittest.mock import patch

import pytest

from app.schema import node_validator
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


# ---------------------------------------------------------------------------
# Validator compilation cache
# ---------------------------------------------------------------------------
class TestValidatorCache:
    """Validators are compiled once per schema instead of per record.

    Batch upserts validate every node individually, so rebuilding the validator
    and its reference registry per call dominated the connector's CPU. These
    pin both halves of the fix: that it caches, and that the cache cannot serve
    a validator built from a schema that has since been replaced.
    """

    def _make_validator(self):
        return NodeSchemaValidator()

    def test_validator_is_compiled_once_per_schema(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "additionalProperties": True,
        }
        validator = self._make_validator()
        with patch(
            "app.schema.node_validator.get_node_schema", return_value=schema
        ), patch(
            "app.schema.node_validator._compile", wraps=node_validator._compile
        ) as compile_spy:
            for _ in range(5):
                validator.validate_node("cacheCol", {"name": "x"})
                validator.validate_node_update("cacheCol", {"name": "x"})

        # One full validator + one partial validator, not one pair per call.
        assert compile_spy.call_count == 2

    def test_replacing_the_schema_invalidates_the_cache(self):
        strict = {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ["active"]}},
            "additionalProperties": True,
        }
        relaxed = {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "additionalProperties": True,
        }
        validator = self._make_validator()

        with patch("app.schema.node_validator.get_node_schema", return_value=strict):
            with pytest.raises(SchemaValidationError):
                validator.validate_node("swapCol", {"status": "gone"})

        # Same collection name, different schema object: the cached validator
        # must not be reused.
        with patch("app.schema.node_validator.get_node_schema", return_value=relaxed):
            validator.validate_node("swapCol", {"status": "gone"})

    def test_instance_is_not_mutated(self):
        """The per-record path no longer deep-copies, so validation must leave
        the caller's dict alone — including when _id has to be stripped."""
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "additionalProperties": False,
        }
        node = {"name": "x", "_id": "people/123"}
        validator = self._make_validator()
        with patch(
            "app.schema.node_validator.get_node_schema", return_value=schema
        ):
            validator.validate_node("noMutateCol", node)
            validator.validate_node_update("noMutateCol", node)

        assert node == {"name": "x", "_id": "people/123"}
