"""Strict OpenAPI audit of POST /api/v1/connectors/vector-store/reindex.

Negative cases only: an admin call re-embeds every connector in the shared org.
"""

from __future__ import annotations

import pytest
from connectors_audit_support import ConnectorsAuditClient, request_as
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/vector-store/reindex"
PATH = "/vector-store/reindex"


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-token"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="garbage-token"),
    ],
)
def test_reindex_without_valid_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient, headers: dict[str, str]
) -> None:
    resp = connectors_client.post(PATH, auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_is_refused_by_the_admin_check(second_user: SecondUser) -> None:
    # userAdminCheck sits before requireScopes and the proxy, so nothing is re-embedded.
    resp = request_as(second_user, "POST", PATH)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
