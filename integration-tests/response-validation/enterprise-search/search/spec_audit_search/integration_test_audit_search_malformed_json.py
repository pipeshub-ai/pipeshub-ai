"""Every /api/v1/search route answers 500 to a malformed JSON body.

The JSON body parser runs before authentication and the router, and its
failure is reported as an internal error rather than a 400.
"""

from __future__ import annotations

import pytest
from search_audit_support import (
    ARCHIVE_TEMPLATE,
    JSON_HEADERS,
    MALFORMED_JSON_BODY,
    MISSING_SEARCH_ID,
    ROOT_TEMPLATE,
    SEARCH_TEMPLATE,
    SHARE_TEMPLATE,
    UNARCHIVE_TEMPLATE,
    UNSHARE_TEMPLATE,
    UPDATE_APP_CONFIG_ROUTE,
    SearchAuditClient,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

S = MISSING_SEARCH_ID


@pytest.mark.parametrize(
    ("method", "path", "route"),
    [
        pytest.param("POST", "/", ROOT_TEMPLATE, id="post-root"),
        pytest.param("GET", "/", ROOT_TEMPLATE, id="get-root"),
        pytest.param("DELETE", "/", ROOT_TEMPLATE, id="delete-root"),
        pytest.param("GET", f"/{S}", SEARCH_TEMPLATE, id="get-search"),
        pytest.param("DELETE", f"/{S}", SEARCH_TEMPLATE, id="delete-search"),
        pytest.param("PATCH", f"/{S}/share", SHARE_TEMPLATE, id="patch-share"),
        pytest.param("PATCH", f"/{S}/unshare", UNSHARE_TEMPLATE, id="patch-unshare"),
        pytest.param("PATCH", f"/{S}/archive", ARCHIVE_TEMPLATE, id="patch-archive"),
        pytest.param("PATCH", f"/{S}/unarchive", UNARCHIVE_TEMPLATE, id="patch-unarchive"),
        pytest.param("POST", "/updateAppConfig", UPDATE_APP_CONFIG_ROUTE, id="post-update-app-config"),
    ],
)
def test_malformed_json_body_is_an_internal_error(
    search_audit_client: SearchAuditClient, method: str, path: str, route: str
) -> None:
    resp = getattr(search_audit_client, method.lower())(
        path,
        auth=False,
        data=MALFORMED_JSON_BODY,
        headers=JSON_HEADERS,
    )

    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
