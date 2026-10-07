"""Every /api/v1/userGroups operation answers 500 to a JSON body that does not parse.

The JSON body parser runs before the router, so the failure comes before
authentication and is reported as an internal error.
"""

from __future__ import annotations

import pytest
from helper.clients.user_groups_client import UserGroupsClient
from strict_openapi import assert_strict_openapi_exchange
from user_groups_audit_support import (
    ADD_USERS_ROUTE,
    GROUP_ROUTE,
    GROUP_USERS_ROUTE,
    GROUPS_ROUTE,
    HEALTH_ROUTE,
    JSON_HEADERS,
    MALFORMED_JSON_BODY,
    MISSING_ID,
    REMOVE_USERS_ROUTE,
    STATS_ROUTE,
    USER_GROUPS_ROUTE,
)

pytestmark = pytest.mark.spec_audit

_OPERATIONS = [
    ("POST", "/", GROUPS_ROUTE),
    ("GET", "/", GROUPS_ROUTE),
    ("GET", "/health", HEALTH_ROUTE),
    ("GET", f"/{MISSING_ID}", GROUP_ROUTE),
    ("PUT", f"/{MISSING_ID}", GROUP_ROUTE),
    ("DELETE", f"/{MISSING_ID}", GROUP_ROUTE),
    ("POST", "/add-users", ADD_USERS_ROUTE),
    ("POST", "/remove-users", REMOVE_USERS_ROUTE),
    ("GET", f"/{MISSING_ID}/users", GROUP_USERS_ROUTE),
    ("GET", f"/users/{MISSING_ID}", USER_GROUPS_ROUTE),
    ("GET", "/stats/list", STATS_ROUTE),
]


@pytest.mark.parametrize(
    ("method", "sub_path", "route"),
    [pytest.param(*op, id=f"{op[0]} {op[2]}") for op in _OPERATIONS],
)
def test_malformed_json_body_is_an_internal_error(
    user_groups_client: UserGroupsClient, method: str, sub_path: str, route: str
) -> None:
    resp = getattr(user_groups_client, method.lower())(
        sub_path, auth=False, data=MALFORMED_JSON_BODY, headers=JSON_HEADERS
    )
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
