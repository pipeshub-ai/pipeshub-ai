"""Record processing diagnostics: four optional internal fields on a stored record.

`firstQueuedAt`, `failedAt`, `indexingAttempts` and `pageCount` are legal on every
store (the Arango schema is strict, and Neo4j and PostgreSQL validate against the
same registry) and are never returned by the record endpoint. Nothing writes them
yet, so a record without them must stay valid everywhere.
"""

from __future__ import annotations

import ast
import copy
import re
from pathlib import Path

import pytest

from app.config.constants.arangodb import CollectionNames
from app.models.entities import Record
from app.schema.arango.documents import record_schema
from app.schema.node_validator import NodeSchemaValidator, SchemaValidationError
from app.services.graph_db.common.utils import INTERNAL_RECORD_FIELDS, api_record
from tests.unit.models.test_entities import _record_kwargs

RECORDS = CollectionNames.RECORDS.value
FIELDS = ("firstQueuedAt", "failedAt", "indexingAttempts", "pageCount")

_BASE_DOC = {
    "orgId": "org-1",
    "recordName": "a.pdf",
    "externalRecordId": "ext-1",
    "recordType": "FILE",
    "origin": "CONNECTOR",
    "connectorId": "conn-1",
    "createdAtTimestamp": 1,
}


def _doc(**fields: object) -> dict[str, object]:
    return {**_BASE_DOC, **fields}


@pytest.fixture
def validator() -> NodeSchemaValidator:
    return NodeSchemaValidator()


class TestSchemaProperties:
    """TC-A01"""

    def test_the_four_properties_are_optional(self) -> None:
        rule = record_schema["rule"]
        for name in FIELDS:
            assert name in rule["properties"], name
            assert name not in rule["required"], name

    def test_a_record_without_the_fields_is_valid_on_both_validators(self, validator) -> None:
        validator.validate_node(RECORDS, _doc())
        validator.validate_node_update(RECORDS, {"indexingStatus": "QUEUED"})

    @pytest.mark.parametrize(
        "fields",
        [
            {"firstQueuedAt": 1_700_000_000_000},
            {"firstQueuedAt": 1_700_000_000_000.5},
            {"failedAt": 1_700_000_000_000},
            {"indexingAttempts": 0},
            {"indexingAttempts": 3},
            {"pageCount": 1},
            {"pageCount": 812},
            {"firstQueuedAt": None, "failedAt": None, "indexingAttempts": None, "pageCount": None},
            {"firstQueuedAt": 1, "failedAt": 2, "indexingAttempts": 1, "pageCount": 4},
        ],
    )
    def test_valid_values_are_accepted_by_the_full_and_partial_validators(self, validator, fields) -> None:
        validator.validate_node(RECORDS, _doc(**fields))
        validator.validate_node_update(RECORDS, fields)

    @pytest.mark.parametrize(
        "fields",
        [
            {"firstQueuedAt": "1700000000000"},
            {"failedAt": "yesterday"},
            {"indexingAttempts": "2"},
            {"indexingAttempts": -1},
            {"indexingAttempts": 1.5},
            {"pageCount": 0},
            {"pageCount": -3},
            {"pageCount": "12"},
            {"pageCount": 2.5},
        ],
    )
    def test_invalid_values_are_rejected_by_the_full_and_partial_validators(self, validator, fields) -> None:
        with pytest.raises(SchemaValidationError):
            validator.validate_node(RECORDS, _doc(**fields))
        with pytest.raises(SchemaValidationError):
            validator.validate_node_update(RECORDS, fields)

    def test_the_schema_is_otherwise_unchanged_and_still_strict(self) -> None:
        rule = record_schema["rule"]
        assert rule["additionalProperties"] is False
        assert record_schema["level"] == "strict"
        narrowed = copy.deepcopy(record_schema["rule"])
        for name in FIELDS:
            del narrowed["properties"][name]
        assert set(rule["properties"]) - set(narrowed["properties"]) == set(FIELDS)


class TestEntity:
    """TC-A02"""

    def test_unset_fields_are_not_serialised(self) -> None:
        doc = Record(**_record_kwargs()).to_arango_base_record()
        for name in FIELDS:
            assert name not in doc, f"{name} would be written as null (Neo4j `SET n +=` deletes the stored value)"

    def test_set_fields_are_serialised(self) -> None:
        doc = Record(**_record_kwargs(first_queued_at=11, indexing_attempts=2)).to_arango_base_record()
        assert doc["firstQueuedAt"] == 11
        assert doc["indexingAttempts"] == 2
        assert "failedAt" not in doc and "pageCount" not in doc

    def test_zero_attempts_is_a_value_not_unset(self) -> None:
        doc = Record(**_record_kwargs(indexing_attempts=0)).to_arango_base_record()
        assert doc["indexingAttempts"] == 0

    def test_a_serialised_record_with_the_fields_passes_the_schema(self, validator) -> None:
        doc = Record(**_record_kwargs(first_queued_at=11, indexing_attempts=2)).to_arango_base_record()
        validator.validate_node(RECORDS, {k: v for k, v in doc.items() if k != "_key"})

    def test_a_round_trip_of_an_entity_loaded_with_stale_values_writes_none_of_them(self) -> None:
        """Entities are read and saved by unrelated writers; echoing the four keys back could
        overwrite a newer stored value with the stale one this entity read earlier."""
        stored = {"_key": "r1", **Record(**_record_kwargs()).to_arango_base_record()}
        stored.update({"firstQueuedAt": 11, "failedAt": 12, "indexingAttempts": 2, "pageCount": 9})
        loaded = Record.from_arango_base_record(stored)
        assert loaded.first_queued_at is None and loaded.indexing_attempts is None
        written = loaded.to_arango_base_record()
        assert not set(FIELDS) & set(written), sorted(set(FIELDS) & set(written))

    def test_the_record_endpoint_strips_every_field(self) -> None:
        stored = {"id": "r1", "recordName": "a.pdf", **dict.fromkeys(FIELDS, 1)}
        assert api_record(stored) == {"id": "r1", "recordName": "a.pdf"}
        assert set(FIELDS) <= INTERNAL_RECORD_FIELDS


# Only these files write the four keys (everything else goes through ``record_diagnostics``). A new writer is
# added here on purpose and in the writer inventory (tests/unit/modules/indexing/status_writer_sites.py).
_REPO = Path(__file__).resolve().parents[5]
_APP = _REPO / "backend" / "python" / "app"
_NODE_SRC = _REPO / "backend" / "nodejs" / "apps" / "src"
_ALLOWED = {
    _APP / "schema" / "arango" / "documents.py",
    _APP / "services" / "graph_db" / "common" / "utils.py",
    _APP / "models" / "entities.py",
    _APP / "modules" / "indexing" / "record_diagnostics.py",
    _APP / "modules" / "indexing" / "parse_now_marker.py",
    _APP / "modules" / "transformers" / "sink_orchestrator.py",
    _APP / "events" / "events.py",
    _APP / "events" / "processor.py",
    _APP / "api" / "routes" / "chatbot.py",
    _APP / "services" / "messaging" / "kafka" / "handlers" / "record.py",
    _APP / "connectors" / "core" / "base" / "data_processor" / "data_source_entities_processor.py",
    _APP / "services" / "graph_db" / "arango" / "arango_http_provider.py",
    _APP / "services" / "graph_db" / "neo4j" / "neo4j_provider.py",
    _APP / "services" / "graph_db" / "postgres" / "trash.py",
}
_ENTITY_ATTRIBUTES = {"first_queued_at", "indexing_attempts"}
#: The stored names and the entity names, in any string: a dict key, a subscript, a keyword
#: passed by name, ``model_copy(update={...})``, ``setattr``, an AQL, Cypher or SQL statement.
_NAME = re.compile(r"\b(?:firstQueuedAt|failedAt|indexingAttempts|pageCount|first_queued_at|indexing_attempts)\b")


def _docstrings(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def _writers(source: str) -> list[int]:
    """Lines that name one of the four fields (as a string or an entity attribute store) outside a docstring."""
    # Skipping a file whose text never spells a name keeps the whole-tree scan inside the unit
    # timeout. Identifiers always appear in the text; a string hit is caught only when the name is
    # written out whole (not split across adjacent literals or hidden behind an escape).
    if not _NAME.search(source):
        return []
    tree = ast.parse(source)
    skip = _docstrings(tree)
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            if _NAME.search(node.value):
                hits.append(node.lineno)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store) and node.attr in _ENTITY_ATTRIBUTES:
            hits.append(node.lineno)
        elif isinstance(node, ast.keyword) and node.arg in _ENTITY_ATTRIBUTES:
            hits.append(node.value.lineno)
    return hits


def test_only_the_listed_files_write_the_fields() -> None:
    offenders = {
        str(path.relative_to(_APP)): lines
        for path in _APP.rglob("*.py")
        if path not in _ALLOWED and (lines := _writers(path.read_text(encoding="utf-8-sig")))
    }
    assert offenders == {}, "these files set a record processing diagnostic: " + repr(offenders)


def test_no_node_code_names_the_fields_yet() -> None:
    names = re.compile(r"\b(?:firstQueuedAt|failedAt|indexingAttempts|pageCount)\b")
    allowed = {"internal-record-fields.ts"}
    offenders = {
        str(path.relative_to(_NODE_SRC)): [n for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1) if names.search(line)]
        for path in _NODE_SRC.rglob("*.ts")
        if path.name not in allowed
    }
    offenders = {name: lines for name, lines in offenders.items() if lines}
    assert offenders == {}, "these files name a record processing diagnostic: " + repr(offenders)


@pytest.mark.parametrize(
    "source",
    [
        "record.indexing_attempts = 1",
        "record.first_queued_at += 1",
        'q = "FOR r IN records UPDATE r WITH { pageCount: 1 } IN records"',
        'record.model_copy(update={"first_queued_at": 1})',
        'doc["failedAt"] = 1',
        'node = {"firstQueuedAt": 1}',
        "Record(indexing_attempts=1)",
        'cypher = "SET n.indexingAttempts = $n"',
    ],
)
def test_the_guard_sees_every_way_of_writing_a_field(source: str) -> None:
    assert _writers(source), source


def test_the_guard_ignores_docstrings_and_other_names() -> None:
    assert _writers('"""The pageCount of a record."""\nx = {"pageNumber": 1}\nrecord.queued_at = 1') == []


def test_no_entity_loader_reads_the_stored_diagnostics() -> None:
    """The names appear in ``entities.py`` only where ``Record`` serialises them, and no loader passes
    one to a constructor: reading them back would make every unrelated save write a stale value.

    Checked on the source because most subclass loaders need a typed document of their own to run.
    """
    tree = ast.parse((_APP / "models" / "entities.py").read_text(encoding="utf-8-sig"))
    skip = _docstrings(tree)
    reads: list[tuple[str, int]] = []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) or function.name == "to_arango_base_record":
            continue
        for node in ast.walk(function):
            named = isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip and _NAME.search(node.value)
            passed = isinstance(node, ast.keyword) and node.arg in _ENTITY_ATTRIBUTES
            if named or passed:
                reads.append((function.name, node.lineno))
    assert reads == [], reads


class TestIntegerEdgeValues:
    """The validator Neo4j and PostgreSQL use agrees with ArangoDB on what an ``integer`` is."""

    @pytest.mark.parametrize("value", [2.0, 1e20, -0.0, 10**30, -(2**63) - 1, 2**63, True])
    @pytest.mark.parametrize("name", ["indexingAttempts", "pageCount"])
    def test_a_float_or_an_oversized_int_is_not_an_integer(self, validator, name, value) -> None:
        with pytest.raises(SchemaValidationError):
            validator.validate_node_update(RECORDS, {name: value})

    @pytest.mark.parametrize("value", [1, 7, 2**62])
    def test_a_whole_int_still_is(self, validator, value) -> None:
        validator.validate_node_update(RECORDS, {"pageCount": value, "indexingAttempts": value})

    def test_a_float_is_still_a_number(self, validator) -> None:
        validator.validate_node_update(RECORDS, {"firstQueuedAt": 1_700_000_000_000.5, "failedAt": 2.0})
