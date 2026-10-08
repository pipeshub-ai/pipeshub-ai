"""Strict OpenAPI audit of POST /api/v1/crawlingManager/:connector/:connectorId/pause."""

from __future__ import annotations

import pytest
from crawling_manager_audit_support import (
    MISSING_CONNECTOR_ID,
    SEED_CONNECTOR_TYPE,
    UNSAFE_CONNECTOR_ID,
    WRONG_CONNECTOR_SEGMENT,
    CrawlingManagerClient,
    SeedConnector,
    SeededConnector,
    bearer,
    error_code,
    error_message,
    request_as,
    schedule_body,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

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
    assert_strict_openapi_exchange(resp, ROUTE)

    status = crawling_manager_client.get_schedule(
        scheduled_connector.connector_type, scheduled_connector.connector_id
    )
    assert status.status_code == 200, status.text[:500]
    assert status.json()["data"]["state"] == "paused"


def test_pause_a_repeating_schedule(
    crawling_manager_client: CrawlingManagerClient, seed_connector: SeedConnector
) -> None:
    seeded = seed_connector()
    scheduled = crawling_manager_client.schedule(
        seeded.connector_type, seeded.connector_id, schedule_body("interval")
    )
    assert scheduled.status_code == 201, scheduled.text[:500]

    resp = crawling_manager_client.pause(seeded.connector_type, seeded.connector_id)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    status = crawling_manager_client.get_schedule(seeded.connector_type, seeded.connector_id)
    assert status.status_code == 200, status.text[:500]
    job = status.json()["data"]
    assert job["state"] == "paused"
    assert job["data"]["scheduleConfig"] == scheduled.json()["data"]["scheduleConfig"]


def test_pause_of_a_paused_job_is_bad_request(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
) -> None:
    seeded = scheduled_connector
    first = crawling_manager_client.pause(seeded.connector_type, seeded.connector_id)
    assert first.status_code == 200, first.text[:500]

    resp = crawling_manager_client.pause(seeded.connector_type, seeded.connector_id)

    assert resp.status_code == 400, resp.text[:500]
    assert error_message(resp) == "Job is already paused"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_pause_without_token_is_unauthorized(
    crawling_manager_client: CrawlingManagerClient,
) -> None:
    resp = crawling_manager_client.pause(
        SEED_CONNECTOR_TYPE, MISSING_CONNECTOR_ID, auth=False
    )

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_pause_without_crawl_write_scope_is_forbidden(
    crawling_manager_client: CrawlingManagerClient, token_without_crawl_scopes: str
) -> None:
    resp = crawling_manager_client.pause(
        SEED_CONNECTOR_TYPE,
        MISSING_CONNECTOR_ID,
        auth=False,
        headers=bearer(token_without_crawl_scopes),
    )

    assert resp.status_code == 403, resp.text[:500]
    assert "crawl:write" in (error_message(resp) or "")
    assert_strict_openapi_exchange(resp, ROUTE)


def test_pause_unknown_connector_is_not_found(
    crawling_manager_client: CrawlingManagerClient,
) -> None:
    resp = crawling_manager_client.pause(SEED_CONNECTOR_TYPE, MISSING_CONNECTOR_ID)

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_pause_connector_without_job_is_bad_request(
    crawling_manager_client: CrawlingManagerClient,
    seed_connector: SeedConnector,
) -> None:
    seeded = seed_connector()

    resp = crawling_manager_client.pause(seeded.connector_type, seeded.connector_id)

    assert resp.status_code == 400, resp.text[:500]
    assert error_message(resp) == "No active job found to pause"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_pause_under_another_connector_segment_finds_no_job(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
) -> None:
    resp = crawling_manager_client.pause(WRONG_CONNECTOR_SEGMENT, scheduled_connector.connector_id)

    assert resp.status_code == 400, resp.text[:500]
    assert error_message(resp) == "No active job found to pause"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("connector", "connector_id"),
    [(SEED_CONNECTOR_TYPE, UNSAFE_CONNECTOR_ID), (UNSAFE_CONNECTOR_ID, MISSING_CONNECTOR_ID)],
    ids=["connector-id", "connector"],
)
def test_pause_unsafe_path_segment_is_rejected_before_auth(
    crawling_manager_client: CrawlingManagerClient, connector: str, connector_id: str
) -> None:
    resp = crawling_manager_client.pause(connector, connector_id, auth=False)

    assert resp.status_code == 400, resp.text[:500]
    assert error_code(resp) == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)


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
    assert_strict_openapi_exchange(resp, ROUTE)

    status = crawling_manager_client.get_schedule(
        scheduled_connector.connector_type, scheduled_connector.connector_id
    )
    assert status.status_code == 200, status.text[:500]
    assert status.json()["data"]["state"] != "paused"
