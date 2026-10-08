"""Client and constants for the strict OpenAPI audit of the /.well-known discovery routes."""

from __future__ import annotations

from typing import Any

import requests

from helper.http.api_client import APIClient

WELL_KNOWN = "/.well-known"
OPENID_CONFIGURATION_ROUTE = f"{WELL_KNOWN}/openid-configuration"
AUTHORIZATION_SERVER_ROUTE = f"{WELL_KNOWN}/oauth-authorization-server"
PROTECTED_RESOURCE_ROUTE = f"{WELL_KNOWN}/oauth-protected-resource/mcp"
JWKS_ROUTE = f"{WELL_KNOWN}/jwks.json"

FIRST_PARTY_DEVICE_CLIENT_ID = "pipeshub-agent"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"

MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}
UNKNOWN_QUERY = {"spec_audit": "ignored"}

# Present unless the instance turns the device grant off (PIPESHUB_ENABLE_DEVICE_GRANT=false).
DEVICE_METADATA_KEYS = {"device_authorization_endpoint", "pipeshub_device_client_id"}
# Present only with PIPESHUB_ENABLE_DCR=true.
DCR_METADATA_KEY = "registration_endpoint"
SERVER_METADATA_KEYS = {
    "issuer",
    "authorization_endpoint",
    "token_endpoint",
    "userinfo_endpoint",
    "revocation_endpoint",
    "introspection_endpoint",
    "jwks_uri",
    "scopes_supported",
    "response_types_supported",
    "grant_types_supported",
    "token_endpoint_auth_methods_supported",
    "subject_types_supported",
    "id_token_signing_alg_values_supported",
    "claims_supported",
    "code_challenge_methods_supported",
}


class DiscoveryClient(APIClient):
    """Client for /.well-known; the router has no auth middleware."""

    BASE = WELL_KNOWN

    def fetch(self, route: str, **kwargs: Any) -> requests.Response:
        return self._client.request("GET", route, auth=False, **kwargs)


def assert_server_metadata(body: dict[str, Any]) -> None:
    """The authorization server metadata both discovery routes serve."""
    assert set(body) - {DCR_METADATA_KEY} == SERVER_METADATA_KEYS | DEVICE_METADATA_KEYS, sorted(body)
    issuer = body["issuer"]
    base = f"{issuer}/api/v1/oauth2"
    for key, suffix in [
        ("authorization_endpoint", "/authorize"),
        ("token_endpoint", "/token"),
        ("userinfo_endpoint", "/userinfo"),
        ("revocation_endpoint", "/revoke"),
        ("introspection_endpoint", "/introspect"),
        ("device_authorization_endpoint", "/device_authorization"),
    ]:
        assert body[key] == base + suffix, (key, body[key])
    if DCR_METADATA_KEY in body:
        assert body[DCR_METADATA_KEY] == f"{base}/register"
    assert body["jwks_uri"] == f"{issuer}/.well-known/jwks.json"
    assert body["pipeshub_device_client_id"] == FIRST_PARTY_DEVICE_CLIENT_ID
    assert {"openid", "profile", "email", "offline_access"} <= set(body["scopes_supported"])
    assert body["response_types_supported"] == ["code"]
    assert body["grant_types_supported"] == [
        "authorization_code",
        "client_credentials",
        "refresh_token",
        DEVICE_GRANT_TYPE,
    ]
    assert body["token_endpoint_auth_methods_supported"] == [
        "none",
        "client_secret_basic",
        "client_secret_post",
    ]
    assert body["subject_types_supported"] == ["public"]
    assert body["id_token_signing_alg_values_supported"] in (["HS256"], ["RS256"])
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert "email" in body["claims_supported"]
