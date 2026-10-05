"""Strict OpenAPI audit of GET /api/v1/crawlingManager/schedule/all."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from crawling_manager_audit_support import (
    CrawlingManagerClient,
    SeededConnector,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/crawlingManager/schedule/all"


def _entry_for(resp: requests.Response, seeded: SeededConnector) -> dict[str, Any]:
    """The listing is org-wide, so pick out the seeded connector's own job."""
    body = resp.json()
    assert body["success"] is True
    assert body["message"] == "All job statuses retrieved successfully"
    assert isinstance(body["data"], list)
    mine = [
        job
        for job in body["data"]
        if (job.get("data") or {}).get("connectorId") == seeded.connector_id
    ]
    assert len(mine) == 1, f"expected one job for the seeded connector, got {mine}"
    return mine[0]


def test_admin_lists_scheduled_job(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
) -> None:
    resp = crawling_manager_client.list_all()
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    job = _entry_for(resp, scheduled_connector)
    assert job["data"]["connector"] == scheduled_connector.connector_type
    assert job["data"]["scheduleConfig"]["scheduleType"] == "once"
    assert job["state"] == "delayed"
    assert job["delay"] > 0


def test_admin_lists_paused_job(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
) -> None:
    # A paused job is rebuilt from Node memory, not read from BullMQ, so its shape differs.
    paused = crawling_manager_client.pause(
        scheduled_connector.connector_type, scheduled_connector.connector_id
    )
    assert paused.status_code == 200, paused.text[:500]

    resp = crawling_manager_client.list_all()
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    job = _entry_for(resp, scheduled_connector)
    assert job["state"] == "paused"
    assert job["progress"] == 0
    assert job["attemptsMade"] == 0


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", "/schedule/all")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [None, {"Authorization": "Bearer not-a-jwt"}],
    ids=["no-token", "malformed-token"],
)
def test_without_valid_token_is_unauthorized(
    crawling_manager_client: CrawlingManagerClient,
    headers: dict[str, str] | None,
) -> None:
    kwargs: dict[str, Any] = {"headers": headers} if headers else {}
    resp = crawling_manager_client.list_all(auth=False, **kwargs)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
