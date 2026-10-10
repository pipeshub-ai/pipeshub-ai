"""Strict OpenAPI audit of GET /.well-known/oauth-protected-resource/mcp."""

from __future__ import annotations

import pytest
from oidc_discovery_audit_support import (
    FIRST_PARTY_DEVICE_CLIENT_ID,
    OPENID_CONFIGURATION_ROUTE,
    PROTECTED_RESOURCE_ROUTE,
    UNKNOWN_QUERY,
    DiscoveryClient,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit


def test_protected_resource_metadata_names_the_mcp_endpoint(
    discovery_client: DiscoveryClient,
) -> None:
    resp = discovery_client.fetch(PROTECTED_RESOURCE_ROUTE)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, PROTECTED_RESOURCE_ROUTE)
    body = resp.json()
    issuer = discovery_client.fetch(OPENID_CONFIGURATION_ROUTE).json()["issuer"]
    assert body == {
        "resource": f"{issuer}/mcp",
        "authorization_servers": [issuer],
        "scopes_supported": body["scopes_supported"],
        "bearer_methods_supported": ["header"],
        "resource_documentation": f"{issuer}/api/v1/docs",
        "pipeshub_device_client_id": FIRST_PARTY_DEVICE_CLIENT_ID,
    }
    assert body["scopes_supported"] and all(isinstance(s, str) for s in body["scopes_supported"])


def test_unknown_query_parameters_are_ignored(discovery_client: DiscoveryClient) -> None:
    with outside_request_contract("the route takes no parameters; this shows it ignores them"):
        resp = discovery_client.fetch(PROTECTED_RESOURCE_ROUTE, params=UNKNOWN_QUERY)
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, PROTECTED_RESOURCE_ROUTE)
