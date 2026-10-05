"""Strict OpenAPI audit of PATCH /api/v1/notifications/:id/unarchive."""

from __future__ import annotations

import pytest
from bson import ObjectId
from notifications_audit_support import (
    MALFORMED_NOTIFICATION_ID,
    MISSING_NOTIFICATION_ID,
    NotificationsClient,
    SeedNotification,
)
from helper.second_user import SecondUser
from pymongo.collection import Collection
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/notifications/:id/unarchive"


def test_unarchive_archived_notification_returns_read_notification(
    notifications_client: NotificationsClient,
    seed_notification: SeedNotification,
) -> None:
    notification_id = seed_notification(status="archived")

    resp = notifications_client.set_state(notification_id, "unarchive")

    assert resp.status_code == 200, resp.text[:500]
    notification = resp.json()["notification"]
    assert notification["_id"] == notification_id
    assert notification["status"] == "read"
    assert_strict_openapi_response(resp, ROUTE)


def test_unarchive_notification_that_is_not_archived_returns_404(
    notifications_client: NotificationsClient,
    seed_notification: SeedNotification,
) -> None:
    notification_id = seed_notification(status="unread")

    resp = notifications_client.set_state(notification_id, "unarchive")

    assert resp.status_code == 404, resp.text[:500]
    assert resp.json() == {"message": "Notification not found"}
    assert_strict_openapi_response(resp, ROUTE)


def test_unarchive_another_users_archived_notification_returns_404(
    notifications_client: NotificationsClient,
    seed_notification: SeedNotification,
    second_user: SecondUser,
    notifications_collection: Collection,
) -> None:
    notification_id = seed_notification(
        status="archived", assigned_to=second_user.user_id
    )

    resp = notifications_client.set_state(notification_id, "unarchive")

    assert resp.status_code == 404, resp.text[:500]
    stored = notifications_collection.find_one({"_id": ObjectId(notification_id)})
    assert stored is not None and stored["status"] == "archived"
    assert_strict_openapi_response(resp, ROUTE)


def test_unarchive_malformed_id_returns_400(
    notifications_client: NotificationsClient,
) -> None:
    resp = notifications_client.set_state(MALFORMED_NOTIFICATION_ID, "unarchive")

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_unarchive_without_token_returns_401(
    notifications_client: NotificationsClient,
) -> None:
    resp = notifications_client.set_state(
        MISSING_NOTIFICATION_ID, "unarchive", auth=False
    )

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
