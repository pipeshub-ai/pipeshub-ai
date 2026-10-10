"""Strict OpenAPI audit of GET /api/v1/notifications (authenticate -> zod query -> listNotifications)."""

from __future__ import annotations

import base64
import json

import pytest
from notifications_audit_support import (
    MISSING_NOTIFICATION_ID,
    NOTIFICATION_STATUSES,
    NotificationsClient,
    SeedNotification,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/notifications"
MAX_PAGE_SIZE = 50


def _cursor(payload: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def test_list_defaults_exclude_archived_and_clamp_limit(
    notifications_client: NotificationsClient,
    seed_notification: SeedNotification,
    admin_user_id: str,
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
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("status", NOTIFICATION_STATUSES)
def test_list_status_filter_returns_only_that_status(
    notifications_client: NotificationsClient,
    seed_notification: SeedNotification,
    status: str,
) -> None:
    seeded = {each: seed_notification(status=each) for each in NOTIFICATION_STATUSES}

    resp = notifications_client.list(status=status, limit=MAX_PAGE_SIZE)

    assert resp.status_code == 200, resp.text[:500]
    rows = resp.json()["notifications"]
    assert {row["status"] for row in rows} == {status}
    # Newest first, so the one seeded a moment ago is on the first page.
    assert seeded[status] in [row["_id"] for row in rows]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_status_filter_paginates_with_cursor(
    notifications_client: NotificationsClient, seed_notification: SeedNotification
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
    assert_strict_openapi_exchange(first, ROUTE)

    second = notifications_client.list(
        status="archived", limit=MAX_PAGE_SIZE, cursor=page["cursor"]
    )

    assert second.status_code == 200, second.text[:500]
    first_id = page["notifications"][0]["_id"]
    next_ids = [row["_id"] for row in second.json()["notifications"]]
    assert first_id not in next_ids
    if first_id == newer_id:
        assert older_id in next_ids
    assert_strict_openapi_exchange(second, ROUTE)


def test_list_returns_only_the_callers_notifications(
    second_user: SecondUser, seed_notification: SeedNotification
) -> None:
    own_id = seed_notification(assigned_to=second_user.user_id)
    admins_id = seed_notification()

    resp = request_as(second_user, "GET", params={"limit": MAX_PAGE_SIZE})

    assert resp.status_code == 200, resp.text[:500]
    rows = resp.json()["notifications"]
    assert {row["assignedTo"] for row in rows} == {second_user.user_id}
    ids = [row["_id"] for row in rows]
    assert own_id in ids and admins_id not in ids
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_returns_a_notification_as_the_consumer_stores_it(
    notifications_client: NotificationsClient, seed_notification: SeedNotification
) -> None:
    # The shape the bulk-invite flow really writes: a payload, no originService.
    payload = {"invalid": ["not-an-email"], "mailFailed": []}
    notification_id = seed_notification(
        type="user.bulkInvite",
        severity="success",
        originService=None,
        redirectLink="/workspace/users",
        payload=payload,
    )

    resp = notifications_client.list(limit=MAX_PAGE_SIZE)

    assert resp.status_code == 200, resp.text[:500]
    (row,) = [r for r in resp.json()["notifications"] if r["_id"] == notification_id]
    assert row["payload"] == payload
    assert row["redirectLink"] == "/workspace/users"
    assert "originService" not in row
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_cursor_past_the_last_notification_is_an_empty_page(
    notifications_client: NotificationsClient,
) -> None:
    cursor = _cursor({"c": "2000-01-01T00:00:00.000Z", "i": MISSING_NOTIFICATION_ID})

    resp = notifications_client.list(cursor=cursor)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == {"notifications": [], "cursor": None, "hasMore": False}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_empty_cursor_is_the_first_page(
    notifications_client: NotificationsClient, seed_notification: SeedNotification
) -> None:
    notification_id = seed_notification()

    resp = notifications_client.list(cursor="", limit=MAX_PAGE_SIZE)

    assert resp.status_code == 200, resp.text[:500]
    assert notification_id in [row["_id"] for row in resp.json()["notifications"]]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_without_token_is_unauthorized(
    notifications_client: NotificationsClient,
) -> None:
    resp = notifications_client.list(auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("param", "value"),
    [
        ("status", "bogus"),
        ("status", ""),
        ("limit", "0"),
        ("limit", "-1"),
        ("limit", "101"),
        ("limit", "1.5"),
        ("limit", "abc"),
        ("limit", "true"),
    ],
)
def test_list_query_outside_the_validator_is_rejected(
    notifications_client: NotificationsClient, param: str, value: str
) -> None:
    resp = notifications_client.list(**{param: value})

    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR", error
    assert [e["field"] for e in error["metadata"]["errors"]] == [f"query.{param}"]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("param", ["status", "limit", "cursor"])
def test_list_repeated_parameter_is_rejected(
    notifications_client: NotificationsClient, param: str
) -> None:
    value = {"status": "read", "limit": "5", "cursor": ""}[param]
    with outside_request_contract("a repeated parameter reaches the validator as a list"):
        resp = notifications_client.list(**{param: [value, value]})

    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR", error
    assert [e["field"] for e in error["metadata"]["errors"]] == [f"query.{param}"]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    ("limit", "page_size"),
    [("", None), ("2e0", 2), ("0x2", 2), (" 2 ", 2)],
    ids=["empty", "exponent", "hex", "padded"],
)
def test_list_limit_is_read_as_a_javascript_number(
    notifications_client: NotificationsClient,
    seed_notification: SeedNotification,
    limit: str,
    page_size: int | None,
) -> None:
    for _ in range(3):
        seed_notification(status="archived")

    with outside_request_contract(
        "limit goes through JavaScript Number(), which reads more than an OpenAPI integer; "
        "an empty value is the default page size"
    ):
        resp = notifications_client.list(status="archived", limit=limit)

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    if page_size is None:
        assert len(body["notifications"]) >= 3
    else:
        assert len(body["notifications"]) == page_size
        assert body["hasMore"] is True
    assert_strict_openapi_response(resp, ROUTE)


def test_list_ignores_unknown_query_parameters(
    notifications_client: NotificationsClient, seed_notification: SeedNotification
) -> None:
    notification_id = seed_notification()

    with outside_request_contract("the validator drops query keys it does not know"):
        resp = notifications_client.list(limit=MAX_PAGE_SIZE, assignedTo="someone-else")

    assert resp.status_code == 200, resp.text[:500]
    assert notification_id in [row["_id"] for row in resp.json()["notifications"]]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "cursor",
    [
        "not-a-cursor",
        _cursor({"c": "not-a-date", "i": MISSING_NOTIFICATION_ID}),
        _cursor({"c": "2000-01-01T00:00:00.000Z", "i": "not-an-object-id"}),
        _cursor({"i": MISSING_NOTIFICATION_ID}),
        _cursor([]),
    ],
    ids=["not_base64_json", "bad_date", "bad_id", "no_date", "not_an_object"],
)
def test_list_undecodable_cursor_is_rejected(
    notifications_client: NotificationsClient, cursor: str
) -> None:
    resp = notifications_client.list(cursor=cursor)

    assert resp.status_code == 400, resp.text[:500]
    assert resp.json() == {"message": "Invalid cursor"}
    assert_strict_openapi_exchange(resp, ROUTE)
