"""Strict OpenAPI audit of PUT /api/v1/oauth-clients/:appId.

authenticate -> requireSessionAuth -> rate limiter -> refuseServiceAccountCaller ->
updateAppSchema (with the redirect-URI refine) -> updateApp (creator-scoped lookup,
role-aware scope check, redirect URI rules). 401, 403, a malformed id (400) and an
unknown or foreign app (404) are in integration_test_audit_oauth_clients_app_refusals.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from oauth_clients_audit_support import APP_ROUTE, OAuthClientsAuditClient, SeedOAuthApp, app_name
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = APP_ROUTE


def _put(client: OAuthClientsAuditClient, app_id: str, **kwargs: Any):
    return client._client.request("PUT", f"{client.BASE}/{app_id}", **kwargs)


def test_update_changes_only_the_fields_sent(
    oauth_clients_client: OAuthClientsAuditClient, seed_oauth_app: SeedOAuthApp
) -> None:
    app = seed_oauth_app(homepageUrl="https://spec-audit.example")
    change = {
        "name": app_name(),
        "description": "updated",
        "redirectUris": ["https://spec-audit.example/cb", "http://localhost:3000/cb"],
        "allowedGrantTypes": ["authorization_code", "client_credentials"],
        "allowedScopes": ["openid", "email"],
        "privacyPolicyUrl": "https://spec-audit.example/privacy",
        "termsOfServiceUrl": "https://spec-audit.example/terms",
        "accessTokenLifetime": 86400,
        "refreshTokenLifetime": 31536000,
    }

    resp = _put(oauth_clients_client, app["id"], json=change)

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert body["message"] == "OAuth app updated successfully"
    updated = body["app"]
    for key, value in change.items():
        assert updated[key] == value, key
    assert updated["homepageUrl"] == "https://spec-audit.example"
    assert "clientSecret" not in updated
    assert_strict_openapi_exchange(resp, ROUTE)


def test_null_clears_a_url_field(
    oauth_clients_client: OAuthClientsAuditClient, seed_oauth_app: SeedOAuthApp
) -> None:
    app = seed_oauth_app(homepageUrl="https://spec-audit.example")

    resp = _put(oauth_clients_client, app["id"], json={"homepageUrl": None})

    assert resp.status_code == 200, resp.text[:500]
    assert "homepageUrl" not in resp.json()["app"]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "kwargs",
    [pytest.param({}, id="no-body"), pytest.param({"json": {}}, id="empty-object")],
)
def test_update_with_nothing_to_change_succeeds(
    oauth_clients_client: OAuthClientsAuditClient, seed_oauth_app: SeedOAuthApp, kwargs: dict[str, Any]
) -> None:
    app = seed_oauth_app()

    resp = _put(oauth_clients_client, app["id"], **kwargs)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json()["app"]["name"] == app["name"]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"redirectUris": []}, id="clear-redirects-without-grants"),
        # The refine only looks at the body, so an app with no redirect URI can be given authorization_code.
        pytest.param({"allowedGrantTypes": ["authorization_code"]}, id="authorization-code-without-redirects"),
    ],
)
def test_refine_only_checks_redirects_sent_with_authorization_code(
    oauth_clients_client: OAuthClientsAuditClient, seed_oauth_app: SeedOAuthApp, change: dict[str, Any]
) -> None:
    app = seed_oauth_app()

    resp = _put(oauth_clients_client, app["id"], json=change)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json()["app"]["redirectUris"] == []
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("change", "fields"),
    [
        pytest.param({"name": ""}, ["body.name"], id="empty-name"),
        pytest.param({"name": "n" * 101}, ["body.name"], id="name-too-long"),
        pytest.param({"description": "d" * 501}, ["body.description"], id="description-too-long"),
        pytest.param({"allowedScopes": []}, ["body.allowedScopes"], id="no-scopes"),
        pytest.param({"allowedGrantTypes": ["implicit"]}, ["body.allowedGrantTypes.0"], id="unknown-grant"),
        pytest.param({"redirectUris": ["not a url"]}, ["body.redirectUris.0"], id="redirect-not-a-url"),
        pytest.param({"homepageUrl": "spec-audit"}, ["body.homepageUrl"], id="homepage-not-a-url"),
        pytest.param({"privacyPolicyUrl": "spec-audit"}, ["body.privacyPolicyUrl"], id="privacy-not-a-url"),
        pytest.param({"termsOfServiceUrl": "spec-audit"}, ["body.termsOfServiceUrl"], id="terms-not-a-url"),
        pytest.param({"accessTokenLifetime": 299}, ["body.accessTokenLifetime"], id="access-lifetime-too-short"),
        pytest.param({"refreshTokenLifetime": 31536001}, ["body.refreshTokenLifetime"], id="refresh-lifetime-too-long"),
        pytest.param(
            {"allowedGrantTypes": ["authorization_code"], "redirectUris": []},
            ["body.redirectUris"],
            id="authorization-code-with-no-redirect",
        ),
    ],
)
def test_invalid_body_is_a_validation_error(
    oauth_clients_client: OAuthClientsAuditClient,
    seed_oauth_app: SeedOAuthApp,
    change: dict[str, Any],
    fields: list[str],
) -> None:
    app = seed_oauth_app()

    resp = _put(oauth_clients_client, app["id"], json=change)

    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [e["field"] for e in error["metadata"]["errors"]] == fields
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "redirect_uri",
    ["http://spec-audit.example/callback", "https://spec-audit.example/callback#frag"],
    ids=["plain-http", "fragment"],
)
def test_redirect_uris_the_service_refuses(
    oauth_clients_client: OAuthClientsAuditClient, seed_oauth_app: SeedOAuthApp, redirect_uri: str
) -> None:
    app = seed_oauth_app()

    resp = _put(oauth_clients_client, app["id"], json={"redirectUris": [redirect_uri]})

    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "OAUTH_INVALID_REDIRECT_URI"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_unknown_scope_is_refused(
    oauth_clients_client: OAuthClientsAuditClient, seed_oauth_app: SeedOAuthApp
) -> None:
    app = seed_oauth_app()

    resp = _put(oauth_clients_client, app["id"], json={"allowedScopes": ["spec-audit:not-a-scope"]})

    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "OAUTH_INVALID_SCOPE"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_is_confidential_cannot_be_changed(
    oauth_clients_client: OAuthClientsAuditClient, seed_oauth_app: SeedOAuthApp
) -> None:
    app = seed_oauth_app()
    assert app["isConfidential"] is True

    with outside_request_contract("isConfidential is not an update field; the validator strips it"):
        resp = _put(oauth_clients_client, app["id"], json={"isConfidential": False})
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json()["app"]["isConfidential"] is True
