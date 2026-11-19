"""``indexedMd5``: the hex md5 of the bytes last fully indexed. Optional, nullable, internal; absent reads as null."""
from __future__ import annotations

import pytest

from app.config.constants.arangodb import CollectionNames
from app.schema.arango.documents import record_schema
from app.schema.node_validator import NodeSchemaValidator, SchemaValidationError
from app.services.graph_db.common.utils import INTERNAL_RECORD_FIELDS, api_record
from tests.unit.schema.test_record_diagnostics_schema import _doc

RECORDS = CollectionNames.RECORDS.value
MD5 = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def validator() -> NodeSchemaValidator:
    return NodeSchemaValidator()


def test_the_property_is_optional_and_nullable() -> None:
    rule = record_schema["rule"]
    assert "indexedMd5" in rule["properties"]
    assert "indexedMd5" not in rule["required"]
    assert "null" in rule["properties"]["indexedMd5"]["type"]


def test_a_document_stored_before_the_field_existed_validates(validator: NodeSchemaValidator) -> None:
    """Upgrade compatibility: records already in the store have no such key, and must stay valid on every store."""
    validator.validate_node(RECORDS, _doc())
    validator.validate_node_update(RECORDS, {"indexingStatus": "COMPLETED"})


@pytest.mark.parametrize("fields", [{"indexedMd5": MD5}, {"indexedMd5": None}])
def test_a_string_or_null_is_accepted(validator: NodeSchemaValidator, fields: dict) -> None:
    validator.validate_node(RECORDS, _doc(**fields))
    validator.validate_node_update(RECORDS, fields)


@pytest.mark.parametrize("fields", [{"indexedMd5": 12}, {"indexedMd5": ["x"]}, {"indexedMd5": True}])
def test_anything_else_is_rejected(validator: NodeSchemaValidator, fields: dict) -> None:
    with pytest.raises(SchemaValidationError):
        validator.validate_node(RECORDS, _doc(**fields))
    with pytest.raises(SchemaValidationError):
        validator.validate_node_update(RECORDS, fields)


def test_the_record_endpoint_never_returns_it() -> None:
    assert "indexedMd5" in INTERNAL_RECORD_FIELDS
    assert api_record({"id": "r1", "recordName": "a.pdf", "indexedMd5": MD5}) == {"id": "r1", "recordName": "a.pdf"}


def test_a_record_without_it_reads_as_null_for_the_keep_rule() -> None:
    from app.modules.indexing import page_limit

    assert page_limit.normalise_md5({}.get("indexedMd5")) is None
    assert page_limit.content_state({"contentRev": "x"}, "y", MD5) == "uncertain"
