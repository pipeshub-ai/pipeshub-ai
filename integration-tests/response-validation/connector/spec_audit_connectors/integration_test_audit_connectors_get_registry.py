"""Strict OpenAPI audit of GET /api/v1/connectors/registry."""

from __future__ import annotations

import pytest
from connectors_audit_support import ConnectorsAuditClient, request_as
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/registry"


def test_admin_lists_every_registered_connector_type(
    connectors_client: ConnectorsAuditClient,
) -> None:
    # The largest page, so the strict check sees as many connector shapes as possible.
    resp = connectors_client.get("/registry", params={"limit": 200})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    assert isinstance(body["connectors"], list)
    assert body["pagination"]["page"] == 1
    assert body["pagination"]["limit"] == 200
    assert body["pagination"]["totalCount"] >= len(body["connectors"])
    assert set(body["registryCountsByScope"]) == {"personal", "team"}


def test_member_pages_and_searches_the_personal_registry(
    second_user: SecondUser,
) -> None:
    # No admin gate on this route; a one-item page also exercises nextPage / search echo.
    resp = request_as(
        second_user,
        "GET",
        "/registry",
        params={"scope": "personal", "page": 1, "limit": 1, "search": "a"},
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert len(body["connectors"]) <= 1
    assert body["pagination"]["limit"] == 1
    assert body["pagination"]["search"] == "a"
    assert body["pagination"]["hasPrev"] is False
    assert body["pagination"]["prevPage"] is None


def test_registry_without_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.get("/registry", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"scope": "organisation"}, id="scope-not-in-enum"),
        pytest.param({"limit": 201}, id="limit-above-200"),
    ],
)
def test_registry_rejects_invalid_query(
    connectors_client: ConnectorsAuditClient, params: dict[str, str | int]
) -> None:
    resp = connectors_client.get("/registry", params=params)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
