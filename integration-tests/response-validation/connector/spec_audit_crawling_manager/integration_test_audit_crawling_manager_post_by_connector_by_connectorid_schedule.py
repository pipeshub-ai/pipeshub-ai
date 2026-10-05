"""Strict OpenAPI audit of POST /api/v1/crawlingManager/:connector/:connectorId/schedule."""

from __future__ import annotations

from typing import Any

import pytest
from crawling_manager_audit_support import (
    MISSING_CONNECTOR_ID,
    SEED_CONNECTOR_TYPE,
    CrawlingManagerClient,
    SeedConnector,
    once_schedule_body,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/crawlingManager/:connector/:connectorId/schedule"


def test_schedule_once_job_is_created(
    crawling_manager_client: CrawlingManagerClient, seed_connector: SeedConnector
) -> None:
    seeded = seed_connector()
    body = once_schedule_body(priority=3, maxRetries=2)

    resp = crawling_manager_client.schedule(seeded.connector_type, seeded.connector_id, body)

    assert resp.status_code == 201, resp.text[:500]
    payload = resp.json()
    assert payload["success"] is True
    data = payload["data"]
    assert data["connectorId"] == seeded.connector_id
    assert data["connector"] == seeded.connector_type
    assert data["jobId"]
    assert data["scheduleConfig"]["scheduleType"] == "once"
    assert data["scheduleConfig"]["scheduledTime"] == body["scheduleConfig"]["scheduledTime"]
    assert_strict_openapi_response(resp, ROUTE)


def test_schedule_without_token_is_unauthorized(
    crawling_manager_client: CrawlingManagerClient,
) -> None:
    resp = crawling_manager_client.schedule(
        SEED_CONNECTOR_TYPE, MISSING_CONNECTOR_ID, once_schedule_body(), auth=False
    )

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        # Refused by the zod schema: a VALIDATION_ERROR body carrying an errors list.
        pytest.param({"priority": 5}, id="missing-schedule-config"),
        # Passes zod and the connector lookup; refused by the scheduler itself.
        pytest.param(once_schedule_body(days_ahead=-1), id="scheduled-time-in-the-past"),
    ],
)
def test_schedule_rejects_unusable_body(
    crawling_manager_client: CrawlingManagerClient,
    seed_connector: SeedConnector,
    body: dict[str, Any],
) -> None:
    seeded = seed_connector()

    resp = crawling_manager_client.schedule(seeded.connector_type, seeded.connector_id, body)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_schedule_for_unknown_connector_is_not_found(
    crawling_manager_client: CrawlingManagerClient,
) -> None:
    resp = crawling_manager_client.schedule(
        SEED_CONNECTOR_TYPE, MISSING_CONNECTOR_ID, once_schedule_body()
    )

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
