"""Review fixes: enforcement switch spellings, _key/id conflicts, internal property types, template memory."""

import logging

import pytest

from app.schema import node_validator
from app.schema.arango.documents import agent_template_schema
from app.schema.node_validator import NodeSchemaValidator, SchemaValidationError

V = NodeSchemaValidator()
BAD = {"id": 5}  # any schema-violating node would do; the switch tests only need "does it raise"


@pytest.mark.parametrize("value", ["off", "OFF", " false ", "0", "no", "Disabled"])
def test_disabled_spellings_turn_enforcement_off(monkeypatch, value) -> None:
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", value)
    assert node_validator._enforced() is False
    V.validate_node("records", BAD)  # no-op


def test_unknown_value_stays_strict_and_warns_once(monkeypatch, caplog) -> None:
    monkeypatch.setattr(node_validator, "_warned_values", set())
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "maybe")
    with caplog.at_level(logging.WARNING, logger=node_validator.__name__):
        assert node_validator._enforced() is True
        assert node_validator._enforced() is True
    assert [r for r in caplog.records if "maybe" in r.getMessage()].__len__() == 1
    with pytest.raises(SchemaValidationError):
        V.validate_node("records", BAD)


@pytest.mark.parametrize("value", ["strict", "STRICT", ""])
def test_strict_values_do_not_warn(monkeypatch, caplog, value) -> None:
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", value)
    with caplog.at_level(logging.WARNING, logger=node_validator.__name__):
        assert node_validator._enforced() is True
    assert not caplog.records


def test_key_and_id_that_differ_are_accepted() -> None:
    # Providers let the requested key win over a stale `_key` in the node (create_node_if_absent).
    V.validate_node_update("records", {"_key": "a", "id": "b"})


def test_non_string_key_is_rejected() -> None:
    with pytest.raises(SchemaValidationError, match="_key"):
        V.validate_node_update("records", {"_key": 7})


def test_equal_key_and_id_pass() -> None:
    V.validate_node_update("records", {"_key": "a", "id": "a"})


@pytest.mark.parametrize("value", [{"a": 1}, ["x"]])
def test_internal_property_must_be_scalar(value) -> None:
    with pytest.raises(SchemaValidationError, match="khSortName"):
        V.validate_node_update("records", {"khSortName": value})


@pytest.mark.parametrize("value", ["x", 1, 1.5, True, None])
def test_internal_property_scalars_pass(value) -> None:
    V.validate_node_update("records", {"khSortName": value, "matchLock": value})


def test_template_memory_schema_is_an_object_again() -> None:
    assert agent_template_schema["rule"]["properties"]["memory"]["type"] == "object"
    with pytest.raises(SchemaValidationError):
        V.validate_node_update("agentTemplates", {"memory": None})
