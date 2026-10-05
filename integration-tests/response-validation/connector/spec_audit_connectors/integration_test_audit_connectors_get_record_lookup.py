"""Strict OpenAPI audit of GET /api/v1/connectors/record/lookup."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from connectors_audit_support import ConnectorsAuditClient, request_as
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/record/lookup"


def _unknown_identifier() -> str:
    return f"SPECAUDIT-{uuid.uuid4().hex[:10]}"


def test_admin_lookup_of_unknown_identifier_is_empty_200(
    connectors_client: ConnectorsAuditClient,
) -> None:
    identifier = _unknown_identifier()

    resp = connectors_client.lookup_record(identifier)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    # Not-found and no-access are indistinguishable: a miss is a 200, not a 404.
    body = resp.json()
    assert body["matches"] == []
    assert body["ambiguous"] is False
    assert body["not_found_identifiers"] == [identifier]
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
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["matches"] == []
    assert body["not_found_identifiers"] == identifiers


def test_lookup_without_token_is_401(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.lookup_record(_unknown_identifier(), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({}, id="identifiers-missing"),
        # Passes zod (length 1) but the controller trims it away and refuses it itself.
        pytest.param({"identifiers": " "}, id="identifiers-blank"),
    ],
)
def test_lookup_with_invalid_identifiers_is_400(
    connectors_client: ConnectorsAuditClient, params: dict[str, Any]
) -> None:
    resp = connectors_client.get("/record/lookup", params=params)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
