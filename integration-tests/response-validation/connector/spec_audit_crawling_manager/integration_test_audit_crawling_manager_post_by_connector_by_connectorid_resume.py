"""Strict OpenAPI audit of POST /api/v1/crawlingManager/:connector/:connectorId/resume."""

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

ROUTE = "/api/v1/crawlingManager/:connector/:connectorId/resume"


def _pause(client: CrawlingManagerClient, seeded: SeededConnector) -> None:
    paused = client.pause(seeded.connector_type, seeded.connector_id)
    if paused.status_code != 200:
        pytest.fail(f"could not pause the seeded schedule: {paused.status_code} {paused.text[:300]}")


def test_resume_reschedules_a_paused_job(
    crawling_manager_client: CrawlingManagerClient, scheduled_connector: SeededConnector
) -> None:
    seeded = scheduled_connector
    _pause(crawling_manager_client, seeded)

    resp = crawling_manager_client.resume(seeded.connector_type, seeded.connector_id)

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["connector"] == seeded.connector_type
    assert body["data"]["resumedAt"]
    restored = crawling_manager_client.get_schedule(seeded.connector_type, seeded.connector_id)
    assert restored.status_code == 200, restored.text[:500]
    assert restored.json()["data"]["state"] == "delayed"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_resume_restores_a_repeating_schedule_unchanged(
    crawling_manager_client: CrawlingManagerClient, seed_connector: SeedConnector
) -> None:
    seeded = seed_connector()
    scheduled = crawling_manager_client.schedule(
        seeded.connector_type, seeded.connector_id, schedule_body("weekly")
    )
    assert scheduled.status_code == 201, scheduled.text[:500]
    _pause(crawling_manager_client, seeded)

    resp = crawling_manager_client.resume(seeded.connector_type, seeded.connector_id)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    restored = crawling_manager_client.get_schedule(seeded.connector_type, seeded.connector_id)
    assert restored.status_code == 200, restored.text[:500]
    job = restored.json()["data"]
    assert job["state"] == "delayed"
    assert job["data"]["scheduleConfig"] == scheduled.json()["data"]["scheduleConfig"]


def test_resume_twice_is_bad_request(
    crawling_manager_client: CrawlingManagerClient, scheduled_connector: SeededConnector
) -> None:
    seeded = scheduled_connector
    _pause(crawling_manager_client, seeded)
    first = crawling_manager_client.resume(seeded.connector_type, seeded.connector_id)
    assert first.status_code == 200, first.text[:500]

    resp = crawling_manager_client.resume(seeded.connector_type, seeded.connector_id)

    assert resp.status_code == 400, resp.text[:500]
    assert error_message(resp) == "No paused job found to resume"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_resume_without_token_is_unauthorized(
    crawling_manager_client: CrawlingManagerClient,
) -> None:
    resp = crawling_manager_client.resume(SEED_CONNECTOR_TYPE, MISSING_CONNECTOR_ID, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_resume_without_crawl_write_scope_is_forbidden(
    crawling_manager_client: CrawlingManagerClient, token_without_crawl_scopes: str
) -> None:
    resp = crawling_manager_client.resume(
        SEED_CONNECTOR_TYPE,
        MISSING_CONNECTOR_ID,
        auth=False,
        headers=bearer(token_without_crawl_scopes),
    )

    assert resp.status_code == 403, resp.text[:500]
    assert "crawl:write" in (error_message(resp) or "")
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("connector", "connector_id"),
    [(SEED_CONNECTOR_TYPE, UNSAFE_CONNECTOR_ID), (UNSAFE_CONNECTOR_ID, MISSING_CONNECTOR_ID)],
    ids=["connector-id", "connector"],
)
def test_resume_refuses_unsafe_path_segment_before_auth(
    crawling_manager_client: CrawlingManagerClient, connector: str, connector_id: str
) -> None:
    # guardPathParams is a router.param hook, so it answers ahead of authenticate.
    resp = crawling_manager_client.resume(connector, connector_id, auth=False)

    assert resp.status_code == 400, resp.text[:500]
    assert error_code(resp) == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_resume_of_unknown_connector_is_not_found(
    crawling_manager_client: CrawlingManagerClient,
) -> None:
    resp = crawling_manager_client.resume(SEED_CONNECTOR_TYPE, MISSING_CONNECTOR_ID)

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_resume_of_a_job_that_is_not_paused_is_bad_request(
    crawling_manager_client: CrawlingManagerClient, scheduled_connector: SeededConnector
) -> None:
    seeded = scheduled_connector

    resp = crawling_manager_client.resume(seeded.connector_type, seeded.connector_id)

    assert resp.status_code == 400, resp.text[:500]
    assert error_message(resp) == "No paused job found to resume"
    still_scheduled = crawling_manager_client.get_schedule(seeded.connector_type, seeded.connector_id)
    assert still_scheduled.status_code == 200, still_scheduled.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_resume_under_another_connector_segment_finds_no_paused_job(
    crawling_manager_client: CrawlingManagerClient, scheduled_connector: SeededConnector
) -> None:
    seeded = scheduled_connector
    _pause(crawling_manager_client, seeded)

    resp = crawling_manager_client.resume(WRONG_CONNECTOR_SEGMENT, seeded.connector_id)

    assert resp.status_code == 400, resp.text[:500]
    assert error_message(resp) == "No paused job found to resume"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_resume_as_member_on_admin_team_connector_is_not_found(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
    second_user: SecondUser,
) -> None:
    seeded = scheduled_connector
    _pause(crawling_manager_client, seeded)

    resp = request_as(
        second_user, "POST", f"/{seeded.connector_type}/{seeded.connector_id}/resume"
    )

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    status = crawling_manager_client.get_schedule(seeded.connector_type, seeded.connector_id)
    assert status.status_code == 200, status.text[:500]
    assert status.json()["data"]["state"] == "paused"
