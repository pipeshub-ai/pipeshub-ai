"""Strict node validation, run against every schema in NODE_SCHEMA_REGISTRY.

Written ahead of the change. Documents come from schema_docgen (seeded, derived from the schema),
and the collection list is read from the registry at collection time.

Red until the change lands (the API or behaviour does not exist yet):
  - every test that calls validate_node_upsert
  - additionalProperties enforced on validate_node_update (unknown key on an
    additionalProperties:false collection) and the INTERNAL_PROPERTIES tests
  - GRAPH_SCHEMA_ENFORCEMENT=off turning every validate call into a no-op
Green now (behaviour that already exists): validate_node, the type, enum and null checks of
validate_node_update, partial documents, `_id`, collections without a schema, the default being strict.
Semantics pinned: GRAPH_SCHEMA_ENFORCEMENT is read on every call; "off" disables, anything else is strict.
"""

import re

import pytest

from app.config.constants.arangodb import CollectionNames
from app.schema import node_validator
from app.schema.node_schema_registry import NODE_SCHEMA_REGISTRY
from app.schema.node_validator import NodeSchemaValidator, SchemaValidationError
from tests.unit.schema import schema_docgen as gen

V = NodeSchemaValidator()
SCHEMAS = {name: schema for name, schema in NODE_SCHEMA_REGISTRY.items() if schema}
COLLECTIONS = sorted(SCHEMAS)
NO_SCHEMA = sorted(name for name, schema in NODE_SCHEMA_REGISTRY.items() if not schema)
ENV = "GRAPH_SCHEMA_ENFORCEMENT"

DOCS: dict[tuple[str, bool], dict] = {}
UNGENERATABLE: dict[tuple[str, bool], str] = {}
for _name in COLLECTIONS:
    for _full in (True, False):
        try:
            DOCS[(_name, _full)] = gen.build_doc(SCHEMAS[_name], _name, _full)
        except gen.Ungeneratable as exc:
            UNGENERATABLE[(_name, _full)] = str(exc)


def _params(kinds, collections=COLLECTIONS) -> list:
    return [
        pytest.param(name, bad, path, id=f"{name}:{kind}:{label}")
        for name in collections
        if (name, True) in DOCS
        for kind in kinds
        for label, bad, path in gen.mutations(SCHEMAS[name], DOCS[(name, True)], kind)
    ]


OTHER_REJECTED = _params([k for k in gen.REJECTED if k != "required"])
ACCEPTED = _params(gen.ACCEPTED)
DROPS = _params(["required"])
WITH_REQUIRED = [name for name in COLLECTIONS if SCHEMAS[name].get("required")]
STRICT_SHAPE = [name for name in COLLECTIONS if SCHEMAS[name].get("additionalProperties") is False]
OPEN_SHAPE = [name for name in COLLECTIONS if name not in STRICT_SHAPE]
SUBSETS = [
    pytest.param(name, subset, id=f"{name}:{i}")
    for name in COLLECTIONS
    if (name, True) in DOCS
    for i, subset in enumerate(gen.partial_subsets(DOCS[(name, True)], name))
]
INTERNAL = sorted(getattr(node_validator, "INTERNAL_PROPERTIES", None) or {"khSortName", "khScope"})


METHODS = pytest.mark.parametrize("method", ["validate_node", "validate_node_update", "validate_node_upsert"])


def _accepts(method: str, name: str, doc: dict) -> None:
    """Does not raise; upsert also reports nothing missing (update and full return None)."""
    assert not getattr(V, method)(name, doc)


def _fail_text(call, *args) -> str:
    with pytest.raises(SchemaValidationError) as caught:
        call(*args)
    return str(caught.value)


# --- the fixtures themselves ------------------------------------------------------------------
def test_collections_come_from_the_registry() -> None:
    assert COLLECTIONS and NO_SCHEMA
    assert not set(COLLECTIONS) & set(NO_SCHEMA)


def test_every_schema_is_generatable() -> None:
    """A schema the generator cannot handle is listed here, never skipped."""
    assert not UNGENERATABLE


# --- accepted documents -----------------------------------------------------------------------
@METHODS
@pytest.mark.parametrize("full", [True, False], ids=["all-props", "required-only"])
@pytest.mark.parametrize("name", COLLECTIONS)
def test_valid_document_passes(name, full, method) -> None:
    _accepts(method, name, DOCS[(name, full)])


@METHODS
@pytest.mark.parametrize("name,bad,path", ACCEPTED)
def test_whole_number_is_a_valid_number(name, bad, path, method) -> None:
    _accepts(method, name, bad)


@pytest.mark.parametrize("name,subset", SUBSETS)
def test_partial_document_passes_update(name, subset) -> None:
    V.validate_node_update(name, subset)


@pytest.mark.parametrize("name,subset", SUBSETS)
def test_upsert_reports_exactly_the_missing_required_names(name, subset) -> None:
    missing = sorted(set(SCHEMAS[name].get("required") or []) - set(subset))
    assert sorted(V.validate_node_upsert(name, subset)) == missing


@METHODS
@pytest.mark.parametrize("name", COLLECTIONS)
def test_id_is_ignored(name, method) -> None:
    _accepts(method, name, {**DOCS[(name, True)], "_id": f"{name}/abc"})


@METHODS
@pytest.mark.parametrize("key", INTERNAL)
@pytest.mark.parametrize("name", COLLECTIONS)
def test_internal_properties_are_ignored_not_rejected(name, key, method) -> None:
    _accepts(method, name, {**DOCS[(name, True)], key: "x"})


def test_internal_properties_are_declared_by_the_validator() -> None:
    assert {"khSortName", "khScope"} <= set(getattr(node_validator, "INTERNAL_PROPERTIES", ()))


@METHODS
@pytest.mark.parametrize("name", [*NO_SCHEMA, "noSuchCollection"])
def test_collection_without_schema_passes_silently(name, method) -> None:
    _accepts(method, name, {"anything": [1, {"x": None}], "_id": "x/y"})


# --- rejected documents -----------------------------------------------------------------------
def _expect(name: str, path: str, text: str) -> None:
    assert re.match(rf"^Schema validation failed for collection '{re.escape(name)}': at '{re.escape(path)}': .+", text), text


@pytest.mark.parametrize("name,bad,path", DROPS)
def test_missing_required_key_fails_full_validation(name, bad, path) -> None:
    _expect(name, path, _fail_text(V.validate_node, name, bad))


@pytest.mark.parametrize("name,bad,path", DROPS)
def test_missing_required_key_is_reported_by_upsert(name, bad, path) -> None:
    expected = sorted(set(SCHEMAS[name]["required"]) - set(bad))
    assert len(expected) == 1
    assert V.validate_node_upsert(name, bad) == expected


@pytest.mark.parametrize("name,bad,path", DROPS)
def test_missing_required_key_does_not_fail_update(name, bad, path) -> None:
    V.validate_node_update(name, bad)


@pytest.mark.parametrize("name", WITH_REQUIRED)
def test_upsert_reports_every_missing_required_name(name) -> None:
    assert sorted(V.validate_node_upsert(name, {})) == sorted(SCHEMAS[name]["required"])


@pytest.mark.parametrize("name,bad,path", OTHER_REJECTED)
def test_full_validation_rejects(name, bad, path) -> None:
    _expect(name, path, _fail_text(V.validate_node, name, bad))


@pytest.mark.parametrize("name,bad,path", OTHER_REJECTED)
def test_update_rejects(name, bad, path) -> None:
    _expect(name, path, _fail_text(V.validate_node_update, name, bad))


@pytest.mark.parametrize("name,bad,path", OTHER_REJECTED)
def test_upsert_rejects(name, bad, path) -> None:
    _expect(name, path, _fail_text(V.validate_node_upsert, name, bad))


def test_error_message_format() -> None:
    name = CollectionNames.RECORDS.value
    text = _fail_text(V.validate_node, name, {**DOCS[(name, True)], "recordName": 5})
    assert re.match(r"^Schema validation failed for collection 'records': at 'recordName': .*string", text), text


@METHODS
@pytest.mark.parametrize("name", OPEN_SHAPE)
def test_unknown_key_is_accepted_where_additional_properties_are_open(name, method) -> None:
    _accepts(method, name, {**DOCS[(name, True)], gen.UNKNOWN_KEY: 1})


# --- GRAPH_SCHEMA_ENFORCEMENT -----------------------------------------------------------------
def _first_bad(name: str) -> dict:
    for kind in ("wrong_type", "unknown_key", "bad_enum", "required"):
        cases = gen.mutations(SCHEMAS[name], DOCS[(name, True)], kind)
        if cases:
            return cases[0][1]
    raise AssertionError(f"no way to break {name}")


@METHODS
@pytest.mark.parametrize("name", [n for n in COLLECTIONS if (n, True) in DOCS])
def test_enforcement_off_makes_every_call_a_no_op(name, method, monkeypatch) -> None:
    monkeypatch.setenv(ENV, "off")
    _accepts(method, name, _first_bad(name))


def test_enforcement_off_also_skips_missing_required_names(monkeypatch) -> None:
    monkeypatch.setenv(ENV, "off")
    _accepts("validate_node_upsert", WITH_REQUIRED[0], {})


@pytest.mark.parametrize("setting", [None, "strict"])
def test_default_and_strict_enforce(setting, monkeypatch) -> None:
    name = WITH_REQUIRED[0]
    monkeypatch.delenv(ENV, raising=False)
    if setting:
        monkeypatch.setenv(ENV, setting)
    with pytest.raises(SchemaValidationError):
        V.validate_node(name, {})


def test_switching_back_to_strict_enforces_again(monkeypatch) -> None:
    name = WITH_REQUIRED[0]
    monkeypatch.setenv(ENV, "off")
    V.validate_node(name, {})
    monkeypatch.setenv(ENV, "strict")
    with pytest.raises(SchemaValidationError):
        V.validate_node(name, {})
