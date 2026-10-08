"""Strict OpenAPI audit of GET /api/v1/crawlingManager/stats."""

from __future__ import annotations

import pytest
from crawling_manager_audit_support import (
    ROUTE_STATS,
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

STAT_FIELDS = (
    "waiting",
    "active",
    "completed",
    "failed",
    "delayed",
    "paused",
    "repeatable",
    "total",
)


def _assert_stats_envelope(body: dict) -> dict[str, int]:
    assert body.get("success") is True, body
    assert body.get("message") == "Queue statistics retrieved successfully", body
    stats = body.get("data")
    assert isinstance(stats, dict) and set(stats) == set(STAT_FIELDS), body
    for field in STAT_FIELDS:
        assert isinstance(stats[field], int) and stats[field] >= 0, body
    return stats


def test_stats_as_admin_counts_the_orgs_delayed_schedule(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
) -> None:
    resp = crawling_manager_client.stats()
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE_STATS)
    stats = _assert_stats_envelope(resp.json())
    # Org-wide counts: the seeded one-time job is delayed, others may exist too.
    assert stats["delayed"] >= 1, stats
    assert stats["total"] >= stats["delayed"], stats


def test_stats_counts_a_repeating_schedule(
    crawling_manager_client: CrawlingManagerClient, seed_connector: SeedConnector
) -> None:
    seeded = seed_connector()
    scheduled = crawling_manager_client.schedule(
        seeded.connector_type, seeded.connector_id, schedule_body("monthly")
    )
    assert scheduled.status_code == 201, scheduled.text[:500]

    resp = crawling_manager_client.stats()
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE_STATS)
    stats = _assert_stats_envelope(resp.json())
    assert stats["repeatable"] >= 1, stats
    # The schedule's next run is the delayed job; the schedule itself is not part of the total.
    assert stats["delayed"] >= 1, stats


def test_stats_counts_a_paused_job(
    crawling_manager_client: CrawlingManagerClient, scheduled_connector: SeededConnector
) -> None:
    paused = crawling_manager_client.pause(
        scheduled_connector.connector_type, scheduled_connector.connector_id
    )
    assert paused.status_code == 200, paused.text[:500]

    resp = crawling_manager_client.stats()
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE_STATS)
    stats = _assert_stats_envelope(resp.json())
    assert stats["paused"] >= 1, stats
    assert stats["total"] >= stats["paused"], stats


def test_stats_ignores_query_parameters(
    crawling_manager_client: CrawlingManagerClient,
) -> None:
    # No validator on the route, so a stray query param is not a 400.
    with outside_request_contract("the route declares no query parameters and ignores any sent"):
        resp = crawling_manager_client.stats(params={"connector": "bogus"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE_STATS)
    _assert_stats_envelope(resp.json())


def test_stats_is_open_to_a_non_admin_member(
    second_user: SecondUser, scheduled_connector: SeededConnector
) -> None:
    # No userAdminCheck here, and requireScopes is a no-op for a session JWT: a member
    # gets the whole organization's counts, the admin's team connector included.
    resp = request_as(second_user, "GET", "/stats")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE_STATS)
    stats = _assert_stats_envelope(resp.json())
    assert stats["delayed"] >= 1, stats


def test_stats_without_crawl_read_scope_is_forbidden(
    crawling_manager_client: CrawlingManagerClient, token_without_crawl_scopes: str
) -> None:
    resp = crawling_manager_client.stats(auth=False, headers=bearer(token_without_crawl_scopes))
    assert resp.status_code == 403, resp.text[:500]
    assert "crawl:read" in (error_message(resp) or "")
    assert_strict_openapi_exchange(resp, ROUTE_STATS)


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-token"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="invalid-token"),
    ],
)
def test_stats_rejects_unauthenticated_calls(
    crawling_manager_client: CrawlingManagerClient, headers: dict[str, str]
) -> None:
    resp = crawling_manager_client.stats(auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE_STATS)
