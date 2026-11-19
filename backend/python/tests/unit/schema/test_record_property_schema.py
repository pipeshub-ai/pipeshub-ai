"""The strict record schemas know the projection's fields on both stores."""

import pytest

from app.schema.arango.documents import record_schema
from app.schema.node_schema_registry import RECORD_PROPERTY_KEY_PATTERN, get_node_schema
from app.schema.node_validator import NodeSchemaValidator, SchemaValidationError

pytestmark = pytest.mark.unit

BASE = {
    "id": "r1", "orgId": "o", "recordName": "n", "externalRecordId": "e", "recordType": "FILE", "origin": "UPLOAD",
    "createdAtTimestamp": 1, "connectorId": "c",
}
CONTROLS = {"propsSet": "0123456789abcdef", "propsGen": 2, "propsSrc": 1_790_000_000_000, "propsVer": 3}


def test_arango_record_schema_declares_the_projection_fields() -> None:
    props = record_schema["rule"]["properties"]
    assert {"recordProps", "propsSet", "propsGen", "propsSrc", "propsVer"} <= set(props)
    assert record_schema["rule"]["additionalProperties"] is False


def test_neo4j_accepts_flat_projected_values_of_every_shape_and_the_controls() -> None:
    node = {**BASE, **CONTROLS, "p_status": "OPEN", "p_count": 3, "p_ratio": 1.5, "p_flag": True, "p_labels": ["a", "b"]}
    NodeSchemaValidator().validate_node("records", node)
    NodeSchemaValidator().validate_node_update("records", {"p_status": None, **CONTROLS})


@pytest.mark.parametrize("key", ["p_bad-name", "p_", "p_" + "x" * 65, "q_status", "p_has space"])
def test_neo4j_still_refuses_any_other_unknown_key(key: str) -> None:
    with pytest.raises(SchemaValidationError):
        NodeSchemaValidator().validate_node("records", {**BASE, key: "x"})


def test_a_projected_value_cannot_be_a_nested_object() -> None:
    with pytest.raises(SchemaValidationError):
        NodeSchemaValidator().validate_node("records", {**BASE, "p_status": {"a": 1}})
    with pytest.raises(SchemaValidationError):
        NodeSchemaValidator().validate_node("records", {**BASE, "p_labels": [1, 2]})


def test_only_the_records_collection_gets_the_pattern() -> None:
    assert RECORD_PROPERTY_KEY_PATTERN in get_node_schema("records")["patternProperties"]
    assert "patternProperties" not in (get_node_schema("files") or {})
