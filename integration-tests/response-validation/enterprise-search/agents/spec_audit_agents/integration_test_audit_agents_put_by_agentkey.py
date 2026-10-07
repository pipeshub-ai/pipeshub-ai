"""Strict OpenAPI audit of PUT /api/v1/agents/:agentKey."""

from __future__ import annotations

from typing import Any

import pytest
from agents_audit_support import (
    JSON_HEADERS,
    MISSING_AGENT_KEY,
    UNSAFE_PATH_SEGMENT,
    AgentsAuditClient,
    MakeAgent,
    error_of,
    reasoning_model_entry,
    request_as,
    unique_agent_name,
)
from ai_models_setup import SeededAIModel
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/:agentKey"

UPDATED = {"status": "success", "message": "Agent updated successfully"}


def test_partial_update_changes_only_the_fields_sent(
    agents_audit_client: AgentsAuditClient, make_agent: MakeAgent
) -> None:
    key = make_agent(description="before")
    name = unique_agent_name("spec-audit-renamed")

    resp = agents_audit_client.update_agent(key, {"name": name, "tags": ["spec-audit"]})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == UPDATED
    agent = agents_audit_client.get_agent(key).json()["agent"]
    assert (agent["name"], agent["tags"], agent["description"]) == (name, ["spec-audit"], "before")


def test_models_can_be_set_and_cleared(
    agents_audit_client: AgentsAuditClient,
    make_agent: MakeAgent,
    reasoning_multimodal_llm_model: SeededAIModel,
) -> None:
    key = make_agent()

    set_resp = agents_audit_client.update_agent(
        key, {"models": [reasoning_model_entry(reasoning_multimodal_llm_model)]}
    )
    assert set_resp.status_code == 200, set_resp.text[:500]
    assert_strict_openapi_exchange(set_resp, ROUTE)
    assert len(agents_audit_client.get_agent(key).json()["agent"]["models"]) == 1

    cleared = agents_audit_client.update_agent(key, {"models": []})
    assert cleared.status_code == 200, cleared.text[:500]
    assert_strict_openapi_exchange(cleared, ROUTE)
    assert agents_audit_client.get_agent(key).json()["agent"]["models"] == []


@pytest.mark.parametrize("body", [{}, None], ids=["empty-object", "no-body"])
def test_empty_update_succeeds(
    agents_audit_client: AgentsAuditClient, make_agent: MakeAgent, body: dict[str, Any] | None
) -> None:
    key = make_agent()

    if body is None:
        resp = agents_audit_client.put(f"/{key}", data="", headers=JSON_HEADERS)
    else:
        resp = agents_audit_client.update_agent(key, body)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == UPDATED


def test_unknown_fields_and_query_parameters_are_ignored(
    agents_audit_client: AgentsAuditClient, make_agent: MakeAgent
) -> None:
    key = make_agent()
    body = {"description": "after", "unexpectedTopLevelField": "drop-me"}

    with outside_request_contract("unknown body fields are stripped and the query is not read"):
        resp = agents_audit_client.update_agent(key, body, params={"page": "1"})
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 200, resp.text[:500]
    agent = agents_audit_client.get_agent(key).json()["agent"]
    assert agent["description"] == "after"
    assert "unexpectedTopLevelField" not in agent


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({"name": "   "}, "body.name", id="blank-name"),
        pytest.param({"name": "x" * 201}, "body.name", id="overlong-name"),
        pytest.param({"models": ["spec-audit-model"]}, "body.models", id="string-models-only"),
        pytest.param(
            {"models": [{"modelKey": "spec-audit-model", "isReasoning": False}]},
            "body.models",
            id="no-reasoning-model",
        ),
        pytest.param({"toolsets": [{"name": "spec-audit-no-such-toolset"}]}, "body.toolsets.0.name", id="unregistered-toolset"),
        pytest.param({"mcpServers": [{"name": "s"}]}, "body.mcpServers.0.instanceId", id="mcp-server-without-instance"),
        pytest.param({"sendUserContext": "no"}, "body.sendUserContext", id="send-user-context-not-boolean"),
        pytest.param({"knowledge": [{"connectorId": ""}]}, "body.knowledge.0.connectorId", id="knowledge-empty-connector"),
        pytest.param({"knowledge": [{"connectorId": "  "}]}, "body.knowledge.0.connectorId", id="knowledge-blank-connector"),
        pytest.param({"models": [{"modelKey": "  ", "isReasoning": True}]}, "body.models.0", id="model-blank-key"),
        pytest.param({"webSearch": ""}, "body.webSearch", id="web-search-empty-string"),
        pytest.param({"webSearch": {"provider": " "}}, "body.webSearch", id="web-search-blank-provider"),
        pytest.param(
            {"toolsets": [{"name": "slack", "tools": [{"name": " "}]}]},
            "body.toolsets.0.tools.0.name",
            id="tool-blank-name",
        ),
    ],
)
def test_invalid_body_is_refused_by_the_validator(
    agents_audit_client: AgentsAuditClient, make_agent: MakeAgent, body: dict[str, Any], field: str
) -> None:
    key = make_agent()

    resp = agents_audit_client.update_agent(key, body)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = error_of(resp)
    assert error["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert any(e["field"].startswith(field) for e in error["metadata"]["errors"]), resp.text[:500]


def test_service_account_cannot_be_turned_back(
    agents_audit_client: AgentsAuditClient, make_agent: MakeAgent
) -> None:
    key = make_agent(isServiceAccount=True)

    resp = agents_audit_client.update_agent(key, {"isServiceAccount": False})

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert error_of(resp)["code"] == "HTTP_BAD_REQUEST"
    assert agents_audit_client.get_agent(key).json()["agent"]["isServiceAccount"] is True


def test_member_cannot_edit_an_org_shared_agent(
    agents_audit_client: AgentsAuditClient, second_user: SecondUser, make_agent: MakeAgent
) -> None:
    key = make_agent(shareWithOrg=True, description="kept")

    resp = request_as(second_user, "PUT", f"/{key}", json={"description": "changed"})

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert agents_audit_client.get_agent(key).json()["agent"]["description"] == "kept"


def test_member_gets_not_found_for_a_private_agent(
    second_user: SecondUser, make_agent: MakeAgent
) -> None:
    key = make_agent()

    resp = request_as(second_user, "PUT", f"/{key}", json={"description": "changed"})

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_agent_is_not_found(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.update_agent(MISSING_AGENT_KEY, {"description": "x"})

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unsafe_agent_key_is_rejected(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.update_agent(UNSAFE_PATH_SEGMENT, {"description": "x"})

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert error_of(resp)["code"] == "HTTP_BAD_REQUEST"


def test_without_token_is_unauthorized(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.update_agent(MISSING_AGENT_KEY, {"description": "x"}, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_agent_write_scope_is_forbidden(
    agents_audit_client: AgentsAuditClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = agents_audit_client.update_agent(
        MISSING_AGENT_KEY, {"description": "x"}, auth=False, headers=narrow_scope_headers
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
