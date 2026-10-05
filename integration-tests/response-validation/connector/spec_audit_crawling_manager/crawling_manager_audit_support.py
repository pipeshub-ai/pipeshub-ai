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

SCHEDULE_TYPES = ("hourly", "daily", "weekly", "monthly", "custom", "once", "interval")


@dataclass(frozen=True)
class SeededConnector:
    """A team-scoped connector instance owned by the shared admin.

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


class CrawlingManagerClient(APIClient):
    """Client for /api/v1/crawlingManager, acting as the shared org admin."""

    BASE = CRAWLING_MANAGER_BASE

    def schedule(
        self,
        connector: str,
        connector_id: str,
        body: dict[str, Any] | None = None,
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
