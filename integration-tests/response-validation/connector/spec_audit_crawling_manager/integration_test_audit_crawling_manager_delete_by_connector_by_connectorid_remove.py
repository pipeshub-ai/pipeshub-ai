"""Strict OpenAPI audit of DELETE /api/v1/crawlingManager/:connector/:connectorId/remove."""

from __future__ import annotations

import pytest
from crawling_manager_audit_support import (
    MISSING_CONNECTOR_ID,
    ROUTE_REMOVE,
    SEED_CONNECTOR_TYPE,
    UNSAFE_CONNECTOR_ID,
    CrawlingManagerClient,
    SeededConnector,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/crawlingManager/:connector/:connectorId/remove"
assert ROUTE == ROUTE_REMOVE


def test_remove_deletes_schedule_and_is_idempotent(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
) -> None:
    connector_type = scheduled_connector.connector_type
    connector_id = scheduled_connector.connector_id

    resp = crawling_manager_client.remove(connector_type, connector_id)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {
        "success": True,
        "message": "Crawling job removed successfully",
    }

    status = crawling_manager_client.get_schedule(connector_type, connector_id)
    assert status.status_code == 404, status.text[:500]

    # The service removes whatever matches and never reports "nothing to remove".
    again = crawling_manager_client.remove(connector_type, connector_id)
    assert again.status_code == 200, again.text[:500]
    assert_strict_openapi_response(again, ROUTE)


def test_remove_without_token_is_unauthorized(
    crawling_manager_client: CrawlingManagerClient,
) -> None:
    resp = crawling_manager_client.remove(
        SEED_CONNECTOR_TYPE, MISSING_CONNECTOR_ID, auth=False
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_remove_unsafe_connector_id_is_rejected_before_auth(
    crawling_manager_client: CrawlingManagerClient,
) -> None:
    resp = crawling_manager_client.remove(
        SEED_CONNECTOR_TYPE, UNSAFE_CONNECTOR_ID, auth=False
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_remove_unknown_connector_is_not_found(
    crawling_manager_client: CrawlingManagerClient,
) -> None:
    resp = crawling_manager_client.remove(SEED_CONNECTOR_TYPE, MISSING_CONNECTOR_ID)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_cannot_remove_admins_team_connector_schedule(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
    second_user: SecondUser,
) -> None:
    connector_type = scheduled_connector.connector_type
    connector_id = scheduled_connector.connector_id

    # The connector service hides another user's team connector from a member
    # as a 404, so Node's own team-scope 403 is never reached.
    resp = request_as(second_user, "DELETE", f"/{connector_type}/{connector_id}/remove")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    still_there = crawling_manager_client.get_schedule(connector_type, connector_id)
    assert still_there.status_code == 200, still_there.text[:500]
