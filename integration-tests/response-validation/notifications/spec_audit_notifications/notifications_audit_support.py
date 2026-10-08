"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/notifications."""

from __future__ import annotations

import datetime
from typing import Any, Callable

import requests

from helper.http.api_client import APIClient
from helper.second_user import SecondUser

NOTIFICATIONS_BASE = "/api/v1/notifications"
MISSING_NOTIFICATION_ID = "0123456789abcdef01234567"
MALFORMED_NOTIFICATION_ID = "not-an-object-id"
NOTIFICATION_STATUSES = ("unread", "read", "archived")

# Mongoose pluralises the "Notifications" model name to itself.
COLLECTION = "notifications"

SeedNotification = Callable[..., str]

NOT_FOUND_BODY = {"message": "Notification not found"}
RETENTION_DAYS = 30
# Why an existing notification of the caller is still outside every route's filter.
OUT_OF_SCOPE_KINDS = ("deleted", "older_than_retention")

MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}


def out_of_scope_fields(kind: str) -> dict[str, Any]:
    """Seed fields that put a notification outside the routes' filter for ``kind``."""
    if kind == "deleted":
        return {"isDeleted": True}
    past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        days=RETENTION_DAYS + 1
    )
    return {"createdAt": past}


class NotificationsClient(APIClient):
    """Client for /api/v1/notifications, acting as the shared org admin."""

    BASE = NOTIFICATIONS_BASE

    def list(self, *, auth: bool = True, **params: Any) -> requests.Response:
        return self.get("", auth=auth, params=params)

    def stats(self, *, auth: bool = True) -> requests.Response:
        return self.get("/stats", auth=auth)

    def read_all(self, *, auth: bool = True) -> requests.Response:
        return self.patch("/read-all", auth=auth)

    def set_state(
        self, notification_id: str, action: str, *, auth: bool = True, **kwargs: Any
    ) -> requests.Response:
        """PATCH /:id/<action> where action is read, unread, archive or unarchive."""
        return self.patch(f"/{notification_id}/{action}", auth=auth, **kwargs)

    def remove(
        self, notification_id: str, *, auth: bool = True, **kwargs: Any
    ) -> requests.Response:
        return self.delete(f"/{notification_id}", auth=auth, **kwargs)


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a notifications route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.request(
        method,
        f"{user.base_url}{NOTIFICATIONS_BASE}{path}",
        headers=user.headers,
        **kwargs,
    )
