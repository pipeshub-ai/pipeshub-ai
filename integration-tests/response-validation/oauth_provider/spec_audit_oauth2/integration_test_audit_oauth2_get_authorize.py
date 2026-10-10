"""Strict OpenAPI audit of GET /api/v1/oauth2/authorize."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from helper.mcp_oauth import OAuthApp
from oauth2_audit_support import (
    MALFORMED_ACCESS_TOKEN,
    UNKNOWN_CLIENT_ID,
    OAuth2Client,
    authorize_params,
    pkce_pair,
    redirect_params,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/oauth2/authorize"


@pytest.mark.parametrize(
    "authorization",
    [
        pytest.param(None, id="no_token"),
        pytest.param(f"Bearer {MALFORMED_ACCESS_TOKEN}", id="malformed_token"),
    ],
)
def test_without_a_valid_token_redirects_to_the_frontend(
    oauth2_client: OAuth2Client, confidential_app: OAuthApp, authorization: str | None
) -> None:
    params = authorize_params(confidential_app)
    headers = {"Authorization": authorization} if authorization else None

    resp = oauth2_client.authorize(auth=False, headers=headers, **params)

    assert resp.status_code == 302, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    location = urlsplit(resp.headers["Location"])
    assert location.path == "/oauth/authorize", resp.headers["Location"]
    assert {k: v[0] for k, v in parse_qs(location.query).items()} == params
    assert resp.headers["Content-Type"].startswith("text/plain")
    assert resp.text == f"Found. Redirecting to {resp.headers['Location']}"


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"response_type": "code", "client_id": "x"}, id="missing_required"),
        pytest.param(
            {"response_type": "token", "client_id": "", "code_challenge": "too-short"},
            id="invalid_values",
        ),
        pytest.param({}, id="empty_query"),
    ],
)
def test_without_a_token_redirects_before_the_query_is_validated(
    oauth2_client: OAuth2Client, params: dict[str, str]
) -> None:
    with outside_request_contract(
        "the redirect middleware answers before the query is validated; "
        "OpenAPI cannot make parameters conditional on authentication"
    ):
        resp = oauth2_client.authorize(auth=False, **params)
        assert resp.status_code == 302, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    location = urlsplit(resp.headers["Location"])
    assert location.path == "/oauth/authorize", resp.headers["Location"]
    sent = parse_qs(location.query, keep_blank_values=True)
    assert {k: v[0] for k, v in sent.items()} == params


@pytest.mark.parametrize("with_pkce", [False, True], ids=["no_pkce", "pkce"])
def test_session_gets_the_consent_page_data(
    oauth2_session_client: OAuth2Client, confidential_app: OAuthApp, with_pkce: bool
) -> None:
    extra: dict[str, Any] = {}
    if with_pkce:
        _, challenge = pkce_pair()
        extra = {"code_challenge": challenge, "code_challenge_method": "S256", "nonce": "n-1"}
    params = authorize_params(confidential_app, scope="openid email", **extra)

    resp = oauth2_session_client.authorize(**params)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["requiresConsent"] is True
    data = body["consentData"]
    assert data["app"] == {"name": data["app"]["name"], "isDynamic": False}
    assert [s["name"] for s in data["scopes"]] == ["openid", "email"]
    assert data["notGrantedScopes"] == []
    assert data["user"]["email"]
    # The redirect middleware does not copy the user's name onto the request.
    assert "name" not in data["user"]
    assert data["redirectUri"] == confidential_app.redirect_uri
    assert data["state"] == params["state"]
    if with_pkce:
        assert body["codeChallenge"] == extra["code_challenge"]
        assert body["codeChallengeMethod"] == "S256"
    else:
        assert "codeChallenge" not in body and "codeChallengeMethod" not in body


def test_scopes_the_app_does_not_allow_are_listed_as_not_granted(
    oauth2_session_client: OAuth2Client, confidential_app: OAuthApp
) -> None:
    # The allowed part is granted and the rest is dropped, not refused; a repeated scope shows once.
    params = authorize_params(confidential_app, scope="openid kb:write openid")

    resp = oauth2_session_client.authorize(**params)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    data = resp.json()["consentData"]
    assert [s["name"] for s in data["scopes"]] == ["openid"]
    assert [s["name"] for s in data["notGrantedScopes"]] == ["kb:write"]
    assert all(s["description"] and s["category"] for s in data["notGrantedScopes"])


def test_oauth_access_token_gets_consent_data_without_a_user(
    oauth2_client: OAuth2Client, confidential_app: OAuthApp
) -> None:
    # Unlike POST /authorize, this route accepts a client-credentials token, which carries no email.
    resp = oauth2_client.authorize(**authorize_params(confidential_app))

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["consentData"]["user"] == {}


@pytest.mark.parametrize(
    ("app_fixture", "overrides", "error"),
    [
        pytest.param("public_app", {}, "invalid_request", id="public_client_without_pkce"),
        pytest.param(
            "confidential_app", {"scope": "kb:write"}, "invalid_scope", id="scope_not_allowed"
        ),
    ],
)
def test_refusal_after_redirect_check_is_a_redirect_url(
    request: pytest.FixtureRequest,
    oauth2_session_client: OAuth2Client,
    app_fixture: str,
    overrides: dict[str, str],
    error: str,
) -> None:
    app: OAuthApp = request.getfixturevalue(app_fixture)
    params = authorize_params(app, **overrides)

    resp = oauth2_session_client.authorize(**params)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    redirect_url = resp.json()["redirectUrl"]
    assert redirect_url.startswith(app.redirect_uri + "?")
    query = redirect_params(redirect_url)
    assert query["error"] == error
    assert query["state"] == params["state"]
    assert "code" not in query


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        pytest.param({"client_id": UNKNOWN_CLIENT_ID}, "invalid_client", id="unknown_client"),
        pytest.param(
            {"redirect_uri": "http://localhost/not-registered"},
            "invalid_request",
            id="unregistered_redirect_uri",
        ),
    ],
)
def test_client_or_redirect_uri_refusal_is_an_oauth_400(
    oauth2_session_client: OAuth2Client,
    confidential_app: OAuthApp,
    overrides: dict[str, str],
    error: str,
) -> None:
    resp = oauth2_session_client.authorize(**authorize_params(confidential_app, **overrides))

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["error"] == error
    assert set(body) == {"error", "error_description"}


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        pytest.param({"state": None}, "query.state", id="missing_state"),
        pytest.param({"state": ""}, "query.state", id="empty_state"),
        pytest.param({"scope": ""}, "query.scope", id="empty_scope"),
        pytest.param({"client_id": ""}, "query.client_id", id="empty_client_id"),
        pytest.param({"response_type": "token"}, "query.response_type", id="implicit_grant"),
        pytest.param(
            {"code_challenge": "too-short"}, "query.code_challenge", id="malformed_challenge"
        ),
        pytest.param(
            {"code_challenge_method": "plain"},
            "query.code_challenge_method",
            id="plain_challenge_method",
        ),
    ],
)
def test_invalid_query_fails_validation(
    oauth2_session_client: OAuth2Client,
    confidential_app: OAuthApp,
    overrides: dict[str, str | None],
    field: str,
) -> None:
    params = {
        k: v for k, v in authorize_params(confidential_app, **overrides).items() if v is not None
    }

    resp = oauth2_session_client.authorize(**params)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [e["field"] for e in error["metadata"]["errors"]] == [field]


def test_unknown_query_parameters_are_ignored(
    oauth2_session_client: OAuth2Client, confidential_app: OAuthApp
) -> None:
    params = authorize_params(confidential_app, spec_audit="ignored", prompt="consent")

    with outside_request_contract("parameters the route does not define, to show they are dropped"):
        resp = oauth2_session_client.authorize(**params)
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["requiresConsent"] is True
