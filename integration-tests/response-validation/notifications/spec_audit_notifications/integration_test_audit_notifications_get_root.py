"""Strict OpenAPI audit of GET /api/v1/notifications (authenticate -> zod query -> listNotifications)."""

from __future__ import annotations

from typing import Callable

import pytest
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/notifications"
MAX_PAGE_SIZE = 50


def test_list_defaults_exclude_archived_and_clamp_limit(
    notifications_client, seed_notification: Callable[..., str], admin_user_id: str
) -> None:
    unread_id = seed_notification()
    archived_id = seed_notification(status="archived")

    # zod accepts up to 100; the controller silently clamps to 50.
    resp = notifications_client.list(limit=100)

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert set(body) == {"notifications", "cursor", "hasMore"}, body.keys()
    rows = body["notifications"]
    assert len(rows) <= MAX_PAGE_SIZE
    ids = [row["_id"] for row in rows]
    assert unread_id in ids
    assert archived_id not in ids
    assert all(row["assignedTo"] == admin_user_id for row in rows)
    assert all(row["status"] != "archived" for row in rows)
    assert_strict_openapi_response(resp, ROUTE)


def test_list_status_filter_paginates_with_cursor(
    notifications_client, seed_notification: Callable[..., str]
) -> None:
    older_id = seed_notification(status="archived")
    newer_id = seed_notification(status="archived")

    first = notifications_client.list(status="archived", limit=1)

    assert first.status_code == 200, first.text[:500]
    page = first.json()
    assert len(page["notifications"]) == 1
    assert page["notifications"][0]["status"] == "archived"
    assert page["hasMore"] is True
    assert isinstance(page["cursor"], str) and page["cursor"]
    assert_strict_openapi_response(first, ROUTE)

    second = notifications_client.list(
        status="archived", limit=MAX_PAGE_SIZE, cursor=page["cursor"]
    )

    assert second.status_code == 200, second.text[:500]
    first_id = page["notifications"][0]["_id"]
    next_ids = [row["_id"] for row in second.json()["notifications"]]
    assert first_id not in next_ids
    if first_id == newer_id:
        assert older_id in next_ids
    assert_strict_openapi_response(second, ROUTE)


def test_list_without_token_is_unauthorized(notifications_client) -> None:
    resp = notifications_client.list(auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_list_limit_above_validator_max_is_rejected(notifications_client) -> None:
    resp = notifications_client.list(limit=101)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_list_undecodable_cursor_is_rejected(notifications_client) -> None:
    resp = notifications_client.list(cursor="not-a-cursor")

    assert resp.status_code == 400, resp.text[:500]
    assert resp.json() == {"message": "Invalid cursor"}
    assert_strict_openapi_response(resp, ROUTE)
