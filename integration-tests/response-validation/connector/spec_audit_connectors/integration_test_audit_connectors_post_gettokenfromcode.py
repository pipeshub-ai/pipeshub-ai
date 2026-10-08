"""Strict OpenAPI audit of POST /api/v1/connectors/getTokenFromCode.

No success case: an admin call with Google Workspace OAuth configured posts the
code to Google's token endpoint and verifies Google's ID token, which needs a
real consent from Google. On this stack Google Workspace is not configured, so
the admin call stops at the missing client ID.
"""

from __future__ import annotations

from typing import Any

import pytest
from connectors_audit_support import ConnectorsAuditClient, bearer, request_as
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

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
    assert_strict_openapi_exchange(resp, ROUTE)


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
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"tempCode": "spec-audit-code"}, id="with-temp-code"),
        # The configuration is read before the code is used, so a missing code is not what is refused.
        pytest.param({}, id="empty-body"),
    ],
)
def test_admin_without_google_workspace_configured_is_404(
    connectors_client: ConnectorsAuditClient, body: dict[str, Any]
) -> None:
    resp = connectors_client.post(PATH, json=body)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["message"] == "Client ID is missing"


def test_token_without_the_write_scope_is_forbidden(
    connectors_client: ConnectorsAuditClient, token_without_connector_scopes: str
) -> None:
    resp = connectors_client.post(
        PATH, auth=False, headers=bearer(token_without_connector_scopes), json={"tempCode": "spec-audit-code"}
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
