"""Strict OpenAPI audit of GET /.well-known/jwks.json."""

from __future__ import annotations

import pytest
from oidc_discovery_audit_support import (
    JWKS_ROUTE,
    OPENID_CONFIGURATION_ROUTE,
    UNKNOWN_QUERY,
    DiscoveryClient,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit


def test_jwks_matches_the_signing_algorithm(discovery_client: DiscoveryClient) -> None:
    algorithm = discovery_client.fetch(OPENID_CONFIGURATION_ROUTE).json()[
        "id_token_signing_alg_values_supported"
    ][0]

    resp = discovery_client.fetch(JWKS_ROUTE)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, JWKS_ROUTE)
    keys = resp.json()["keys"]
    # An instance signs with one algorithm; a symmetric key has nothing public to publish.
    if algorithm == "HS256":
        assert keys == []
    else:
        assert [set(k) for k in keys] == [{"kty", "use", "alg", "kid", "n", "e"}]
        assert keys[0]["kty"] == "RSA" and keys[0]["alg"] == "RS256"


def test_unknown_query_parameters_are_ignored(discovery_client: DiscoveryClient) -> None:
    with outside_request_contract("the route takes no parameters; this shows it ignores them"):
        resp = discovery_client.fetch(JWKS_ROUTE, params=UNKNOWN_QUERY)
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, JWKS_ROUTE)
