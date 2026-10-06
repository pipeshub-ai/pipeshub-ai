"""Strict OpenAPI audit of GET /api/v1/crawlingManager/:connector/:connectorId/schedule."""

from __future__ import annotations

import pytest
from crawling_manager_audit_support import (
    MISSING_CONNECTOR_ID,
    NO_JOB_BODY,
    SEED_CONNECTOR_TYPE,
    UNSAFE_CONNECTOR_ID,
    WRONG_CONNECTOR_SEGMENT,
    CrawlingManagerClient,
    SeedConnector,
    SeededConnector,
    bearer,
    error_code,
    error_message,
    once_schedule_body,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

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
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    job = body["data"]
    assert job["data"]["connectorId"] == scheduled_connector.connector_id
    assert job["data"]["connector"] == scheduled_connector.connector_type
    assert job["data"]["scheduleConfig"]["scheduleType"] == "once"
    assert job["state"] == "delayed"
    assert job["delay"] > 0


def test_get_schedule_of_a_paused_job_is_rebuilt_without_queue_fields(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
) -> None:
    seeded = scheduled_connector
    paused = crawling_manager_client.pause(seeded.connector_type, seeded.connector_id)
    assert paused.status_code == 200, paused.text[:500]

    resp = crawling_manager_client.get_schedule(seeded.connector_type, seeded.connector_id)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    job = resp.json()["data"]
    assert job["state"] == "paused"
    assert job["progress"] == 0
    assert job["attemptsMade"] == 0
    # A paused job is held in Node memory, not in the queue, so these are simply absent.
    assert not {"delay", "finishedOn", "processedOn", "failedReason"} & set(job)
    assert job["data"]["scheduleConfig"]["scheduleType"] == "once"


def test_get_schedule_without_a_job_is_not_found_with_null_data(
    crawling_manager_client: CrawlingManagerClient,
    seed_connector: SeedConnector,
) -> None:
    seeded = seed_connector()

    resp = crawling_manager_client.get_schedule(seeded.connector_type, seeded.connector_id)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    # Written by the controller itself, not the error middleware.
    assert resp.json() == NO_JOB_BODY


@pytest.mark.parametrize(
    "segment",
    [WRONG_CONNECTOR_SEGMENT, SEED_CONNECTOR_TYPE.lower()],
    ids=["another-type", "wrong-case"],
)
def test_job_is_only_found_under_the_instance_type_exactly(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
    segment: str,
) -> None:
    resp = crawling_manager_client.get_schedule(segment, scheduled_connector.connector_id)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == NO_JOB_BODY


def test_unknown_connector_is_not_found_in_the_error_envelope(
    crawling_manager_client: CrawlingManagerClient,
) -> None:
    resp = crawling_manager_client.get_schedule(SEED_CONNECTOR_TYPE, MISSING_CONNECTOR_ID)
    assert resp.status_code == 404, resp.text[:500]
    assert error_code(resp) == "HTTP_NOT_FOUND"
    assert_strict_openapi_exchange(resp, ROUTE)


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
    assert error_code(resp) == "HTTP_NOT_FOUND"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_reads_own_personal_connector_schedule_and_the_admin_cannot(
    crawling_manager_client: CrawlingManagerClient,
    second_user: SecondUser,
    member_personal_connector: SeededConnector,
) -> None:
    seeded = member_personal_connector
    path = f"/{seeded.connector_type}/{seeded.connector_id}/schedule"
    scheduled = request_as(second_user, "POST", path, json=once_schedule_body())
    assert scheduled.status_code == 201, scheduled.text[:500]

    resp = request_as(second_user, "GET", path)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["data"]["data"]["userId"] == second_user.user_id

    # A personal connector is invisible to everyone else, the admin included: 404, not 403.
    as_admin = crawling_manager_client.get_schedule(seeded.connector_type, seeded.connector_id)
    assert as_admin.status_code == 404, as_admin.text[:500]
    assert error_code(as_admin) == "HTTP_NOT_FOUND"
    assert_strict_openapi_exchange(as_admin, ROUTE)


@pytest.mark.parametrize(
    ("connector", "connector_id"),
    [(SEED_CONNECTOR_TYPE, UNSAFE_CONNECTOR_ID), (UNSAFE_CONNECTOR_ID, MISSING_CONNECTOR_ID)],
    ids=["connector-id", "connector"],
)
def test_get_schedule_unsafe_path_segment_is_rejected_before_auth(
    crawling_manager_client: CrawlingManagerClient, connector: str, connector_id: str
) -> None:
    resp = crawling_manager_client.get_schedule(connector, connector_id, auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert error_code(resp) == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_get_schedule_without_token_is_unauthorized(
    crawling_manager_client: CrawlingManagerClient,
) -> None:
    resp = crawling_manager_client.get_schedule(
        SEED_CONNECTOR_TYPE, MISSING_CONNECTOR_ID, auth=False
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_get_schedule_without_crawl_read_scope_is_forbidden(
    crawling_manager_client: CrawlingManagerClient, token_without_crawl_scopes: str
) -> None:
    resp = crawling_manager_client.get_schedule(
        SEED_CONNECTOR_TYPE,
        MISSING_CONNECTOR_ID,
        auth=False,
        headers=bearer(token_without_crawl_scopes),
    )
    assert resp.status_code == 403, resp.text[:500]
    assert "crawl:read" in (error_message(resp) or "")
    assert_strict_openapi_exchange(resp, ROUTE)
