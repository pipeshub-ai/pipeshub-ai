"""Strict OpenAPI audit of PATCH /api/v1/notifications/:id/archive."""

from __future__ import annotations

from typing import Any, Callable

import pytest
from strict_openapi import assert_strict_openapi_response

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
    assert_strict_openapi_response(resp, ROUTE)


def test_archive_already_archived_notification_is_not_found(
    notifications_client: Any, seed_notification: Callable[..., str]
) -> None:
    # The update filter excludes archived documents, so a repeat is a 404, not a no-op 200.
    notification_id = seed_notification(status="archived")

    resp = notifications_client.set_state(notification_id, "archive")

    assert resp.status_code == 404, resp.text[:500]
    assert resp.json() == {"message": "Notification not found"}
    assert_strict_openapi_response(resp, ROUTE)


def test_archive_unknown_notification_is_not_found(notifications_client: Any) -> None:
    resp = notifications_client.set_state(MISSING_NOTIFICATION_ID, "archive")

    assert resp.status_code == 404, resp.text[:500]
    assert resp.json() == {"message": "Notification not found"}
    assert_strict_openapi_response(resp, ROUTE)


def test_archive_malformed_id_fails_validation(notifications_client: Any) -> None:
    resp = notifications_client.set_state(MALFORMED_NOTIFICATION_ID, "archive")

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_archive_without_token_is_unauthorized(notifications_client: Any) -> None:
    resp = notifications_client.set_state(MISSING_NOTIFICATION_ID, "archive", auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
