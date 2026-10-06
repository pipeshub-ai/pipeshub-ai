"""Strict OpenAPI audit of GET /api/v1/connectors/navigate."""

from __future__ import annotations

from typing import Any

import pytest
from connectors_audit_support import (
    ConnectorsAuditClient,
    KbRecords,
    bearer,
    request_as,
    spec_query_value_errors,
)
from helper.second_user import SecondUser
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/navigate"


def test_navigate_root_lists_reachable_records(
    connectors_client: ConnectorsAuditClient, kb_records: KbRecords
) -> None:
    # limit=200 so other agents' records cannot push the two seeded ones off the page.
    resp = connectors_client.navigate(limit=200)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["current"] is None
    assert body["breadcrumbs"] == []
    assert isinstance(body["text"], str)
    assert body["pagination"]["page"] == 1
    assert body["pagination"]["limit"] == 200
    assert body["pagination"]["has_prev"] is False
    if not body["pagination"]["has_next"]:
        rows = {row["id"]: row for row in body["rows"]}
        row = rows[kb_records["text_record_id"]]
        assert row["node_type"] == "record"
        assert row["is_record"] is True
        assert row["level"] == 1


def test_navigate_into_a_knowledge_base_and_its_record(
    connectors_client: ConnectorsAuditClient, kb_records: KbRecords
) -> None:
    opened_kb = connectors_client.navigate(kb_records["kb_id"])
    assert opened_kb.status_code == 200, opened_kb.text[:500]
    assert_strict_openapi_exchange(opened_kb, ROUTE)
    assert opened_kb.json()["current"] == {
        "id": kb_records["kb_id"],
        "name": opened_kb.json()["current"]["name"],
        "node_type": "app",
        "sub_type": "KB",
        "is_record": False,
    }
    assert opened_kb.json()["connector"] == "KB"
    # The records sit two levels below the knowledge base node, so depth 1 lists none.
    assert opened_kb.json()["rows"] == []
    assert opened_kb.json()["pagination"]["total"] == 0

    opened_record = connectors_client.navigate(kb_records["text_record_id"], depth=3)
    assert opened_record.status_code == 200, opened_record.text[:500]
    assert_strict_openapi_exchange(opened_record, ROUTE)
    body = opened_record.json()
    assert body["current"]["id"] == kb_records["text_record_id"]
    assert body["current"]["is_record"] is True
    assert [crumb["id"] for crumb in body["breadcrumbs"]] == [kb_records["kb_id"]]
    assert body["web_url"] == f"/record/{kb_records['text_record_id']}"
    assert body["indexing_status"] == kb_records["text_record_status"]
    assert kb_records["text_record_id"] in body["context_block"]


def test_navigate_into_a_never_synced_connector_is_an_empty_view(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    # Even its creator has no permission edge to the app node of an instance that never
    # synced, and the route does not pass the caller's connector ids that would let the
    # navigator open it anyway, so the admin gets the not-found view.
    resp = connectors_client.navigate(
        connector_id,
        page=1,
        limit=50,
        depth=2,
        node_types=["recordGroup", "record"],
        created_after="2020-01-01",
        created_before="2099-12-31T23:59:59+05:30",
        modified_after="2020-01-01T00:00:00Z",
        modified_before="2099-12-31",
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["current"] is None
    assert body["rows"] == []
    assert body["pagination"] is None


def _row_ids(resp_body: dict[str, Any]) -> set[str]:
    return {row["id"] for row in resp_body["rows"]}


def test_navigate_node_types_filter_only_by_the_types_it_knows(
    connectors_client: ConnectorsAuditClient, kb_records: KbRecords
) -> None:
    # Inside the throwaway knowledge base, so other agents' records cannot change the counts.
    # Its records sit two levels below the app node, so depth 1 would list nothing.
    records = {kb_records["text_record_id"], kb_records["unsupported_record_id"]}

    # Sent once it is still read as a list of one. A type the store does not know adds no
    # condition at all, so the listing is not narrowed.
    unknown = connectors_client.get(
        "/navigate", params={"nodeId": kb_records["kb_id"], "depth": 2, "nodeTypes": "specAuditNoSuchType"}
    )
    assert unknown.status_code == 200, unknown.text[:500]
    assert_strict_openapi_exchange(unknown, ROUTE)
    assert not spec_query_value_errors("GET", ROUTE, "nodeTypes", ["specAuditNoSuchType"])
    assert _row_ids(unknown.json()) == records, unknown.text[:500]

    only_records = connectors_client.navigate(kb_records["kb_id"], depth=2, node_types=["record"])
    assert only_records.status_code == 200, only_records.text[:500]
    assert_strict_openapi_exchange(only_records, ROUTE)
    assert _row_ids(only_records.json()) == records, only_records.text[:500]

    groups_only = connectors_client.navigate(kb_records["kb_id"], depth=2, node_types=["recordGroup"])
    assert groups_only.status_code == 200, groups_only.text[:500]
    assert_strict_openapi_exchange(groups_only, ROUTE)
    assert groups_only.json()["rows"] == [], groups_only.text[:500]


def test_navigate_to_a_node_that_does_not_exist_is_an_empty_view(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.navigate("spec-audit-no-such-node")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["current"] is None
    assert body["rows"] == []
    assert body["pagination"] is None


def test_member_cannot_tell_another_users_record_from_a_missing_one(
    second_user: SecondUser, kb_records: KbRecords
) -> None:
    resp = request_as(
        second_user, "GET", "/navigate", params={"nodeId": kb_records["text_record_id"]}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["current"] is None
    assert body["rows"] == []
    assert body["pagination"] is None


def test_navigate_ignores_an_unknown_query_parameter(
    connectors_client: ConnectorsAuditClient,
) -> None:
    with outside_request_contract("the validator strips query parameters it does not know"):
        resp = connectors_client.get("/navigate", params={"specAuditUnknown": "1"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"limit": 10}, id="limit-below-floor"),
        pytest.param({"limit": 201}, id="limit-above-max"),
        pytest.param({"limit": ""}, id="limit-empty"),
        pytest.param({"page": 0}, id="page-zero"),
        pytest.param({"page": "abc"}, id="page-not-a-number"),
        pytest.param({"depth": 0}, id="depth-zero"),
        pytest.param({"depth": 4}, id="depth-above-max"),
        pytest.param({"depth": "x"}, id="depth-not-a-number"),
        pytest.param({"nodeId": ""}, id="node-id-empty"),
        # Query values are trimmed before validation, so a blank one is an empty one.
        pytest.param({"nodeId": " "}, id="node-id-blank"),
        pytest.param({"nodeId": "x" * 2049}, id="node-id-too-long"),
        pytest.param({"createdAfter": ""}, id="created-after-empty"),
        pytest.param({"createdBefore": ""}, id="created-before-empty"),
        pytest.param({"modifiedAfter": ""}, id="modified-after-empty"),
        pytest.param({"modifiedBefore": ""}, id="modified-before-empty"),
        pytest.param([("nodeId", "a"), ("nodeId", "b")], id="node-id-repeated"),
    ],
)
def test_navigate_query_refused_by_the_validator(
    connectors_client: ConnectorsAuditClient, params: Any
) -> None:
    resp = connectors_client.get("/navigate", params=params)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"createdAfter": "2024-01-15T08:00:00"}, id="created-after-without-timezone"),
        pytest.param({"createdBefore": "not-a-date"}, id="created-before-not-a-date"),
        pytest.param({"modifiedAfter": "15/01/2024"}, id="modified-after-not-iso"),
        pytest.param({"modifiedBefore": "2024-01-15 08:00"}, id="modified-before-without-timezone"),
    ],
)
def test_navigate_date_the_time_parser_refuses(
    connectors_client: ConnectorsAuditClient, params: dict[str, str]
) -> None:
    # Any non-empty string passes the Node validator; the Python parser is what refuses these.
    resp = connectors_client.get("/navigate", params=params)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    ("params", "message"),
    [
        pytest.param({"createdAfter": "2999-01-01"}, "cannot be in the future", id="created-after-in-the-future"),
        pytest.param(
            {"createdAfter": "2024-02-01", "createdBefore": "2024-01-01"},
            "inverted range",
            id="created-range-inverted",
        ),
        pytest.param(
            {"modifiedAfter": "2024-02-01", "modifiedBefore": "2024-01-01"},
            "inverted range",
            id="modified-range-inverted",
        ),
        pytest.param({"createdAfter": "2024-02-31"}, "invalid calendar date", id="date-not-on-the-calendar"),
    ],
)
def test_navigate_well_formed_dates_that_make_no_sense_are_bad_request(
    connectors_client: ConnectorsAuditClient, params: dict[str, str], message: str
) -> None:
    # Each value is a valid date string on its own, so no parameter schema can refuse these.
    resp = connectors_client.get("/navigate", params=params)
    assert resp.status_code == 400, resp.text[:500]
    assert message in resp.json()["error"]["message"], resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_navigate_without_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.navigate(auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_navigate_with_neither_read_scope_is_forbidden(
    connectors_client: ConnectorsAuditClient, token_without_connector_scopes: str
) -> None:
    resp = connectors_client.navigate(auth=False, headers=bearer(token_without_connector_scopes))
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
