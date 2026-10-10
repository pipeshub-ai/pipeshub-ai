"""Strict OpenAPI audit of PATCH /api/v1/notifications/:id/archive."""

from __future__ import annotations

from typing import Any, Callable

import pytest
from bson import ObjectId
from notifications_audit_support import (
    NOT_FOUND_BODY,
    OUT_OF_SCOPE_KINDS,
    NotificationsClient,
    SeedNotification,
    out_of_scope_fields,
)
from helper.second_user import SecondUser
from pymongo.collection import Collection
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/notifications/:id/archive"
MISSING_NOTIFICATION_ID = "0123456789abcdef01234567"
MALFORMED_NOTIFICATION_ID = "not-an-object-id"


def test_archive_unread_notification_returns_archived_document(
    notifications_client: Any, seed_notification: Callable[..., str]
) -> None:
    notification_id = seed_notification(status="unread")

    resp = notifications_client.set_state(notification_id, "archive")

    assert resp.status_code == 200, resp.text[:500]
    notification = resp.json()["notification"]
    assert notification["_id"] == notification_id
    assert notification["status"] == "archived"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_archive_already_archived_notification_is_not_found(
    notifications_client: Any, seed_notification: Callable[..., str]
) -> None:
    # The update filter excludes archived documents, so a repeat is a 404, not a no-op 200.
    notification_id = seed_notification(status="archived")

    resp = notifications_client.set_state(notification_id, "archive")

    assert resp.status_code == 404, resp.text[:500]
    assert resp.json() == {"message": "Notification not found"}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_archive_unknown_notification_is_not_found(notifications_client: Any) -> None:
    resp = notifications_client.set_state(MISSING_NOTIFICATION_ID, "archive")

    assert resp.status_code == 404, resp.text[:500]
    assert resp.json() == {"message": "Notification not found"}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_archive_malformed_id_fails_validation(notifications_client: Any) -> None:
    resp = notifications_client.set_state(MALFORMED_NOTIFICATION_ID, "archive")

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_archive_without_token_is_unauthorized(notifications_client: Any) -> None:
    resp = notifications_client.set_state(MISSING_NOTIFICATION_ID, "archive", auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("kind", OUT_OF_SCOPE_KINDS)
def test_archive_notification_outside_the_filter_is_not_found(
    notifications_client: NotificationsClient,
    seed_notification: SeedNotification,
    kind: str,
) -> None:
    notification_id = seed_notification(status="unread", **out_of_scope_fields(kind))

    resp = notifications_client.set_state(notification_id, "archive")

    assert resp.status_code == 404, resp.text[:500]
    assert resp.json() == NOT_FOUND_BODY
    assert_strict_openapi_exchange(resp, ROUTE)


def test_archive_another_users_notification_is_not_found(
    notifications_client: NotificationsClient,
    seed_notification: SeedNotification,
    second_user: SecondUser,
    notifications_collection: Collection,
) -> None:
    notification_id = seed_notification(status="unread", assigned_to=second_user.user_id)

    resp = notifications_client.set_state(notification_id, "archive")

    assert resp.status_code == 404, resp.text[:500]
    assert resp.json() == NOT_FOUND_BODY
    stored = notifications_collection.find_one({"_id": ObjectId(notification_id)})
    assert stored is not None and stored["status"] == "unread"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_archive_reads_neither_query_nor_body(
    notifications_client: NotificationsClient, seed_notification: SeedNotification
) -> None:
    notification_id = seed_notification(status="unread")

    with outside_request_contract("the validator checks only the id path parameter"):
        resp = notifications_client.set_state(
            notification_id,
            "archive",
            params={"status": "unread"},
            json={"status": "unread", "isDeleted": True},
        )

    assert resp.status_code == 200, resp.text[:500]
    notification = resp.json()["notification"]
    assert notification["status"] == "archived"
    assert notification["isDeleted"] is False
    assert_strict_openapi_response(resp, ROUTE)
