"""Constants and helpers for the strict OpenAPI audit of /api/v1/projects."""

from __future__ import annotations

from typing import Any, Callable

import requests

from helper.second_user import SecondUser

PROJECTS_BASE = "/api/v1/projects"
MISSING_PROJECT_ID = "0123456789abcdef01234567"
MALFORMED_PROJECT_ID = "not-an-object-id"

PROJECT_TEMPLATE = f"{PROJECTS_BASE}/:projectId"
MEMBERS_TEMPLATE = f"{PROJECTS_BASE}/:projectId/members"

# seed_project(**create_fields) -> the created project object (its id is "_id").
SeedProject = Callable[..., dict[str, Any]]


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a projects route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.request(
        method,
        f"{user.base_url}{PROJECTS_BASE}{path}",
        headers=user.headers,
        **kwargs,
    )
