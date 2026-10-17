"""
Node Schema Validator

Provides application-level validation for Neo4j node properties using JSON Schema.
This mirrors the validation that ArangoDB performs at the database engine level.

Validators are compiled once per collection and reused. `jsonschema.validate()`
rebuilds the validator class and its reference registry on every call, which is
fine occasionally but not on a per-record path: batch upserts validate every
node individually (neo4j_provider.batch_upsert_nodes), and profiling a
concurrent sync showed ~60% of the connector's event-loop CPU going into
jsonschema/referencing setup rather than into the actual validation walk.
"""

from typing import Any, Callable, Dict, Optional

from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validators
from jsonschema.exceptions import best_match
from jsonschema.protocols import Validator

from app.schema.node_schema_registry import get_node_schema


class SchemaValidationError(Exception):
    """
    Custom exception for schema validation failures.

    Wraps jsonschema.ValidationError with additional context about the collection
    and provides a clear error message.
    """

    def __init__(self, collection: str, message: str, original_error: Optional[Exception] = None) -> None:
        self.collection = collection
        self.original_error = original_error
        super().__init__(f"Schema validation failed for collection '{collection}': {message}")


def _compile(schema: dict) -> Validator:
    validator_cls = validators.validator_for(schema)
    validator_cls.check_schema(schema)
    return validator_cls(schema)


#: collection -> (schema object it was built from, compiled validator)
_FULL_CACHE: dict[str, tuple[dict, Validator]] = {}
_PARTIAL_CACHE: dict[str, tuple[dict, Validator]] = {}


def _cached(
    cache: dict[str, tuple[dict, Validator]],
    collection: str,
    build: Callable[[dict], Validator],
) -> Validator | None:
    """Compiled validator for `collection`, rebuilt if its schema was replaced.

    Keyed on the schema object's identity rather than the collection name alone.
    The registry hands back the same object every time in production, so this is
    a plain cache hit — but a name-only key would silently keep serving a stale
    validator if an entry were ever swapped, which is both a real (if unlikely)
    correctness trap and the reason the unit tests patch get_node_schema.
    """
    schema = get_node_schema(collection)
    if schema is None:
        return None
    entry = cache.get(collection)
    if entry is not None and entry[0] is schema:
        return entry[1]
    validator = build(schema)
    cache[collection] = (schema, validator)
    return validator


def _full_validator(collection: str) -> Validator | None:
    """Validator enforcing the whole schema, including `required`."""
    return _cached(_FULL_CACHE, collection, _compile)


def _build_partial(schema: dict) -> Validator:
    """Validator for partial updates: types and enums, but no `required`.

    A shallow copy is enough and deliberate — only top-level keys change, so the
    nested `properties` mapping is shared with the registry rather than
    deep-copied per call. Nothing mutates it after construction.
    """
    partial = {key: value for key, value in schema.items() if key != "required"}
    # Updates legitimately carry fields outside the schema; we still want the
    # fields that ARE in the schema to be type-checked.
    partial["additionalProperties"] = True
    return _compile(partial)


def _partial_validator(collection: str) -> Validator | None:
    return _cached(_PARTIAL_CACHE, collection, _build_partial)


def _without_id(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop the ArangoDB-only composite `_id`, which no schema declares.

    Returns the original mapping untouched when there is nothing to strip:
    validation does not mutate its instance, so copying every payload just to
    remove an absent key is pure overhead on the per-record path.
    """
    if "_id" not in payload:
        return payload
    return {key: value for key, value in payload.items() if key != "_id"}


def _raise(collection: str, error: JsonSchemaValidationError) -> None:
    error_path = ".".join(str(p) for p in error.path) if error.path else "root"
    raise SchemaValidationError(collection, f"at '{error_path}': {error.message}", error)


class NodeSchemaValidator:
    """
    Validates node properties against JSON Schemas before writing to Neo4j.

    This ensures data integrity by enforcing the same constraints that ArangoDB
    enforces at the database level, but applied in the application layer for Neo4j.
    """

    def __init__(self) -> None:
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
        validator = _full_validator(collection)

        # Collections without schemas pass validation silently
        if validator is None:
            return

        # best_match rather than the first error: it is what jsonschema.validate()
        # selects, so the reported message stays the same as before.
        error = best_match(validator.iter_errors(_without_id(node)))
        if error is not None:
            _raise(collection, error)

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
        validator = _partial_validator(collection)

        if validator is None:
            return

        error = best_match(validator.iter_errors(_without_id(updates)))
        if error is not None:
            _raise(collection, error)
