"""Strict OpenAPI audit of POST /api/v1/connectors/getTokenFromCode.

Negative cases only: an admin call reads the Google Workspace OAuth config and
posts the code to Google's token endpoint, then stores the credentials.
"""

from __future__ import annotations

from typing import Any

import pytest
from connectors_audit_support import ConnectorsAuditClient, request_as
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/getTokenFromCode"
PATH = "/getTokenFromCode"


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-token"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="garbage-token"),
    ],
)
def test_token_exchange_without_valid_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient, headers: dict[str, str]
) -> None:
    resp = connectors_client.post(
        PATH, auth=False, headers=headers, json={"tempCode": "spec-audit-code"}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"tempCode": "spec-audit-code"}, id="with-temp-code"),
        # No validator sits on this route, so a missing tempCode is not a 400.
        pytest.param({}, id="empty-body"),
    ],
)
def test_member_is_refused_by_the_admin_check(
    second_user: SecondUser, body: dict[str, Any]
) -> None:
    # userAdminCheck runs after requireScopes and before the handler, so Google is never called.
    resp = request_as(second_user, "POST", PATH, json=body)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
