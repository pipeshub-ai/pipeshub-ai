"""Every /api/v1/users operation answers 500 to a JSON body that does not parse.

The JSON body parser runs before the router, so the failure comes before
authentication and is reported as an internal error. No token is sent, so no
handler is reached, DELETE /users/{id} included.
"""

from __future__ import annotations

import pytest
from helper.clients.users_client import UsersClient
from strict_openapi import assert_strict_openapi_exchange
from users_audit_support import JSON_HEADERS, MALFORMED_JSON_BODY, MISSING_USER_ID

pytestmark = pytest.mark.spec_audit

_ID = MISSING_USER_ID

_OPERATIONS = [
    ("GET", "/", "/api/v1/users"),
    ("POST", "/", "/api/v1/users"),
    ("GET", "/fetch/with-groups", "/api/v1/users/fetch/with-groups"),
    ("GET", "/me/role", "/api/v1/users/me/role"),
    ("GET", f"/{_ID}/email", "/api/v1/users/:id/email"),
    ("PATCH", f"/{_ID}/email", "/api/v1/users/:id/email"),
    ("PUT", f"/{_ID}/unblock", "/api/v1/users/:id/unblock"),
    ("POST", "/by-ids", "/api/v1/users/by-ids"),
    ("GET", "/email/exists", "/api/v1/users/email/exists"),
    ("PATCH", f"/{_ID}/fullname", "/api/v1/users/:id/fullname"),
    ("PATCH", f"/{_ID}/firstName", "/api/v1/users/:id/firstName"),
    ("PATCH", f"/{_ID}/lastName", "/api/v1/users/:id/lastName"),
    ("PUT", "/dp", "/api/v1/users/dp"),
    ("DELETE", "/dp", "/api/v1/users/dp"),
    ("GET", "/dp", "/api/v1/users/dp"),
    ("PATCH", f"/{_ID}/designation", "/api/v1/users/:id/designation"),
    ("GET", "/health", "/api/v1/users/health"),
    ("GET", f"/{_ID}", "/api/v1/users/:id"),
    ("PUT", f"/{_ID}", "/api/v1/users/:id"),
    ("DELETE", f"/{_ID}", "/api/v1/users/:id"),
    ("GET", f"/{_ID}/adminCheck", "/api/v1/users/:id/adminCheck"),
    ("POST", "/bulk/invite", "/api/v1/users/bulk/invite"),
    ("POST", "/bulk/invite/upload", "/api/v1/users/bulk/invite/upload"),
    ("POST", f"/{_ID}/resend-invite", "/api/v1/users/:id/resend-invite"),
    ("POST", "/updateAppConfig", "/api/v1/users/updateAppConfig"),
    ("GET", "/graph/list", "/api/v1/users/graph/list"),
]


@pytest.mark.parametrize(
    ("method", "sub_path", "route"),
    [pytest.param(*op, id=f"{op[0]} {op[2]}") for op in _OPERATIONS],
)
def test_malformed_json_body_is_an_internal_error(
    users_client: UsersClient, method: str, sub_path: str, route: str
) -> None:
    resp = getattr(users_client, method.lower())(
        sub_path, auth=False, data=MALFORMED_JSON_BODY, headers=JSON_HEADERS
    )
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
