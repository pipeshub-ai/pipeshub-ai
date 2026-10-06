"""Strict OpenAPI audit of GET /api/v1/connectors/record/lookup."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from connectors_audit_support import (
    ConnectorsAuditClient,
    bearer,
    request_as,
    spec_query_value_errors,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/record/lookup"

# The gate skips array-typed query parameters, so for `identifiers` it can neither see
# that the spec refuses a value nor that it allows one. These tests check the parameter
# schema themselves and tell the gate to leave the request side alone.
ARRAY_PARAMETER = "the gate does not evaluate array-typed query parameters"


def _unknown_identifier() -> str:
    return f"SPECAUDIT-{uuid.uuid4().hex[:10]}"


def test_admin_lookup_of_unknown_identifier_is_empty_200(
    connectors_client: ConnectorsAuditClient,
) -> None:
    identifier = _unknown_identifier()

    resp = connectors_client.lookup_record(identifier)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert not spec_query_value_errors("GET", ROUTE, "identifiers", [identifier])

    # Not-found and no-access are indistinguishable: a miss is a 200, not a 404.
    body = resp.json()
    assert body["matches"] == []
    assert body["ambiguous"] is False
    assert body["not_found_identifiers"] == [identifier]
    assert isinstance(body["searched_connectors"], dict)
    assert isinstance(body["text"], str)


def test_member_lookup_with_repeated_identifiers_and_connector_hint(
    second_user: SecondUser,
) -> None:
    identifiers = [
        _unknown_identifier(),
        f"https://spec-audit.invalid/browse/{uuid.uuid4().hex[:10]}?a=1,2",
    ]

    resp = request_as(
        second_user,
        "GET",
        "/record/lookup",
        params={"identifiers": identifiers, "connectorName": "JIRA"},
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["matches"] == []
    assert body["not_found_identifiers"] == identifiers


def test_lookup_takes_ten_identifiers_and_keeps_duplicates(
    connectors_client: ConnectorsAuditClient,
) -> None:
    identifier = _unknown_identifier()
    identifiers = [identifier] * 10

    resp = connectors_client.lookup_record(identifiers)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert not spec_query_value_errors("GET", ROUTE, "identifiers", identifiers)
    assert resp.json()["not_found_identifiers"] == identifiers


def test_lookup_connector_hint_is_any_non_empty_string(
    connectors_client: ConnectorsAuditClient,
) -> None:
    # A hint only reorders the search, so a name no connector has is not an error.
    resp = connectors_client.lookup_record(
        _unknown_identifier(), connector_name="SPEC_AUDIT_NOT_A_CONNECTOR"
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["matches"] == []


def test_lookup_ignores_an_unknown_query_parameter(
    connectors_client: ConnectorsAuditClient,
) -> None:
    with outside_request_contract("the validator strips query parameters it does not know"):
        resp = connectors_client.get(
            "/record/lookup", params={"identifiers": _unknown_identifier(), "specAuditUnknown": "1"}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_lookup_without_token_is_401(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.lookup_record(_unknown_identifier(), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_lookup_with_neither_read_scope_is_forbidden(
    connectors_client: ConnectorsAuditClient, token_without_connector_scopes: str
) -> None:
    resp = connectors_client.lookup_record(
        _unknown_identifier(), auth=False, headers=bearer(token_without_connector_scopes)
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_lookup_without_identifiers_is_400(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.get("/record/lookup")
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "identifiers",
    [
        pytest.param([""], id="one-empty"),
        # Query values are trimmed before validation, so a blank one is an empty one.
        pytest.param([" "], id="one-blank"),
        pytest.param(["PA-1", ""], id="one-of-two-empty"),
        pytest.param(["x" * 2049], id="longer-than-2048"),
        pytest.param([f"PA-{index}" for index in range(11)], id="eleven"),
    ],
)
def test_lookup_identifiers_refused_by_the_validator(
    connectors_client: ConnectorsAuditClient, identifiers: list[str]
) -> None:
    with outside_request_contract(ARRAY_PARAMETER):
        resp = connectors_client.get("/record/lookup", params={"identifiers": identifiers})
        assert resp.status_code == 400, resp.text[:500]
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    # The value the validator saw: a blank identifier reaches it trimmed.
    seen = [identifier.strip() for identifier in identifiers]
    assert spec_query_value_errors("GET", ROUTE, "identifiers", seen), (
        "the API refuses these identifiers but the spec's parameter schema allows them"
    )


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"connectorName": ""}, id="connector-name-empty"),
        pytest.param([("connectorName", "JIRA"), ("connectorName", "SLACK")], id="connector-name-repeated"),
    ],
)
def test_lookup_connector_name_refused_by_the_validator(
    connectors_client: ConnectorsAuditClient, params: Any
) -> None:
    query = [("identifiers", _unknown_identifier()), *(params.items() if isinstance(params, dict) else params)]
    resp = connectors_client.get("/record/lookup", params=query)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
