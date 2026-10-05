"""Strict OpenAPI audit of GET /api/v1/crawlingManager/stats."""

from __future__ import annotations

import pytest
from crawling_manager_audit_support import (
    ROUTE_STATS,
    CrawlingManagerClient,
    SeededConnector,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

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
    # No validator on the route, so a stray query param is not a 400.
    resp = crawling_manager_client.stats(params={"connector": "bogus"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE_STATS)
    stats = _assert_stats_envelope(resp.json())
    # Org-wide counts: the seeded one-time job is delayed, others may exist too.
    assert stats["delayed"] >= 1, stats
    assert stats["total"] >= stats["delayed"], stats


def test_stats_is_open_to_a_non_admin_member(second_user: SecondUser) -> None:
    # No userAdminCheck here, and requireScopes is a no-op for a session JWT.
    resp = request_as(second_user, "GET", "/stats")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE_STATS)
    _assert_stats_envelope(resp.json())


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
    assert_strict_openapi_response(resp, ROUTE_STATS)
