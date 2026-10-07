"""/api/v1/service-accounts routes answer 500 to a malformed JSON body.

The JSON body parser runs before the router and its failure is reported as an
internal error, so the request never reaches the token check.
"""

from __future__ import annotations

import pytest
from service_accounts_audit_support import (
    BY_ID_TEMPLATE,
    MISSING_SERVICE_ACCOUNT_ID,
    ROOT_TEMPLATE,
    SERVICE_ACCOUNTS_BASE,
    ServiceAccountsClient,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}


@pytest.mark.parametrize(
    ("method", "sub_path", "route"),
    [
        ("GET", "", ROOT_TEMPLATE),
        ("POST", "", ROOT_TEMPLATE),
        ("GET", f"/{MISSING_SERVICE_ACCOUNT_ID}", BY_ID_TEMPLATE),
        ("PATCH", f"/{MISSING_SERVICE_ACCOUNT_ID}", BY_ID_TEMPLATE),
        ("DELETE", f"/{MISSING_SERVICE_ACCOUNT_ID}", BY_ID_TEMPLATE),
    ],
    ids=["list", "create", "get", "update", "delete"],
)
def test_malformed_json_body_is_an_internal_error_before_the_token_check(
    service_accounts_client: ServiceAccountsClient, method: str, sub_path: str, route: str
) -> None:
    # API bug: a body that does not parse is a caller mistake, yet it answers 500.
    resp = service_accounts_client._client.request(
        method,
        f"{SERVICE_ACCOUNTS_BASE}{sub_path}",
        auth=False,
        data=MALFORMED_JSON_BODY,
        headers=JSON_HEADERS,
    )

    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
