"""Every skills route answers 500 to a malformed JSON body.

The JSON body parser runs before the router and its failure is reported as an internal
error, so the request never reaches authentication, the scope check or the skills service.
"""

from __future__ import annotations

import pytest
from skills_audit_support import JSON_HEADERS, MALFORMED_JSON_BODY, SKILLS_BASE
from skills_operations import OPERATIONS, SkillsOperation
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit


@pytest.mark.parametrize("operation", OPERATIONS, ids=[op.id for op in OPERATIONS])
def test_malformed_json_body_is_an_internal_error(pipeshub_client, operation: SkillsOperation) -> None:
    # API bug: a body that does not parse is a caller mistake, yet it answers 500.
    resp = pipeshub_client.request(
        operation.method, f"{SKILLS_BASE}{operation.path}", data=MALFORMED_JSON_BODY, headers=JSON_HEADERS
    )
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, operation.route)
