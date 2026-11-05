"""Strict edge validation, run against every schema in the edge registry.

Written ahead of the change. Documents come from schema_docgen. The collection list is read from
EDGE_SCHEMA_REGISTRY (in app.schema.edge_schema_registry or app.schema.node_schema_registry) at
collection time; until that exists it falls back to the Arango edge schemas, adapted the same way,
so the matrix is already shaped and these tests fail on the missing API rather than not running.

Red until the change lands: all of it. validate_edge, validate_edge_update, get_edge_schema,
EDGE_SCHEMA_REGISTRY and the GRAPH_SCHEMA_ENFORCEMENT switch do not exist yet. The two tests that
do not call the API (the collections come from a registry, every schema is generatable) are green.
Semantics pinned: both input formats ({_from, _to} and {from_id, to_id, from_collection,
to_collection}) are accepted, endpoint keys are stripped before checking, validate_edge_update is
the partial variant (no `required`), unknown collections pass silently.
"""

import importlib
import re

import pytest

from app.config.constants.arangodb import CollectionNames
from app.schema.node_schema_registry import adapt_schema
from app.schema.node_validator import NodeSchemaValidator, SchemaValidationError
from app.services.graph_db.arango.arango_http_provider import EDGE_COLLECTIONS
from tests.unit.schema import schema_docgen as gen

V = NodeSchemaValidator()
ENV = "GRAPH_SCHEMA_ENFORCEMENT"
ENDPOINTS = frozenset({"_from", "_to", "from_id", "to_id", "from_collection", "to_collection"})


def _registry_member(name: str):
    for module in ("app.schema.edge_schema_registry", "app.schema.node_schema_registry"):
        try:
            value = getattr(importlib.import_module(module), name, None)
        except ImportError:
            continue
        if value is not None:
            return value
    return None


# Arango's own edge schemas stand in until the registry exists; they are what it is built from.
REGISTRY = _registry_member("EDGE_SCHEMA_REGISTRY") or {name: adapt_schema(s) for name, s in EDGE_COLLECTIONS}
SCHEMAS = {name: schema for name, schema in REGISTRY.items() if schema}
COLLECTIONS = sorted(SCHEMAS)
NO_SCHEMA = sorted(name for name, schema in REGISTRY.items() if not schema)


def _ids(doc: dict) -> dict:
    body = {k: v for k, v in doc.items() if k not in ("_from", "_to")}
    return {**body, "from_id": "key1", "to_id": "key2", "from_collection": "records", "to_collection": "files"}


FORMATS = pytest.mark.parametrize("fmt", [lambda d: d, _ids], ids=["from-to", "ids"])
METHODS = pytest.mark.parametrize("method", ["validate_edge", "validate_edge_update"])

DOCS: dict[tuple[str, bool], dict] = {}
UNGENERATABLE: dict[tuple[str, bool], str] = {}
for _name in COLLECTIONS:
    for _full in (True, False):
        try:
            DOCS[(_name, _full)] = gen.build_doc(SCHEMAS[_name], _name, _full)
        except gen.Ungeneratable as exc:
            UNGENERATABLE[(_name, _full)] = str(exc)


def _params(kinds):
    return [
        pytest.param(name, bad, path, id=f"{name}:{kind}:{label}")
        for name in COLLECTIONS
        if (name, True) in DOCS
        for kind in kinds
        for label, bad, path in gen.mutations(SCHEMAS[name], DOCS[(name, True)], kind, skip=ENDPOINTS)
    ]


REJECTED = _params([k for k in gen.REJECTED if k != "required"])
ACCEPTED = _params(gen.ACCEPTED)
DROPS = _params(["required"])
SUBSETS = [
    pytest.param(name, subset, id=f"{name}:{i}")
    for name in COLLECTIONS
    if (name, True) in DOCS
    for i, subset in enumerate(gen.partial_subsets(DOCS[(name, True)], name))
]
ENUMS = [
    pytest.param(name, prop, value, id=f"{name}:{prop}={value}")
    for name in COLLECTIONS
    for prop, sub in (SCHEMAS[name].get("properties") or {}).items()
    if "enum" in sub and prop not in ENDPOINTS
    for value in sub["enum"]
    if value is not None
]
BASIC = [
    c.value for c in (
        CollectionNames.BELONGS_TO_DEPARTMENT, CollectionNames.ORG_DEPARTMENT_RELATION,
        CollectionNames.ORG_APP_RELATION, CollectionNames.BELONGS_TO_RECORD_GROUP,
        CollectionNames.INTER_CATEGORY_RELATIONS,
    )
]


def _accepts(method: str, name: str, doc: dict) -> None:
    assert not getattr(V, method)(name, doc)


def _expect(name: str, path: str, call, doc: dict) -> None:
    with pytest.raises(SchemaValidationError) as caught:
        call(name, doc)
    text = str(caught.value)
    assert re.match(rf"^Schema validation failed for collection '{re.escape(name)}': at '{re.escape(path)}': .+", text), text


# --- fixtures and registry --------------------------------------------------------------------
def test_collections_come_from_a_registry():
    assert set(BASIC) <= set(COLLECTIONS)


def test_every_schema_is_generatable():
    """A schema the generator cannot handle is listed here, never skipped."""
    assert not UNGENERATABLE


def test_edge_registry_and_lookup_exist():
    assert _registry_member("EDGE_SCHEMA_REGISTRY") is not None
    assert _registry_member("get_edge_schema") is not None


@pytest.mark.parametrize("name", COLLECTIONS + NO_SCHEMA + ["noSuchEdgeCollection"])
def test_get_edge_schema_returns_the_registry_entry(name):
    get_edge_schema = _registry_member("get_edge_schema")
    assert get_edge_schema is not None and get_edge_schema(name) == REGISTRY.get(name)


# --- accepted ---------------------------------------------------------------------------------
@METHODS
@FORMATS
@pytest.mark.parametrize("full", [True, False], ids=["all-props", "required-only"])
@pytest.mark.parametrize("name", COLLECTIONS)
def test_valid_edge_passes_in_both_formats(name, full, fmt, method):
    _accepts(method, name, fmt(DOCS[(name, full)]))


@METHODS
@FORMATS
@pytest.mark.parametrize("name,bad,path", ACCEPTED)
def test_whole_number_is_a_valid_number(name, bad, path, fmt, method):
    _accepts(method, name, fmt(bad))


@pytest.mark.parametrize("name,subset", SUBSETS)
def test_partial_edge_passes_update(name, subset):
    _accepts("validate_edge_update", name, subset)


@METHODS
@pytest.mark.parametrize("name", BASIC)
def test_basic_edge_with_endpoint_keys_and_timestamp_passes(name, method):
    """basic_edge_schema has additionalProperties:false and declares only _from, _to, createdAtTimestamp."""
    _accepts(method, name, {"_from": "records/a", "_to": "recordGroups/b", "createdAtTimestamp": 1})
    _accepts(method, name, _ids({"createdAtTimestamp": 1}))


@METHODS
@pytest.mark.parametrize("name", BASIC)
def test_endpoint_keys_are_stripped_before_checking(name, method):
    _accepts(method, name, {"_from": 123, "_to": None, "createdAtTimestamp": 1})
    _accepts(method, name, {"from_id": 1, "to_id": 2, "from_collection": 3, "to_collection": 4, "createdAtTimestamp": 1})


@pytest.mark.parametrize("name,prop,value", ENUMS)
def test_every_enum_value_is_accepted(name, prop, value):
    _accepts("validate_edge", name, {**DOCS[(name, True)], prop: value})


@METHODS
@pytest.mark.parametrize("name", NO_SCHEMA + ["noSuchEdgeCollection"])
def test_collection_without_schema_passes_silently(name, method):
    _accepts(method, name, {"_from": "a/b", "_to": "c/d", "anything": [1, None]})


# --- rejected ---------------------------------------------------------------------------------
@FORMATS
@pytest.mark.parametrize("name,bad,path", DROPS)
def test_missing_required_key_fails_full_validation(name, bad, path, fmt):
    _expect(name, path, V.validate_edge, fmt(bad))


@FORMATS
@pytest.mark.parametrize("name,bad,path", DROPS)
def test_missing_required_key_does_not_fail_update(name, bad, path, fmt):
    V.validate_edge_update(name, fmt(bad))


@pytest.mark.parametrize("name", BASIC)
def test_basic_edge_requires_created_at_timestamp(name):
    _expect(name, "root", V.validate_edge, {"_from": "records/a", "_to": "recordGroups/b"})
    _expect(name, "root", V.validate_edge, _ids({}))


@METHODS
@FORMATS
@pytest.mark.parametrize("name,bad,path", REJECTED)
def test_invalid_edge_is_rejected(name, bad, path, fmt, method):
    _expect(name, path, getattr(V, method), fmt(bad))


@METHODS
@pytest.mark.parametrize("name", BASIC)
def test_basic_edge_rejects_an_unknown_key_next_to_endpoints(name, method):
    _expect(name, "root", getattr(V, method), _ids({"createdAtTimestamp": 1, gen.UNKNOWN_KEY: 1}))


def test_relationship_type_enum_is_enforced():
    names = [n for n in COLLECTIONS if "enum" in (SCHEMAS[n].get("properties") or {}).get("relationshipType", {})]
    assert names
    for name in names:
        for method in (V.validate_edge, V.validate_edge_update):
            _expect(name, "relationshipType", method, {**DOCS[(name, True)], "relationshipType": "NOT_A_RELATIONSHIP"})


# --- GRAPH_SCHEMA_ENFORCEMENT -----------------------------------------------------------------
@METHODS
@pytest.mark.parametrize("name", BASIC)
def test_enforcement_off_makes_every_call_a_no_op(name, method, monkeypatch):
    monkeypatch.setenv(ENV, "off")
    _accepts(method, name, {"_from": "a/b", "_to": "c/d", "createdAtTimestamp": "not a number", "extra": 1})


@pytest.mark.parametrize("setting", [None, "strict"])
def test_default_and_strict_enforce(setting, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    if setting:
        monkeypatch.setenv(ENV, setting)
    with pytest.raises(SchemaValidationError):
        V.validate_edge(BASIC[0], {"_from": "a/b", "_to": "c/d"})
