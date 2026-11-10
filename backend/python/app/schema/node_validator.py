"""
Node Schema Validator

Provides application-level validation of node and edge properties using JSON Schema,
for the graph backends that have no schema enforcement of their own (Neo4j, Postgres).
This mirrors the validation that ArangoDB performs at the database engine level.

Validators are compiled once per collection and reused. `jsonschema.validate()`
rebuilds the validator class and its reference registry on every call, which is
fine occasionally but not on a per-record path: batch upserts validate every
node individually (neo4j_provider.batch_upsert_nodes), and profiling a
concurrent sync showed ~60% of the connector's event-loop CPU going into
jsonschema/referencing setup rather than into the actual validation walk.
"""

import logging
import os
from collections.abc import Callable
from typing import Any, Optional

from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validators
from jsonschema.exceptions import best_match
from jsonschema.protocols import Validator

from app.schema.node_schema_registry import get_edge_schema, get_node_schema


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


#: Properties a backend keeps on a node or edge for its own bookkeeping: the listing sort name and
#: scope, and the transient lock and created markers one statement sets and removes. No schema
#: declares them; they are stripped before validation, never rejected.
INTERNAL_PROPERTIES = frozenset({
    "khSortName", "khScope",
    "matchLock", "moveLock", "purgeLock", "_aliasLock",
    "createdMark", "__created", "edgeNew",
})

#: Edge endpoints, in both input formats ({_from, _to} and {from_id, to_id, from_collection, to_collection}).
EDGE_ENDPOINT_KEYS = frozenset({"_from", "_to", "from_id", "to_id", "from_collection", "to_collection"})

# `_id` is ArangoDB-only and declared nowhere; `_key` is the same field as `id` (adapt_schema renames it).
_NODE_STRIP = INTERNAL_PROPERTIES | {"_id", "_key"}
_EDGE_STRIP = _NODE_STRIP | EDGE_ENDPOINT_KEYS

_ENFORCEMENT_ENV = "GRAPH_SCHEMA_ENFORCEMENT"


_DISABLED_VALUES = frozenset({"off", "false", "0", "no", "disabled"})
_warned_values: set[str] = set()


def _enforced() -> bool:
    """Read on every call so the switch works without a restart.

    The disabled spellings turn it off; any other unknown value stays strict and is warned about once.
    """
    value = os.environ.get(_ENFORCEMENT_ENV, "strict").strip().lower()
    if value in _DISABLED_VALUES:
        return False
    if value not in ("", "strict") and value not in _warned_values:
        _warned_values.add(value)
        logging.getLogger(__name__).warning(
            "Unrecognised %s=%r: schema enforcement stays strict (use 'off' to disable)", _ENFORCEMENT_ENV, value
        )
    return True


#: collection -> (schema object it was built from, compiled validator)
_FULL_CACHE: dict[str, tuple[dict, Validator]] = {}
_PARTIAL_CACHE: dict[str, tuple[dict, Validator]] = {}
_EDGE_FULL_CACHE: dict[str, tuple[dict, Validator]] = {}
_EDGE_PARTIAL_CACHE: dict[str, tuple[dict, Validator]] = {}


def _cached(
    cache: dict[str, tuple[dict, Validator]],
    collection: str,
    build: Callable[[dict], Validator],
    lookup: Callable[[str], dict | None] | None = None,
) -> Validator | None:
    """Compiled validator for `collection`, rebuilt if its schema was replaced.

    Keyed on the schema object's identity rather than the collection name alone.
    The registry hands back the same object every time in production, so this is
    a plain cache hit — but a name-only key would silently keep serving a stale
    validator if an entry were ever swapped, which is both a real (if unlikely)
    correctness trap and the reason the unit tests patch get_node_schema.
    """
    schema = (lookup or get_node_schema)(collection)
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
    """Validator for partial updates: everything but `required`.

    A shallow copy is enough and deliberate — only a top-level key is dropped, so the
    nested `properties` mapping is shared with the registry rather than
    deep-copied per call. Nothing mutates it after construction. `additionalProperties`
    stays as the schema declares it: ArangoDB enforces it on an update too.
    """
    return _compile({key: value for key, value in schema.items() if key != "required"})


def _partial_validator(collection: str) -> Validator | None:
    return _cached(_PARTIAL_CACHE, collection, _build_partial)


def _full_edge_validator(collection: str) -> Validator | None:
    return _cached(_EDGE_FULL_CACHE, collection, _compile, get_edge_schema)


def _partial_edge_validator(collection: str) -> Validator | None:
    return _cached(_EDGE_PARTIAL_CACHE, collection, _build_partial, get_edge_schema)


def _clean(collection: str, payload: dict[str, Any], strip: frozenset[str]) -> dict[str, Any]:
    """Drop the keys no schema declares (`strip`) and map `_key` to `id`.

    Returns the original mapping untouched when there is nothing to strip:
    validation does not mutate its instance, so copying every payload just to
    remove absent keys is pure overhead on the per-record path.
    """
    if strip.isdisjoint(payload):
        return payload
    for key in INTERNAL_PROPERTIES.intersection(payload):
        if not (payload[key] is None or isinstance(payload[key], (str, int, float, bool))):
            raise SchemaValidationError(collection, f"at '{key}': internal property must be a string, number, boolean or null")
    if "_key" in payload and not isinstance(payload["_key"], str):
        raise SchemaValidationError(collection, "at '_key': must be a string")
    # A differing `_key` is not an error: providers let the requested key win over a stale one in the node.
    cleaned = {key: value for key, value in payload.items() if key not in strip}
    if "_key" in payload and "id" not in payload:
        cleaned["id"] = payload["_key"]
    return cleaned


def _raise(collection: str, error: JsonSchemaValidationError) -> None:
    error_path = ".".join(str(p) for p in error.path) if error.path else "root"
    raise SchemaValidationError(collection, f"at '{error_path}': {error.message}", error)


def _check(validator: Validator | None, collection: str, payload: dict[str, Any]) -> None:
    # Collections without schemas pass validation silently.
    if validator is None:
        return
    # best_match rather than the first error: it is what jsonschema.validate()
    # selects, so the reported message stays the same as before.
    error = best_match(validator.iter_errors(payload))
    if error is not None:
        _raise(collection, error)


class NodeSchemaValidator:
    """
    Validates node and edge properties against JSON Schemas before writing to a graph backend.

    This enforces the same constraints ArangoDB enforces at the database level, but in the
    application layer. Collections without a schema pass silently, and
    GRAPH_SCHEMA_ENFORCEMENT=off turns every method into a no-op.
    """

    def __init__(self) -> None:
        """Initialize the validator."""
        pass

    def validate_node(self, collection: str, node: dict[str, Any]) -> None:
        """
        Validate a node against its schema (full validation).

        Used for creates and whole-node replaces. Validates types, required
        fields, enums, and additionalProperties constraints.

        Raises:
            SchemaValidationError: If validation fails
        """
        if _enforced():
            _check(_full_validator(collection), collection, _clean(collection, node, _NODE_STRIP))

    def validate_node_update(self, collection: str, updates: dict[str, Any]) -> None:
        """
        Validate a partial node update against its schema (partial validation).

        Used for updates where only some fields are being modified. Validates
        types, enums and additionalProperties for the fields that are present,
        but does not require all 'required' fields to be present.

        Raises:
            SchemaValidationError: If validation fails
        """
        if _enforced():
            _check(_partial_validator(collection), collection, _clean(collection, updates, _NODE_STRIP))

    def validate_node_upsert(self, collection: str, node: dict[str, Any]) -> list[str]:
        """
        Validate an upsert payload, which may be partial when the node already exists.

        Checks what is present like validate_node_update, then returns the names of
        the required fields the payload lacks (empty = complete). Whether a missing
        name is an error depends on whether the node exists, which the caller knows.

        Raises:
            SchemaValidationError: If a field that is present is invalid
        """
        if not _enforced():
            return []
        payload = _clean(collection, node, _NODE_STRIP)
        _check(_partial_validator(collection), collection, payload)
        schema = get_node_schema(collection)
        if schema is None:
            return []
        return [name for name in (schema.get("required") or ()) if name not in payload]

    def validate_edge(self, collection: str, edge: dict[str, Any]) -> None:
        """Validate a whole edge against its schema; endpoint keys are ignored, in either format."""
        if _enforced():
            _check(_full_edge_validator(collection), collection, _clean(collection, edge, _EDGE_STRIP))

    def validate_edge_update(self, collection: str, edge: dict[str, Any]) -> None:
        """Validate a partial edge update: types, enums and additionalProperties, no `required`."""
        if _enforced():
            _check(_partial_edge_validator(collection), collection, _clean(collection, edge, _EDGE_STRIP))
