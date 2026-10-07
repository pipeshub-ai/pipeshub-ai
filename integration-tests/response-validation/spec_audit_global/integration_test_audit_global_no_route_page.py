"""A method and path no route handles get Express's own 404 page, which the spec describes once.

The page is text/html reading "Cannot <METHOD> <path>"; it is not an operation, so the
spec says so in info.description instead of listing such pairs.
"""

from __future__ import annotations

import pytest
import requests
from global_audit_support import NO_ROUTE_PAGE_MARKER, spec
from strict_openapi import assert_strict_openapi_response

from helper.pipeshub_client import PipeshubClient

pytestmark = pytest.mark.spec_audit


def test_the_spec_describes_the_express_page_once() -> None:
    assert NO_ROUTE_PAGE_MARKER in spec()["info"]["description"]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        pytest.param("DELETE", "/api/v1/spec-audit-no-such-route", id="unknown-path"),
        pytest.param("POST", "/api/v1/users/health", id="method-the-path-does-not-have"),
        pytest.param("PATCH", "/.well-known/jwks.json", id="root-level-path"),
    ],
)
def test_a_method_and_path_no_route_handles_get_the_express_page(
    pipeshub_client: PipeshubClient, method: str, path: str
) -> None:
    resp = requests.request(method, f"{pipeshub_client.base_url}{path}", timeout=pipeshub_client.timeout_seconds)
    assert resp.status_code == 404, resp.text[:500]
    assert resp.headers["content-type"].startswith("text/html"), resp.headers
    assert f"Cannot {method} {path}" in resp.text, resp.text[:500]
    assert_strict_openapi_response(resp, path)
