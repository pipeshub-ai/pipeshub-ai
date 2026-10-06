"""Strict OpenAPI audit of DELETE /api/v1/connectors/:connectorId."""

from __future__ import annotations

from typing import Iterator

import pytest
from connectors_audit_support import (
    MALFORMED_CONNECTOR_ID,
    MISSING_CONNECTOR_ID,
    UNSAFE_CONNECTOR_ID,
    ConnectorsAuditClient,
    SeedConnector,
    bearer,
    request_as,
    unique_name,
    wait_until_deleted,
)
from helper.clients.agents_client import AgentsClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/:connectorId"
LLM_MODELS_PATH = "/api/v1/configurationManager/ai-models/llm"


@pytest.fixture
def agent_using(pipeshub_client: PipeshubClient) -> Iterator[SeedConnector]:
    """Factory: an agent with one connector as its knowledge source, deleted on teardown."""
    agents = AgentsClient(pipeshub_client)
    created: list[str] = []

    def _create(connector_id: str) -> str:
        models = pipeshub_client.request("GET", LLM_MODELS_PATH)
        assert models.status_code == 200, models.text[:300]
        configured = models.json().get("models") or []
        assert configured, "no LLM is configured on the stack, so no agent can be created"
        model = configured[0]
        name = unique_name("spec-audit-agent")
        resp = agents.create_agent(
            name=name,
            models=[
                {
                    "modelKey": model["modelKey"],
                    "modelName": model["configuration"]["model"],
                    "provider": model["provider"],
                    "isReasoning": True,
                }
            ],
            knowledge=[{"connectorId": connector_id, "filters": {}}],
        )
        assert resp.status_code < 300, f"agent create failed: {resp.status_code} {resp.text[:300]}"
        created.append(str(resp.json()["agent"]["_key"]))
        return name

    try:
        yield _create
    finally:
        for agent_key in created:
            agents.delete_agent(agent_key)


def test_admin_deletes_a_team_instance(
    connectors_client: ConnectorsAuditClient, seed_connector: SeedConnector
) -> None:
    connector_id = seed_connector()

    resp = connectors_client.delete(f"/{connector_id}")
    # Accepted, not done: the graph and vector cleanup runs in the sync consumer.
    assert resp.status_code == 202, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "success": True,
        "message": "Connector deletion initiated",
        "connectorId": connector_id,
        "status": "DELETING",
    }

    wait_until_deleted(connectors_client, connector_id)
    # Once gone, a repeat is a 404 like any unknown id.
    repeat = connectors_client.delete(f"/{connector_id}")
    assert repeat.status_code == 404, repeat.text[:500]
    assert_strict_openapi_exchange(repeat, ROUTE)


def test_member_deletes_own_personal_instance(
    second_user: SecondUser, member_connector: SeedConnector
) -> None:
    connector_id = member_connector()

    resp = request_as(second_user, "DELETE", f"/{connector_id}")
    assert resp.status_code == 202, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["connectorId"] == connector_id


def test_admin_deletes_a_members_personal_instance(
    connectors_client: ConnectorsAuditClient, member_connector: SeedConnector
) -> None:
    # The admin cannot read another user's personal instance (GET is 404) but may delete it.
    connector_id = member_connector()
    assert connectors_client.get(f"/{connector_id}").status_code == 404

    resp = connectors_client.delete(f"/{connector_id}")
    assert resp.status_code == 202, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_ignores_a_request_body_and_query(
    connectors_client: ConnectorsAuditClient, seed_connector: SeedConnector
) -> None:
    connector_id = seed_connector()

    with outside_request_contract("the route validates only the path parameter"):
        resp = connectors_client.delete(
            f"/{connector_id}", params={"force": "true"}, json={"force": True}
        )
        assert resp.status_code == 202, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_of_an_instance_an_agent_uses_is_a_conflict(
    connectors_client: ConnectorsAuditClient,
    seed_connector: SeedConnector,
    agent_using: SeedConnector,
) -> None:
    connector_id = seed_connector()
    agent_name = agent_using(connector_id)

    resp = connectors_client.delete(f"/{connector_id}")
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert agent_name in resp.json()["error"]["message"], resp.text[:500]
    # Refused before anything was changed.
    assert connectors_client.get(f"/{connector_id}").json()["connector"]["status"] is None


def test_member_is_told_an_admin_team_instance_does_not_exist(
    second_user: SecondUser, connector_id: str, connectors_client: ConnectorsAuditClient
) -> None:
    # Not creator and not admin: a 404, never a 403.
    resp = request_as(second_user, "DELETE", f"/{connector_id}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert connectors_client.get(f"/{connector_id}").status_code == 200


@pytest.mark.parametrize(
    ("requested_id", "expected_status"),
    [
        pytest.param(MISSING_CONNECTOR_ID, 404, id="unknown-connector"),
        pytest.param(MALFORMED_CONNECTOR_ID, 400, id="id-fails-param-schema"),
        pytest.param(UNSAFE_CONNECTOR_ID, 400, id="unsafe-path-segment"),
    ],
)
def test_delete_for_unusable_connector_id(
    connectors_client: ConnectorsAuditClient, requested_id: str, expected_status: int
) -> None:
    resp = connectors_client.delete(f"/{requested_id}")
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_without_a_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.delete(f"/{connector_id}", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_without_the_connector_delete_scope_is_forbidden(
    connectors_client: ConnectorsAuditClient,
    connector_id: str,
    token_without_connector_scopes: str,
) -> None:
    resp = connectors_client.delete(
        f"/{connector_id}", auth=False, headers=bearer(token_without_connector_scopes)
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert connectors_client.get(f"/{connector_id}").status_code == 200
