"""Strict OpenAPI audit of POST /api/v1/personal-access-tokens.

authenticate -> requireSessionAuth -> rate limiter -> refuseServiceAccountCaller
-> zod createPatTokenSchema (unknown body fields are stripped) -> createToken.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from personal_access_tokens_audit_support import (
    UNKNOWN_SCOPE,
    MintPat,
    PatsClient,
    pat_name,
    request_with_token,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/personal-access-tokens"

# PAT_TOKEN_PREFIX in oauth_provider/constants/constants.ts; the support module's PAT_PREFIX differs.
ACCESS_TOKEN_PREFIX = "phpat_"
DEFAULT_EXPIRY_DAYS = 30
# NEVER_EXPIRES_DAYS in pat.service.ts: "never" is stored as a 100-year expiry.
NEVER_EXPIRES_DAYS = 365 * 100
SESSION_ONLY = "interactive user session"


def _lifetime_days(token: dict[str, Any]) -> float:
    def parse(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    return (parse(token["expiresAt"]) - parse(token["createdAt"])).total_seconds() / 86400


def _revoke_if_created(pats_client: PatsClient, resp: Any) -> None:
    if resp.status_code == 201:
        pats_client.admin_revoke(resp.json()["token"]["id"])


def test_create_returns_token_with_one_time_secret(pats_client: PatsClient) -> None:
    name = pat_name()

    resp = pats_client.create({"name": name, "expiryDays": 90})

    token_id: str | None = None
    try:
        assert resp.status_code == 201, resp.text[:500]
        body = resp.json()
        token = body["token"]
        token_id = token["id"]
        assert body["message"] == "Personal access token created successfully"
        assert token["name"] == name
        assert token["scopes"], "omitted scopes should fall back to the instance MCP scope set"
        assert token["accessToken"].startswith(ACCESS_TOKEN_PREFIX)
        assert_strict_openapi_exchange(resp, ROUTE)
    finally:
        if token_id is not None:
            pats_client.admin_revoke(token_id)


@pytest.mark.parametrize(
    ("expiry", "days"),
    [
        pytest.param(30, 30, id="30"),
        pytest.param(90, 90, id="90"),
        pytest.param(365, 365, id="365"),
        pytest.param("never", NEVER_EXPIRES_DAYS, id="never"),
        pytest.param(None, DEFAULT_EXPIRY_DAYS, id="omitted-defaults-to-30"),
    ],
)
def test_expiry_choice_sets_the_lifetime(
    pats_client: PatsClient, expiry: int | str | None, days: int
) -> None:
    body: dict[str, Any] = {"name": pat_name(), "scopes": ["kb:read"]}
    if expiry is not None:
        body["expiryDays"] = expiry

    resp = pats_client.create(body)
    try:
        assert resp.status_code == 201, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        token = resp.json()["token"]
        assert token["scopes"] == ["kb:read"]
        assert abs(_lifetime_days(token) - days) < 0.01, token
    finally:
        _revoke_if_created(pats_client, resp)


@pytest.mark.parametrize(
    ("body", "field"),
    [
        # zod: a union of the literals 30, 90, 365 and "never"; nothing else, not even null.
        pytest.param({"name": "n", "expiryDays": 45}, "body.expiryDays", id="expiry-45"),
        pytest.param({"name": "n", "expiryDays": "30"}, "body.expiryDays", id="expiry-string-30"),
        pytest.param({"name": "n", "expiryDays": "forever"}, "body.expiryDays", id="expiry-forever"),
        pytest.param({"name": "n", "expiryDays": None}, "body.expiryDays", id="expiry-null"),
        pytest.param({"name": ""}, "body.name", id="empty-name"),
        pytest.param({"name": "x" * 101}, "body.name", id="name-over-100"),
        pytest.param({}, "body.name", id="no-name"),
        pytest.param({"name": "n", "scopes": "kb:read"}, "body.scopes", id="scopes-not-a-list"),
        pytest.param({"name": "n", "scopes": []}, "body.scopes", id="empty-scopes"),
        pytest.param(None, "body.name", id="no-body"),
    ],
)
def test_invalid_body_is_a_validation_error(
    pats_client: PatsClient, body: dict[str, Any] | None, field: str
) -> None:
    resp = pats_client.create(body)
    _revoke_if_created(pats_client, resp)

    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR", error
    assert [e["field"] for e in error["metadata"]["errors"]] == [field], error
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_scope_is_refused_by_the_service(pats_client: PatsClient) -> None:
    resp = pats_client.create({"name": pat_name(), "scopes": [UNKNOWN_SCOPE]})
    _revoke_if_created(pats_client, resp)

    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] != "VALIDATION_ERROR", resp.text[:500]
    assert UNKNOWN_SCOPE in resp.text
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_body_fields_are_ignored(pats_client: PatsClient) -> None:
    with outside_request_contract("an undocumented body field; zod strips it"):
        resp = pats_client.create(
            {"name": pat_name(), "scopes": ["kb:read"], "userId": "someone-else", "orgId": "x"}
        )
        assert_strict_openapi_exchange(resp, ROUTE)
    try:
        assert resp.status_code == 201, resp.text[:500]
        assert resp.json()["token"]["scopes"] == ["kb:read"]
        # Still the caller's own token: the userId sent in the body was dropped.
        listed = pats_client.list()
        assert resp.json()["token"]["id"] in [t["id"] for t in listed.json()["tokens"]]
    finally:
        _revoke_if_created(pats_client, resp)


def test_create_without_token_is_unauthorized(pats_client: PatsClient) -> None:
    resp = pats_client.create({"name": pat_name()}, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_with_oauth_token_is_forbidden(oauth_pats_client: PatsClient) -> None:
    resp = oauth_pats_client.create({"name": pat_name()})

    # A 201 here would leave a live token behind; revoke it before failing.
    if resp.status_code == 201:
        oauth_pats_client.admin_revoke(resp.json()["token"]["id"])
    assert resp.status_code == 403, resp.text[:500]
    assert SESSION_ONLY in resp.text
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_with_a_pat_or_a_service_token_is_forbidden(
    pats_client: PatsClient, mint_pat: MintPat, service_token: str
) -> None:
    base_url = pats_client._client.base_url
    for credential in (mint_pat()["accessToken"], service_token):
        resp = request_with_token(base_url, credential, "POST", json={"name": pat_name()})
        _revoke_if_created(pats_client, resp)

        assert resp.status_code == 403, resp.text[:500]
        assert SESSION_ONLY in resp.text
        assert_strict_openapi_exchange(resp, ROUTE)
