"""Admin-only routes refuse a signed-in member and let the admin through.

Every other suite calls these routes as the org admin, so a route that lost its
admin check would pass them all while any member could change the org's
sign-in settings, AI models or service tokens. Here each route in
``helper/admin_route_table.py`` is called twice:

* as a throwaway member, who must be refused with the product's own answer:
  403 "You need admin access to do this" from Node's ``userAdminCheck`` and
  ``adminValidator``, or 403 from the Python services (passed through by the
  gateway);
* as the admin, who must get past the check. The admin request is shaped so
  that whatever comes after the check (a validator, a lookup of an id that does
  not exist) stops it before anything is written.

``unit/test_admin_route_inventory.py`` keeps the table in step with the source,
so a new admin route cannot skip this suite.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import requests

from helper.admin_route_table import (
    ABSENT_ID,
    ADMIN_BODIES,
    INVALID_ID,
    NODE_ADMIN_ROUTES,
    NODE_REFUSAL_MESSAGE,
    PYTHON_ADMIN_ROUTES,
    AdminRoute,
)
from helper.http.session_client import SessionClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser, create_second_user, delete_second_user

logger = logging.getLogger("admin-routes")

pytestmark = [pytest.mark.integration, pytest.mark.permissions]

ALL_ROUTES = NODE_ADMIN_ROUTES + PYTHON_ADMIN_ROUTES
ADMIN_CALLABLE = [r for r in ALL_ROUTES if r.admin is not None]

# Enough of a body to read an error message; an admin read of a stream route
# never reads past the headers.
_BODY_LIMIT = 16_384
# A 1x1 PNG, for the logo route that checks its upload before the admin check.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)
# Python answers this when MCP is switched off for the org, before any admin check.
_MCP_DISABLED = "MCP servers are disabled"


def _call(
    base_url: str,
    token: str,
    route: AdminRoute,
    *,
    path_value: str,
    body: Any,
    timeout: int,
) -> tuple[int, str]:
    """Status and (for non-stream replies) the start of the body. Never logs the body."""
    url = f"{base_url}{route.url_path(path_value)}"
    headers = {"Authorization": f"Bearer {token}"}
    kwargs: dict[str, Any] = {"headers": headers, "timeout": timeout, "stream": True}
    if route.upload:
        kwargs["files"] = {"file": ("logo.png", _PNG, "image/png")}
    elif route.method != "GET" or body not in ({}, None):
        kwargs["json"] = body
    with requests.request(route.method, url, **kwargs) as resp:
        if "text/event-stream" in resp.headers.get("Content-Type", ""):
            return resp.status_code, ""
        text = resp.raw.read(_BODY_LIMIT, decode_content=True).decode("utf-8", "replace")
        return resp.status_code, text


def _error_excerpt(status: int, text: str) -> str:
    """The start of an error reply. A successful reply to these routes can hold
    credentials (SMTP, OAuth, service tokens), so it is never echoed."""
    if status < 400:
        return "(reply body withheld: it may contain credentials)"
    return f"Reply starts: {text[:200]!r}"


def _member_body(route: AdminRoute) -> Any:
    body = route.member_body
    if isinstance(body, dict) and body.get("email", "").startswith("it-admin-probe@"):
        # Unique, so a broken check that lets it through fails on its own and
        # does not leave a clash for the next run.
        return {**body, "email": f"it-admin-probe-{uuid.uuid4().hex[:8]}@test-pipeshub.com"}
    return body


def _admin_request(route: AdminRoute) -> tuple[str, Any]:
    if route.admin == "invalid":
        return INVALID_ID, []
    if route.admin == "admin_body":
        return ABSENT_ID, ADMIN_BODIES[route.key]
    return ABSENT_ID, route.member_body


@pytest.fixture(scope="module")
def member(pipeshub_client: PipeshubClient) -> Iterator[SecondUser]:
    pipeshub_client._ensure_access_token()
    user = create_second_user(pipeshub_client)
    try:
        yield user
    finally:
        delete_second_user(pipeshub_client, user, strict=True)


class TestMembersAreRefused:
    @pytest.mark.parametrize("route", ALL_ROUTES, ids=lambda r: r.id)
    def test_a_member_is_refused(self, route: AdminRoute, member: SecondUser) -> None:
        status, text = _call(
            member.base_url, member.token, route,
            path_value=ABSENT_ID, body=_member_body(route), timeout=member.timeout,
        )
        if route.handler.startswith("api/routes/mcp_servers.py") and _MCP_DISABLED in text:
            pytest.skip("MCP is switched off on this stack, so its admin check is never reached")
        assert status == route.refusal_status and route.refusal_words.lower() in text.lower(), (
            f"A member calling {route.id} got HTTP {status}, not the admin refusal "
            f"({route.refusal_status} mentioning {route.refusal_words!r}). "
            + _error_excerpt(status, text)
        )


class TestTheAdminGetsThrough:
    @pytest.mark.parametrize("route", ADMIN_CALLABLE, ids=lambda r: r.id)
    def test_the_admin_passes_the_check(
        self, route: AdminRoute, user_session_client: SessionClient
    ) -> None:
        path_value, body = _admin_request(route)
        status, text = _call(
            user_session_client.base_url, user_session_client.token, route,
            path_value=path_value, body=body, timeout=user_session_client.timeout_seconds,
        )
        if route.handler.startswith("api/routes/mcp_servers.py") and _MCP_DISABLED in text:
            pytest.skip("MCP is switched off on this stack, so its admin check is never reached")
        refused = status in (401, 403) or NODE_REFUSAL_MESSAGE.lower() in text.lower()
        assert not refused, (
            f"The admin was refused on {route.id}: HTTP {status}. " + _error_excerpt(status, text)
        )

