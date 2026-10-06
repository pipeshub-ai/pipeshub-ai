"""Strict OpenAPI audit of GET /api/v1/connectors/registry/:connectorType/schema."""

from __future__ import annotations

from urllib.parse import quote

import pytest
from connectors_audit_support import (
    MISSING_CONNECTOR_TYPE,
    PERSONAL_CONNECTOR_TYPE,
    SEED_CONNECTOR_TYPE,
    UNSAFE_CONNECTOR_ID,
    ConnectorsAuditClient,
    bearer,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/registry/:connectorType/schema"


def _path(connector_type: str) -> str:
    return f"/registry/{quote(connector_type, safe='%')}/schema"


def test_schema_as_admin_returns_the_registry_config(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.get(_path(SEED_CONNECTOR_TYPE))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["success"] is True, body
    assert body["schema"]["auth"]["supportedAuthTypes"] == ["NONE"], body
    assert set(body["schema"]["filters"]) == {"sync", "indexing"}, body


@pytest.mark.parametrize("scope", ["team", "personal"])
def test_schema_of_every_registered_type_matches_the_spec(
    connectors_client: ConnectorsAuditClient, scope: str
) -> None:
    # Each connector declares its own auth, sync and filter fields; one type would
    # leave most of ConnectorSchema unobserved.
    listing = connectors_client.get("/registry", params={"scope": scope, "limit": 200})
    assert listing.status_code == 200, listing.text[:500]
    types = [entry["type"] for entry in listing.json()["connectors"]]
    assert types, f"the {scope} registry is empty"

    problems: list[str] = []
    for connector_type in types:
        resp = connectors_client.get(_path(connector_type))
        if resp.status_code != 200:
            problems.append(f"{connector_type}: HTTP {resp.status_code} {resp.text[:200]}")
            continue
        try:
            assert_strict_openapi_exchange(resp, ROUTE)
        except AssertionError as error:
            problems.append(f"{connector_type}: {error}")
    assert not problems, "\n".join(problems)


def test_schema_is_readable_by_a_non_admin_member(second_user: SecondUser) -> None:
    # No userAdminCheck on this route, in Node or in Python.
    resp = request_as(second_user, "GET", _path(PERSONAL_CONNECTOR_TYPE))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert isinstance(resp.json()["schema"], dict), resp.text[:500]


def test_schema_ignores_query_parameters(
    connectors_client: ConnectorsAuditClient,
) -> None:
    with outside_request_contract("the route validates only the path parameter"):
        resp = connectors_client.get(_path(SEED_CONNECTOR_TYPE), params={"scope": "nonsense"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_schema_of_an_unregistered_type_is_404(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.get(_path(MISSING_CONNECTOR_TYPE))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_schema_type_lookup_is_case_sensitive(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.get(_path(SEED_CONNECTOR_TYPE.lower()))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_schema_without_a_token_is_401(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.get(_path(SEED_CONNECTOR_TYPE), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_schema_without_the_connector_read_scope_is_forbidden(
    connectors_client: ConnectorsAuditClient, token_without_connector_scopes: str
) -> None:
    resp = connectors_client.get(
        _path(SEED_CONNECTOR_TYPE), auth=False, headers=bearer(token_without_connector_scopes)
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_schema_with_an_unsafe_type_is_400_before_auth(
    connectors_client: ConnectorsAuditClient,
) -> None:
    # guardPathParams is a router.param hook, so it answers ahead of authenticate.
    resp = connectors_client.get(_path(UNSAFE_CONNECTOR_ID), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
