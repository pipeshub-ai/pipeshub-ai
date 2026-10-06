"""Strict OpenAPI audit of GET /api/v1/oauth-clients.

authenticate -> requireSessionAuth -> rate limiter -> refuseServiceAccountCaller ->
listAppsQuerySchema -> listApps (creator-scoped, newest first).
"""

from __future__ import annotations

import uuid

import pytest
from oauth_clients_audit_support import (
    LIST_ROUTE,
    OAuthClientsAuditClient,
    SeedOAuthApp,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = LIST_ROUTE


def test_list_returns_the_callers_apps_newest_first_without_secrets(
    oauth_clients_client: OAuthClientsAuditClient, seed_oauth_app: SeedOAuthApp
) -> None:
    tag = uuid.uuid4().hex[:10]
    older = seed_oauth_app(name=f"spec-audit {tag} older")
    newer = seed_oauth_app(name=f"spec-audit {tag} newer", description="described")

    resp = oauth_clients_client.list_apps(search=tag)

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert [app["id"] for app in body["data"]] == [newer["id"], older["id"]]
    assert body["pagination"] == {"page": 1, "limit": 20, "total": 2, "totalPages": 1}
    assert all("clientSecret" not in app for app in body["data"])
    assert body["data"][0]["description"] == "described"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_search_matches_the_description_too(
    oauth_clients_client: OAuthClientsAuditClient, seed_oauth_app: SeedOAuthApp
) -> None:
    tag = uuid.uuid4().hex[:10]
    app = seed_oauth_app(description=f"about {tag.upper()}")

    resp = oauth_clients_client.list_apps(search=tag)

    assert resp.status_code == 200, resp.text[:500]
    assert [row["id"] for row in resp.json()["data"]] == [app["id"]]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_page_and_limit_slice_the_list(
    oauth_clients_client: OAuthClientsAuditClient, seed_oauth_app: SeedOAuthApp
) -> None:
    tag = uuid.uuid4().hex[:10]
    older = seed_oauth_app(name=f"spec-audit {tag} a")
    seed_oauth_app(name=f"spec-audit {tag} b")

    resp = oauth_clients_client.list_apps(search=tag, page=2, limit=1)

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert [row["id"] for row in body["data"]] == [older["id"]]
    assert body["pagination"] == {"page": 2, "limit": 1, "total": 2, "totalPages": 2}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_empty_page_and_limit_fall_back_to_the_defaults(
    oauth_clients_client: OAuthClientsAuditClient, seed_oauth_app: SeedOAuthApp
) -> None:
    tag = uuid.uuid4().hex[:10]
    seed_oauth_app(name=f"spec-audit {tag}")

    resp = oauth_clients_client.list_apps(search=tag, page="", limit="")

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json()["pagination"] == {"page": 1, "limit": 20, "total": 1, "totalPages": 1}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_status_filter_and_revoked_apps_never_listed(
    oauth_clients_client: OAuthClientsAuditClient, seed_oauth_app: SeedOAuthApp
) -> None:
    tag = uuid.uuid4().hex[:10]
    active = seed_oauth_app(name=f"spec-audit {tag} active")
    suspended = seed_oauth_app(name=f"spec-audit {tag} suspended")
    deleted = seed_oauth_app(name=f"spec-audit {tag} deleted")
    assert oauth_clients_client.suspend_app(suspended["id"]).status_code == 200
    assert oauth_clients_client.delete_app(deleted["id"]).status_code == 200

    by_status = {}
    for status in ("active", "suspended", "revoked"):
        resp = oauth_clients_client.list_apps(search=tag, status=status)
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        by_status[status] = [row["id"] for row in resp.json()["data"]]

    assert by_status["active"] == [active["id"]]
    assert by_status["suspended"] == [suspended["id"]]
    # Deleting an app marks it revoked and deleted, and deleted apps are filtered out.
    assert by_status["revoked"] == []


@pytest.mark.parametrize(
    ("params", "field"),
    [
        pytest.param({"page": "abc"}, "query.page", id="page-not-a-number"),
        pytest.param({"page": "1.5"}, "query.page", id="page-fraction"),
        pytest.param({"page": "0"}, "query.page", id="page-zero"),
        pytest.param({"limit": "0"}, "query.limit", id="limit-zero"),
        pytest.param({"limit": "101"}, "query.limit", id="limit-over-100"),
        pytest.param({"status": "deleted"}, "query.status", id="status-unknown"),
    ],
)
def test_invalid_query_is_a_validation_error(
    oauth_clients_client: OAuthClientsAuditClient, params: dict[str, str], field: str
) -> None:
    resp = oauth_clients_client.list_apps(**params)

    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [e["field"] for e in error["metadata"]["errors"]] == [field]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_query_parameter_is_ignored(
    oauth_clients_client: OAuthClientsAuditClient, seed_oauth_app: SeedOAuthApp
) -> None:
    tag = uuid.uuid4().hex[:10]
    app = seed_oauth_app(name=f"spec-audit {tag}")

    with outside_request_contract("an undocumented query parameter, which the validator strips"):
        resp = oauth_clients_client.list_apps(search=tag, sort="name")
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 200, resp.text[:500]
    assert [row["id"] for row in resp.json()["data"]] == [app["id"]]


def test_list_without_token_is_unauthorized(oauth_clients_client: OAuthClientsAuditClient) -> None:
    resp = oauth_clients_client.list_apps(auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_with_oauth_access_token_is_forbidden(
    oauth_token_clients_client: OAuthClientsAuditClient,
) -> None:
    resp = oauth_token_clients_client.list_apps()

    assert resp.status_code == 403, resp.text[:500]
    assert "interactive user session" in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)
