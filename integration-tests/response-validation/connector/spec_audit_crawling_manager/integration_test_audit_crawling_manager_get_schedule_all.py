"""Strict OpenAPI audit of GET /api/v1/crawlingManager/schedule/all."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from crawling_manager_audit_support import (
    CrawlingManagerClient,
    SeedConnector,
    SeededConnector,
    bearer,
    error_message,
    request_as,
    schedule_body,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

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
    assert_strict_openapi_exchange(resp, ROUTE)

    job = _entry_for(resp, scheduled_connector)
    assert job["data"]["connector"] == scheduled_connector.connector_type
    assert job["data"]["scheduleConfig"]["scheduleType"] == "once"
    assert job["state"] == "delayed"
    assert job["delay"] > 0


@pytest.mark.parametrize("schedule_type", ["hourly", "interval"])
def test_admin_lists_repeating_job(
    crawling_manager_client: CrawlingManagerClient,
    seed_connector: SeedConnector,
    schedule_type: str,
) -> None:
    seeded = seed_connector()
    scheduled = crawling_manager_client.schedule(
        seeded.connector_type, seeded.connector_id, schedule_body(schedule_type)
    )
    assert scheduled.status_code == 201, scheduled.text[:500]

    resp = crawling_manager_client.list_all()
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    job = _entry_for(resp, seeded)
    assert job["id"] == scheduled.json()["data"]["jobId"]
    assert job["data"]["scheduleConfig"] == scheduled.json()["data"]["scheduleConfig"]
    assert job["state"] == "delayed"


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
    assert_strict_openapi_exchange(resp, ROUTE)

    job = _entry_for(resp, scheduled_connector)
    assert job["state"] == "paused"
    assert job["progress"] == 0
    assert job["attemptsMade"] == 0


def test_query_parameters_are_ignored(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
) -> None:
    # No validator and the handler reads no query: nothing filters or pages the listing.
    with outside_request_contract("the route declares no query parameters and ignores any sent"):
        resp = crawling_manager_client.list_all(
            params={"connector": "SpecAuditNoSuchType", "page": "0", "limit": "0"}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    _entry_for(resp, scheduled_connector)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", "/schedule/all")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_crawl_read_scope_is_forbidden(
    crawling_manager_client: CrawlingManagerClient, token_without_crawl_scopes: str
) -> None:
    resp = crawling_manager_client.list_all(auth=False, headers=bearer(token_without_crawl_scopes))
    assert resp.status_code == 403, resp.text[:500]
    assert "crawl:read" in (error_message(resp) or "")
    assert_strict_openapi_exchange(resp, ROUTE)


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
    assert_strict_openapi_exchange(resp, ROUTE)
