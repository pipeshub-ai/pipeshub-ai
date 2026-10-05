"""The strict OpenAPI check fails on what the spec leaves out, not only on what it forbids."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_RV_HELPER = Path(__file__).resolve().parents[1] / "response-validation" / "helper"
if str(_RV_HELPER) not in sys.path:
    sys.path.insert(0, str(_RV_HELPER))

from openapi_schema_validator import _make_registry  # noqa: E402
from strict_openapi import adapt_document, strict_response_problems  # noqa: E402

pytestmark = pytest.mark.unit

_DOC = adapt_document({
    "paths": {
        "/teams/{teamId}": {
            "get": {
                "responses": {
                    "200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Team"}}}},
                    "204": {"description": "nothing"},
                    "404": {"$ref": "#/components/responses/NotFound"},
                }
            }
        }
    },
    "components": {
        "responses": {
            "NotFound": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}}
        },
        "schemas": {
            "Error": {"type": "object", "properties": {"message": {"type": "string"}}},
            "Named": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}},
            "Team": {
                "allOf": [
                    {"$ref": "#/components/schemas/Named"},
                    {
                        "type": "object",
                        "properties": {
                            "owner": {"type": "object", "nullable": True, "properties": {"id": {"type": "string"}}},
                            "members": {"type": "array", "items": {"$ref": "#/components/schemas/Named"}},
                            "labels": {"type": "object", "additionalProperties": {"type": "string"}},
                            "meta": {"type": "object"},
                        },
                    },
                ]
            },
        },
    },
})
_REGISTRY = _make_registry(_DOC)


def _problems(body: object, *, status: int = 200, path: str = "/api/v1/teams/:id", method: str = "GET",
              content_type: str = "application/json; charset=utf-8") -> list[str]:
    raw = b"" if body is None else json.dumps(body).encode()
    return strict_response_problems(_DOC, _REGISTRY, method, path, status, content_type, raw)


def test_a_described_response_has_no_problems() -> None:
    body = {"name": "a", "owner": None, "members": [{"name": "b"}], "labels": {"any": "thing"}, "meta": {}}
    assert _problems(body) == []
    assert _problems(body, path="/teams/{teamId}") == []
    assert _problems(None, status=204) == []


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"name": "a", "extra": 1}, "$.extra: field is returned but is not in the spec"),
        ({"name": "a", "owner": {"id": "1", "email": "x"}}, "$.owner.email: field is returned"),
        ({"name": "a", "members": [{"name": "b", "role": "x"}]}, "$.members[].role: field is returned"),
        ({"name": "a", "meta": {"k": 1}}, "$.meta: object is returned with fields ['k'] but the spec describes none"),
        ({"owner": None}, "'name' is a required property"),
        ({"name": "a", "labels": {"k": 1}}, "$.labels.k: 1 is not of type 'string'"),
    ],
)
def test_what_the_spec_leaves_out_is_reported(body: dict, expected: str) -> None:
    problems = _problems(body)
    assert len(problems) == 1, problems
    assert expected in problems[0]


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"body": {}, "path": "/api/v1/teams"}, "is not in the OpenAPI spec"),
        ({"body": {}, "method": "DELETE"}, "is not in the OpenAPI spec"),
        ({"body": {"message": "no"}, "status": 403}, "status is not documented"),
        ({"body": {"message": "no", "code": 1}, "status": 404}, "$.code: field is returned"),
        ({"body": {"name": "a"}, "status": 204}, "the spec documents none"),
        ({"body": None}, "the response is empty"),
        ({"body": {"name": "a"}, "content_type": "text/html"}, "content type 'text/html' is not documented"),
    ],
)
def test_undocumented_routes_statuses_and_bodies_are_reported(kwargs: dict, expected: str) -> None:
    problems = _problems(**kwargs)
    assert len(problems) == 1, problems
    assert expected in problems[0]
