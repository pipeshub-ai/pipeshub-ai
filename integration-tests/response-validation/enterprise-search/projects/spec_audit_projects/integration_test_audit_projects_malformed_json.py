"""Every /api/v1/projects route answers 500 to a malformed JSON body.

The JSON body parser runs before authentication and the router, and its
failure is reported as an internal error rather than a 400.
"""

from __future__ import annotations

import pytest
from helper.clients.projects_client import ProjectsClient
from projects_audit_support import (
    ARCHIVE_TEMPLATE,
    CONVERSATIONS_TEMPLATE,
    JSON_HEADERS,
    MALFORMED_JSON_BODY,
    MEMBERS_TEMPLATE,
    MISSING_PROJECT_ID,
    PIN_TEMPLATE,
    PROJECT_TEMPLATE,
    ROOT_TEMPLATE,
    UNARCHIVE_TEMPLATE,
    UNPIN_TEMPLATE,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

P = MISSING_PROJECT_ID


@pytest.mark.parametrize(
    ("method", "path", "route"),
    [
        pytest.param("POST", "", ROOT_TEMPLATE, id="post-root"),
        pytest.param("GET", "", ROOT_TEMPLATE, id="get-root"),
        pytest.param("GET", f"/{P}", PROJECT_TEMPLATE, id="get-project"),
        pytest.param("PATCH", f"/{P}", PROJECT_TEMPLATE, id="patch-project"),
        pytest.param("DELETE", f"/{P}", PROJECT_TEMPLATE, id="delete-project"),
        pytest.param("POST", f"/{P}/archive", ARCHIVE_TEMPLATE, id="post-archive"),
        pytest.param("POST", f"/{P}/unarchive", UNARCHIVE_TEMPLATE, id="post-unarchive"),
        pytest.param("POST", f"/{P}/pin", PIN_TEMPLATE, id="post-pin"),
        pytest.param("POST", f"/{P}/unpin", UNPIN_TEMPLATE, id="post-unpin"),
        pytest.param("GET", f"/{P}/conversations", CONVERSATIONS_TEMPLATE, id="get-conversations"),
        pytest.param("GET", f"/{P}/members", MEMBERS_TEMPLATE, id="get-members"),
        pytest.param("PUT", f"/{P}/members", MEMBERS_TEMPLATE, id="put-members"),
    ],
)
def test_malformed_json_body_is_an_internal_error(
    projects_client: ProjectsClient, method: str, path: str, route: str
) -> None:
    resp = getattr(projects_client, method.lower())(
        path,
        auth=False,
        data=MALFORMED_JSON_BODY,
        headers=JSON_HEADERS,
    )

    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
