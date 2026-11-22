"""An SDK operation must not reach a `not` schema, or Speakeasy leaves it out of the SDKs."""

from __future__ import annotations

import pytest

from sdk_tests.check_sdk_spec import _SPEC, main, violations

pytestmark = pytest.mark.unit


def _spec(operation: dict, schemas: dict | None = None) -> dict:
    return {
        "paths": {"/things": {"post": {"operationId": "createThing", **operation}}},
        "components": {"schemas": schemas or {}},
    }


def test_a_not_reached_through_a_ref_is_found():
    spec = _spec(
        {
            "x-pipeshub-sdk": True,
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/Thing"}
                    }
                }
            },
        },
        {
            "Thing": {
                "type": "object",
                "properties": {"name": {"not": {"pattern": "%s"}}},
            }
        },
    )
    assert violations(spec) == [
        ("createThing", "#/components/schemas/Thing/properties/name/not")
    ]


def test_a_not_in_a_parameter_is_found():
    spec = _spec(
        {
            "x-pipeshub-sdk": True,
            "parameters": [
                {
                    "name": "q",
                    "in": "query",
                    "schema": {"type": "string", "not": {"enum": ["x"]}},
                }
            ],
        }
    )
    assert violations(spec) == [
        ("createThing", "#/paths/~1things/post/parameters/0/schema/not")
    ]


def test_operations_outside_the_sdk_are_not_checked():
    spec = _spec({"parameters": [{"name": "q", "in": "query", "schema": {"not": {}}}]})
    assert violations(spec) == []


def test_an_ignored_property_and_a_property_named_not_are_allowed():
    spec = _spec(
        {
            "x-pipeshub-sdk": True,
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "where": {"not": {}, "x-speakeasy-ignore": True},
                                "not": {"type": "string"},
                            },
                            "example": {"not": {}},
                        }
                    }
                }
            },
        }
    )
    assert violations(spec) == []


def test_a_recursive_schema_is_walked_once():
    spec = _spec(
        {
            "x-pipeshub-sdk": True,
            "responses": {
                "200": {
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Node"}
                        }
                    }
                }
            },
        },
        {
            "Node": {
                "properties": {
                    "children": {"items": {"$ref": "#/components/schemas/Node"}}
                }
            }
        },
    )
    assert violations(spec) == []


def test_the_spec_has_no_sdk_operation_speakeasy_would_leave_out(capsys):
    assert main([str(_SPEC)]) == 0, capsys.readouterr().out


def test_a_not_in_a_default_response_is_found():
    spec = _spec(
        {
            "x-pipeshub-sdk": True,
            "responses": {
                "default": {
                    "content": {
                        "application/json": {"schema": {"not": {"type": "null"}}}
                    }
                }
            },
        }
    )
    assert violations(spec) == [
        (
            "createThing",
            "#/paths/~1things/post/responses/default/content/application~1json/schema/not",
        )
    ]


def test_a_schema_default_value_is_data():
    spec = _spec(
        {
            "x-pipeshub-sdk": True,
            "parameters": [
                {
                    "name": "q",
                    "in": "query",
                    "schema": {"type": "object", "default": {"not": {}}},
                }
            ],
        }
    )
    assert violations(spec) == []


def test_a_not_in_a_path_level_parameter_is_found():
    spec = _spec({"x-pipeshub-sdk": True})
    spec["paths"]["/things"]["parameters"] = [{"$ref": "#/components/parameters/Q"}]
    spec["components"]["parameters"] = {
        "Q": {"name": "q", "in": "query", "schema": {"not": {"enum": ["x"]}}}
    }
    assert violations(spec) == [("createThing", "#/components/parameters/Q/schema/not")]


def test_a_path_level_parameter_the_operation_overrides_is_not_checked():
    spec = _spec(
        {
            "x-pipeshub-sdk": True,
            "parameters": [{"name": "q", "in": "query", "schema": {"type": "string"}}],
        }
    )
    spec["paths"]["/things"]["parameters"] = [
        {"name": "q", "in": "query", "schema": {"not": {"enum": ["x"]}}}
    ]
    assert violations(spec) == []


def test_a_discriminator_mapping_named_not_is_not_a_schema():
    spec = _spec(
        {
            "x-pipeshub-sdk": True,
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "oneOf": [{"$ref": "#/components/schemas/Thing"}],
                            "discriminator": {
                                "propertyName": "kind",
                                "mapping": {"not": "#/components/schemas/Thing"},
                            },
                        }
                    }
                }
            },
        },
        {"Thing": {"type": "object"}},
    )
    assert violations(spec) == []
