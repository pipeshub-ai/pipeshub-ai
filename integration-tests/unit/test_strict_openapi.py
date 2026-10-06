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
from strict_openapi import adapt_document, strict_request_problems, strict_response_problems  # noqa: E402

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


_REQUEST_DOC = adapt_document({
    "paths": {
        "/teams": {
            "post": {
                "parameters": [
                    {"name": "notify", "in": "query", "schema": {"type": "boolean"}},
                    {"name": "kind", "in": "query", "required": True, "schema": {"type": "string", "enum": ["a", "b"]}},
                ],
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/NewTeam"}}},
                },
                "responses": {"201": {"description": "created"}},
            }
        }
    },
    "components": {
        "schemas": {
            "NewTeam": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}, "size": {"type": "integer"}},
            }
        }
    },
})
_REQUEST_REGISTRY = _make_registry(_REQUEST_DOC)


def _request_problems(body: object, *, query: str = "kind=a", status: int = 201, rejected: list[str] | None = None) -> list[str]:
    response = b""
    if rejected is not None:
        errors = [{"field": f, "message": "is required."} for f in rejected]
        response = json.dumps({"error": {"code": "VALIDATION_ERROR", "metadata": {"errors": errors}}}).encode()
    raw = b"" if body is None else json.dumps(body).encode()
    return strict_request_problems(
        _REQUEST_DOC, _REQUEST_REGISTRY, "POST", "/api/v1/teams", query, "application/json", raw, status, response
    )


def test_an_accepted_request_the_spec_allows_has_no_problems() -> None:
    assert _request_problems({"name": "a", "size": 3}, query="kind=a&notify=true") == []


def test_an_accepted_request_must_be_one_the_spec_allows() -> None:
    assert "'name' is a required property" in _request_problems({"size": 3})[0]
    assert "query.kind: the spec says it is required" in _request_problems({"name": "a"}, query="")[0]
    assert "query.kind: 'c' is not one of" in _request_problems({"name": "a"}, query="kind=c")[0]
    assert "body: the spec says a request body is required" in _request_problems(None)[0]


def test_an_accepted_request_may_not_carry_what_the_spec_leaves_out() -> None:
    assert _request_problems({"name": "a", "colour": "red"}) == [
        "POST /teams request, accepted with 201: body.colour: field is sent but is not in the spec"
    ]
    assert "query.page: parameter is sent but is not in the spec" in _request_problems({"name": "a"}, query="kind=a&page=2")[0]


def test_a_request_the_validator_rejects_must_be_one_the_spec_forbids() -> None:
    assert _request_problems({"size": 3}, status=400, rejected=["body.name"]) == []
    problems = _request_problems({"name": "a"}, status=400, rejected=["body.size"])
    assert problems == [
        "POST /teams request, rejected with 400: body.size: the API rejects it (is required.) but the spec allows this request"
    ]
    assert _request_problems({"name": "a"}, status=400, rejected=["params.teamId"]) == []


def test_a_rejection_that_is_not_from_the_validator_is_not_judged() -> None:
    assert _request_problems({"name": "a"}, status=400) == []
    assert _request_problems({"name": "a"}, status=404) == []
