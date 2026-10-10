"""Strict OpenAPI audit of GET /api/v1/knowledgeBase/limits.

authenticate -> requireScopes(kb:read) -> an inline handler: maxFilesPerRequest is the constant
KB_UPLOAD_LIMITS.maxFilesPerRequest and maxFileSizeBytes is read from the platform settings on
every call. There is no validator and nothing from the request is read.
"""

from __future__ import annotations

import pytest
from helper.clients.kb_client import KBClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from knowledge_base_audit_support import PLATFORM_SETTINGS_PATH, request_as, upload_max_size_set_to
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/limits"
MAX_FILES_PER_REQUEST = 1000


def _platform_max_size(client: PipeshubClient) -> int:
    resp = client.request("GET", PLATFORM_SETTINGS_PATH)
    assert resp.status_code == 200, resp.text[:300]
    return int(resp.json()["fileUploadMaxSizeBytes"])


def test_limits_report_the_file_count_and_the_platform_size_limit(
    kb_client: KBClient, pipeshub_client: PipeshubClient
) -> None:
    resp = kb_client.get("/limits")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "maxFilesPerRequest": MAX_FILES_PER_REQUEST,
        "maxFileSizeBytes": _platform_max_size(pipeshub_client),
    }


def test_limits_follow_a_change_of_the_platform_setting(
    kb_client: KBClient, pipeshub_client: PipeshubClient
) -> None:
    raised = _platform_max_size(pipeshub_client) + 1
    with upload_max_size_set_to(pipeshub_client, raised):
        resp = kb_client.get("/limits")
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert resp.json()["maxFileSizeBytes"] == raised


def test_limits_are_the_same_for_a_member(kb_client: KBClient, second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", "/limits")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == kb_client.get("/limits").json()


def test_limits_ignore_the_query(kb_client: KBClient) -> None:
    with outside_request_contract("the route has no validator and reads nothing from the request"):
        resp = kb_client.get("/limits", params={"kbId": "anything"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["maxFilesPerRequest"] == MAX_FILES_PER_REQUEST


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-token"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="invalid-token"),
    ],
)
def test_limits_reject_unauthenticated_calls(kb_client: KBClient, headers: dict[str, str]) -> None:
    resp = kb_client.get("/limits", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_limits_with_a_token_lacking_kb_read_are_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.get("/limits", auth=False, headers=unscoped_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:read"
    assert_strict_openapi_exchange(resp, ROUTE)
