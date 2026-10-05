"""Strict OpenAPI audit of GET /api/v1/knowledgeBase/demo-data/status."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from helper.clients.kb_client import KBClient
from helper.second_user import SecondUser
from knowledge_base_audit_support import DEMO_STATUS_FIELDS, request_as
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/demo-data/status"
PATH = "/demo-data/status"


def _checked_status(resp: requests.Response) -> dict[str, Any]:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    assert set(body) == DEMO_STATUS_FIELDS, body
    assert body["hasDemo"] is bool(body["demoConnectorIds"]), body
    if not body["hasDemo"]:
        assert body["chosen"] is None and body["realData"] is False, body
    if not body["hasDemo"] or body["offForEveryone"]:
        expected_include = False
    elif body["chosen"] is not None:
        expected_include = body["chosen"]
    else:
        expected_include = not body["realData"]
    assert body["include"] is expected_include, body
    return body


def test_status_as_admin_ignores_unknown_query_params(kb_client: KBClient) -> None:
    # The route has no validator and the controller forwards no query string.
    plain = _checked_status(kb_client.get(PATH))
    with_query = _checked_status(kb_client.get(PATH, params={"include": "bogus"}))
    for org_wide in ("hasDemo", "offForEveryone", "demoConnectorIds"):
        assert with_query[org_wide] == plain[org_wide], (plain, with_query)


def test_status_as_member_shares_the_org_wide_fields(
    kb_client: KBClient, second_user: SecondUser
) -> None:
    # KB_READ only: there is no admin gate, and only `chosen`/`include` are per user.
    member = _checked_status(request_as(second_user, "GET", PATH))
    admin = _checked_status(kb_client.get(PATH))
    assert member["hasDemo"] == admin["hasDemo"], (admin, member)
    assert member["offForEveryone"] == admin["offForEveryone"], (admin, member)
    assert sorted(member["demoConnectorIds"]) == sorted(admin["demoConnectorIds"])


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-token"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="invalid-token"),
    ],
)
def test_status_rejects_unauthenticated_calls(
    kb_client: KBClient, headers: dict[str, str]
) -> None:
    resp = kb_client.get(PATH, auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
