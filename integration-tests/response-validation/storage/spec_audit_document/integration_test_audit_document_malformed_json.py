"""POST /api/v1/document/updateAppConfig answers 500 to a malformed JSON body.

The JSON body parser runs before the router, so the failure comes before the scoped
token is checked, on a route that never reads a body.
"""

from __future__ import annotations

import pytest
from document_audit_support import (
    JSON_HEADERS,
    MALFORMED_JSON_BODY,
    UPDATE_APP_CONFIG_ROUTE,
    DocumentClient,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit


@pytest.mark.parametrize(
    ("sub_path", "route"),
    [("/updateAppConfig", UPDATE_APP_CONFIG_ROUTE)],
    ids=["updateAppConfig"],
)
def test_malformed_json_body_is_an_internal_error_before_the_token_check(
    document_client: DocumentClient, sub_path: str, route: str
) -> None:
    resp = document_client.call(
        "POST", sub_path, auth=False, data=MALFORMED_JSON_BODY, headers=JSON_HEADERS
    )
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
