"""Every skills route refuses an OAuth token without its skill scope with 403.

The gateway's scope check runs before the import limiter and before the request is
proxied, so these calls cost no import budget and never reach the skills service.
"""

from __future__ import annotations

import pytest
from skills_audit_support import SKILLS_BASE
from skills_operations import OPERATIONS, SkillsOperation
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit


@pytest.mark.parametrize("operation", OPERATIONS, ids=[op.id for op in OPERATIONS])
def test_token_without_skill_scope_is_forbidden(
    pipeshub_client, unscoped_headers: dict[str, str], operation: SkillsOperation
) -> None:
    resp = pipeshub_client.request(
        operation.method, f"{SKILLS_BASE}{operation.path}", headers=unscoped_headers, json=operation.body
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, operation.route)
    error = resp.json()["error"]
    assert error["code"] == "HTTP_FORBIDDEN"
    assert error["message"] == f"Insufficient scope. Required: {operation.scope}"
