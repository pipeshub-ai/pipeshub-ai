"""Strict OpenAPI audit of the connector read every per-connector crawlingManager route makes first.

Before touching a schedule, Node reads the connector from the connector service with the
caller's own token, so that service's scope and visibility rules apply on top of the crawl:* scope.
"""

from __future__ import annotations

from typing import Any

import pytest
from crawling_manager_audit_support import (
    CRAWLING_MANAGER_BASE,
    ROUTE_PAUSE,
    ROUTE_REMOVE,
    ROUTE_RESUME,
    ROUTE_SCHEDULE,
    CrawlingManagerClient,
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

PER_CONNECTOR_CALLS = [
    pytest.param("POST", "schedule", ROUTE_SCHEDULE, True, id="post-schedule"),
    pytest.param("GET", "schedule", ROUTE_SCHEDULE, False, id="get-schedule"),
    pytest.param("DELETE", "remove", ROUTE_REMOVE, False, id="remove"),
    pytest.param("POST", "pause", ROUTE_PAUSE, False, id="pause"),
    pytest.param("POST", "resume", ROUTE_RESUME, False, id="resume"),
]


def _kwargs(with_body: bool) -> dict[str, Any]:
    return {"json": once_schedule_body()} if with_body else {}


@pytest.mark.parametrize(("method", "action", "route", "with_body"), PER_CONNECTOR_CALLS)
def test_crawl_scopes_alone_are_forbidden_without_connector_read(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
    token_with_only_crawl_scopes: str,
    method: str,
    action: str,
    route: str,
    with_body: bool,
) -> None:
    resp = crawling_manager_client.send(
        method,
        f"/{scheduled_connector.connector_type}/{scheduled_connector.connector_id}/{action}",
        auth=False,
        headers=bearer(token_with_only_crawl_scopes),
        **_kwargs(with_body),
    )

    assert resp.status_code == 403, resp.text[:500]
    assert error_code(resp) == "HTTP_FORBIDDEN"
    assert error_message(resp) == "Insufficient scope. Required: connector:read or kb:write"
    assert_strict_openapi_exchange(resp, route)

    still = crawling_manager_client.get_schedule(
        scheduled_connector.connector_type, scheduled_connector.connector_id
    )
    assert still.status_code == 200, still.text[:500]


def test_crawl_scopes_alone_still_read_stats(
    crawling_manager_client: CrawlingManagerClient, token_with_only_crawl_scopes: str
) -> None:
    resp = crawling_manager_client.stats(auth=False, headers=bearer(token_with_only_crawl_scopes))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, f"{CRAWLING_MANAGER_BASE}/stats")


@pytest.mark.parametrize(("method", "action", "route", "with_body"), PER_CONNECTOR_CALLS)
def test_member_who_created_a_team_connector_is_forbidden(
    demoted_team_connector_creator: tuple[SecondUser, SeededConnector],
    method: str,
    action: str,
    route: str,
    with_body: bool,
) -> None:
    member, connector = demoted_team_connector_creator
    # The connector service lets the creator read it, so the 403 is Node's admin rule, not a 404.
    readable = member.get(f"/api/v1/connectors/{connector.connector_id}")
    assert readable.status_code == 200, readable.text[:500]

    resp = request_as(
        member,
        method,
        f"/{connector.connector_type}/{connector.connector_id}/{action}",
        **_kwargs(with_body),
    )

    assert resp.status_code == 403, resp.text[:500]
    assert error_code(resp) == "HTTP_FORBIDDEN"
    assert error_message(resp) == "You are not authorized to schedule this connector"
    assert_strict_openapi_exchange(resp, route)
