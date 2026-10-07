"""An OAuth access token without the route's ``user:*`` scope is refused with 403.

requireScopes runs right after authentication and before validation, so the
ids and bodies here never reach a handler. Session tokens are never
scope-restricted; GET /users/health, GET /users/me/role and
POST /users/updateAppConfig have no scope check.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests
from helper.pipeshub_client import PipeshubClient
from strict_openapi import assert_strict_openapi_exchange
from users_audit_support import MISSING_USER_ID, USERS_BASE

pytestmark = pytest.mark.spec_audit

_ID = MISSING_USER_ID

# (method, sub-path, route, JSON body, scope named in the refusal)
_OPERATIONS: list[tuple[str, str, str, Any, str]] = [
    ("GET", "", "", None, "user:read"),
    ("POST", "", "", {"fullName": "Spec Audit", "email": "spec-audit@example.com"}, "user:invite"),
    ("GET", "/fetch/with-groups", "/fetch/with-groups", None, "user:read"),
    ("GET", f"/{_ID}/email", "/:id/email", None, "user:read"),
    ("PATCH", f"/{_ID}/email", "/:id/email", {"email": "spec-audit@example.com"}, "user:write"),
    ("PUT", f"/{_ID}/unblock", "/:id/unblock", None, "user:write"),
    ("POST", "/by-ids", "/by-ids", {"userIds": [_ID]}, "user:read"),
    ("PATCH", f"/{_ID}/fullname", "/:id/fullname", {"fullName": "Spec Audit"}, "user:write"),
    ("PATCH", f"/{_ID}/firstName", "/:id/firstName", {"firstName": "Spec"}, "user:write"),
    ("PATCH", f"/{_ID}/lastName", "/:id/lastName", {"lastName": "Audit"}, "user:write"),
    ("DELETE", "/dp", "/dp", None, "user:write"),
    ("GET", "/dp", "/dp", None, "user:read"),
    ("PATCH", f"/{_ID}/designation", "/:id/designation", {"designation": "Auditor"}, "user:write"),
    ("GET", f"/{_ID}", "/:id", None, "user:read"),
    ("PUT", f"/{_ID}", "/:id", {"designation": "Auditor"}, "user:write"),
    ("DELETE", f"/{_ID}", "/:id", None, "user:delete"),
    ("GET", f"/{_ID}/adminCheck", "/:id/adminCheck", None, "user:read"),
    ("POST", "/bulk/invite", "/bulk/invite", {"emails": ["spec-audit@example.com"]}, "user:invite"),
    ("POST", f"/{_ID}/resend-invite", "/:id/resend-invite", None, "user:invite"),
    ("GET", "/graph/list", "/graph/list", None, "user:read"),
]


@pytest.mark.parametrize(
    ("method", "sub_path", "route", "body", "scope"),
    [pytest.param(*op, id=f"{op[0]} {op[2] or '/'}") for op in _OPERATIONS],
)
def test_a_token_without_the_scope_is_forbidden(
    pipeshub_client: PipeshubClient,
    narrow_scope_token: str,
    method: str,
    sub_path: str,
    route: str,
    body: Any,
    scope: str,
) -> None:
    resp = requests.request(
        method,
        f"{pipeshub_client.base_url}{USERS_BASE}{sub_path}",
        headers={"Authorization": f"Bearer {narrow_scope_token}"},
        json=body,
        timeout=pipeshub_client.timeout_seconds,
    )
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_FORBIDDEN", resp.text[:500]
    assert f"Insufficient scope. Required: {scope}" in resp.text, resp.text[:500]
    assert_strict_openapi_exchange(resp, f"{USERS_BASE}{route}")


@pytest.mark.parametrize("multipart", ["PUT /dp", "POST /bulk/invite/upload"])
def test_an_upload_without_the_scope_is_forbidden(
    pipeshub_client: PipeshubClient, narrow_scope_token: str, multipart: str
) -> None:
    method, path = multipart.split(" ")
    files = {"file": ("f.csv", b"spec-audit@example.com\n", "text/csv")}
    resp = requests.request(
        method,
        f"{pipeshub_client.base_url}{USERS_BASE}{path}",
        headers={"Authorization": f"Bearer {narrow_scope_token}"},
        files=files,
        timeout=pipeshub_client.timeout_seconds,
    )
    assert resp.status_code == 403, resp.text[:500]
    assert "Insufficient scope" in resp.text, resp.text[:500]
    assert_strict_openapi_exchange(resp, f"{USERS_BASE}{path}")
