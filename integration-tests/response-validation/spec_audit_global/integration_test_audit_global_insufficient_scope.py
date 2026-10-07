"""On every route guarded by requireScopes, an OAuth token without the route's scope gets 403.

requireScopes passes a token holding any one of the scopes it names, while OpenAPI reads the
scopes of one security requirement as all required. So each accepted scope gets its own `oauth2`
entry, except on SDK operations: Speakeasy refuses more than one `oauth2` entry, so those list one
scope and name the alternative in their description (GATE_SCOPES in the support file).
requireScopes runs right after authentication and is a no-op for session tokens.
"""

from __future__ import annotations

import pytest
from global_audit_support import (
    GATE_SCOPES,
    INSUFFICIENT_SCOPE_DESCRIPTION,
    Operation,
    call,
    error_of,
    find,
    operations,
    params_for,
)
from strict_openapi import assert_strict_openapi_exchange

from helper.pipeshub_client import PipeshubClient

pytestmark = pytest.mark.spec_audit

SCOPED = [op for op in operations() if op.oauth_scopes]


def spec_admits(op: Operation, scope: str) -> bool:
    if any(scope in entry for entry in op.oauth_entries):
        return True
    return op.in_sdk and f"`{scope}`" in (op.operation.get("description") or "")


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
    assert error["message"] == f"Insufficient scope. Required: {' or '.join(op.gate_scopes)}", resp.text[:500]
    assert_strict_openapi_exchange(resp, op.spec_path)
    described = (op.response("403") or {}).get("description", "")
    assert INSUFFICIENT_SCOPE_DESCRIPTION.search(described), f"the 403 of {op.id} does not mention the scope check"


@pytest.mark.parametrize("op", params_for(SCOPED))
def test_every_scope_the_gate_accepts_is_admitted_by_the_spec(op: Operation) -> None:
    missing = [s for s in op.gate_scopes if not spec_admits(op, s)]
    assert not missing, f"{op.id} accepts a token holding {missing} but the spec does not say so"


@pytest.mark.parametrize("op", params_for([op for op in SCOPED if op.id not in GATE_SCOPES]))
def test_each_alternative_scope_is_its_own_requirement(op: Operation) -> None:
    assert all(len(entry) == 1 for entry in op.oauth_entries), (
        f"{op.id}: {op.oauth_entries} reads as all scopes required, requireScopes wants any one"
    )


@pytest.mark.parametrize("op", params_for([op for op in SCOPED if op.in_sdk]))
def test_an_sdk_operation_has_one_oauth2_requirement(op: Operation) -> None:
    assert len(op.oauth_entries) == 1, f"{op.id}: Speakeasy refuses {len(op.oauth_entries)} oauth2 entries"


def test_a_token_holding_only_connector_read_can_navigate(
    pipeshub_client: PipeshubClient, connector_read_token: str
) -> None:
    op = find("get", "/connectors/navigate")
    resp = call(
        pipeshub_client.base_url, op, timeout=pipeshub_client.timeout_seconds,
        headers={"Authorization": f"Bearer {connector_read_token}"},
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, op.spec_path)
    assert spec_admits(op, "connector:read"), f"{op.id} security {op.oauth_entries} does not admit connector:read"
