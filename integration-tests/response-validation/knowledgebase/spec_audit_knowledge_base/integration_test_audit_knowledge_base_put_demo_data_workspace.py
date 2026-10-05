"""Strict OpenAPI audit of PUT /api/v1/knowledgeBase/demo-data/workspace.

authenticate -> KB_WRITE scope -> zod body -> Node admin gate -> connector service.
Refusals only: the admin success path turns the sample workspace and the sample
accounts' sign-in on or off for the whole org.
"""

from __future__ import annotations

from typing import Any

import pytest
from knowledge_base_audit_support import request_as
from helper.clients.kb_client import KBClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/demo-data/workspace"
PATH = "/demo-data/workspace"


def test_workspace_without_token_is_unauthorized(kb_client: KBClient) -> None:
    resp = kb_client.put(PATH, auth=False, json={"enabled": True})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_workspace_as_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "PUT", PATH, json={"enabled": False})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert "Only admins can change this for everyone" in resp.text


# The body is validated before the admin gate, so these never reach the handler.
@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="enabled-missing"),
        pytest.param({"enabled": "false"}, id="enabled-not-boolean"),
        pytest.param({"enabled": True, "orgId": "other-org"}, id="unknown-key"),
    ],
)
def test_workspace_invalid_body_is_rejected(kb_client: KBClient, body: dict[str, Any]) -> None:
    resp = kb_client.put(PATH, json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
