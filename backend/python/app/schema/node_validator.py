"""
Node Schema Validator

Provides application-level validation for Neo4j node properties using JSON Schema.
This mirrors the validation that ArangoDB performs at the database engine level.
"""

import copy
from typing import Any, Dict, Optional

import jsonschema
from jsonschema import ValidationError as JsonSchemaValidationError

from app.schema.node_schema_registry import get_node_schema


class SchemaValidationError(Exception):
    """
    Custom exception for schema validation failures.

    Wraps jsonschema.ValidationError with additional context about the collection
    and provides a clear error message.
    """

    def __init__(self, collection: str, message: str, original_error: Optional[Exception] = None):
        self.collection = collection
        self.original_error = original_error
        super().__init__(f"Schema validation failed for collection '{collection}': {message}")


class NodeSchemaValidator:
    """
    Validates node properties against JSON Schemas before writing to Neo4j.

    This ensures data integrity by enforcing the same constraints that ArangoDB
    enforces at the database level, but applied in the application layer for Neo4j.
    """

    def __init__(self):
        """Initialize the validator."""
        pass

    def validate_node(self, collection: str, node: Dict[str, Any]) -> None:
        """
        Validate a node against its schema (full validation).

        Used for inserts/upserts where all required fields should be present.
        Validates types, required fields, enums, and additionalProperties constraints.

        Args:
            collection: Collection name
            node: Node dictionary to validate

        Raises:
            SchemaValidationError: If validation fails
        """
        schema = get_node_schema(collection)

        # Collections without schemas pass validation silently
        if schema is None:
            return

        # Create a copy of the node to avoid modifying the original
        node_to_validate = copy.deepcopy(node)

        # Strip '_id' field if present (it's an ArangoDB composite field, not in schemas)
        node_to_validate.pop("_id", None)

        try:
            jsonschema.validate(instance=node_to_validate, schema=schema)
        except JsonSchemaValidationError as e:
            # Extract a clear error message from the validation error
            error_path = ".".join(str(p) for p in e.path) if e.path else "root"
            error_msg = f"at '{error_path}': {e.message}"
            raise SchemaValidationError(collection, error_msg, e)

    def validate_node_update(self, collection: str, updates: Dict[str, Any]) -> None:
        """
        Validate a partial node update against its schema (partial validation).

        Used for updates where only some fields are being modified.
        Validates types and enums for the fields that are present, but does not
        require all 'required' fields to be present.

        Args:
            collection: Collection name
            updates: Dictionary of fields to update

        Raises:
            SchemaValidationError: If validation fails
        """
        schema = get_node_schema(collection)

        # Collections without schemas pass validation silently
        if schema is None:
            return

        # Create a modified schema for partial validation
        partial_schema = copy.deepcopy(schema)

        # Remove 'required' constraint (we're only updating some fields)
        partial_schema.pop("required", None)

        # Allow additional properties for updates (even if the schema says no)
        # This is because we might be updating fields that aren't in the original schema
        # but we still want to validate the types of fields that ARE in the schema
        partial_schema["additionalProperties"] = True

        # Create a copy of updates to avoid modifying the original
        updates_to_validate = copy.deepcopy(updates)

        # Strip '_id' field if present
        updates_to_validate.pop("_id", None)

        try:
            jsonschema.validate(instance=updates_to_validate, schema=partial_schema)
        except JsonSchemaValidationError as e:
            # Extract a clear error message from the validation error
            error_path = ".".join(str(p) for p in e.path) if e.path else "root"
            error_msg = f"at '{error_path}': {e.message}"
            raise SchemaValidationError(collection, error_msg, e)
