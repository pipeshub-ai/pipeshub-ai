"""Strict OpenAPI audit of POST /api/v1/knowledgeBase/reindex/record-group/:recordGroupId.

authenticate -> KB_WRITE scope -> zod (depth int -1..100, statusFilters string[]) ->
connector service POST /api/v1/record-groups/:id/reindex. The connector looks the
group up (404), then checks the caller's permission on it (403), then publishes
the reindex event and answers 200.
"""

from __future__ import annotations

import pytest
from knowledge_base_audit_support import MISSING_RECORD_GROUP_ID, request_as
from helper.clients.kb_client import KBClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/reindex/record-group/:recordGroupId"


def _path(record_group_id: str) -> str:
    return f"/reindex/record-group/{record_group_id}"


def test_reindex_empty_knowledge_base_publishes_event(
    kb_client: KBClient, audit_kb_id: str
) -> None:
    # The knowledge base holds no records, so the published event queues no embedding work.
    resp = kb_client.post(_path(audit_kb_id), json={"depth": 0})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    assert body["success"] is True
    assert body["recordGroupId"] == audit_kb_id
    assert body["depth"] == 0
    assert body["eventPublished"] is True


def test_reindex_without_token_is_unauthorized(kb_client: KBClient) -> None:
    resp = kb_client.post(_path(MISSING_RECORD_GROUP_ID), auth=False, json={"depth": 0})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_reindex_depth_out_of_range_is_rejected(kb_client: KBClient) -> None:
    # Rejected by the gateway, so the id never has to exist.
    resp = kb_client.post(_path(MISSING_RECORD_GROUP_ID), json={"depth": 101})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_reindex_unknown_record_group_is_not_found(kb_client: KBClient) -> None:
    resp = kb_client.post(_path(MISSING_RECORD_GROUP_ID), json={"depth": 0})
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert MISSING_RECORD_GROUP_ID in resp.text


def test_reindex_as_member_without_access_is_forbidden(
    second_user: SecondUser, audit_kb_id: str
) -> None:
    resp = request_as(second_user, "POST", _path(audit_kb_id), json={"depth": 0})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
