"""Strict OpenAPI audit of GET /api/v1/connectors/record/:recordId/content."""

from __future__ import annotations

import pytest
from connectors_audit_support import (
    MISSING_CONNECTOR_ID,
    UNSAFE_CONNECTOR_ID,
    ConnectorsAuditClient,
    KbRecords,
    bearer,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/record/:recordId/content"
NOT_AVAILABLE = "No record found"


def test_content_of_an_indexed_record_is_its_parsed_text(
    connectors_client: ConnectorsAuditClient, kb_records: KbRecords
) -> None:
    if kb_records["text_record_status"] != "COMPLETED":
        pytest.fail(
            "the text record did not finish indexing, so its parsed content cannot be read: "
            f"indexingStatus={kb_records['text_record_status']!r} "
            f"reason={kb_records['text_record_reason']!r}"
        )

    resp = connectors_client.get_record_content(kb_records["text_record_id"])
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert set(body) == {"content"}
    assert kb_records["text_record_id"] in body["content"]
    assert kb_records["text_sentinel"] in body["content"]


def test_content_of_a_record_that_was_not_indexed_is_a_200_placeholder(
    connectors_client: ConnectorsAuditClient, kb_records: KbRecords
) -> None:
    # The file type is not supported, so nothing was parsed: still a 200, with a fixed string.
    resp = connectors_client.get_record_content(kb_records["unsupported_record_id"])
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"content": NOT_AVAILABLE}


def test_content_ignores_query_parameters(
    connectors_client: ConnectorsAuditClient, kb_records: KbRecords
) -> None:
    with outside_request_contract("the route validates only the path parameter"):
        resp = connectors_client.get_record_content(
            kb_records["unsupported_record_id"], params={"format": "html"}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"content": NOT_AVAILABLE}


def test_member_without_access_to_the_record_is_forbidden(
    second_user: SecondUser, kb_records: KbRecords
) -> None:
    resp = request_as(second_user, "GET", f"/record/{kb_records['text_record_id']}/content")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_record_is_forbidden_not_missing(
    connectors_client: ConnectorsAuditClient,
) -> None:
    # The access check cannot tell a missing record from an inaccessible one, so existence
    # is never leaked: there is no 404 on this route.
    resp = connectors_client.get_record_content(MISSING_CONNECTOR_ID)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_content_without_the_connector_read_scope_is_forbidden(
    connectors_client: ConnectorsAuditClient,
    kb_records: KbRecords,
    token_without_connector_scopes: str,
) -> None:
    resp = connectors_client.get_record_content(
        kb_records["text_record_id"],
        auth=False,
        headers=bearer(token_without_connector_scopes),
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    # A record-access denial is a 403 too, so pin the scope check specifically.
    assert "scope" in resp.json()["error"]["message"].lower(), resp.text[:500]


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param(None, id="no-token"),
        pytest.param({"Authorization": "Bearer not.a.jwt"}, id="invalid-token"),
    ],
)
def test_content_without_a_valid_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient,
    kb_records: KbRecords,
    headers: dict[str, str] | None,
) -> None:
    resp = connectors_client.get_record_content(
        kb_records["text_record_id"], auth=False, headers=headers
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_content_with_an_unsafe_record_id_is_400_before_auth(
    connectors_client: ConnectorsAuditClient,
) -> None:
    # guardPathParams is a router.param hook, so it answers ahead of authenticate.
    resp = connectors_client.get(f"/record/{UNSAFE_CONNECTOR_ID}/content", auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
