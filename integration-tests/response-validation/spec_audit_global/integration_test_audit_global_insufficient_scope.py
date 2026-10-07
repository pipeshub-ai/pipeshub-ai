"""On every route guarded by requireScopes, an OAuth token without the route's scope gets 403.

The scopes come from each operation's `oauth2` security requirement. requireScopes runs
right after authentication and is a no-op for session tokens, so the check holds for
GETs with no admin check too. The token here carries no scope at all.
"""

from __future__ import annotations

import pytest
from global_audit_support import (
    INSUFFICIENT_SCOPE_DESCRIPTION,
    Operation,
    call,
    error_of,
    operations,
    params_for,
)
from strict_openapi import assert_strict_openapi_exchange

from helper.pipeshub_client import PipeshubClient

pytestmark = pytest.mark.spec_audit

SCOPED = [op for op in operations() if op.oauth_scopes]


@pytest.mark.parametrize("op", params_for(SCOPED))
def test_a_token_without_the_scope_is_forbidden(
    pipeshub_client: PipeshubClient, no_scope_token: str, op: Operation
) -> None:
    resp = call(
        pipeshub_client.base_url, op, timeout=pipeshub_client.timeout_seconds,
        headers={"Authorization": f"Bearer {no_scope_token}"},
    )
    assert resp.status_code == 403, resp.text[:500]
    error = error_of(resp)
    assert error["code"] == "HTTP_FORBIDDEN", resp.text[:500]
    assert error["message"] == f"Insufficient scope. Required: {' or '.join(op.oauth_scopes)}", resp.text[:500]
    assert_strict_openapi_exchange(resp, op.spec_path)
    described = (op.response("403") or {}).get("description", "")
    assert INSUFFICIENT_SCOPE_DESCRIPTION.search(described), f"the 403 of {op.id} does not mention the scope check"
