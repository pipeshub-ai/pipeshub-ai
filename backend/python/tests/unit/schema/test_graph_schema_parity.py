"""The schemas ArangoDB enforces are the schemas the other graph backends validate against.

Written ahead of the change: the registry-coverage tests fail until the edge registry
(``get_edge_schema`` / ``EDGE_SCHEMA_REGISTRY``) exists and the node registry maps the
collections it is missing, and the INTERNAL_PROPERTIES tests fail until ``node_validator``
defines that list. The draft, ``check_schema`` and JSON_ENCODED_FIELDS checks run now.

A collection with an Arango schema but no registry entry belongs in WAIVERS with a one-line
reason. The set starts empty on purpose.
"""

import importlib
from collections.abc import Iterator

import pytest
from jsonschema import validators

from app.config.constants.arangodb import CollectionNames
from app.schema import node_validator
from app.schema.node_schema_registry import (
    JSON_ENCODED_FIELDS,
    NODE_SCHEMA_REGISTRY,
    adapt_schema,
)
from app.services.graph_db.arango.arango_http_provider import (
    EDGE_COLLECTIONS,
    NODE_COLLECTIONS,
)

# (kind, collection) -> one-line reason the collection may lack a registry schema.
WAIVERS: dict[tuple[str, str], str] = {}

ARANGO_NODE_SCHEMAS: dict[str, dict] = {name: s for name, s in NODE_COLLECTIONS if s is not None}
ARANGO_EDGE_SCHEMAS: dict[str, dict] = {name: s for name, s in EDGE_COLLECTIONS if s is not None}

# Keys the validator strips from an edge before checking it; the planned contract.
EDGE_ENDPOINT_KEYS = frozenset({"_from", "_to", "from_id", "to_id", "from_collection", "to_collection"})

# Keywords ArangoDB's draft-4 validator treats differently from jsonschema's default (latest) draft.
# Boolean exclusiveMinimum/exclusiveMaximum modify minimum/maximum in draft 4 but are errors or
# numeric bounds later; the rest are unknown to draft 4 (Arango ignores them, jsonschema enforces them).
DRAFT_DIVERGENT_KEYWORDS = frozenset({
    "$ref", "$defs", "$id", "$anchor", "$dynamicRef", "$recursiveRef", "format", "dependencies",
    "dependentRequired", "dependentSchemas", "const", "if", "then", "else", "contains", "minContains",
    "maxContains", "propertyNames", "prefixItems", "unevaluatedItems", "unevaluatedProperties",
    "additionalItems", "contentEncoding", "contentMediaType", "contentSchema",
})

_REGISTRY_MODULES = ("app.schema.edge_schema_registry", "app.schema.node_schema_registry")


def _from_registry_modules(name: str) -> object:
    """`name` from wherever the change defines it, or None while it does not exist."""
    for module in _REGISTRY_MODULES:
        try:
            value = getattr(importlib.import_module(module), name, None)
        except ImportError:
            continue
        if value is not None:
            return value
    return None


def _edge_registry() -> dict[str, dict | None] | None:
    return _from_registry_modules("EDGE_SCHEMA_REGISTRY")


def _subschemas(schema: dict, path: str = "root") -> Iterator[tuple[str, dict]]:
    """Yield (path, schema node) for every schema position. Property names are not keywords."""
    yield path, schema
    for key in ("properties", "patternProperties", "definitions", "$defs"):
        for name, sub in (schema.get(key) or {}).items():
            if isinstance(sub, dict):
                yield from _subschemas(sub, f"{path}.{key}.{name}")
    for key in ("anyOf", "allOf", "oneOf"):
        for i, sub in enumerate(schema.get(key) or []):
            if isinstance(sub, dict):
                yield from _subschemas(sub, f"{path}.{key}[{i}]")
    items = schema.get("items")
    for i, sub in enumerate(items if isinstance(items, list) else [items]):
        if isinstance(sub, dict):
            yield from _subschemas(sub, f"{path}.items[{i}]")
    for key in ("additionalProperties", "not"):
        if isinstance(schema.get(key), dict):
            yield from _subschemas(schema[key], f"{path}.{key}")


def _adapted_schemas() -> dict[str, dict]:
    """Every schema the non-Arango backends would validate against, keyed by a readable id."""
    schemas = {f"node:{n}": adapt_schema(s) for n, s in ARANGO_NODE_SCHEMAS.items()}
    schemas.update({f"edge:{n}": adapt_schema(s) for n, s in ARANGO_EDGE_SCHEMAS.items()})
    schemas.update({f"node-registry:{n}": s for n, s in NODE_SCHEMA_REGISTRY.items() if s})
    schemas.update({f"edge-registry:{n}": s for n, s in (_edge_registry() or {}).items() if s})
    return schemas


ADAPTED = _adapted_schemas()


# (a) coverage ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("collection", sorted(ARANGO_NODE_SCHEMAS))
def test_node_collection_with_arango_schema_is_in_registry(collection: str) -> None:
    if ("node", collection) in WAIVERS:
        pytest.skip(WAIVERS[("node", collection)])
    assert NODE_SCHEMA_REGISTRY.get(collection) is not None, (
        f"{collection} has an Arango schema but no node registry schema"
    )


def test_edge_registry_exists() -> None:
    assert _edge_registry() is not None, "EDGE_SCHEMA_REGISTRY is not defined"
    assert _from_registry_modules("get_edge_schema") is not None, "get_edge_schema is not defined"


@pytest.mark.parametrize("collection", sorted(ARANGO_EDGE_SCHEMAS))
def test_edge_collection_with_arango_schema_is_in_registry(collection: str) -> None:
    if ("edge", collection) in WAIVERS:
        pytest.skip(WAIVERS[("edge", collection)])
    registry = _edge_registry()
    assert registry is not None, "EDGE_SCHEMA_REGISTRY is not defined"
    assert registry.get(collection) is not None, f"{collection} has an Arango schema but no edge registry schema"
    get_edge_schema = _from_registry_modules("get_edge_schema")
    assert get_edge_schema is not None and get_edge_schema(collection) is registry[collection]


def test_edge_registry_has_no_unknown_collection() -> None:
    registry = _edge_registry()
    if registry is None:
        pytest.skip("edge registry not built yet")
    assert set(registry) <= {name for name, _ in EDGE_COLLECTIONS}


def test_waivers_name_real_collections() -> None:
    for kind, collection in WAIVERS:
        known = ARANGO_NODE_SCHEMAS if kind == "node" else ARANGO_EDGE_SCHEMAS
        assert collection in known, f"waiver for unknown {kind} collection {collection}"


_REGISTERED_NODES = sorted(set(ARANGO_NODE_SCHEMAS) & {k for k, v in NODE_SCHEMA_REGISTRY.items() if v})


@pytest.mark.parametrize("collection", _REGISTERED_NODES)
def test_registry_schema_is_the_adapted_arango_schema(collection: str) -> None:
    registry_schema = dict(NODE_SCHEMA_REGISTRY[collection])
    registry_schema.pop("patternProperties", None)  # records add flat p_* keys on top of the Arango rule
    expected = dict(adapt_schema(ARANGO_NODE_SCHEMAS[collection]))
    expected.pop("patternProperties", None)
    assert registry_schema == expected


# (b) valid JSON Schema ------------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(ADAPTED))
def test_adapted_schema_passes_check_schema(name: str) -> None:
    schema = ADAPTED[name]
    validators.validator_for(schema).check_schema(schema)


# (c) draft compatibility ----------------------------------------------------------------------------
def _draft_problems(schema: dict) -> list[str]:
    problems = []
    for path, node in _subschemas(schema):
        problems += [f"{path}: {key}" for key in sorted(DRAFT_DIVERGENT_KEYWORDS & set(node))]
        problems += [
            f"{path}: boolean {key}" for key in ("exclusiveMinimum", "exclusiveMaximum")
            if isinstance(node.get(key), bool)
        ]
    return problems


@pytest.mark.parametrize("name", sorted(ADAPTED))
def test_schema_uses_no_keyword_that_differs_between_drafts(name: str) -> None:
    assert not _draft_problems(ADAPTED[name])


def test_draft_scan_flags_each_divergent_keyword() -> None:
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "number", "minimum": 0, "exclusiveMinimum": True},
            "b": {"anyOf": [{"const": 1}, {"$ref": "#/x"}]},
            "c": {"type": "array", "items": {"type": "string", "format": "uri"}},
            "format": {"type": "string"},  # a property named like a keyword is fine
        },
    }
    assert sorted(_draft_problems(schema)) == [
        "root.properties.a: boolean exclusiveMinimum",
        "root.properties.b.anyOf[0]: const",
        "root.properties.b.anyOf[1]: $ref",
        "root.properties.c.items[0]: format",
    ]


# (d) JSON-encoded fields ----------------------------------------------------------------------------
def _types(prop: dict) -> set:
    declared = prop.get("type")
    return set(declared) if isinstance(declared, list) else {declared}


def _is_object_like(prop: dict) -> bool:
    return "object" in _types(prop) or (
        "array" in _types(prop) and bool(_types(prop.get("items") or {}) & {"object", "array"})
    )


# Has no schema, so there is nothing to check its list against.
_JSON_FIELDS_WITHOUT_SCHEMA = {CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value}


@pytest.mark.parametrize("collection", sorted(set(JSON_ENCODED_FIELDS) - _JSON_FIELDS_WITHOUT_SCHEMA))
def test_json_encoded_fields_are_declared_object_in_schema(collection: str) -> None:
    assert collection in ARANGO_NODE_SCHEMAS, f"{collection} lists JSON fields but has no schema"
    properties = adapt_schema(ARANGO_NODE_SCHEMAS[collection]).get("properties", {})
    for field in JSON_ENCODED_FIELDS[collection]:
        assert field in properties, f"{collection}.{field} is not declared"
        assert _is_object_like(properties[field]), f"{collection}.{field} is not an object or array of objects"


@pytest.mark.parametrize("collection", sorted(ARANGO_NODE_SCHEMAS))
def test_every_object_field_is_json_encoded(collection: str) -> None:
    properties = adapt_schema(ARANGO_NODE_SCHEMAS[collection]).get("properties", {})
    expected = {name for name, prop in properties.items() if _is_object_like(prop)}
    assert set(JSON_ENCODED_FIELDS.get(collection, ())) == expected


# (e) internal properties ----------------------------------------------------------------------------
def _internal_properties() -> frozenset[str] | None:
    value = getattr(node_validator, "INTERNAL_PROPERTIES", None)
    return None if value is None else frozenset(value)


def test_internal_properties_list_exists() -> None:
    internal = _internal_properties()
    assert internal, "node_validator.INTERNAL_PROPERTIES is not defined or is empty"
    assert {"khSortName", "khScope"} <= internal


@pytest.mark.parametrize("name", sorted(ADAPTED))
def test_internal_properties_do_not_shadow_a_declared_property(name: str) -> None:
    internal = _internal_properties()
    if internal is None:
        pytest.skip("INTERNAL_PROPERTIES not built yet")
    declared = set(ADAPTED[name].get("properties", {}))
    assert not internal & declared, f"{name} declares internal properties {sorted(internal & declared)}"


@pytest.mark.parametrize("name", sorted(n for n in ADAPTED if n.startswith("edge")))
def test_edge_schemas_do_not_depend_on_endpoint_keys(name: str) -> None:
    # validate_edge strips the endpoint keys before checking. A schema that requires one would reject
    # every edge. Declaring `_from`/`_to` is fine (Arango does); the neo4j-style names are never declared.
    schema = ADAPTED[name]
    assert not EDGE_ENDPOINT_KEYS & set(schema.get("required", [])), f"{name} requires an endpoint key"
    assert not (EDGE_ENDPOINT_KEYS - {"_from", "_to"}) & set(schema.get("properties", {}))
