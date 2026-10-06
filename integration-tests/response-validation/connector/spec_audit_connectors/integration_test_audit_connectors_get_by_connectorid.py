"""Strict OpenAPI audit of GET /api/v1/connectors/:connectorId."""

from __future__ import annotations

import pytest
from connectors_audit_support import (
    MALFORMED_CONNECTOR_ID,
    MISSING_CONNECTOR_ID,
    PERSONAL_CONNECTOR_TYPE,
    SEED_CONNECTOR_TYPE,
    UNSAFE_CONNECTOR_ID,
    ConnectorsAuditClient,
    KbRecords,
    SeedConnector,
    bearer,
    created_connector_id,
    request_as,
    seed_body,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/:connectorId"


def test_admin_reads_a_team_instance_with_its_setup_form(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.get(f"/{connector_id}")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    connector = body["connector"]
    assert connector["_key"] == connector_id
    assert connector["type"] == SEED_CONNECTOR_TYPE
    assert connector["scope"] == "team"
    assert connector["authType"] == "NONE"
    assert connector["isActive"] is False
    assert connector["isLocked"] is False
    assert connector["status"] is None
    assert set(connector["config"]) >= {"auth", "sync", "filters"}
    # Promoted to the instance, so not repeated inside config.
    assert "documentationLinks" in connector
    assert "documentationLinks" not in connector["config"]
    # No configuration was saved, so the stored-auth URLs are not merged in.
    assert "authorizeUrl" not in connector["config"]["auth"]


@pytest.mark.parametrize("scope", ["team", "personal"])
def test_instance_of_every_registered_type_matches_the_spec(
    connectors_client: ConnectorsAuditClient, cleanup_connectors: list[str], scope: str
) -> None:
    # config is the type's setup form, different for every connector; one type would
    # leave most of it unobserved. Nothing is configured, so nothing external is contacted.
    listing = connectors_client.get("/registry", params={"scope": scope, "limit": 200})
    assert listing.status_code == 200, listing.text[:500]
    types = [entry["type"] for entry in listing.json()["connectors"]]
    assert types, f"the {scope} registry is empty"

    problems: list[str] = []
    for connector_type in types:
        body = seed_body(connectorType=connector_type, scope=scope)
        del body["authType"]
        created = connectors_client.create_instance(**body)
        if created.status_code != 200:
            problems.append(f"{connector_type}: create {created.status_code} {created.text[:200]}")
            continue
        connector_id = created_connector_id(created)
        cleanup_connectors.append(connector_id)
        resp = connectors_client.get(f"/{connector_id}")
        if resp.status_code != 200:
            problems.append(f"{connector_type}: HTTP {resp.status_code} {resp.text[:200]}")
            continue
        try:
            assert_strict_openapi_exchange(resp, ROUTE)
        except AssertionError as error:
            problems.append(f"{connector_type}: {error}")
    assert not problems, "\n".join(problems)


def test_instance_with_saved_config_carries_the_stored_auth_urls(
    connectors_client: ConnectorsAuditClient, seed_connector: SeedConnector
) -> None:
    connector_id = seed_connector(config={"sync": {"selectedStrategy": "MANUAL"}})

    resp = connectors_client.get(f"/{connector_id}")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    auth = resp.json()["connector"]["config"]["auth"]
    # Demo has no OAuth flow, so both are merged in as empty strings.
    assert auth["authorizeUrl"] == ""
    assert auth["tokenUrl"] == ""
    assert auth["oauthConfigs"] == {"NONE": {"authorizeUrl": "", "tokenUrl": ""}}


def test_member_reads_own_personal_instance(
    second_user: SecondUser, member_connector: SeedConnector
) -> None:
    connector_id = member_connector()

    resp = request_as(second_user, "GET", f"/{connector_id}")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    connector = resp.json()["connector"]
    assert connector["type"] == PERSONAL_CONNECTOR_TYPE
    assert connector["scope"] == "personal"
    assert connector["createdBy"] == second_user.user_id
    assert "OAUTH" in connector["config"]["auth"]["oauthConfigs"]


def test_knowledge_base_id_is_readable_as_a_connector_instance(
    connectors_client: ConnectorsAuditClient, kb_records: KbRecords
) -> None:
    # A knowledge base is stored as a connector instance of type KB.
    resp = connectors_client.get(f"/{kb_records['kb_id']}")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["connector"]["type"] == "KB"


def test_instance_ignores_query_parameters(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    with outside_request_contract("the route validates only the path parameter"):
        resp = connectors_client.get(f"/{connector_id}", params={"scope": "nonsense"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_told_an_admin_team_instance_does_not_exist(
    second_user: SecondUser, connector_id: str
) -> None:
    # Members see team instances in the lists, yet reading one is creator/admin only,
    # and the refusal is a 404 rather than a 403.
    resp = request_as(second_user, "GET", f"/{connector_id}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_admin_is_told_a_members_personal_instance_does_not_exist(
    connectors_client: ConnectorsAuditClient, member_connector: SeedConnector
) -> None:
    resp = connectors_client.get(f"/{member_connector()}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("requested_id", "expected_status"),
    [
        pytest.param(MISSING_CONNECTOR_ID, 404, id="unknown-connector"),
        pytest.param(MALFORMED_CONNECTOR_ID, 400, id="id-fails-param-schema"),
        pytest.param("x" * 65, 400, id="id-longer-than-64"),
        pytest.param(UNSAFE_CONNECTOR_ID, 400, id="unsafe-path-segment"),
    ],
)
def test_instance_for_unusable_connector_id(
    connectors_client: ConnectorsAuditClient, requested_id: str, expected_status: int
) -> None:
    resp = connectors_client.get(f"/{requested_id}")
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_instance_without_a_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.get(f"/{connector_id}", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_instance_without_the_connector_read_scope_is_forbidden(
    connectors_client: ConnectorsAuditClient,
    connector_id: str,
    token_without_connector_scopes: str,
) -> None:
    resp = connectors_client.get(
        f"/{connector_id}", auth=False, headers=bearer(token_without_connector_scopes)
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
