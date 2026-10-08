"""Every /api/v1/toolsets route answers 500 to a malformed JSON body.

The JSON body parser runs before the router and its failure is reported as an
internal error, so the request never reaches the token check or the validator.
"""

from __future__ import annotations

import pytest
from strict_openapi import assert_strict_openapi_exchange
from toolsets_audit_support import (
    JSON_HEADERS,
    MALFORMED_JSON_BODY,
    SHARED_BEHAVIOUR_OPERATIONS,
    TOOLSETS_BASE,
    ToolsetsClient,
    error_of,
)

pytestmark = pytest.mark.spec_audit


@pytest.mark.parametrize(
    ("method", "sub_path", "route"),
    [operation[1:] for operation in SHARED_BEHAVIOUR_OPERATIONS],
    ids=[operation[0] for operation in SHARED_BEHAVIOUR_OPERATIONS],
)
def test_malformed_json_body_is_an_internal_error_before_the_token_check(
    toolsets_client: ToolsetsClient, method: str, sub_path: str, route: str
) -> None:
    # API bug: a body that does not parse is a caller mistake, yet it answers 500.
    resp = getattr(toolsets_client, method.lower())(
        sub_path, auth=False, data=MALFORMED_JSON_BODY, headers=JSON_HEADERS
    )
    assert resp.status_code == 500, resp.text[:500]
    assert error_of(resp)["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, f"{TOOLSETS_BASE}{route}")
