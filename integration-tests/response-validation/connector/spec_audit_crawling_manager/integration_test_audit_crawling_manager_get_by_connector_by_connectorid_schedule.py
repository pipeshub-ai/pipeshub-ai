"""Strict OpenAPI audit of GET /api/v1/crawlingManager/:connector/:connectorId/schedule."""

from __future__ import annotations

import pytest
from crawling_manager_audit_support import (
    UNSAFE_CONNECTOR_ID,
    CrawlingManagerClient,
    SeedConnector,
    SeededConnector,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/crawlingManager/:connector/:connectorId/schedule"


def test_get_schedule_returns_the_pending_job(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
) -> None:
    resp = crawling_manager_client.get_schedule(
        scheduled_connector.connector_type, scheduled_connector.connector_id
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    job = body["data"]
    assert job["data"]["connectorId"] == scheduled_connector.connector_id
    assert job["data"]["connector"] == scheduled_connector.connector_type
    assert job["data"]["scheduleConfig"]["scheduleType"] == "once"


def test_get_schedule_without_a_job_is_not_found_with_null_data(
    crawling_manager_client: CrawlingManagerClient,
    seed_connector: SeedConnector,
) -> None:
    seeded = seed_connector()

    resp = crawling_manager_client.get_schedule(seeded.connector_type, seeded.connector_id)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    # Written by the controller itself, not the error middleware.
    assert resp.json() == {
        "success": False,
        "message": "No scheduled job found for this connector",
        "data": None,
    }


def test_member_reading_admin_team_connector_schedule_is_not_found(
    second_user: SecondUser,
    scheduled_connector: SeededConnector,
) -> None:
    # The connector service hides a team connector from a non-admin non-creator
    # as a 404, so Node's own team-scope 403 is never reached.
    resp = request_as(
        second_user,
        "GET",
        f"/{scheduled_connector.connector_type}/{scheduled_connector.connector_id}/schedule",
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_get_schedule_unsafe_connector_id_is_rejected_before_auth(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
) -> None:
    resp = crawling_manager_client.get_schedule(
        scheduled_connector.connector_type, UNSAFE_CONNECTOR_ID, auth=False
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_get_schedule_without_token_is_unauthorized(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
) -> None:
    resp = crawling_manager_client.get_schedule(
        scheduled_connector.connector_type, scheduled_connector.connector_id, auth=False
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
