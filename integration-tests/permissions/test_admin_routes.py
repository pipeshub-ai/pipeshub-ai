"""Admin-only routes refuse a signed-in member and let the admin through.

Every other suite calls these routes as the org admin, so a route that lost its
admin check would pass them all while any member could change the org's
sign-in settings, AI models or service tokens. Here each route in
``helper/admin_route_table.py`` is called twice:

* as a throwaway member, who must be refused with the product's own answer:
  403 "You need admin access to do this" from Node's ``userAdminCheck``, or
  403 from the Python services (passed through by the gateway);
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
    OLD_NODE_REFUSAL_MESSAGE,
    ORG_TOOL_RULES_OFF,
    PYTHON_ADMIN_ROUTES,
    SWITCHED_OFF,
    TOOL_RULES_HANDLERS,
    AdminRoute,
)
from helper.feature_flags import feature_flag
from helper.http.session_client import SessionClient
from helper.mcp_oauth import authorization_code_token, create_oauth_app, delete_oauth_app
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
# What Python answers when a Labs feature is switched off for the org, before any admin check.
_FEATURE_DISABLED = {
    "api/routes/mcp_servers.py": "MCP servers are disabled",
    "api/routes/api_toolsets.py": "API Toolsets are disabled",
}


def _skip_if_feature_is_off(route: AdminRoute, status: int, text: str) -> None:
    for handler_file, disabled in _FEATURE_DISABLED.items():
        if route.handler.startswith(handler_file) and disabled in text:
            pytest.skip(f"{disabled} on this stack, so the admin check is never reached")
    if route.handler in TOOL_RULES_HANDLERS and status == 404 and ORG_TOOL_RULES_OFF in text:
        pytest.skip("Organization tool rules are off in this edition, so the admin check is never reached")


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


def _skip_if_switched_off(route: AdminRoute, status: int, text: str) -> None:
    for rule in SWITCHED_OFF:
        if rule.applies(route, status, text):
            pytest.skip(rule.why)


def _admin_request(route: AdminRoute) -> tuple[str, Any]:
    if route.admin == "invalid":
        return INVALID_ID, []
    if route.admin == "admin_body":
        return ABSENT_ID, ADMIN_BODIES[route.key]
    return ABSENT_ID, route.member_body


@pytest.fixture(scope="module", autouse=True)
def api_actions_on(pipeshub_client: PipeshubClient) -> Iterator[None]:
    """API actions are off by default, and while off their routes answer before the admin check."""
    with feature_flag(pipeshub_client, "ENABLE_API_TOOLSETS", True):
        yield


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
        _skip_if_feature_is_off(route, status, text)
        _skip_if_switched_off(route, status, text)
        assert status == route.refusal_status and route.refusal_words.lower() in text.lower(), (
            f"A member calling {route.id} got HTTP {status}, not the admin refusal "
            f"({route.refusal_status} mentioning {route.refusal_words!r}). "
            + _error_excerpt(status, text)
        )


# Writes that change how people sign in, where sign-in and account links point, the mail
# transport, a user's sign-in address or who is admin take an interactive user session. The
# token that exercises these guards is an OAuth access token for an admin who consented to an
# app with every scope: it holds config:write and user:write on a default install, while a
# personal access token is capped below those scopes.
_SESSION_ONLY_WRITES: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("POST", "/api/v1/configurationManager/authConfig/microsoft", {}),
    ("POST", "/api/v1/configurationManager/authConfig/google", {}),
    ("POST", "/api/v1/configurationManager/frontendPublicUrl", {}),
    ("POST", "/api/v1/configurationManager/connectorPublicUrl", {}),
    ("POST", "/api/v1/configurationManager/smtpConfig", {}),
    ("DELETE", "/api/v1/configurationManager/smtpConfig", {}),
    ("PUT", "/api/v1/configurationManager/smtpConfig/enabled", {"enabled": True}),
    ("PATCH", "/api/v1/users/{me}/email", {"email": "it-moved@example.invalid"}),
    ("PUT", "/api/v1/users/{me}", {"email": "it-moved@example.invalid"}),
    ("PUT", "/api/v1/users/{me}", {"role": "admin"}),
    ("POST", "/api/v1/users", {"fullName": "It Person", "email": "it-new@example.invalid", "role": "admin"}),
)


@pytest.fixture(scope="module")
def identity_admin_session(request: pytest.FixtureRequest) -> SessionClient:
    """The admin's password session. Unlike the shared fixture it fails, not skips, without credentials."""
    import os  # local: kept out of the module's import block so this section stands alone

    missing = [v for v in ("PIPESHUB_TEST_USER_EMAIL", "PIPESHUB_TEST_USER_PASSWORD") if not os.getenv(v)]
    assert not missing, f"These routes must be checked on every run; set {', '.join(missing)}"
    return request.getfixturevalue("user_session_client")


@pytest.fixture(scope="module")
def identity_admin_oauth_token(identity_admin_session: SessionClient) -> Iterator[str]:
    client = identity_admin_session
    app = create_oauth_app(
        client.base_url,
        client.token,
        f"it-admin-oauth-{uuid.uuid4().hex[:8]}",
        grant_types=["authorization_code", "refresh_token"],
        timeout=client.timeout_seconds,
    )
    try:
        yield authorization_code_token(client.base_url, client.token, app)["access_token"]
    finally:
        delete_oauth_app(client.base_url, client.token, app.id, timeout=client.timeout_seconds)


def _session_user_id(token: str) -> str:
    """The userId claim of a session token (read, not verified: the server did that)."""
    import base64
    import json

    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))["userId"]


class TestIdentityControlWritesRequireASession:
    @pytest.mark.parametrize("method,path,body", _SESSION_ONLY_WRITES, ids=str)
    def test_an_oauth_access_token_is_refused(
        self,
        method: str,
        path: str,
        body: dict[str, Any],
        identity_admin_oauth_token: str,
        identity_admin_session: SessionClient,
    ) -> None:
        client = identity_admin_session
        resp = requests.request(
            method,
            f"{client.base_url}{path.format(me=_session_user_id(client.token))}",
            headers={"Authorization": f"Bearer {identity_admin_oauth_token}"},
            json=body,
            timeout=client.timeout_seconds,
        )
        assert resp.status_code == 403 and "interactive user session" in resp.text, (
            f"An OAuth access token calling {method} {path} with {sorted(body)} got HTTP "
            f"{resp.status_code}, not the 403 session-only refusal. Reply starts: {resp.text[:200]!r}"
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
        _skip_if_feature_is_off(route, status, text)
        _skip_if_switched_off(route, status, text)
        lowered = text.lower()
        refused = (
            status in (401, 403)
            or NODE_REFUSAL_MESSAGE.lower() in lowered
            or OLD_NODE_REFUSAL_MESSAGE.lower() in lowered
        )
        assert not refused, (
            f"The admin was refused on {route.id}: HTTP {status}. " + _error_excerpt(status, text)
        )

