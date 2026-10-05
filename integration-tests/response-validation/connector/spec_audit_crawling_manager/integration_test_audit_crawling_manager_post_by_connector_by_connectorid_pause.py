"""Strict OpenAPI audit of POST /api/v1/crawlingManager/:connector/:connectorId/pause."""

from __future__ import annotations

import pytest
from crawling_manager_audit_support import (
    MISSING_CONNECTOR_ID,
    SEED_CONNECTOR_TYPE,
    CrawlingManagerClient,
    SeedConnector,
    SeededConnector,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/crawlingManager/:connector/:connectorId/pause"


def test_pause_scheduled_job_returns_paused_envelope(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
) -> None:
    # seed_connector's teardown removes the job, which also drops the in-memory paused entry.
    resp = crawling_manager_client.pause(
        scheduled_connector.connector_type, scheduled_connector.connector_id
    )

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["connector"] == scheduled_connector.connector_type
    assert body["data"]["pausedAt"]
    assert_strict_openapi_response(resp, ROUTE)

    status = crawling_manager_client.get_schedule(
        scheduled_connector.connector_type, scheduled_connector.connector_id
    )
    assert status.status_code == 200, status.text[:500]
    assert status.json()["data"]["state"] == "paused"


def test_pause_without_token_is_unauthorized(
    crawling_manager_client: CrawlingManagerClient,
) -> None:
    resp = crawling_manager_client.pause(
        SEED_CONNECTOR_TYPE, MISSING_CONNECTOR_ID, auth=False
    )

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_pause_unknown_connector_is_not_found(
    crawling_manager_client: CrawlingManagerClient,
) -> None:
    resp = crawling_manager_client.pause(SEED_CONNECTOR_TYPE, MISSING_CONNECTOR_ID)

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_pause_connector_without_job_is_bad_request(
    crawling_manager_client: CrawlingManagerClient,
    seed_connector: SeedConnector,
) -> None:
    seeded = seed_connector()

    resp = crawling_manager_client.pause(seeded.connector_type, seeded.connector_id)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_pause_as_member_on_admin_team_connector_is_not_found(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
    second_user: SecondUser,
) -> None:
    # The connector service hides another user's team connector from a member,
    # so the lookup 404s before Node's own 403 branch can run.
    resp = request_as(
        second_user,
        "POST",
        f"/{scheduled_connector.connector_type}/{scheduled_connector.connector_id}/pause",
    )

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    status = crawling_manager_client.get_schedule(
        scheduled_connector.connector_type, scheduled_connector.connector_id
    )
    assert status.status_code == 200, status.text[:500]
    assert status.json()["data"]["state"] != "paused"
