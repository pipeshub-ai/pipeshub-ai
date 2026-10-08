"""Strict OpenAPI audit of GET /api/v1/configurationManager/public/desktopFrontendUrl."""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from configuration_manager_audit_support import DESKTOP_CLIENT_HEADERS
from helper.clients.config_client import ConfigClient
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/public/desktopFrontendUrl"
PATH = "/public/desktopFrontendUrl"


def test_desktop_client_reads_frontend_url_without_a_session(config_client: ConfigClient) -> None:
    resp = config_client.get(PATH, auth=False, headers=DESKTOP_CLIENT_HEADERS)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert list(body) == ["frontendUrl"], body
    # resolveFrontendPublicUrl falls back to http://localhost:<port>, so it is never empty.
    parts = urlsplit(body["frontendUrl"])
    assert parts.scheme in ("http", "https") and parts.netloc, body
    # The router sets this on every answer, this unauthenticated one included.
    assert resp.headers.get("Cache-Control") == "no-store", dict(resp.headers)


@pytest.mark.parametrize(
    ("auth", "headers"),
    [
        (False, None),
        (False, {"client-name": "web"}),
        # The controller compares with ===, so the value is case-sensitive.
        (False, {"client-name": "Desktop"}),
        # No auth middleware on the route: a valid admin token does not stand in for the header.
        (True, None),
    ],
    ids=["no-header", "other-client", "wrong-case", "admin-token-no-header"],
)
def test_non_desktop_caller_is_forbidden(
    config_client: ConfigClient, auth: bool, headers: dict[str, str] | None
) -> None:
    resp = config_client.get(PATH, auth=auth, headers=headers)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "frontendUrl" not in resp.text, resp.text[:500]
