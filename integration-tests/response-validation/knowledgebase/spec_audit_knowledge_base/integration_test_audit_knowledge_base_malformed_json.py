"""Every /api/v1/knowledgeBase route answers 500 to a malformed JSON body.

The JSON body parser runs before the router, so the failure comes before authentication,
validation and the handler, GET and DELETE routes included.
"""

from __future__ import annotations

import pytest
from helper.clients.kb_client import KBClient
from knowledge_base_audit_support import (
    JSON_HEADERS,
    KB_BASE,
    MALFORMED_JSON_BODY,
    MISSING_RECORD_GROUP_ID,
    MISSING_RECORD_ID,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

RECORD = f"/record/{MISSING_RECORD_ID}"
HUB = "/knowledge-hub/nodes"
KB = f"/{MISSING_RECORD_ID}"
FOLDER = f"{KB}/folder/{MISSING_RECORD_ID}"

# (method, path under the router, route template)
OPERATIONS = [
    ("POST", "", ""),
    ("GET", "", ""),
    ("GET", "/demo-data/status", "/demo-data/status"),
    ("PUT", "/demo-data/preference", "/demo-data/preference"),
    ("PUT", "/demo-data/workspace", "/demo-data/workspace"),
    ("GET", HUB, HUB),
    ("GET", f"{HUB}/app/{MISSING_RECORD_ID}", f"{HUB}/:parentType/:parentId"),
    ("GET", RECORD, "/record/:recordId"),
    ("PUT", RECORD, "/record/:recordId"),
    ("DELETE", RECORD, "/record/:recordId"),
    ("POST", f"{RECORD}/restore", "/record/:recordId/restore"),
    ("POST", "/records/restore", "/records/restore"),
    ("GET", f"/stream{RECORD}", "/stream/record/:recordId"),
    ("POST", f"/reindex{RECORD}", "/reindex/record/:recordId"),
    ("POST", f"/reindex/record-group/{MISSING_RECORD_GROUP_ID}", "/reindex/record-group/:recordGroupId"),
    ("GET", "/limits", "/limits"),
    ("GET", KB, "/:kbId"),
    ("PUT", KB, "/:kbId"),
    ("DELETE", KB, "/:kbId"),
    ("POST", f"{KB}/upload", "/:kbId/upload"),
    ("POST", f"{KB}/folder", "/:kbId/folder"),
    ("PUT", FOLDER, "/:kbId/folder/:folderId"),
    ("DELETE", FOLDER, "/:kbId/folder/:folderId"),
    ("POST", f"{KB}/permissions", "/:kbId/permissions"),
    ("GET", f"{KB}/permissions", "/:kbId/permissions"),
    ("PUT", f"{KB}/permissions", "/:kbId/permissions"),
    ("DELETE", f"{KB}/permissions", "/:kbId/permissions"),
    ("PUT", f"{KB}{RECORD}/move", "/:kbId/record/:recordId/move"),
]


@pytest.mark.parametrize(
    ("method", "path", "route"),
    OPERATIONS,
    ids=[f"{method} {route or '/'}" for method, _, route in OPERATIONS],
)
def test_malformed_json_body_is_an_internal_error_before_the_token_check(
    kb_client: KBClient, method: str, path: str, route: str
) -> None:
    resp = kb_client._client.request(
        method, f"{KB_BASE}{path}", auth=False, data=MALFORMED_JSON_BODY, headers=JSON_HEADERS
    )
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, f"{KB_BASE}{route}")
