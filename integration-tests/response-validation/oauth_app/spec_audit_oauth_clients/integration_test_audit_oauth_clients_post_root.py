"""Strict OpenAPI audit of POST /api/v1/oauth-clients.

authenticate -> requireSessionAuth -> rate limiter -> refuseServiceAccountCaller ->
createAppSchema (with the redirect-URI refine) -> createApp (role-aware scope check,
redirect URI rules, then the insert).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from helper.second_user import SecondUser
from oauth_clients_audit_support import (
    ADMIN_ONLY_SCOPES,
    LIST_ROUTE,
    OAuthClientsAuditClient,
    app_name,
    request_as,
)
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = LIST_ROUTE
CLIENT_CREDENTIALS = {"allowedGrantTypes": ["client_credentials"], "allowedScopes": ["openid"]}


@pytest.fixture
def created_app_ids(oauth_clients_client: OAuthClientsAuditClient) -> Iterator[list[str]]:
    """Ids of apps the admin creates in a test; deleted on teardown."""
    ids: list[str] = []
    try:
        yield ids
    finally:
        for app_id in ids:
            oauth_clients_client.delete_app(app_id)


def _create(
    client: OAuthClientsAuditClient, created: list[str], **body: Any
):
    body.setdefault("name", app_name())
    resp = client.create_app(**body)
    if resp.status_code == 201:
        created.append(resp.json()["app"]["id"])
    return resp


def test_create_with_every_field_returns_the_app_and_its_secret_once(
    oauth_clients_client: OAuthClientsAuditClient, created_app_ids: list[str]
) -> None:
    body = {
        "name": app_name(),
        "description": "spec audit app",
        "redirectUris": ["https://spec-audit.example/callback"],
        "allowedGrantTypes": ["authorization_code", "refresh_token", "client_credentials"],
        "allowedScopes": ["openid", "profile", *ADMIN_ONLY_SCOPES[:1]],
        "homepageUrl": "https://spec-audit.example",
        "privacyPolicyUrl": "https://spec-audit.example/privacy",
        "termsOfServiceUrl": "https://spec-audit.example/terms",
        "isConfidential": False,
        "accessTokenLifetime": 300,
        "refreshTokenLifetime": 3600,
    }

    resp = _create(oauth_clients_client, created_app_ids, **body)

    assert resp.status_code == 201, resp.text[:500]
    payload = resp.json()
    assert payload["message"] == "OAuth app created successfully"
    app = payload["app"]
    for key, value in body.items():
        assert app[key] == value, key
    assert app["status"] == "active"
    assert len(app["clientSecret"]) == 64
    assert_strict_openapi_exchange(resp, ROUTE)


def test_defaults_when_only_the_required_fields_and_a_redirect_are_sent(
    oauth_clients_client: OAuthClientsAuditClient, created_app_ids: list[str]
) -> None:
    resp = _create(
        oauth_clients_client,
        created_app_ids,
        allowedScopes=["openid"],
        redirectUris=["http://localhost:3000/callback"],
    )

    assert resp.status_code == 201, resp.text[:500]
    app = resp.json()["app"]
    assert app["allowedGrantTypes"] == ["authorization_code", "refresh_token"]
    assert app["isConfidential"] is True
    assert app["accessTokenLifetime"] == 3600
    assert app["refreshTokenLifetime"] == 2592000
    for absent in ("description", "homepageUrl", "privacyPolicyUrl", "termsOfServiceUrl"):
        assert absent not in app
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param({"allowedGrantTypes": []}, id="no-grant-types"),
        pytest.param({"allowedGrantTypes": ["refresh_token"], "redirectUris": []}, id="no-authorization-code"),
    ],
)
def test_no_redirect_uri_needed_without_authorization_code(
    oauth_clients_client: OAuthClientsAuditClient, created_app_ids: list[str], extra: dict[str, Any]
) -> None:
    resp = _create(oauth_clients_client, created_app_ids, allowedScopes=["openid"], **extra)

    assert resp.status_code == 201, resp.text[:500]
    assert resp.json()["app"]["redirectUris"] == []
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "cursor://anysphere.cursor-mcp/oauth/callback",
        "http://localhost:3000/callback",
        "http://127.0.0.1/callback",
        "ftp://localhost/callback",
        # new URL() lowercases scheme and host, skips extra or missing slashes and reads "\\" as "/".
        "HTTPS://spec-audit.example/callback",
        "Https://Spec-Audit.example/callback",
        "https:spec-audit.example/callback",
        "https:///spec-audit.example/callback",
        "https:\\\\spec-audit.example\\callback",
        "http://LOCALHOST:3000/callback",
        "HTTP://127.0.0.1/callback",
    ],
)
def test_redirect_uris_the_service_lets_through(
    oauth_clients_client: OAuthClientsAuditClient, created_app_ids: list[str], redirect_uri: str
) -> None:
    resp = _create(
        oauth_clients_client, created_app_ids, allowedScopes=["openid"], redirectUris=[redirect_uri]
    )

    assert resp.status_code == 201, resp.text[:500]
    assert resp.json()["app"]["redirectUris"] == [redirect_uri]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_redirect_uri_surrounding_whitespace_is_trimmed(
    oauth_clients_client: OAuthClientsAuditClient, created_app_ids: list[str]
) -> None:
    resp = _create(
        oauth_clients_client,
        created_app_ids,
        allowedScopes=["openid"],
        redirectUris=[" https://spec-audit.example/callback "],
    )

    assert resp.status_code == 201, resp.text[:500]
    assert resp.json()["app"]["redirectUris"] == ["https://spec-audit.example/callback"]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("redirect_uri", "message"),
    [
        ("http://spec-audit.example/callback", "Redirect URI must use HTTPS (except localhost)"),
        ("foo:bar", "Redirect URI must use HTTPS (except localhost)"),
        ("https://spec-audit.example/callback#frag", "Redirect URI must not contain a fragment"),
    ],
    ids=["plain-http", "custom-scheme", "fragment"],
)
def test_redirect_uris_the_service_refuses(
    oauth_clients_client: OAuthClientsAuditClient,
    created_app_ids: list[str],
    redirect_uri: str,
    message: str,
) -> None:
    resp = _create(
        oauth_clients_client, created_app_ids, allowedScopes=["openid"], redirectUris=[redirect_uri]
    )

    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "OAUTH_INVALID_REDIRECT_URI"
    assert error["message"].startswith(message)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        pytest.param({}, ["body.name", "body.allowedScopes"], id="empty-body"),
        pytest.param({"name": "", **CLIENT_CREDENTIALS}, ["body.name"], id="empty-name"),
        pytest.param({"name": "n" * 101, **CLIENT_CREDENTIALS}, ["body.name"], id="name-too-long"),
        pytest.param({"name": "n", "description": "d" * 501, **CLIENT_CREDENTIALS}, ["body.description"], id="description-too-long"),
        pytest.param({"name": "n", "allowedScopes": [], "allowedGrantTypes": ["client_credentials"]}, ["body.allowedScopes"], id="no-scopes"),
        pytest.param({"name": "n", "allowedScopes": ["openid"], "allowedGrantTypes": ["password"]}, ["body.allowedGrantTypes.0"], id="unknown-grant"),
        pytest.param({"name": "n", **CLIENT_CREDENTIALS, "redirectUris": ["not a url"]}, ["body.redirectUris.0"], id="redirect-not-a-url"),
        pytest.param({"name": "n", **CLIENT_CREDENTIALS, "redirectUris": ["https://"]}, ["body.redirectUris.0"], id="redirect-without-host"),
        pytest.param({"name": "n", **CLIENT_CREDENTIALS, "redirectUris": ["https://?x"]}, ["body.redirectUris.0"], id="redirect-query-without-host"),
        pytest.param({"name": "n", **CLIENT_CREDENTIALS, "redirectUris": [f"https://a.example/{i}" for i in range(11)]}, ["body.redirectUris"], id="eleven-redirects"),
        pytest.param({"name": "n", **CLIENT_CREDENTIALS, "homepageUrl": "spec-audit"}, ["body.homepageUrl"], id="homepage-not-a-url"),
        pytest.param({"name": "n", **CLIENT_CREDENTIALS, "privacyPolicyUrl": "spec-audit"}, ["body.privacyPolicyUrl"], id="privacy-not-a-url"),
        pytest.param({"name": "n", **CLIENT_CREDENTIALS, "termsOfServiceUrl": "spec-audit"}, ["body.termsOfServiceUrl"], id="terms-not-a-url"),
        pytest.param({"name": "n", **CLIENT_CREDENTIALS, "isConfidential": "yes"}, ["body.isConfidential"], id="confidential-not-boolean"),
        pytest.param({"name": "n", **CLIENT_CREDENTIALS, "accessTokenLifetime": 299}, ["body.accessTokenLifetime"], id="access-lifetime-too-short"),
        pytest.param({"name": "n", **CLIENT_CREDENTIALS, "accessTokenLifetime": 86401}, ["body.accessTokenLifetime"], id="access-lifetime-too-long"),
        pytest.param({"name": "n", **CLIENT_CREDENTIALS, "refreshTokenLifetime": 3599}, ["body.refreshTokenLifetime"], id="refresh-lifetime-too-short"),
        pytest.param({"name": "n", **CLIENT_CREDENTIALS, "refreshTokenLifetime": 31536001}, ["body.refreshTokenLifetime"], id="refresh-lifetime-too-long"),
        pytest.param({"name": "n", "allowedScopes": ["openid"]}, ["body.redirectUris"], id="default-grants-need-a-redirect"),
        pytest.param({"name": "n", "allowedScopes": ["openid"], "allowedGrantTypes": ["authorization_code"], "redirectUris": []}, ["body.redirectUris"], id="authorization-code-with-no-redirect"),
    ],
)
def test_invalid_body_is_a_validation_error(
    oauth_clients_client: OAuthClientsAuditClient,
    created_app_ids: list[str],
    body: dict[str, Any],
    fields: list[str],
) -> None:
    resp = oauth_clients_client._client.request("POST", oauth_clients_client.BASE, json=body)
    if resp.status_code == 201:
        created_app_ids.append(resp.json()["app"]["id"])

    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [e["field"] for e in error["metadata"]["errors"]] == fields
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_scope_is_refused(
    oauth_clients_client: OAuthClientsAuditClient, created_app_ids: list[str]
) -> None:
    resp = _create(
        oauth_clients_client,
        created_app_ids,
        allowedScopes=["openid", "spec-audit:not-a-scope"],
        allowedGrantTypes=["client_credentials"],
    )

    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "OAUTH_INVALID_SCOPE"
    assert error["metadata"] == {"invalidScopes": ["spec-audit:not-a-scope"]}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_may_create_but_not_with_admin_only_scopes(second_user: SecondUser) -> None:
    resp = request_as(
        second_user,
        "POST",
        json={"name": app_name(), "allowedScopes": ["org:admin"], "allowedGrantTypes": ["client_credentials"]},
    )

    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "OAUTH_INVALID_SCOPE"
    assert error["metadata"] == {"disallowedScopes": ["org:admin"]}
    assert_strict_openapi_exchange(resp, ROUTE)

    resp = request_as(
        second_user,
        "POST",
        json={"name": app_name(), "allowedScopes": ["openid"], "allowedGrantTypes": ["client_credentials"]},
    )
    assert resp.status_code == 201, resp.text[:500]
    app_id = resp.json()["app"]["id"]
    try:
        assert_strict_openapi_exchange(resp, ROUTE)
    finally:
        cleanup = request_as(second_user, "DELETE", f"/{app_id}")
        assert cleanup.status_code == 200, cleanup.text[:300]


def test_unknown_body_field_is_ignored(
    oauth_clients_client: OAuthClientsAuditClient, created_app_ids: list[str]
) -> None:
    with outside_request_contract("an undocumented body field, which the validator strips"):
        resp = _create(
            oauth_clients_client, created_app_ids, **CLIENT_CREDENTIALS, logoUrl="https://spec-audit.example/logo.png"
        )
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 201, resp.text[:500]
    assert "logoUrl" not in resp.json()["app"]


def test_create_without_token_is_unauthorized(oauth_clients_client: OAuthClientsAuditClient) -> None:
    resp = oauth_clients_client.create_app(auth=False, name=app_name(), **CLIENT_CREDENTIALS)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_with_oauth_access_token_is_forbidden(
    oauth_token_clients_client: OAuthClientsAuditClient,
) -> None:
    resp = oauth_token_clients_client.create_app(name=app_name(), **CLIENT_CREDENTIALS)

    assert resp.status_code == 403, resp.text[:500]
    assert "interactive user session" in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)
