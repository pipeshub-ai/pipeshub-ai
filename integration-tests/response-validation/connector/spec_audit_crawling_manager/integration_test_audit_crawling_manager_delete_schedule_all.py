"""Strict OpenAPI audit of DELETE /api/v1/crawlingManager/schedule/all.

Negative cases only: the success path removes every sync schedule in the shared org
and cannot be put back.
"""

from __future__ import annotations

import pytest
from crawling_manager_audit_support import (
    CrawlingManagerClient,
    SeededConnector,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/crawlingManager/schedule/all"


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param(None, id="no-token"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="malformed-jwt"),
        pytest.param({"Authorization": "Basic dXNlcjpwYXNz"}, id="non-bearer-scheme"),
    ],
)
def test_remove_all_without_valid_token_is_unauthorized(
    crawling_manager_client: CrawlingManagerClient,
    headers: dict[str, str] | None,
) -> None:
    kwargs = {"headers": headers} if headers else {}
    resp = crawling_manager_client.remove_all(auth=False, **kwargs)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_remove_all_as_member_is_forbidden_and_removes_nothing(
    crawling_manager_client: CrawlingManagerClient,
    scheduled_connector: SeededConnector,
    second_user: SecondUser,
) -> None:
    resp = request_as(second_user, "DELETE", "/schedule/all")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    survivor = crawling_manager_client.get_schedule(
        scheduled_connector.connector_type, scheduled_connector.connector_id
    )
    assert survivor.status_code == 200, (
        f"a refused member call must leave schedules in place: {survivor.text[:500]}"
    )
