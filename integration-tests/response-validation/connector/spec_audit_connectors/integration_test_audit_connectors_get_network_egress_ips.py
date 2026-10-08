"""Strict OpenAPI audit of GET /api/v1/connectors/network/egress-ips."""

from __future__ import annotations

import ipaddress
from typing import Any

import pytest
from connectors_audit_support import ConnectorsAuditClient, bearer, request_as
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/network/egress-ips"
PATH = "/network/egress-ips"


def test_admin_reads_the_egress_addresses(connectors_client: ConnectorsAuditClient) -> None:
    resp = connectors_client.get(PATH)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["success"] is True
    # Empty when the deployment's address cannot be determined; never anything but addresses.
    for address in body["egressIps"]:
        ipaddress.ip_address(address)


def test_a_non_admin_member_reads_the_same_list(
    connectors_client: ConnectorsAuditClient, second_user: SecondUser
) -> None:
    # No userAdminCheck on this route, in Node or in Python.
    resp = request_as(second_user, "GET", PATH)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == connectors_client.get(PATH).json()


def test_query_parameters_are_ignored(connectors_client: ConnectorsAuditClient) -> None:
    with outside_request_contract("the route takes no parameters and has no validator"):
        resp = connectors_client.get(PATH, params={"refresh": "true"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_no_token_is_unauthorized(connectors_client: ConnectorsAuditClient) -> None:
    resp = connectors_client.get(PATH, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("scopes", [(), ("connector:write",)], ids=["no-connector-scope", "write-only"])
def test_a_token_without_connector_read_is_forbidden(
    connectors_client: ConnectorsAuditClient,
    token_without_connector_scopes: str,
    token_with_scopes: Any,
    scopes: tuple[str, ...],
) -> None:
    token = token_with_scopes(*scopes) if scopes else token_without_connector_scopes
    resp = connectors_client.get(PATH, auth=False, headers=bearer(token))

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: connector:read"


def test_a_token_with_connector_read_reads_the_list(
    connectors_client: ConnectorsAuditClient, token_with_scopes: Any
) -> None:
    resp = connectors_client.get(PATH, auth=False, headers=bearer(token_with_scopes("connector:read")))

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
