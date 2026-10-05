"""Strict OpenAPI audit of POST /api/v1/crawlingManager/:connector/:connectorId/resume."""

from __future__ import annotations

import pytest
from crawling_manager_audit_support import (
    MISSING_CONNECTOR_ID,
    SEED_CONNECTOR_TYPE,
    UNSAFE_CONNECTOR_ID,
    CrawlingManagerClient,
    SeededConnector,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/crawlingManager/:connector/:connectorId/resume"


def test_resume_reschedules_a_paused_job(
    crawling_manager_client: CrawlingManagerClient, scheduled_connector: SeededConnector
) -> None:
    seeded = scheduled_connector
    paused = crawling_manager_client.pause(seeded.connector_type, seeded.connector_id)
    if paused.status_code != 200:
        pytest.fail(f"could not pause the seeded schedule: {paused.status_code} {paused.text[:300]}")

    resp = crawling_manager_client.resume(seeded.connector_type, seeded.connector_id)

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["connector"] == seeded.connector_type
    assert body["data"]["resumedAt"]
    restored = crawling_manager_client.get_schedule(seeded.connector_type, seeded.connector_id)
    assert restored.status_code == 200, restored.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_resume_without_token_is_unauthorized(
    crawling_manager_client: CrawlingManagerClient,
) -> None:
    resp = crawling_manager_client.resume(SEED_CONNECTOR_TYPE, MISSING_CONNECTOR_ID, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_resume_refuses_unsafe_connector_id_before_auth(
    crawling_manager_client: CrawlingManagerClient,
) -> None:
    # guardPathParams is a router.param hook, so it answers ahead of authenticate.
    resp = crawling_manager_client.resume(SEED_CONNECTOR_TYPE, UNSAFE_CONNECTOR_ID, auth=False)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_resume_of_unknown_connector_is_not_found(
    crawling_manager_client: CrawlingManagerClient,
) -> None:
    resp = crawling_manager_client.resume(SEED_CONNECTOR_TYPE, MISSING_CONNECTOR_ID)

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_resume_of_a_job_that_is_not_paused_is_bad_request(
    crawling_manager_client: CrawlingManagerClient, scheduled_connector: SeededConnector
) -> None:
    seeded = scheduled_connector

    resp = crawling_manager_client.resume(seeded.connector_type, seeded.connector_id)

    assert resp.status_code == 400, resp.text[:500]
    still_scheduled = crawling_manager_client.get_schedule(seeded.connector_type, seeded.connector_id)
    assert still_scheduled.status_code == 200, still_scheduled.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
