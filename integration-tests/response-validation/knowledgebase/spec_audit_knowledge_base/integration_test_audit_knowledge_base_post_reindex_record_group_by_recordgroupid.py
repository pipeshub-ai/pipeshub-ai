"""Strict OpenAPI audit of POST /api/v1/knowledgeBase/reindex/record-group/:recordGroupId.

guardPathParams(recordGroupId) -> authenticate -> requireScopes(kb:write) -> zod (depth int
-1..100, statusFilters string[], both optional; other fields dropped) -> connector service
POST /api/v1/record-groups/:id/reindex. The connector looks the group up (404), refuses a
disabled connector (409), checks the caller's access, then publishes the reindex event.

A knowledge base is stored as an app and its folders as records, so neither is a record
group: the groups here come from a Web connector synced from a page served by the test.
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.clients.kb_client import KBClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from knowledge_base_audit_support import (
    MISSING_RECORD_GROUP_ID,
    UNSAFE_ID,
    UNSAFE_ID_MESSAGE,
    MakeKb,
    WebRecordGroup,
    request_as,
    web_record_group,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/reindex/record-group/:recordGroupId"
RESPONSE_FIELDS = {"success", "message", "recordGroupId", "depth", "connectorId", "connector", "eventPublished"}


def _path(record_group_id: str) -> str:
    return f"/reindex/record-group/{record_group_id}"


@pytest.mark.parametrize(
    ("depth", "applied"),
    [
        pytest.param(0, 0, id="depth-0"),
        pytest.param(100, 100, id="depth-100"),
        # "Everything" comes back as the deepest depth the route accepts.
        pytest.param(-1, 100, id="depth-unlimited"),
    ],
)
def test_reindex_record_group_publishes_event(
    kb_client: KBClient, synced_record_group: WebRecordGroup, depth: int, applied: int
) -> None:
    group = synced_record_group
    resp = kb_client.post(_path(group.record_group_id), json={"depth": depth})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "success": True,
        "message": f"Reindex initiated for record group {group.record_group_id} with depth {applied}",
        "recordGroupId": group.record_group_id,
        "depth": applied,
        "connectorId": group.connector_id,
        "connector": "web",
        "eventPublished": True,
    }


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(None, id="no-body"),
        pytest.param({}, id="empty-object"),
        pytest.param({"statusFilters": ["FAILED", "AUTO_INDEX_OFF"]}, id="known-statuses"),
        pytest.param({"statusFilters": ["NOT_A_STATUS"]}, id="any-string-status"),
        pytest.param({"statusFilters": []}, id="no-statuses"),
    ],
)
def test_reindex_record_group_with_defaults_and_filters(
    kb_client: KBClient, synced_record_group: WebRecordGroup, payload: Any
) -> None:
    path = _path(synced_record_group.record_group_id)
    resp = kb_client.post(path, json=payload) if payload is not None else kb_client.post(path)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert set(resp.json()) == RESPONSE_FIELDS
    assert resp.json()["depth"] == 0


def test_reindex_record_group_drops_force_and_other_fields(
    kb_client: KBClient, synced_record_group: WebRecordGroup
) -> None:
    # force is stripped by the validator, so the handler always sends force=false.
    with outside_request_contract("the validator drops force and any field it does not know"):
        resp = kb_client.post(
            _path(synced_record_group.record_group_id), params={"wait": "true"}, json={"force": True, "depth": 1}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert set(resp.json()) == RESPONSE_FIELDS


def test_reindex_record_group_as_member_of_the_org(
    second_user: SecondUser, synced_record_group: WebRecordGroup
) -> None:
    # An org-wide (team) connector grants every member read access to its record group.
    resp = request_as(second_user, "POST", _path(synced_record_group.record_group_id), json={"depth": 0})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_reindex_as_member_without_access_is_forbidden(
    kb_client: KBClient, pipeshub_client: PipeshubClient, second_user: SecondUser
) -> None:
    # A personal connector's record group is readable by its owner only.
    with web_record_group(pipeshub_client, kb_client, scope="personal") as group:
        resp = request_as(second_user, "POST", _path(group.record_group_id), json={"depth": 0})
        assert resp.status_code == 403, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_reindex_record_group_of_a_disabled_connector_is_a_conflict(
    kb_client: KBClient, pipeshub_client: PipeshubClient
) -> None:
    with web_record_group(pipeshub_client, kb_client) as group:
        pipeshub_client.toggle_sync(group.connector_id, False)
        resp = kb_client.post(_path(group.record_group_id), json={"depth": 0})
        assert resp.status_code == 409, resp.text[:500]
        assert "is currently disabled. Enable it from Connector Settings and try again." in resp.json()["error"]["message"]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_reindex_without_token_is_unauthorized(kb_client: KBClient) -> None:
    resp = kb_client.post(_path(MISSING_RECORD_GROUP_ID), auth=False, json={"depth": 0})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_reindex_with_a_token_lacking_kb_write_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.post(_path(MISSING_RECORD_GROUP_ID), auth=False, headers=unscoped_headers, json={"depth": 0})
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:write"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"depth": 101}, id="depth-above-100"),
        pytest.param({"depth": -2}, id="depth-below-minus-1"),
        pytest.param({"depth": 1.5}, id="depth-not-whole"),
        pytest.param({"depth": "1"}, id="depth-a-string"),
        pytest.param({"statusFilters": "FAILED"}, id="filters-not-a-list"),
        pytest.param({"statusFilters": [1]}, id="filter-not-a-string"),
        pytest.param([{"depth": 0}], id="body-a-list"),
    ],
)
def test_reindex_rejects_a_body_outside_the_validator(kb_client: KBClient, payload: Any) -> None:
    # Refused by the gateway, so the id never has to exist.
    resp = kb_client.post(_path(MISSING_RECORD_GROUP_ID), json=payload)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_reindex_unknown_record_group_is_not_found(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    folder = kb_client.post(f"/{kb_id}/folder", json={"folderName": "spec-audit-folder"})
    assert folder.status_code == 200, folder.text[:500]
    # Neither a knowledge base nor a folder is a record group.
    for group_id in (MISSING_RECORD_GROUP_ID, kb_id, folder.json()["id"]):
        resp = kb_client.post(_path(group_id), json={"depth": 0})
        assert resp.status_code == 404, resp.text[:500]
        assert resp.json()["error"]["message"] == f"Record group not found: {group_id}"
        assert_strict_openapi_exchange(resp, ROUTE)


def test_reindex_id_that_cannot_be_a_url_segment_is_bad_request(kb_client: KBClient) -> None:
    resp = kb_client.post(_path(UNSAFE_ID), json={"depth": 0})
    assert resp.status_code == 400, resp.text[:500]
    assert UNSAFE_ID_MESSAGE in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)
