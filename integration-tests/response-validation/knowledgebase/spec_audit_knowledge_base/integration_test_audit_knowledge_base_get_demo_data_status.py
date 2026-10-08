"""Strict OpenAPI audit of GET /api/v1/knowledgeBase/demo-data/status.

authenticate -> requireScopes(kb:read) -> getDemoDataStatus, which forwards nothing but the
caller's token to the connector service (GET /api/v1/demo-data/status). No validator: the
query string and any body are never read.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests
from helper.clients.kb_client import KBClient
from helper.second_user import SecondUser
from knowledge_base_audit_support import DEMO_STATUS_FIELDS, request_as
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/demo-data/status"
PATH = "/demo-data/status"


def _checked_status(resp: requests.Response) -> dict[str, Any]:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body: dict[str, Any] = resp.json()
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


def test_status_as_admin(kb_client: KBClient) -> None:
    _checked_status(kb_client.get(PATH))


def test_status_reads_neither_query_nor_body(kb_client: KBClient) -> None:
    plain = _checked_status(kb_client.get(PATH))
    with outside_request_contract("the route has no validator and the handler reads no query or body"):
        loaded = _checked_status(
            kb_client.get(PATH, params={"include": "bogus", "userId": "someone-else"}, json={"include": True})
        )
    assert loaded == plain


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
    assert_strict_openapi_exchange(resp, ROUTE)


def test_status_with_a_token_lacking_kb_read_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.get(PATH, auth=False, headers=unscoped_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:read"
    assert_strict_openapi_exchange(resp, ROUTE)
