"""Every /api/v1/configurationManager route answers 500 to a malformed JSON body.

The JSON body parser runs before the router and its failure is reported as an
internal error, so the request never reaches the token check, GET routes included.
"""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import (
    CONFIGURATION_MANAGER_BASE,
    JSON_HEADERS,
    MALFORMED_JSON_BODY,
    PRE_ROUTER_OPERATIONS,
    concrete_path,
)
from helper.clients.config_client import ConfigClient
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit


@pytest.mark.parametrize(
    ("method", "sub_path"),
    PRE_ROUTER_OPERATIONS,
    ids=[f"{method} {sub_path}" for method, sub_path in PRE_ROUTER_OPERATIONS],
)
def test_malformed_json_body_is_an_internal_error_before_the_token_check(
    config_client: ConfigClient, method: str, sub_path: str
) -> None:
    # API bug: a body that does not parse is a caller mistake, yet it answers 500.
    send = getattr(config_client, method.lower())
    resp = send(concrete_path(sub_path), auth=False, data=MALFORMED_JSON_BODY, headers=JSON_HEADERS)

    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, f"{CONFIGURATION_MANAGER_BASE}{sub_path}")
