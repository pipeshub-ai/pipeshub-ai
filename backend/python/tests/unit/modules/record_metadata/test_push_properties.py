"""``push_properties``: the three outcomes of reading a push's stored values, and that it never raises."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import patch

import pytest

from app.connectors.sources.localKB.push.contract import canonical_properties
from app.models.record_metadata import MetadataProvenance, RecordMetadata
from app.modules.record_metadata import push_properties as module
from app.modules.record_metadata.projection import NOT_PROVIDED
from app.modules.record_metadata.push_properties import PUSH_SOURCE, push_properties

pytestmark = pytest.mark.unit


def test_the_source_is_the_string_the_push_validated_with() -> None:
    import inspect

    from app.connectors.sources.localKB.push import worker

    assert PUSH_SOURCE == "push"
    assert 'source="push"' in inspect.getsource(worker)


def test_valid_values_become_provided_push_values() -> None:
    result = push_properties(
        {
            "properties": canonical_properties(
                "s1", {"a": "x", "b": 2, "c": True, "d": 1.5, "e": ["p", "q"]}
            )
        }
    )

    assert isinstance(result, RecordMetadata)
    assert result.schema_id == "s1"
    assert {k: v.value for k, v in result.values.items()} == {
        "a": "x",
        "b": 2,
        "c": True,
        "d": 1.5,
        "e": ["p", "q"],
    }
    assert all(
        v.provenance is MetadataProvenance.PROVIDED and v.source == PUSH_SOURCE
        for v in result.values.values()
    )
    assert (
        result.values["c"].value is True
        and result.values["b"].value == 2
        and type(result.values["b"].value) is int
    )


@pytest.mark.parametrize(
    "doc",
    [{}, {"properties": None}, {"properties": {}}],
    ids=["no member", "null", "empty"],
)
def test_nothing_pushed_means_the_record_has_none(doc: dict[str, Any]) -> None:
    assert push_properties(doc) is None


def test_values_empty_is_an_empty_set_not_none() -> None:
    result = push_properties({"properties": canonical_properties("s1", {})})

    assert isinstance(result, RecordMetadata) and result.values == {}


@pytest.mark.parametrize(
    "member",
    [
        "text",
        ["a"],
        7,
        {"schemaId": "s1"},
        {"schemaId": "s1", "values": ["x"]},
        {"schemaId": 5, "values": {}},
        {"schemaId": "s1", "values": {"a": {"nested": 1}}},
        {"schemaId": "s1", "values": {"a": [1, 2]}},
        {"schemaId": "s1", "values": {"a": None}},
        {"schemaId": "s1", "values": {1: "x"}},
    ],
    ids=str,
)
def test_a_member_that_cannot_be_read_is_not_provided_never_none(
    member: object,
) -> None:
    """M4: ``None`` here would stamp 'the record has none' over values the record had."""
    result = push_properties({"properties": member})

    assert result is NOT_PROVIDED


def test_malformed_is_logged_by_type_only_and_counted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "do-not-log-this-value"
    with (
        patch.object(module, "pushed_properties_refused") as counter,
        caplog.at_level(logging.WARNING),
    ):
        push_properties(
            {"properties": {"schemaId": "s1", "values": {"a": [secret, 1]}}}
        )

    counter.assert_called_once_with("malformed")
    assert caplog.records and secret not in caplog.text
    assert "ValidationError" in caplog.text


@pytest.mark.parametrize("doc", [None, "text", 3, ["properties"], b"x"], ids=repr)
def test_a_document_that_is_not_a_mapping_never_raises(doc: object) -> None:
    assert push_properties(doc) is NOT_PROVIDED
