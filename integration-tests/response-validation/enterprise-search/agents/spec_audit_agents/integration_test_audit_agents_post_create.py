"""Strict OpenAPI audit of POST /api/v1/agents/create."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from agents_audit_support import (
    JSON_HEADERS,
    AgentsAuditClient,
    TrackAgent,
    error_of,
    reasoning_model_entry,
    unique_agent_name,
)
from ai_models_setup import SeededAIModel
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/create"


def test_name_only_creates_an_agent_on_the_org_default_model(
    agents_audit_client: AgentsAuditClient, track_agent: TrackAgent
) -> None:
    name = unique_agent_name()

    resp = agents_audit_client.create_agent({"name": f"  {name}  "})

    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    track_agent(resp)
    agent = resp.json()["agent"]
    assert agent["name"] == name
    assert agent["models"] == []
    assert agent["isDeleted"] is False


def test_full_configuration_is_stored(
    agents_audit_client: AgentsAuditClient,
    track_agent: TrackAgent,
    reasoning_multimodal_llm_model: SeededAIModel,
) -> None:
    server_type = f"spec-audit-type-{uuid.uuid4().hex[:8]}"
    body: dict[str, Any] = {
        "name": unique_agent_name(),
        "description": "Agent made by the spec audit.",
        "startMessage": "Hi.",
        "systemPrompt": "Answer briefly.",
        "instructions": "Be terse.",
        "models": [reasoning_model_entry(reasoning_multimodal_llm_model)],
        "tags": ["spec-audit"],
        "shareWithOrg": False,
        "isServiceAccount": False,
        "skills": [],
        "knowledge": [],
        "toolsets": [],
        "mcpServers": [
            {
                "instanceId": str(uuid.uuid4()),
                "name": "spec_audit_server",
                "displayName": "Spec audit server",
                "typeId": server_type,
            }
        ],
        "webSearch": {"provider": "duckduckgo"},
        "defaultReasoningEffort": "low",
        "sendUserContext": False,
    }

    resp = agents_audit_client.create_agent(body)

    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    track_agent(resp)
    agent = resp.json()["agent"]
    assert agent["tags"] == ["spec-audit"]
    assert agent["defaultReasoningEffort"] == "low"
    assert agent["sendUserContext"] is False
    assert agent["webSearch"] == {"provider": "duckduckgo"}
    assert [(s["name"], s["displayName"]) for s in agent["mcpServers"]] == [
        ("spec_audit_server", "Spec audit server")
    ]
    assert agent["models"] == [
        f"{reasoning_multimodal_llm_model.model_key}_{reasoning_multimodal_llm_model.model_name}"
    ]


def test_unknown_fields_are_dropped_not_refused(
    agents_audit_client: AgentsAuditClient, track_agent: TrackAgent
) -> None:
    body = {
        "name": unique_agent_name(),
        "unexpectedTopLevelField": "drop-me",
        "webSearch": {"provider": "duckduckgo", "unexpectedWebSearchField": "drop-me"},
        "knowledge": [],
    }

    with outside_request_contract("unknown fields are stripped by the gateway validator"):
        resp = agents_audit_client.create_agent(body)
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 201, resp.text[:500]
    track_agent(resp)
    assert "unexpectedTopLevelField" not in resp.json()["agent"]


def test_toolset_name_with_surrounding_spaces_is_trimmed(
    agents_audit_client: AgentsAuditClient, track_agent: TrackAgent
) -> None:
    body = {"name": unique_agent_name(), "toolsets": [{"name": "  slack  "}]}

    with outside_request_contract("the toolset name is trimmed before the enum check"):
        resp = agents_audit_client.create_agent(body)
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 201, resp.text[:500]
    track_agent(resp)


_MODEL = {"modelKey": "spec-audit-model", "isReasoning": True}

REFUSED_BODIES = [
    pytest.param({}, "body.name", id="missing-name"),
    pytest.param({"name": "   "}, "body.name", id="blank-name"),
    pytest.param({"name": "x" * 201}, "body.name", id="overlong-name"),
    pytest.param({"name": 42}, "body.name", id="name-not-string"),
    pytest.param({"models": ["spec-audit-model"]}, "body.models", id="string-models-only"),
    pytest.param(
        {"models": [{"modelKey": "spec-audit-model", "isReasoning": False}]},
        "body.models",
        id="no-reasoning-model",
    ),
    pytest.param({"models": [{"modelName": "m", "isReasoning": True}]}, "body.models.0", id="model-without-key"),
    pytest.param({"models": [""]}, "body.models.0", id="model-empty-string"),
    pytest.param({"toolsets": [{"name": "spec-audit-no-such-toolset"}]}, "body.toolsets.0.name", id="unregistered-toolset"),
    pytest.param({"tags": ["t"] * 51}, "body.tags", id="too-many-tags"),
    pytest.param({"skills": [{"name": "Not A Slug"}]}, "body.skills.0.name", id="skill-name-not-slug"),
    pytest.param({"knowledge": [{"filters": {}}]}, "body.knowledge.0.connectorId", id="knowledge-without-connector"),
    pytest.param({"knowledge": [{"connectorId": ""}]}, "body.knowledge.0.connectorId", id="knowledge-empty-connector"),
    pytest.param({"knowledge": [{"connectorId": "  "}]}, "body.knowledge.0.connectorId", id="knowledge-blank-connector"),
    pytest.param({"models": [{"modelKey": "  ", "isReasoning": True}]}, "body.models.0", id="model-blank-key"),
    pytest.param({"models": ["  ", _MODEL]}, "body.models.0", id="model-blank-string"),
    pytest.param({"webSearch": ""}, "body.webSearch", id="web-search-empty-string"),
    pytest.param({"webSearch": "  "}, "body.webSearch", id="web-search-blank-string"),
    pytest.param({"webSearch": {"provider": " "}}, "body.webSearch", id="web-search-blank-provider"),
    pytest.param(
        {"toolsets": [{"name": "slack", "tools": [{"name": " "}]}]},
        "body.toolsets.0.tools.0.name",
        id="tool-blank-name",
    ),
    pytest.param(
        {"toolsets": [{"name": "slack", "tools": [{"name": "t", "fullName": " "}]}]},
        "body.toolsets.0.tools.0.fullName",
        id="tool-blank-full-name",
    ),
    pytest.param({"webSearch": {"providerKey": "k"}}, "body.webSearch", id="web-search-without-provider"),
    pytest.param({"defaultReasoningEffort": "extreme"}, "body.defaultReasoningEffort", id="reasoning-effort-unknown"),
    pytest.param({"mcpServers": [{"instanceId": "i-1"}]}, "body.mcpServers.0.name", id="mcp-server-without-name"),
    pytest.param({"mcpServers": "spec-audit"}, "body.mcpServers", id="mcp-servers-not-array"),
    pytest.param({"shareWithOrg": "yes"}, "body.shareWithOrg", id="share-not-boolean"),
]


@pytest.mark.parametrize(("override", "field"), REFUSED_BODIES)
def test_invalid_body_is_refused_by_the_validator(
    agents_audit_client: AgentsAuditClient, override: dict[str, Any], field: str
) -> None:
    body = {"name": unique_agent_name(), "models": [_MODEL], **override}
    if override == {}:
        body.pop("name")

    resp = agents_audit_client.create_agent(body)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = error_of(resp)
    assert error["code"] == "VALIDATION_ERROR", resp.text[:500]
    fields = [e["field"] for e in error["metadata"]["errors"]]
    assert any(f.startswith(field) for f in fields), fields


def test_two_mcp_servers_of_one_type_are_refused(agents_audit_client: AgentsAuditClient) -> None:
    servers = [
        {"instanceId": "spec-audit-a", "name": "a", "typeId": "spec-audit-type"},
        {"instanceId": "spec-audit-b", "name": "b", "typeId": "spec-audit-type"},
    ]

    with outside_request_contract("JSON Schema cannot say that typeId must be unique across items"):
        resp = agents_audit_client.create_agent({"name": unique_agent_name(), "mcpServers": servers})
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 400, resp.text[:500]
    fields = [e["field"] for e in error_of(resp)["metadata"]["errors"]]
    assert fields == ["body.mcpServers.1.typeId"]


def test_no_body_is_refused(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.post("/create", data="", headers=JSON_HEADERS)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert error_of(resp)["code"] == "VALIDATION_ERROR"


def test_without_token_is_unauthorized(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.create_agent({"name": unique_agent_name()}, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_agent_write_scope_is_forbidden(
    agents_audit_client: AgentsAuditClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = agents_audit_client.create_agent(
        {"name": unique_agent_name()}, auth=False, headers=narrow_scope_headers
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
