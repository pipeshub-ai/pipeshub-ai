"""Every /api/v1/docs route answers 500 to a malformed JSON body.

The JSON body parser runs before the router and its failure is reported as an
internal error, so a GET that never reads a body still fails on one.
"""

from __future__ import annotations

import pytest
from api_docs_audit_support import (
    HEALTH_ROUTE,
    JSON_HEADERS,
    JSON_ROUTE,
    MALFORMED_JSON_BODY,
    ONE_SEGMENT_SUB_PATH,
    UI_ROUTE,
    UI_SUB_PATH_ROUTE,
    ApiDocsClient,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit


@pytest.mark.parametrize(
    ("sub_path", "route"),
    [
        ("/health", HEALTH_ROUTE),
        ("/json", JSON_ROUTE),
        ("", UI_ROUTE),
        (ONE_SEGMENT_SUB_PATH, UI_SUB_PATH_ROUTE),
    ],
    ids=["health", "json", "ui_root", "ui_sub_path"],
)
def test_malformed_json_body_is_an_internal_error(
    api_docs_client: ApiDocsClient, sub_path: str, route: str
) -> None:
    resp = api_docs_client.get(
        sub_path, auth=False, data=MALFORMED_JSON_BODY, headers=JSON_HEADERS
    )
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
