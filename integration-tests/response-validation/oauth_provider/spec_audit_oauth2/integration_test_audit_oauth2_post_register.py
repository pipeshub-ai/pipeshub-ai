"""Strict OpenAPI audit of POST /api/v1/oauth2/register (RFC 7591 dynamic client registration)."""

from __future__ import annotations

from typing import Any

import pytest
from helper.pipeshub_client import PipeshubClient
from oauth2_audit_support import DCR_REDIRECT_URI, OAuth2Client, delete_dynamic_client
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/oauth2/register"
DISCOVERY_PATH = "/.well-known/openid-configuration"


@pytest.fixture(scope="module")
def dcr_enabled(pipeshub_client: PipeshubClient) -> bool:
    """Whether the server runs with PIPESHUB_ENABLE_DCR=true.

    The route answers every schema-valid body with 403 unless that flag is set,
    and discovery advertises registration_endpoint under exactly the same flag.
    """
    resp = pipeshub_client.request("GET", DISCOVERY_PATH, auth=False)
    assert resp.status_code == 200, resp.text[:500]
    return "registration_endpoint" in resp.json()


@pytest.mark.parametrize(
    ("auth_method", "confidential"),
    [
        pytest.param("none", False, id="public-client"),
        pytest.param("client_secret_basic", True, id="confidential-client"),
    ],
)
def test_register_creates_a_client_only_when_dcr_is_enabled(
    oauth2_client: OAuth2Client,
    dcr_enabled: bool,
    auth_method: str,
    confidential: bool,
) -> None:
    resp = oauth2_client.register(
        client_name=f"spec-audit-dcr-{auth_method}",
        redirect_uris=[DCR_REDIRECT_URI],
        token_endpoint_auth_method=auth_method,
    )
    try:
        assert resp.status_code == (201 if dcr_enabled else 403), resp.text[:500]
        assert_strict_openapi_response(resp, ROUTE)
        body = resp.json()
        if not dcr_enabled:
            assert body["error"] == "access_denied", body
            return
        assert body["redirect_uris"] == [DCR_REDIRECT_URI], body
        assert body["grant_types"] == ["authorization_code", "refresh_token"], body
        assert body["response_types"] == ["code"], body
        assert body["token_endpoint_auth_method"] == auth_method, body
        assert body["client_secret_expires_at"] == 0, body
        # Only a confidential client is told its secret.
        assert ("client_secret" in body) is confidential, body
    finally:
        if resp.status_code == 201:
            delete_dynamic_client(resp.json()["client_id"])


def test_register_rejects_metadata_failing_the_schema(
    oauth2_client: OAuth2Client,
) -> None:
    # Validation runs before the feature flag, so this is 400 whether DCR is on or off.
    resp = oauth2_client.register(
        redirect_uris=[DCR_REDIRECT_URI], token_endpoint_auth_method="private_key_jwt"
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "metadata",
    [
        pytest.param(
            {"grant_types": ["client_credentials"]}, id="client-credentials-grant"
        ),
        pytest.param(
            {
                "redirect_uris": ["http://example.com/callback"],
                "grant_types": ["authorization_code"],
            },
            id="non-loopback-http-redirect",
        ),
    ],
)
def test_register_refuses_metadata_the_service_does_not_allow(
    oauth2_client: OAuth2Client, dcr_enabled: bool, metadata: dict[str, Any]
) -> None:
    resp = oauth2_client.register(**metadata)
    try:
        # The flag is checked before the metadata, so a disabled server never gets as far as the 400.
        assert resp.status_code == (400 if dcr_enabled else 403), resp.text[:500]
        assert_strict_openapi_response(resp, ROUTE)
        expected_error = "invalid_client_metadata" if dcr_enabled else "access_denied"
        assert resp.json()["error"] == expected_error, resp.text[:500]
    finally:
        if resp.status_code == 201:
            delete_dynamic_client(resp.json()["client_id"])
