"""Strict OpenAPI audit of DELETE /api/v1/oauth/:connectorType/:configId."""

from __future__ import annotations

import pytest
from connector_oauth_audit_support import (
    MISSING_CONFIG_ID,
    OAUTH_BASE,
    OTHER_CONNECTOR_TYPE,
    SEED_CONNECTOR_TYPE,
    UNSAFE_PATH_SEGMENT,
    ConnectorOAuthClient,
    SeedOAuthConfig,
    bearer,
    error_code,
    request_as,
)
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/oauth/:connectorType/:configId"


def test_delete_removes_seeded_config(
    connector_oauth_client: ConnectorOAuthClient,
    seed_oauth_config: SeedOAuthConfig,
) -> None:
    cfg = seed_oauth_config()

    resp = connector_oauth_client.remove(cfg["connector_type"], cfg["id"])
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    assert cfg["id"] in body["message"]

    gone = connector_oauth_client.fetch(cfg["connector_type"], cfg["id"])
    assert gone.status_code == 404, gone.text[:500]

    again = connector_oauth_client.remove(cfg["connector_type"], cfg["id"])
    assert again.status_code == 404, again.text[:500]
    assert_strict_openapi_exchange(again, ROUTE)


def test_delete_unknown_config_is_not_found(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    resp = connector_oauth_client.remove(SEED_CONNECTOR_TYPE, MISSING_CONFIG_ID)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_under_another_connector_type_is_not_found_and_keeps_the_config(
    connector_oauth_client: ConnectorOAuthClient,
    seed_oauth_config: SeedOAuthConfig,
) -> None:
    cfg = seed_oauth_config()

    resp = connector_oauth_client.remove(OTHER_CONNECTOR_TYPE, cfg["id"])
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    kept = connector_oauth_client.fetch(cfg["connector_type"], cfg["id"])
    assert kept.status_code == 200, kept.text[:500]


@pytest.mark.parametrize(
    ("connector_type", "config_id"),
    [(SEED_CONNECTOR_TYPE, UNSAFE_PATH_SEGMENT), (UNSAFE_PATH_SEGMENT, MISSING_CONFIG_ID)],
    ids=["config-id", "connector-type"],
)
def test_delete_rejects_unsafe_path_segment(
    connector_oauth_client: ConnectorOAuthClient, connector_type: str, config_id: str
) -> None:
    resp = connector_oauth_client.remove(connector_type, config_id)
    assert resp.status_code == 400, resp.text[:500]
    assert error_code(resp) == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_requires_authentication(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    resp = connector_oauth_client.remove(SEED_CONNECTOR_TYPE, MISSING_CONFIG_ID, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_requires_connector_delete_scope(
    pipeshub_client: PipeshubClient,
    token_without_connector_read: str,
) -> None:
    resp = pipeshub_client.request(
        "DELETE",
        f"{OAUTH_BASE}/{SEED_CONNECTOR_TYPE}/{MISSING_CONFIG_ID}",
        auth=False,
        headers=bearer(token_without_connector_read),
    )
    assert resp.status_code == 403, resp.text[:500]
    assert "connector:delete" in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_cannot_delete_and_the_config_survives(
    connector_oauth_client: ConnectorOAuthClient,
    second_user: SecondUser,
    seed_oauth_config: SeedOAuthConfig,
) -> None:
    cfg = seed_oauth_config()

    # Node has no admin gate here; the 403 is Python's, relayed by handleBackendError.
    resp = request_as(second_user, "DELETE", f"/{cfg['connector_type']}/{cfg['id']}")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    kept = connector_oauth_client.fetch(cfg["connector_type"], cfg["id"])
    assert kept.status_code == 200, kept.text[:500]
