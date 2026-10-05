"""Strict OpenAPI audit of POST /api/v1/knowledgeBase/reindex/record-group/:recordGroupId.

authenticate -> KB_WRITE scope -> zod (depth int -1..100, statusFilters string[]) ->
connector service POST /api/v1/record-groups/:id/reindex. The connector looks the
group up (404), then checks the caller's permission on it (403), then publishes
the reindex event and answers 200.

A knowledge base is stored as an app, not as a record group: record groups are
only written by a connector sync (a drive, a space, a channel). The two cases
that need an existing group look one up and skip when the org has none.
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


@pytest.fixture(scope="module")
def record_group_id(kb_client: KBClient) -> str:
    """Id of a record group the admin can see, or skip when no connector has synced one."""
    resp = kb_client.get("/knowledge-hub/nodes", params={"nodeTypes": "recordGroup", "limit": 1})
    assert resp.status_code == 200, resp.text[:500]
    items = resp.json()["items"]
    if not items:
        pytest.skip(
            "The org has no record group: only a synced connector writes one (a knowledge base "
            "is an app), and no connector to an external system is configured on this stack."
        )
    return str(items[0]["id"])


def test_reindex_record_group_publishes_event(kb_client: KBClient, record_group_id: str) -> None:
    # Depth 0 limits the event to the group's direct records.
    resp = kb_client.post(_path(record_group_id), json={"depth": 0})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    assert body["success"] is True
    assert body["recordGroupId"] == record_group_id
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
    second_user: SecondUser, record_group_id: str
) -> None:
    resp = request_as(second_user, "POST", _path(record_group_id), json={"depth": 0})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
