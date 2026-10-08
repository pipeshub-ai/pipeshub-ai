"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/crawlingManager."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Callable

import requests

from helper.http.api_client import APIClient
from helper.second_user import SecondUser

CRAWLING_MANAGER_BASE = "/api/v1/crawlingManager"

ROUTE_SCHEDULE = f"{CRAWLING_MANAGER_BASE}/:connector/:connectorId/schedule"
ROUTE_REMOVE = f"{CRAWLING_MANAGER_BASE}/:connector/:connectorId/remove"
ROUTE_PAUSE = f"{CRAWLING_MANAGER_BASE}/:connector/:connectorId/pause"
ROUTE_RESUME = f"{CRAWLING_MANAGER_BASE}/:connector/:connectorId/resume"
ROUTE_SCHEDULE_ALL = f"{CRAWLING_MANAGER_BASE}/schedule/all"
ROUTE_STATS = f"{CRAWLING_MANAGER_BASE}/stats"

# Needs no credentials and supports team scope; creating it reaches no external system.
SEED_CONNECTOR_TYPE = "Web"
MISSING_CONNECTOR_ID = "00000000-0000-4000-8000-000000000000"
# guardPathParams refuses a segment holding "%" (sent as %25) before auth runs.
UNSAFE_CONNECTOR_ID = "bad%25id"
# A `:connector` segment that is not the seeded instance's type.
WRONG_CONNECTOR_SEGMENT = "SpecAuditWrongType"

SCHEDULE_TYPES = ("hourly", "daily", "weekly", "monthly", "custom", "once", "interval")
REPEATING_SCHEDULE_TYPES = ("hourly", "daily", "weekly", "monthly", "custom", "interval")
# The validator fills these in, and the reply and the stored job echo them.
SCHEDULE_CONFIG_DEFAULTS = {"isEnabled": True, "timezone": "UTC"}

MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}

NO_JOB_BODY = {
    "success": False,
    "message": "No scheduled job found for this connector",
    "data": None,
}


@dataclass(frozen=True)
class SeededConnector:
    """A connector instance for schedule tests.

    ``connector_type`` is the instance's own ``type``: the schedule route stores
    jobs under it, and the other routes match the ``:connector`` path segment
    against it exactly, so it is the segment to use.
    """

    connector_id: str
    connector_type: str


SeedConnector = Callable[[], SeededConnector]


def once_schedule_body(days_ahead: int = 365, **overrides: Any) -> dict[str, Any]:
    """A valid POST /schedule body whose single run is too far off to fire during a test."""
    when = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days_ahead)
    # zod's .datetime() takes a "Z" suffix only, not a "+00:00" offset.
    scheduled_time = when.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {
        "scheduleConfig": {"scheduleType": "once", "scheduledTime": scheduled_time},
        **overrides,
    }


def repeating_schedule_config(schedule_type: str) -> dict[str, Any]:
    """A valid repeating scheduleConfig (UTC) whose next run is at least half an hour away.

    A run that fired would start a real sync of the seeded connector, so every
    value is placed well clear of the current time.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    far_minute = (now.minute + 30) % 60
    far_hour = (now.hour + 12) % 24
    # Python's weekday() is Monday=0; cron's day-of-week is Sunday=0.
    far_weekday = ((now.weekday() + 1) % 7 + 3) % 7
    far_day_of_month = (now.day + 13) % 28 + 1
    far_month = (now.month + 5) % 12 + 1
    configs: dict[str, dict[str, Any]] = {
        "hourly": {"scheduleType": "hourly", "minute": far_minute, "interval": 2},
        "daily": {"scheduleType": "daily", "hour": far_hour, "minute": 0},
        "weekly": {"scheduleType": "weekly", "daysOfWeek": [far_weekday], "hour": far_hour, "minute": 0},
        "monthly": {
            "scheduleType": "monthly",
            "dayOfMonth": far_day_of_month,
            "hour": far_hour,
            "minute": 0,
        },
        "custom": {
            "scheduleType": "custom",
            "cronExpression": f"0 0 1 {far_month} *",
            "description": "spec audit: once a year",
        },
        # The interval lives one level down, unlike every other type's fields.
        "interval": {"scheduleType": "interval", "scheduleConfig": {"intervalMinutes": 525600}},
    }
    return configs[schedule_type]


def schedule_body(schedule_type: str, **overrides: Any) -> dict[str, Any]:
    if schedule_type == "once":
        return once_schedule_body(**overrides)
    return {"scheduleConfig": repeating_schedule_config(schedule_type), **overrides}


class CrawlingManagerClient(APIClient):
    """Client for /api/v1/crawlingManager, acting as the shared org admin."""

    BASE = CRAWLING_MANAGER_BASE

    def send(self, method: str, path: str = "", **kwargs: Any) -> requests.Response:
        """Any verb on a router-relative path, for cases that differ only by method."""
        return self._client.request(method, self._path(path), **kwargs)

    def schedule(
        self,
        connector: str,
        connector_id: str,
        body: Any = None,
        *,
        auth: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        return self.post(
            f"/{connector}/{connector_id}/schedule", auth=auth, json=body, **kwargs
        )

    def get_schedule(
        self, connector: str, connector_id: str, *, auth: bool = True, **kwargs: Any
    ) -> requests.Response:
        return self.get(f"/{connector}/{connector_id}/schedule", auth=auth, **kwargs)

    def remove(
        self, connector: str, connector_id: str, *, auth: bool = True, **kwargs: Any
    ) -> requests.Response:
        return self.delete(f"/{connector}/{connector_id}/remove", auth=auth, **kwargs)

    def pause(
        self, connector: str, connector_id: str, *, auth: bool = True, **kwargs: Any
    ) -> requests.Response:
        return self.post(f"/{connector}/{connector_id}/pause", auth=auth, **kwargs)

    def resume(
        self, connector: str, connector_id: str, *, auth: bool = True, **kwargs: Any
    ) -> requests.Response:
        return self.post(f"/{connector}/{connector_id}/resume", auth=auth, **kwargs)

    def list_all(self, *, auth: bool = True, **kwargs: Any) -> requests.Response:
        return self.get("/schedule/all", auth=auth, **kwargs)

    def remove_all(self, *, auth: bool = True, **kwargs: Any) -> requests.Response:
        """Org-wide and not restorable: only for calls expected to be refused."""
        return self.delete("/schedule/all", auth=auth, **kwargs)

    def stats(self, *, auth: bool = True, **kwargs: Any) -> requests.Response:
        return self.get("/stats", auth=auth, **kwargs)


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a crawlingManager route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.request(
        method,
        f"{user.base_url}{CRAWLING_MANAGER_BASE}{path}",
        headers=user.headers,
        **kwargs,
    )


def bearer(token: str) -> dict[str, str]:
    """Headers for a raw token; pass with auth=False, e.g. the scope-less OAuth token."""
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def error_code(resp: requests.Response) -> str | None:
    try:
        return resp.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return None


def error_message(resp: requests.Response) -> str | None:
    try:
        return resp.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return None


def validation_error_fields(resp: requests.Response) -> list[str]:
    """Field paths a 400 from the Node request validator names; empty for any other reply."""
    try:
        error = resp.json()["error"]
        if error.get("code") != "VALIDATION_ERROR":
            return []
        return [str(entry.get("field")) for entry in error["metadata"]["errors"]]
    except (ValueError, KeyError, TypeError, AttributeError):
        return []
