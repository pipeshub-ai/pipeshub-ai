"""Strict OpenAPI audit of DELETE /api/v1/crawlingManager/:connector/:connectorId/remove."""

from __future__ import annotations

import pytest
from crawling_manager_audit_support import (
    MISSING_CONNECTOR_ID,
    NO_JOB_BODY,
    ROUTE_REMOVE,
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

ROUTE = "/api/v1/crawlingManager/:connector/:connectorId/remove"
assert ROUTE == ROUTE_REMOVE

REMOVED_BODY = {"success": True, "message": "Crawling job removed successfully"}


def test_remove_deletes_schedule_and_is_idempotent(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
) -> None:
    connector_type = scheduled_connector.connector_type
    connector_id = scheduled_connector.connector_id

    resp = crawling_manager_client.remove(connector_type, connector_id)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == REMOVED_BODY

    status = crawling_manager_client.get_schedule(connector_type, connector_id)
    assert status.status_code == 404, status.text[:500]

    # The service removes whatever matches and never reports "nothing to remove".
    again = crawling_manager_client.remove(connector_type, connector_id)
    assert again.status_code == 200, again.text[:500]
    assert_strict_openapi_exchange(again, ROUTE)
    assert again.json() == REMOVED_BODY


@pytest.mark.parametrize("schedule_type", ["daily", "interval"])
def test_remove_deletes_a_repeating_schedule(
    crawling_manager_client: CrawlingManagerClient,
    seed_connector: SeedConnector,
    schedule_type: str,
) -> None:
    seeded = seed_connector()
    scheduled = crawling_manager_client.schedule(
        seeded.connector_type, seeded.connector_id, schedule_body(schedule_type)
    )
    assert scheduled.status_code == 201, scheduled.text[:500]

    resp = crawling_manager_client.remove(seeded.connector_type, seeded.connector_id)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    status = crawling_manager_client.get_schedule(seeded.connector_type, seeded.connector_id)
    assert status.status_code == 404, status.text[:500]
    assert status.json() == NO_JOB_BODY


def test_remove_also_forgets_a_paused_job(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
) -> None:
    seeded = scheduled_connector
    paused = crawling_manager_client.pause(seeded.connector_type, seeded.connector_id)
    assert paused.status_code == 200, paused.text[:500]

    resp = crawling_manager_client.remove(seeded.connector_type, seeded.connector_id)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    resumed = crawling_manager_client.resume(seeded.connector_type, seeded.connector_id)
    assert resumed.status_code == 400, resumed.text[:500]


def test_remove_under_another_connector_segment_succeeds_and_removes_nothing(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
) -> None:
    resp = crawling_manager_client.remove(WRONG_CONNECTOR_SEGMENT, scheduled_connector.connector_id)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == REMOVED_BODY

    still_there = crawling_manager_client.get_schedule(
        scheduled_connector.connector_type, scheduled_connector.connector_id
    )
    assert still_there.status_code == 200, still_there.text[:500]


def test_remove_without_token_is_unauthorized(
    crawling_manager_client: CrawlingManagerClient,
) -> None:
    resp = crawling_manager_client.remove(
        SEED_CONNECTOR_TYPE, MISSING_CONNECTOR_ID, auth=False
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_remove_without_crawl_delete_scope_is_forbidden(
    crawling_manager_client: CrawlingManagerClient, token_without_crawl_scopes: str
) -> None:
    resp = crawling_manager_client.remove(
        SEED_CONNECTOR_TYPE,
        MISSING_CONNECTOR_ID,
        auth=False,
        headers=bearer(token_without_crawl_scopes),
    )
    assert resp.status_code == 403, resp.text[:500]
    assert "crawl:delete" in (error_message(resp) or "")
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("connector", "connector_id"),
    [(SEED_CONNECTOR_TYPE, UNSAFE_CONNECTOR_ID), (UNSAFE_CONNECTOR_ID, MISSING_CONNECTOR_ID)],
    ids=["connector-id", "connector"],
)
def test_remove_unsafe_path_segment_is_rejected_before_auth(
    crawling_manager_client: CrawlingManagerClient, connector: str, connector_id: str
) -> None:
    resp = crawling_manager_client.remove(connector, connector_id, auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert error_code(resp) == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_remove_unknown_connector_is_not_found(
    crawling_manager_client: CrawlingManagerClient,
) -> None:
    resp = crawling_manager_client.remove(SEED_CONNECTOR_TYPE, MISSING_CONNECTOR_ID)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_cannot_remove_admins_team_connector_schedule(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
    second_user: SecondUser,
) -> None:
    connector_type = scheduled_connector.connector_type
    connector_id = scheduled_connector.connector_id

    # The connector service hides another user's team connector from a member
    # as a 404, so Node's own team-scope 403 is never reached.
    resp = request_as(second_user, "DELETE", f"/{connector_type}/{connector_id}/remove")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    still_there = crawling_manager_client.get_schedule(connector_type, connector_id)
    assert still_there.status_code == 200, still_there.text[:500]
