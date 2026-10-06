"""Strict OpenAPI audit of DELETE /api/v1/crawlingManager/schedule/all.

The success path removes every sync schedule in the shared org and cannot be put back, so it
runs only when every schedule the org holds belongs to connectors this test created.
"""

from __future__ import annotations

import pytest
from crawling_manager_audit_support import (
    NO_JOB_BODY,
    CrawlingManagerClient,
    SeedConnector,
    SeededConnector,
    bearer,
    error_message,
    request_as,
    schedule_body,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/crawlingManager/schedule/all"


def _scheduled_connector_ids(client: CrawlingManagerClient) -> set[str]:
    resp = client.list_all()
    assert resp.status_code == 200, resp.text[:500]
    return {str((job.get("data") or {}).get("connectorId")) for job in resp.json()["data"]}


def test_remove_all_as_admin_clears_every_schedule_of_the_org(
    crawling_manager_client: CrawlingManagerClient,
    seed_connector: SeedConnector,
) -> None:
    once, repeating, paused = seed_connector(), seed_connector(), seed_connector()
    for seeded, schedule_type in ((once, "once"), (repeating, "hourly"), (paused, "daily")):
        resp = crawling_manager_client.schedule(
            seeded.connector_type, seeded.connector_id, schedule_body(schedule_type)
        )
        assert resp.status_code == 201, resp.text[:500]
    assert crawling_manager_client.pause(paused.connector_type, paused.connector_id).status_code == 200
    mine = {once.connector_id, repeating.connector_id, paused.connector_id}

    others = _scheduled_connector_ids(crawling_manager_client) - mine
    if others:
        pytest.fail(
            "refusing the org-wide removal: the org holds schedules this test did not create "
            f"(connectors {sorted(others)})"
        )

    resp = crawling_manager_client.remove_all()

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == {"success": True, "message": "All crawling jobs removed successfully"}
    assert_strict_openapi_exchange(resp, ROUTE)
    assert not (_scheduled_connector_ids(crawling_manager_client) & mine)
    for seeded in (once, repeating, paused):
        left = crawling_manager_client.get_schedule(seeded.connector_type, seeded.connector_id)
        assert left.status_code == 404, left.text[:500]
        assert left.json() == NO_JOB_BODY


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param(None, id="no-token"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="malformed-jwt"),
        pytest.param({"Authorization": "Basic dXNlcjpwYXNz"}, id="non-bearer-scheme"),
    ],
)
def test_remove_all_without_valid_token_is_unauthorized(
    crawling_manager_client: CrawlingManagerClient,
    headers: dict[str, str] | None,
) -> None:
    kwargs = {"headers": headers} if headers else {}
    resp = crawling_manager_client.remove_all(auth=False, **kwargs)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_remove_all_as_member_is_forbidden_and_removes_nothing(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
    second_user: SecondUser,
) -> None:
    resp = request_as(second_user, "DELETE", "/schedule/all")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    survivor = crawling_manager_client.get_schedule(
        scheduled_connector.connector_type, scheduled_connector.connector_id
    )
    assert survivor.status_code == 200, (
        f"a refused member call must leave schedules in place: {survivor.text[:500]}"
    )


def test_remove_all_without_crawl_delete_scope_is_forbidden_and_removes_nothing(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
    token_without_crawl_scopes: str,
) -> None:
    # The token belongs to the admin, so only the scope check stands between it and the removal.
    resp = crawling_manager_client.remove_all(
        auth=False, headers=bearer(token_without_crawl_scopes)
    )
    assert resp.status_code == 403, resp.text[:500]
    assert "crawl:delete" in (error_message(resp) or "")
    assert_strict_openapi_exchange(resp, ROUTE)

    survivor = crawling_manager_client.get_schedule(
        scheduled_connector.connector_type, scheduled_connector.connector_id
    )
    assert survivor.status_code == 200, survivor.text[:500]
