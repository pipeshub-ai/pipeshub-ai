"""Strict OpenAPI audit of GET /api/v1/connectors."""

from __future__ import annotations

from typing import Any

import pytest
from connectors_audit_support import (
    INVALID_LIST_IDS,
    INVALID_LIST_QUERIES,
    PERSONAL_CONNECTOR_TYPE,
    SEED_CONNECTOR_TYPE,
    ConnectorsAuditClient,
    KbRecords,
    SeedConnector,
    bearer,
    request_as,
    unique_name,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors"


def _ids(body: dict[str, Any]) -> set[str]:
    return {instance["_key"] for instance in body["connectors"]}


def test_admin_lists_team_instances(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    # No scope: the validator defaults it to team.
    resp = connectors_client.get("/", params={"limit": 200})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    assert {instance["scope"] for instance in body["connectors"]} == {"team"}
    seeded = next(c for c in body["connectors"] if c["_key"] == connector_id)
    assert seeded["type"] == SEED_CONNECTOR_TYPE
    assert seeded["isActive"] is False
    # The setup form is only returned by GET /connectors/{connectorId}.
    assert "config" not in seeded
    pagination = body["pagination"]
    assert pagination["page"] == 1
    assert pagination["limit"] == 200
    assert pagination["totalCount"] >= len(body["connectors"])
    # Unlike the registry and the other lists, this one does not echo the search term.
    assert "search" not in pagination


@pytest.mark.parametrize(
    ("params", "listed"),
    [
        pytest.param({"isActive": "false"}, True, id="inactive-only"),
        pytest.param({"isActive": "true"}, False, id="active-only"),
        pytest.param({"isAuthenticated": "false"}, True, id="unauthenticated-only"),
        pytest.param({"isAuthenticated": "true"}, False, id="authenticated-only"),
        pytest.param({"connectorType": SEED_CONNECTOR_TYPE}, True, id="same-type"),
        pytest.param({"connectorType": SEED_CONNECTOR_TYPE.lower()}, False, id="type-is-case-sensitive"),
        pytest.param({"scope": "team"}, True, id="team-scope"),
        pytest.param({"scope": "personal"}, False, id="personal-scope"),
    ],
)
def test_filters_decide_whether_an_unsynced_instance_is_listed(
    connectors_client: ConnectorsAuditClient,
    seed_connector: SeedConnector,
    params: dict[str, str],
    listed: bool,
) -> None:
    # The search narrows the listing to this instance, so other agents' connectors
    # cannot push it off the page.
    name = unique_name("spec-audit-list")
    connector_id = seed_connector(instanceName=name)

    resp = connectors_client.get("/", params={"search": name, **params})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert (connector_id in _ids(resp.json())) is listed, resp.text[:500]


def test_list_pages_through_matching_instances(
    connectors_client: ConnectorsAuditClient, seed_connector: SeedConnector
) -> None:
    token = unique_name("spec-audit-page")
    seeded = {seed_connector(instanceName=f"{token} {index}") for index in range(3)}

    seen: set[str] = set()
    for page in (1, 2, 3):
        resp = connectors_client.get("/", params={"search": token, "limit": 1, "page": page})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        body = resp.json()
        assert len(body["connectors"]) == 1
        pagination = body["pagination"]
        assert pagination["totalCount"] == 3
        assert pagination["totalPages"] == 3
        assert pagination["hasPrev"] is (page > 1)
        assert pagination["prevPage"] == (page - 1 if page > 1 else None)
        assert pagination["hasNext"] is (page < 3)
        assert pagination["nextPage"] == (page + 1 if page < 3 else None)
        seen |= _ids(body)
    assert seen == seeded


def test_member_lists_own_personal_instances(
    second_user: SecondUser, member_connector: SeedConnector
) -> None:
    connector_id = member_connector()

    resp = request_as(second_user, "GET", "/", params={"scope": "personal"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    listed = {c["_key"]: c for c in resp.json()["connectors"]}
    assert connector_id in listed
    assert listed[connector_id]["type"] == PERSONAL_CONNECTOR_TYPE
    assert listed[connector_id]["scope"] == "personal"
    assert listed[connector_id]["createdBy"] == second_user.user_id


def test_admin_does_not_see_another_users_personal_instance(
    connectors_client: ConnectorsAuditClient, member_connector: SeedConnector
) -> None:
    connector_id = member_connector()

    resp = connectors_client.get("/", params={"scope": "personal", "limit": 200})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert connector_id not in _ids(resp.json())


@pytest.mark.parametrize("scope", ["team", "personal"])
def test_knowledge_bases_are_not_listed(
    connectors_client: ConnectorsAuditClient, kb_records: KbRecords, scope: str
) -> None:
    # A knowledge base is an instance of type KB, readable by id, but left out of this list.
    resp = connectors_client.get("/", params={"scope": scope, "limit": 200})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert kb_records["kb_id"] not in _ids(resp.json())
    assert "KB" not in {instance["type"] for instance in resp.json()["connectors"]}


def test_list_ignores_an_unknown_query_parameter(
    connectors_client: ConnectorsAuditClient,
) -> None:
    with outside_request_contract("the validator strips query parameters it does not know"):
        resp = connectors_client.get("/", params={"specAuditUnknown": "1"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("params", INVALID_LIST_QUERIES, ids=INVALID_LIST_IDS)
def test_list_rejects_invalid_query(
    connectors_client: ConnectorsAuditClient, params: Any
) -> None:
    resp = connectors_client.get("/", params=params)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_without_a_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.get("/", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_without_the_connector_read_scope_is_forbidden(
    connectors_client: ConnectorsAuditClient, token_without_connector_scopes: str
) -> None:
    resp = connectors_client.get("/", auth=False, headers=bearer(token_without_connector_scopes))
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
