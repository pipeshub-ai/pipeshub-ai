"""Every /api/v1/teams operation answers 500 to a JSON body that does not parse.

The JSON body parser runs before the router, so the failure comes before
authentication and is reported as an internal error.
"""

from __future__ import annotations

import pytest
from helper.clients.teams_client import TeamsClient
from strict_openapi import assert_strict_openapi_exchange
from teams_audit_support import (
    JSON_HEADERS,
    MALFORMED_JSON_BODY,
    MISSING_TEAM_ID,
    TEAM_ROUTE,
    TEAM_USERS_ROUTE,
    TEAMS_ROUTE,
    USER_TEAMS_ROUTE,
)

pytestmark = pytest.mark.spec_audit

_OPERATIONS = [
    ("POST", "/", TEAMS_ROUTE),
    ("GET", f"/{MISSING_TEAM_ID}", TEAM_ROUTE),
    ("PUT", f"/{MISSING_TEAM_ID}", TEAM_ROUTE),
    ("DELETE", f"/{MISSING_TEAM_ID}", TEAM_ROUTE),
    ("GET", f"/{MISSING_TEAM_ID}/users", TEAM_USERS_ROUTE),
    ("GET", "/user/teams", USER_TEAMS_ROUTE),
]


@pytest.mark.parametrize(
    ("method", "sub_path", "route"),
    [pytest.param(*op, id=f"{op[0]} {op[2]}") for op in _OPERATIONS],
)
def test_malformed_json_body_is_an_internal_error(
    teams_client: TeamsClient, method: str, sub_path: str, route: str
) -> None:
    resp = getattr(teams_client, method.lower())(
        sub_path, auth=False, data=MALFORMED_JSON_BODY, headers=JSON_HEADERS
    )
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
