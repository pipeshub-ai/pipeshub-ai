"""Agent integration tests.

``POST /api/v1/agents/create``
``GET /api/v1/agents``
``GET /api/v1/agents/{agentKey}``
``PUT /api/v1/agents/{agentKey}``
``DELETE /api/v1/agents/{agentKey}``

Exercises the Node gateway (and Python agent service for updates) with positive
and negative request shapes using the existing enterprise-search fixtures.
Update-agent tests assert request/response bodies against the ``updateAgent``
OpenAPI operation where applicable.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import requests

_ROOT = Path(__file__).resolve().parents[3]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

from ai_models_setup import SeededAIModel
from openapi_schema_validator import (
    assert_request_body_matches_openapi_operation,
    assert_response_matches_openapi_operation,
)
from pipeshub_client import PipeshubClient

logger = logging.getLogger(__name__)

_AGENTS_CREATE_PATH = "/api/v1/agents/create"
_AGENTS_LIST_PATH = "/api/v1/agents"
_AGENTS_DETAIL_PATH = "/api/v1/agents/{agent_key}"


def _valid_toolset_entry(
    *,
    name: str = "slack",
    tool_name: str = "send_message",
) -> dict[str, Any]:
    """OpenAPI-valid toolset entry (no UI-only fields such as ``id``)."""
    return {
        "name": name,
        "tools": [
            {
                "name": tool_name,
                "fullName": f"{name}.{tool_name}",
            },
        ],
    }


def _build_payload(
    *,
    name: str,
    seeded_model: SeededAIModel,
    description: str | None = None,
    start_message: str | None = None,
    system_prompt: str | None = None,
    tags: list[str] | None = None,
    share_with_org: bool | None = None,
    is_service_account: bool | None = None,
    knowledge: list[dict[str, Any]] | None = None,
    web_search: str | dict[str, Any] | None = None,
    toolsets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "models": [
            {
                "modelKey": seeded_model.model_key,
                "modelName": seeded_model.model_name,
                "provider": seeded_model.provider,
                "isReasoning": True,
            },
        ],
    }

    if description is not None:
        payload["description"] = description
    if start_message is not None:
        payload["startMessage"] = start_message
    if system_prompt is not None:
        payload["systemPrompt"] = system_prompt
    if tags is not None:
        payload["tags"] = tags
    if share_with_org is not None:
        payload["shareWithOrg"] = share_with_org
    if is_service_account is not None:
        payload["isServiceAccount"] = is_service_account
    if knowledge is not None:
        payload["knowledge"] = knowledge
    if web_search is not None:
        payload["webSearch"] = web_search
    if toolsets is not None:
        payload["toolsets"] = toolsets

    return payload


def _build_update_payload(
    *,
    name: str | None = None,
    seeded_model: SeededAIModel | None = None,
    models: list[dict[str, Any]] | None = None,
    description: str | None = None,
    start_message: str | None = None,
    system_prompt: str | None = None,
    instructions: str | None = None,
    tags: list[str] | None = None,
    share_with_org: bool | None = None,
    is_service_account: bool | None = None,
    knowledge: list[dict[str, Any]] | None = None,
    web_search: str | dict[str, Any] | None = None,
    toolsets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Partial update payload — only includes explicitly provided fields."""
    payload: dict[str, Any] = {}

    if name is not None:
        payload["name"] = name
    if seeded_model is not None:
        payload["models"] = [
            {
                "modelKey": seeded_model.model_key,
                "modelName": seeded_model.model_name,
                "provider": seeded_model.provider,
                "isReasoning": True,
            },
        ]
    elif models is not None:
        payload["models"] = models
    if description is not None:
        payload["description"] = description
    if start_message is not None:
        payload["startMessage"] = start_message
    if system_prompt is not None:
        payload["systemPrompt"] = system_prompt
    if instructions is not None:
        payload["instructions"] = instructions
    if tags is not None:
        payload["tags"] = tags
    if share_with_org is not None:
        payload["shareWithOrg"] = share_with_org
    if is_service_account is not None:
        payload["isServiceAccount"] = is_service_account
    if knowledge is not None:
        payload["knowledge"] = knowledge
    if web_search is not None:
        payload["webSearch"] = web_search
    if toolsets is not None:
        payload["toolsets"] = toolsets

    return payload


def _response_json(resp: requests.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError as exc:
        raise AssertionError(
            f"Expected JSON response, got status={resp.status_code}: {resp.text[:500]}"
        ) from exc
    assert isinstance(data, dict), f"Expected dict JSON body, got: {data!r}"
    return data


def _response_text_fragments(resp: requests.Response) -> str:
    fragments: list[str] = [resp.text]
    try:
        body = resp.json()
    except ValueError:
        body = None

    if isinstance(body, dict):
        for key in ("message", "detail", "error", "msg", "status"):
            value = body.get(key)
            if isinstance(value, str):
                fragments.append(value)
        err = body.get("error")
        if isinstance(err, dict):
            for key in ("message", "detail", "msg"):
                value = err.get(key)
                if isinstance(value, str):
                    fragments.append(value)

    return " ".join(fragments).lower()


@pytest.mark.integration
class TestCreateAgent:

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.base_url = pipeshub_client.base_url
        self.headers = pipeshub_client.auth_headers
        self.org_id = pipeshub_client.org_id
        self.timeout = pipeshub_client.timeout_seconds

    @pytest.fixture
    def created_agent_keys(self):
        created: list[str] = []
        yield created
        for agent_key in reversed(created):
            try:
                resp = requests.delete(
                    f"{self.base_url}/api/v1/agents/{agent_key}",
                    headers=self.headers,
                    timeout=self.timeout,
                )
                if resp.status_code >= 300:
                    logger.warning(
                        "Agent delete failed for %s: HTTP %s %s",
                        agent_key, resp.status_code, resp.text[:300]
                    )
            except Exception:
                # Best-effort cleanup in teardown: preserve the original test failure.
                pass

    def _create_agent_raw(self, payload: dict[str, Any]) -> requests.Response:
        return requests.post(
            f"{self.base_url}{_AGENTS_CREATE_PATH}",
            headers=self.headers,
            json=payload,
            timeout=self.timeout,
        )

    @staticmethod
    def _created_agent_key(resp_json: dict[str, Any]) -> str:
        agent = resp_json.get("agent")
        assert isinstance(agent, dict), f"Expected response.agent object, got: {resp_json!r}"
        agent_key = agent.get("_key")
        assert isinstance(agent_key, str) and agent_key, (
            f"Expected response.agent._key, got: {resp_json!r}"
        )
        return agent_key

    def test_create_agent_minimal_valid_body(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        created_agent_keys: list[str],
    ) -> None:
        payload = _build_payload(
            name=f"it-agent-minimal-{uuid4().hex[:8]}",
            seeded_model=reasoning_multimodal_llm_model,
        )
        assert_request_body_matches_openapi_operation(payload, "createAgent")

        resp = self._create_agent_raw(payload)
        assert resp.status_code == 201, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        agent_key = self._created_agent_key(body)
        created_agent_keys.append(agent_key)

        agent = body["agent"]
        assert body.get("status") in {"success", "partial_success"}
        assert agent.get("name") == payload["name"]
        assert agent.get("isServiceAccount") is False
        models = agent.get("models")
        assert isinstance(models, list) and models, f"Expected non-empty models, got: {agent!r}"

    def test_create_agent_accepts_dialog_style_payload(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        created_agent_keys: list[str],
    ) -> None:
        payload = _build_payload(
            name=f"it-agent-dialog-{uuid4().hex[:8]}",
            seeded_model=reasoning_multimodal_llm_model,
            description="",
            start_message="",
            system_prompt="",
            tags=[],
            share_with_org=False,
            is_service_account=False,
        )
        assert_request_body_matches_openapi_operation(payload, "createAgent")

        resp = self._create_agent_raw(payload)
        assert resp.status_code == 201, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        agent_key = self._created_agent_key(body)
        created_agent_keys.append(agent_key)

        agent = body["agent"]
        assert agent.get("name") == payload["name"]
        assert agent.get("isServiceAccount") is False
        assert agent.get("webSearch") is None

    def test_create_agent_rich_payload_matches_openapi_and_creates(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        session_kb: dict[str, str],
        created_agent_keys: list[str],
    ) -> None:
        payload = _build_payload(
            name=f"it-agent-rich-{uuid4().hex[:8]}",
            seeded_model=reasoning_multimodal_llm_model,
            tags=["integration"],
            knowledge=[
                {
                    "connectorId": f"knowledgeBase_{self.org_id}",
                    "filters": {"recordGroups": [session_kb["kb_id"]], "records": []},
                },
            ],
            web_search={"provider": "tavily", "providerKey": "demo-key"},
            toolsets=[_valid_toolset_entry()],
        )
        assert_request_body_matches_openapi_operation(payload, "createAgent")

        resp = self._create_agent_raw(payload)
        assert resp.status_code == 201, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        agent_key = self._created_agent_key(body)
        created_agent_keys.append(agent_key)

        assert_response_matches_openapi_operation(body, "createAgent", status_code="201")

    def test_create_agent_response_matches_openapi_spec(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        created_agent_keys: list[str],
    ) -> None:
        payload = _build_payload(
            name=f"it-agent-openapi-{uuid4().hex[:8]}",
            seeded_model=reasoning_multimodal_llm_model,
            description="",
            start_message="",
            system_prompt="",
            tags=[],
            share_with_org=False,
            is_service_account=False,
        )

        resp = self._create_agent_raw(payload)
        assert resp.status_code == 201, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        agent_key = self._created_agent_key(body)
        created_agent_keys.append(agent_key)

        assert_response_matches_openapi_operation(body, "createAgent", status_code="201")

    @pytest.mark.parametrize(
        ("web_search_value", "expected_provider"),
        [
            ("tavily", "tavily"),
            ({"provider": "serper", "providerKey": "demo-key"}, "serper"),
        ],
    )
    def test_create_agent_accepts_optional_knowledge_and_web_search(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        session_kb: dict[str, str],
        created_agent_keys: list[str],
        web_search_value: str | dict[str, Any],
        expected_provider: str,
    ) -> None:
        payload = _build_payload(
            name=f"it-agent-optional-{uuid4().hex[:8]}",
            seeded_model=reasoning_multimodal_llm_model,
            tags=[],
            knowledge=[
                {
                    "connectorId": f"knowledgeBase_{self.org_id}",
                    "filters": {"recordGroups": [session_kb["kb_id"]], "records": []},
                },
            ],
            web_search=web_search_value,
        )

        resp = self._create_agent_raw(payload)
        assert resp.status_code == 201, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        agent_key = self._created_agent_key(body)
        created_agent_keys.append(agent_key)

        agent = body["agent"]
        knowledge = agent.get("knowledge")
        assert isinstance(knowledge, list) and knowledge, f"Expected knowledge entries, got: {agent!r}"

        web_search = agent.get("webSearch")
        assert isinstance(web_search, dict), f"Expected webSearch object, got: {agent!r}"
        assert web_search.get("provider") == expected_provider

    def test_create_agent_accepts_unknown_fields_with_gateway_stripping(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        created_agent_keys: list[str],
    ) -> None:
        payload = _build_payload(
            name=f"it-agent-strip-unknown-{uuid4().hex[:8]}",
            seeded_model=reasoning_multimodal_llm_model,
            web_search={"provider": "serper", "providerKey": "demo-key"},
        )
        payload["unexpectedTopLevelField"] = "drop-me"
        payload["models"][0]["unexpectedModelField"] = "drop-me"
        payload["webSearch"]["unexpectedWebSearchField"] = "drop-me"

        resp = self._create_agent_raw(payload)
        assert resp.status_code == 201, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        agent_key = self._created_agent_key(body)
        created_agent_keys.append(agent_key)

        agent = body["agent"]
        assert "unexpectedTopLevelField" not in agent
        models = agent.get("models")
        assert isinstance(models, list) and models, f"Expected models list, got: {agent!r}"
        first_model = models[0]
        if isinstance(first_model, dict):
            assert "unexpectedModelField" not in first_model
        web_search = agent.get("webSearch")
        assert isinstance(web_search, dict), f"Expected webSearch object, got: {agent!r}"
        assert "unexpectedWebSearchField" not in web_search

    def test_create_agent_rejects_missing_name(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
    ) -> None:
        payload = _build_payload(
            name="placeholder",
            seeded_model=reasoning_multimodal_llm_model,
        )
        payload.pop("name")

        resp = self._create_agent_raw(payload)
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"

        error_text = _response_text_fragments(resp)
        assert "name is required" in error_text, f"unexpected error payload: {resp.text}"

    def test_create_agent_rejects_models_without_reasoning_flag(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
    ) -> None:
        payload = {
            "name": f"it-agent-no-reasoning-{uuid4().hex[:8]}",
            "models": [
                {
                    "modelKey": reasoning_multimodal_llm_model.model_key,
                    "modelName": reasoning_multimodal_llm_model.model_name,
                    "provider": reasoning_multimodal_llm_model.provider,
                },
            ],
        }

        resp = self._create_agent_raw(payload)
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"

        error_text = _response_text_fragments(resp)
        assert "reasoning model" in error_text, f"unexpected error payload: {resp.text}"

    def test_create_agent_rejects_invalid_toolset_name(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
    ) -> None:
        payload = _build_payload(
            name=f"it-agent-bad-toolset-{uuid4().hex[:8]}",
            seeded_model=reasoning_multimodal_llm_model,
            toolsets=[{"name": "not_a_real_toolset"}],
        )

        resp = self._create_agent_raw(payload)
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"

        error_text = _response_text_fragments(resp)
        assert "invalid toolset" in error_text, f"unexpected error payload: {resp.text}"

    def test_create_agent_rejects_model_object_without_model_key(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
    ) -> None:
        payload = _build_payload(
            name=f"it-agent-no-model-key-{uuid4().hex[:8]}",
            seeded_model=reasoning_multimodal_llm_model,
        )
        payload["models"] = [{}]

        resp = self._create_agent_raw(payload)
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"


@pytest.mark.integration
class TestCreateAgentOpenApiRequestContract:
    """Offline OpenAPI request-body checks not covered by live gateway negatives."""

    @pytest.fixture
    def valid_payload(self, reasoning_multimodal_llm_model: SeededAIModel) -> dict[str, Any]:
        return _build_payload(
            name=f"it-agent-openapi-req-{uuid4().hex[:8]}",
            seeded_model=reasoning_multimodal_llm_model,
        )

    def test_rejects_flow_property(self, valid_payload: dict[str, Any]) -> None:
        payload = {**valid_payload, "flow": {"nodes": [], "edges": []}}
        with pytest.raises(AssertionError):
            assert_request_body_matches_openapi_operation(payload, "createAgent")

    def test_rejects_extra_top_level_property(self, valid_payload: dict[str, Any]) -> None:
        payload = {**valid_payload, "unexpectedTopLevelField": "x"}
        with pytest.raises(AssertionError):
            assert_request_body_matches_openapi_operation(payload, "createAgent")


@pytest.mark.integration
class TestListAgents:

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.base_url = pipeshub_client.base_url
        self.headers = pipeshub_client.auth_headers
        self.timeout = pipeshub_client.timeout_seconds

    @pytest.fixture
    def created_agent_keys(self):
        created: list[str] = []
        yield created
        for agent_key in reversed(created):
            try:
                resp = requests.delete(
                    f"{self.base_url}/api/v1/agents/{agent_key}",
                    headers=self.headers,
                    timeout=self.timeout,
                )
                if resp.status_code >= 300:
                    logger.warning(
                        "Agent delete failed for %s: HTTP %s %s",
                        agent_key, resp.status_code, resp.text[:300]
                    )
            except Exception:
                pass

    def _create_agent_raw(self, payload: dict[str, Any]) -> requests.Response:
        return requests.post(
            f"{self.base_url}{_AGENTS_CREATE_PATH}",
            headers=self.headers,
            json=payload,
            timeout=self.timeout,
        )

    def _create_agent_for_list_test(
        self,
        *,
        name: str,
        seeded_model: SeededAIModel,
        created_agent_keys: list[str],
        description: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        payload = _build_payload(
            name=name,
            seeded_model=seeded_model,
            description=description,
            tags=tags,
        )
        resp = self._create_agent_raw(payload)
        assert resp.status_code == 201, f"Agent create failed: {resp.status_code}: {resp.text}"

        body = _response_json(resp)
        agent_key = TestCreateAgent._created_agent_key(body)
        created_agent_keys.append(agent_key)
        return agent_key

    def _list_agents_raw(
        self,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return requests.get(
            f"{self.base_url}{_AGENTS_LIST_PATH}",
            headers=headers if headers is not None else self.headers,
            params=params,
            timeout=self.timeout,
        )

    @staticmethod
    def _assert_list_response_shape(body: dict[str, Any]) -> list[dict[str, Any]]:
        assert body.get("success") is True, f"Expected success=true, got: {body!r}"
        agents = body.get("agents")
        assert isinstance(agents, list), f"Expected agents list, got: {body!r}"

        pagination = body.get("pagination")
        assert isinstance(pagination, dict), f"Expected pagination object, got: {body!r}"
        for key in ("currentPage", "limit", "totalItems", "totalPages", "hasNext", "hasPrev"):
            assert key in pagination, f"Missing pagination.{key} in {body!r}"

        return agents

    def test_list_agents_returns_paginated_envelope(self, agent_session: dict[str, Any]) -> None:
        resp = self._list_agents_raw()
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        body = _response_json(resp)
        agents = self._assert_list_response_shape(body)

        assert any(
            agent.get("_key") == agent_session["primary_agent"] for agent in agents if isinstance(agent, dict)
        ), f"Expected primary session agent in response, got: {body!r}"
        assert body["pagination"]["currentPage"] == 1
        assert body["pagination"]["limit"] == 20

    def test_list_agents_supports_page_and_limit_query_params(self, agent_session: dict[str, Any]) -> None:
        resp = self._list_agents_raw(params={"page": 1, "limit": 2})
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        body = _response_json(resp)
        agents = self._assert_list_response_shape(body)

        assert len(agents) <= 2, f"Expected at most 2 agents, got {len(agents)}: {body!r}"
        assert body["pagination"]["currentPage"] == 1
        assert body["pagination"]["limit"] == 2
        assert body["pagination"]["totalItems"] >= len(agent_session["secondary_agents"]) + 1

    def test_list_agents_supports_search_query_param(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        created_agent_keys: list[str],
    ) -> None:
        token = uuid4().hex[:10]
        unique_name = f"it-agent-list-search-{token}"
        self._create_agent_for_list_test(
            name=unique_name,
            seeded_model=reasoning_multimodal_llm_model,
            created_agent_keys=created_agent_keys,
            description=f"searchable description {token}",
            tags=[f"tag-{token}"],
        )

        resp = self._list_agents_raw(params={"search": token})
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        body = _response_json(resp)
        agents = self._assert_list_response_shape(body)

        matching_names = [
            agent.get("name")
            for agent in agents
            if isinstance(agent, dict) and isinstance(agent.get("name"), str)
        ]
        assert unique_name in matching_names, (
            f"Expected search result {unique_name!r} in response, got: {matching_names!r}"
        )

    @pytest.mark.parametrize("sort_order", ["asc", "desc"])
    def test_list_agents_supports_sort_order_variants(
        self,
        sort_order: str,
    ) -> None:
        resp = self._list_agents_raw(
            params={"page": 1, "limit": 5, "sort_by": "updatedAtTimestamp", "sort_order": sort_order}
        )
        assert resp.status_code == 200, f"[{sort_order}] Expected 200, got {resp.status_code}: {resp.text}"

        body = _response_json(resp)
        agents = self._assert_list_response_shape(body)
        assert len(agents) <= 5
        assert body["pagination"]["limit"] == 5

    @pytest.mark.parametrize(
        ("label", "params"),
        [
            ("page zero", {"page": 0}),
            ("page negative", {"page": -1}),
            ("page non-numeric", {"page": "abc"}),
            ("limit zero", {"limit": 0}),
            ("limit too large", {"limit": 201}),
            ("limit non-numeric", {"limit": "oops"}),
            ("blank search", {"search": "   "}),
            ("blank sort_by", {"sort_by": "   "}),
            ("invalid sort_order", {"sort_order": "descending"}),
            ("uppercase sort_order", {"sort_order": "DESC"}),
        ],
    )
    def test_list_agents_rejects_invalid_query_params(
        self,
        label: str,
        params: dict[str, Any],
    ) -> None:
        resp = self._list_agents_raw(params=params)
        assert resp.status_code == 400, (
            f"[{label}] Expected 400, got {resp.status_code}: {resp.text}"
        )

        body = _response_json(resp)
        assert isinstance(body, dict) and "error" in body, (
            f"[{label}] Expected error envelope, got {body!r}"
        )
        error = body["error"]
        assert error["code"] == "VALIDATION_ERROR", (
            f"[{label}] Expected VALIDATION_ERROR, got {error['code']!r}"
        )
        assert error["message"] == "Validation failed", (
            f"[{label}] Expected 'Validation failed', got {error['message']!r}"
        )

    def test_list_agents_requires_auth(self) -> None:
        resp = self._list_agents_raw(headers={})
        assert resp.status_code == 401, (
            f"Expected 401 for missing auth, got {resp.status_code}: {resp.text}"
        )

        body = _response_json(resp)
        assert isinstance(body, dict) and "error" in body, f"Expected error body, got: {body!r}"
        assert body["error"]["message"] == "No token provided", (
            f"Expected 'No token provided', got {body['error']['message']!r}"
        )


@pytest.mark.integration
class TestGetAgent:

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.base_url = pipeshub_client.base_url
        self.headers = pipeshub_client.auth_headers
        self.timeout = pipeshub_client.timeout_seconds

    @pytest.fixture
    def created_agent_keys(self):
        created: list[str] = []
        yield created
        for agent_key in reversed(created):
            try:
                resp = requests.delete(
                    f"{self.base_url}/api/v1/agents/{agent_key}",
                    headers=self.headers,
                    timeout=self.timeout,
                )
                if resp.status_code >= 300:
                    logger.warning(
                        "Agent delete failed for %s: HTTP %s %s",
                        agent_key, resp.status_code, resp.text[:300]
                    )
            except Exception:
                pass

    def _create_agent_raw(self, payload: dict[str, Any]) -> requests.Response:
        return requests.post(
            f"{self.base_url}{_AGENTS_CREATE_PATH}",
            headers=self.headers,
            json=payload,
            timeout=self.timeout,
        )

    def _create_agent_for_get_test(
        self,
        *,
        name: str,
        seeded_model: SeededAIModel,
        created_agent_keys: list[str],
        description: str | None = None,
        tags: list[str] | None = None,
        knowledge: list[dict[str, Any]] | None = None,
    ) -> str:
        payload = _build_payload(
            name=name,
            seeded_model=seeded_model,
            description=description,
            tags=tags,
            knowledge=knowledge,
        )
        resp = self._create_agent_raw(payload)
        assert resp.status_code == 201, f"Agent create failed: {resp.status_code}: {resp.text}"

        body = _response_json(resp)
        agent_key = TestCreateAgent._created_agent_key(body)
        created_agent_keys.append(agent_key)
        return agent_key

    def _get_agent_raw(
        self,
        agent_key: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return requests.get(
            f"{self.base_url}{_AGENTS_DETAIL_PATH.format(agent_key=agent_key)}",
            headers=headers if headers is not None else self.headers,
            params=params,
            timeout=self.timeout,
        )

    @staticmethod
    def _assert_get_agent_response_shape(
        body: dict[str, Any],
        *,
        expected_agent_key: str,
        expected_name: str | None = None,
    ) -> dict[str, Any]:
        assert body.get("status") == "success", f"Expected success status, got: {body!r}"
        assert body.get("message") == "Agent retrieved successfully", (
            f"Expected success message, got: {body!r}"
        )

        agent = body.get("agent")
        assert isinstance(agent, dict), f"Expected body.agent object, got: {body!r}"

        actual_key = agent.get("_key", agent.get("agentKey"))
        assert actual_key == expected_agent_key, (
            f"Expected agent key {expected_agent_key!r}, got: {body!r}"
        )

        if expected_name is not None:
            assert agent.get("name") == expected_name, (
                f"Expected name {expected_name!r}, got: {body!r}"
            )

        models = agent.get("models")
        assert isinstance(models, list) and models, (
            f"Expected non-empty models list, got: {body!r}"
        )

        return agent

    def test_get_agent_returns_existing_agent(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        created_agent_keys: list[str],
    ) -> None:
        unique_name = f"it-agent-get-{uuid4().hex[:8]}"
        agent_key = self._create_agent_for_get_test(
            name=unique_name,
            seeded_model=reasoning_multimodal_llm_model,
            created_agent_keys=created_agent_keys,
            description="detail fetch integration test",
            tags=["detail", "fetch"],
        )

        resp = self._get_agent_raw(agent_key)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        body = _response_json(resp)
        self._assert_get_agent_response_shape(
            body,
            expected_agent_key=agent_key,
            expected_name=unique_name,
        )

    def test_get_agent_accepts_varied_query_params_even_though_controller_ignores_them(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        created_agent_keys: list[str],
        session_kb: dict[str, str],
    ) -> None:
        unique_name = f"it-agent-get-query-{uuid4().hex[:8]}"
        agent_key = self._create_agent_for_get_test(
            name=unique_name,
            seeded_model=reasoning_multimodal_llm_model,
            created_agent_keys=created_agent_keys,
            knowledge=[
                {
                    "connectorId": f"knowledgeBase_{self.client.org_id}",
                    "filters": {"recordGroups": [session_kb["kb_id"]], "records": []},
                }
            ],
        )

        resp = self._get_agent_raw(
            agent_key,
            params={
                "include": "toolsets",
                "page": 2,
                "limit": 10,
                "unexpected": "still-allowed",
            },
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        body = _response_json(resp)
        self._assert_get_agent_response_shape(
            body,
            expected_agent_key=agent_key,
            expected_name=unique_name,
        )

    def test_get_agent_response_matches_openapi_spec(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        created_agent_keys: list[str],
    ) -> None:
        unique_name = f"it-agent-get-openapi-{uuid4().hex[:8]}"
        agent_key = self._create_agent_for_get_test(
            name=unique_name,
            seeded_model=reasoning_multimodal_llm_model,
            created_agent_keys=created_agent_keys,
            description="openapi detail validation",
        )

        resp = self._get_agent_raw(agent_key)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        body = _response_json(resp)
        self._assert_get_agent_response_shape(
            body,
            expected_agent_key=agent_key,
            expected_name=unique_name,
        )
        assert_response_matches_openapi_operation(body, "getAgent", status_code="200")

    def test_get_agent_with_knowledge_matches_openapi_spec(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        session_kb: dict[str, str],
        created_agent_keys: list[str],
    ) -> None:
        unique_name = f"it-agent-get-kb-openapi-{uuid4().hex[:8]}"
        agent_key = self._create_agent_for_get_test(
            name=unique_name,
            seeded_model=reasoning_multimodal_llm_model,
            created_agent_keys=created_agent_keys,
            description="openapi GET with KB knowledge",
            knowledge=[
                {
                    "connectorId": f"knowledgeBase_{self.client.org_id}",
                    "filters": {
                        "recordGroups": [session_kb["kb_id"]],
                        "records": [],
                    },
                },
            ],
        )

        resp = self._get_agent_raw(agent_key)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        body = _response_json(resp)
        self._assert_get_agent_response_shape(
            body,
            expected_agent_key=agent_key,
            expected_name=unique_name,
        )
        assert_response_matches_openapi_operation(body, "getAgent", status_code="200")

        agent = body["agent"]
        knowledge = agent.get("knowledge")
        assert isinstance(knowledge, list) and knowledge, f"Expected knowledge entries: {agent!r}"
        entry = knowledge[0]
        parsed = entry.get("filtersParsed") or {}
        if isinstance(parsed, dict):
            groups = parsed.get("recordGroups") or []
            assert session_kb["kb_id"] in groups, (
                f"Expected kb_id in filtersParsed.recordGroups, got: {entry!r}"
            )

    def test_get_agent_returns_current_error_status_for_unknown_agent_key(self) -> None:
        missing_agent_key = f"missing-agent-{uuid4().hex[:12]}"

        resp = self._get_agent_raw(missing_agent_key)
        assert resp.status_code == 500, (
            f"Expected current 500 error for unknown agent key, got {resp.status_code}: {resp.text}"
        )

        body = _response_json(resp)
        assert_response_matches_openapi_operation(body, "getAgent", status_code="500")

        error_text = _response_text_fragments(resp)
        assert "not found" in error_text or "agent" in error_text, (
            f"unexpected error payload: {resp.text}"
        )

    def test_get_agent_requires_auth(self, agent_session: dict[str, Any]) -> None:
        resp = self._get_agent_raw(agent_session["primary_agent"], headers={})
        assert resp.status_code == 401, (
            f"Expected 401 for missing auth, got {resp.status_code}: {resp.text}"
        )

        body = _response_json(resp)
        assert isinstance(body, dict) and "error" in body, f"Expected error body, got: {body!r}"
        assert body["error"]["message"] == "No token provided", (
            f"Expected 'No token provided', got {body['error']['message']!r}"
        )
        assert_response_matches_openapi_operation(body, "getAgent", status_code="401")


@pytest.mark.integration
class TestUpdateAgent:

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.base_url = pipeshub_client.base_url
        self.headers = pipeshub_client.auth_headers
        self.org_id = pipeshub_client.org_id
        self.timeout = pipeshub_client.timeout_seconds

    @pytest.fixture
    def created_agent_keys(self):
        created: list[str] = []
        yield created
        for agent_key in reversed(created):
            try:
                resp = requests.delete(
                    f"{self.base_url}/api/v1/agents/{agent_key}",
                    headers=self.headers,
                    timeout=self.timeout,
                )
                if resp.status_code >= 300:
                    logger.warning(
                        "Agent delete failed for %s: HTTP %s %s",
                        agent_key, resp.status_code, resp.text[:300]
                    )
            except Exception:
                pass

    def _create_agent_raw(self, payload: dict[str, Any]) -> requests.Response:
        return requests.post(
            f"{self.base_url}{_AGENTS_CREATE_PATH}",
            headers=self.headers,
            json=payload,
            timeout=self.timeout,
        )

    def _create_agent_for_update_test(
        self,
        *,
        name: str,
        seeded_model: SeededAIModel,
        created_agent_keys: list[str],
        description: str | None = None,
    ) -> str:
        payload = _build_payload(
            name=name,
            seeded_model=seeded_model,
            description=description,
        )
        resp = self._create_agent_raw(payload)
        assert resp.status_code == 201, f"Agent create failed: {resp.status_code}: {resp.text}"

        body = _response_json(resp)
        agent_key = TestCreateAgent._created_agent_key(body)
        created_agent_keys.append(agent_key)
        return agent_key

    def _update_agent_raw(
        self,
        agent_key: str,
        *,
        json_body: dict[str, Any] | None = None,
        data: str | bytes | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        request_headers = dict(headers if headers is not None else self.headers)
        if data is not None:
            request_headers.setdefault("Content-Type", "application/json")
        return requests.put(
            f"{self.base_url}{_AGENTS_DETAIL_PATH.format(agent_key=agent_key)}",
            headers=request_headers,
            json=json_body,
            data=data,
            params=params,
            timeout=self.timeout,
        )

    def _get_agent_raw(self, agent_key: str) -> requests.Response:
        return requests.get(
            f"{self.base_url}{_AGENTS_DETAIL_PATH.format(agent_key=agent_key)}",
            headers=self.headers,
            timeout=self.timeout,
        )

    def test_update_agent_empty_body_matches_openapi(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        created_agent_keys: list[str],
    ) -> None:
        original_name = f"it-agent-update-empty-body-{uuid4().hex[:8]}"
        agent_key = self._create_agent_for_update_test(
            name=original_name,
            seeded_model=reasoning_multimodal_llm_model,
            created_agent_keys=created_agent_keys,
        )

        payload: dict[str, Any] = {}
        assert_request_body_matches_openapi_operation(payload, "updateAgent")

        resp = self._update_agent_raw(agent_key, json_body=payload)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        body = _response_json(resp)
        assert_response_matches_openapi_operation(body, "updateAgent", status_code="200")

        get_resp = self._get_agent_raw(agent_key)
        assert get_resp.status_code == 200, (
            f"Expected GET 200 after empty-body update, got {get_resp.status_code}: {get_resp.text}"
        )
        get_body = _response_json(get_resp)
        agent = TestGetAgent._assert_get_agent_response_shape(
            get_body,
            expected_agent_key=agent_key,
            expected_name=original_name,
        )
        assert agent.get("name") == original_name, (
            f"Expected name unchanged after empty-body update, got: {get_body!r}"
        )

    def test_update_agent_partial_name_and_description(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        created_agent_keys: list[str],
    ) -> None:
        original_name = f"it-agent-update-before-{uuid4().hex[:8]}"
        agent_key = self._create_agent_for_update_test(
            name=original_name,
            seeded_model=reasoning_multimodal_llm_model,
            created_agent_keys=created_agent_keys,
            description="original description",
        )

        updated_name = f"it-agent-update-after-{uuid4().hex[:8]}"
        updated_description = f"updated description {uuid4().hex[:8]}"

        payload = _build_update_payload(
            name=updated_name,
            description=updated_description,
        )
        assert_request_body_matches_openapi_operation(payload, "updateAgent")

        resp = self._update_agent_raw(agent_key, json_body=payload)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        body = _response_json(resp)
        assert_response_matches_openapi_operation(body, "updateAgent", status_code="200")

        get_resp = self._get_agent_raw(agent_key)
        assert get_resp.status_code == 200, (
            f"Expected GET 200 after update, got {get_resp.status_code}: {get_resp.text}"
        )
        get_body = _response_json(get_resp)
        agent = TestGetAgent._assert_get_agent_response_shape(
            get_body,
            expected_agent_key=agent_key,
            expected_name=updated_name,
        )
        assert agent.get("description") == updated_description, (
            f"Expected updated description on GET, got: {get_body!r}"
        )

    def test_update_agent_rich_payload_matches_openapi_and_updates(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        session_kb: dict[str, str],
        created_agent_keys: list[str],
    ) -> None:
        agent_key = self._create_agent_for_update_test(
            name=f"it-agent-update-rich-{uuid4().hex[:8]}",
            seeded_model=reasoning_multimodal_llm_model,
            created_agent_keys=created_agent_keys,
        )

        updated_description = f"rich update description {uuid4().hex[:8]}"
        payload = _build_update_payload(
            description=updated_description,
            start_message="Hello from integration test",
            system_prompt="You are a helpful assistant.",
            instructions="Be concise.",
            tags=["integration", "update-rich"],
            seeded_model=reasoning_multimodal_llm_model,
            toolsets=[_valid_toolset_entry()],
            knowledge=[
                {
                    "connectorId": f"knowledgeBase_{self.org_id}",
                    "filters": {"recordGroups": [session_kb["kb_id"]], "records": []},
                },
            ],
            web_search={"provider": "tavily", "providerKey": "demo-key"},
        )
        assert_request_body_matches_openapi_operation(payload, "updateAgent")

        resp = self._update_agent_raw(agent_key, json_body=payload)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        body = _response_json(resp)
        assert_response_matches_openapi_operation(body, "updateAgent", status_code="200")

        get_resp = self._get_agent_raw(agent_key)
        assert get_resp.status_code == 200, (
            f"Expected GET 200 after rich update, got {get_resp.status_code}: {get_resp.text}"
        )
        get_body = _response_json(get_resp)
        agent = TestGetAgent._assert_get_agent_response_shape(
            get_body,
            expected_agent_key=agent_key,
        )
        assert agent.get("description") == updated_description, (
            f"Expected updated description on GET, got: {get_body!r}"
        )
        assert agent.get("tags") == ["integration", "update-rich"], (
            f"Expected updated tags on GET, got: {get_body!r}"
        )

    def test_update_agent_accepts_valid_models_array(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        created_agent_keys: list[str],
    ) -> None:
        agent_key = self._create_agent_for_update_test(
            name=f"it-agent-update-models-{uuid4().hex[:8]}",
            seeded_model=reasoning_multimodal_llm_model,
            created_agent_keys=created_agent_keys,
        )

        payload = _build_update_payload(
            seeded_model=reasoning_multimodal_llm_model,
        )
        assert_request_body_matches_openapi_operation(payload, "updateAgent")

        resp = self._update_agent_raw(agent_key, json_body=payload)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        body = _response_json(resp)
        assert_response_matches_openapi_operation(body, "updateAgent", status_code="200")

        get_resp = self._get_agent_raw(agent_key)
        assert get_resp.status_code == 200, (
            f"Expected GET 200 after models update, got {get_resp.status_code}: {get_resp.text}"
        )
        get_body = _response_json(get_resp)
        agent = TestGetAgent._assert_get_agent_response_shape(
            get_body,
            expected_agent_key=agent_key,
        )
        models = agent.get("models")
        assert isinstance(models, list) and models, f"Expected models after update, got: {agent!r}"
        first_model = models[0]
        assert isinstance(first_model, dict), f"Expected model object, got: {first_model!r}"
        assert first_model.get("modelKey") == reasoning_multimodal_llm_model.model_key

    def test_update_agent_accepts_unknown_fields_with_gateway_stripping(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        created_agent_keys: list[str],
    ) -> None:
        agent_key = self._create_agent_for_update_test(
            name=f"it-agent-update-strip-unknown-{uuid4().hex[:8]}",
            seeded_model=reasoning_multimodal_llm_model,
            created_agent_keys=created_agent_keys,
        )

        payload = _build_update_payload(
            description=f"strip-unknown probe {uuid4().hex[:8]}",
            web_search={"provider": "serper", "providerKey": "demo-key"},
        )
        assert_request_body_matches_openapi_operation(payload, "updateAgent")

        payload["unexpectedTopLevelField"] = "drop-me"
        payload["webSearch"]["unexpectedWebSearchField"] = "drop-me"

        resp = self._update_agent_raw(agent_key, json_body=payload)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        body = _response_json(resp)
        assert_response_matches_openapi_operation(body, "updateAgent", status_code="200")

    def test_update_agent_accepts_varied_query_params_even_though_controller_ignores_them(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        created_agent_keys: list[str],
    ) -> None:
        agent_key = self._create_agent_for_update_test(
            name=f"it-agent-update-query-{uuid4().hex[:8]}",
            seeded_model=reasoning_multimodal_llm_model,
            created_agent_keys=created_agent_keys,
        )

        resp = self._update_agent_raw(
            agent_key,
            json_body={"description": f"query-param probe {uuid4().hex[:8]}"},
            params={"page": 2, "limit": 10, "unexpected": "still-allowed"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        body = _response_json(resp)
        assert_response_matches_openapi_operation(body, "updateAgent", status_code="200")

    def test_update_agent_requires_auth(self, agent_session: dict[str, Any]) -> None:
        resp = self._update_agent_raw(
            agent_session["primary_agent"],
            json_body={"name": "should-not-update"},
            headers={},
        )
        assert resp.status_code == 401, (
            f"Expected 401 for missing auth, got {resp.status_code}: {resp.text}"
        )

        body = _response_json(resp)
        assert isinstance(body, dict) and "error" in body, f"Expected error body, got: {body!r}"
        assert body["error"]["message"] == "No token provided", (
            f"Expected 'No token provided', got {body['error']['message']!r}"
        )

    def test_update_agent_returns_current_error_status_for_empty_models_array(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        created_agent_keys: list[str],
    ) -> None:
        agent_key = self._create_agent_for_update_test(
            name=f"it-agent-update-empty-models-{uuid4().hex[:8]}",
            seeded_model=reasoning_multimodal_llm_model,
            created_agent_keys=created_agent_keys,
        )

        resp = self._update_agent_raw(agent_key, json_body={"models": []})
        assert resp.status_code == 400, (
            f"Expected 400 validation error for empty models array, got {resp.status_code}: {resp.text}"
        )

        body = _response_json(resp)
        assert_response_matches_openapi_operation(body, "updateAgent", status_code="400")

        error_text = _response_text_fragments(resp)
        assert "at least one" in error_text and "model" in error_text, (
            f"unexpected error payload: {resp.text}"
        )

    def test_update_agent_returns_current_error_status_for_models_without_reasoning_flag(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        created_agent_keys: list[str],
    ) -> None:
        """Document current gateway behavior when models omit the reasoning flag."""
        agent_key = self._create_agent_for_update_test(
            name=f"it-agent-update-no-reasoning-{uuid4().hex[:8]}",
            seeded_model=reasoning_multimodal_llm_model,
            created_agent_keys=created_agent_keys,
        )

        resp = self._update_agent_raw(
            agent_key,
            json_body={
                "models": [
                    {
                        "modelKey": reasoning_multimodal_llm_model.model_key,
                        "modelName": reasoning_multimodal_llm_model.model_name,
                        "provider": reasoning_multimodal_llm_model.provider,
                    },
                ],
            },
        )
        assert resp.status_code == 400, (
            f"Expected 400 validation error for models without reasoning flag, "
            f"got {resp.status_code}: {resp.text}"
        )

        body = _response_json(resp)
        assert_response_matches_openapi_operation(body, "updateAgent", status_code="400")

        error_text = _response_text_fragments(resp)
        assert "reasoning model" in error_text, f"unexpected error payload: {resp.text}"

    def test_update_agent_rejects_invalid_toolset_name(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        created_agent_keys: list[str],
    ) -> None:
        agent_key = self._create_agent_for_update_test(
            name=f"it-agent-update-bad-toolset-{uuid4().hex[:8]}",
            seeded_model=reasoning_multimodal_llm_model,
            created_agent_keys=created_agent_keys,
        )

        resp = self._update_agent_raw(
            agent_key,
            json_body={"toolsets": [{"name": "not_a_real_toolset"}]},
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"

        body = _response_json(resp)
        assert_response_matches_openapi_operation(body, "updateAgent", status_code="400")

        error_text = _response_text_fragments(resp)
        assert "invalid toolset" in error_text, f"unexpected error payload: {resp.text}"

    def test_update_agent_rejects_name_exceeding_max_length(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        created_agent_keys: list[str],
    ) -> None:
        agent_key = self._create_agent_for_update_test(
            name=f"it-agent-update-long-name-{uuid4().hex[:8]}",
            seeded_model=reasoning_multimodal_llm_model,
            created_agent_keys=created_agent_keys,
        )

        resp = self._update_agent_raw(agent_key, json_body={"name": "a" * 201})
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"

        body = _response_json(resp)
        assert_response_matches_openapi_operation(body, "updateAgent", status_code="400")

        error_text = _response_text_fragments(resp)
        assert "200 characters" in error_text or "validation" in error_text, (
            f"unexpected error payload: {resp.text}"
        )

    def test_update_agent_returns_current_error_status_for_unknown_agent_key(self) -> None:
        missing_agent_key = f"missing-agent-{uuid4().hex[:12]}"

        resp = self._update_agent_raw(
            missing_agent_key,
            json_body={"name": f"ghost-{uuid4().hex[:8]}"},
        )
        assert resp.status_code == 500, (
            f"Expected current 500 error for unknown agent key, got {resp.status_code}: {resp.text}"
        )

        error_text = _response_text_fragments(resp)
        assert "not found" in error_text or "agent" in error_text, (
            f"unexpected error payload: {resp.text}"
        )

    def test_update_agent_rejects_malformed_json_body(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        created_agent_keys: list[str],
    ) -> None:
        agent_key = self._create_agent_for_update_test(
            name=f"it-agent-update-bad-json-{uuid4().hex[:8]}",
            seeded_model=reasoning_multimodal_llm_model,
            created_agent_keys=created_agent_keys,
        )

        resp = self._update_agent_raw(
            agent_key,
            data='{"name": "broken"',
        )
        assert resp.status_code in {400, 500}, (
            f"Expected client or server error for malformed JSON, got {resp.status_code}: {resp.text}"
        )

        error_text = _response_text_fragments(resp)
        assert (
            "json" in error_text
            or "unexpected" in error_text
            or "syntax" in error_text
            or "parse" in error_text
            or "error" in error_text
        ), f"unexpected error payload: {resp.text}"


@pytest.mark.integration
class TestUpdateAgentOpenApiRequestContract:
    """Offline OpenAPI request-body checks not covered by live gateway negatives."""

    @pytest.fixture
    def valid_partial_payload(self) -> dict[str, Any]:
        return _build_update_payload(name="Partial rename")

    def test_rejects_extra_top_level_property(
        self,
        valid_partial_payload: dict[str, Any],
    ) -> None:
        payload = {**valid_partial_payload, "unexpectedTopLevelField": "x"}
        with pytest.raises(AssertionError):
            assert_request_body_matches_openapi_operation(payload, "updateAgent")


@pytest.mark.integration
class TestDeleteAgent:

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.base_url = pipeshub_client.base_url
        self.headers = pipeshub_client.auth_headers
        self.timeout = pipeshub_client.timeout_seconds

    @pytest.fixture
    def created_agent_keys(self):
        created: list[str] = []
        yield created
        for agent_key in reversed(created):
            try:
                resp = requests.delete(
                    f"{self.base_url}/api/v1/agents/{agent_key}",
                    headers=self.headers,
                    timeout=self.timeout,
                )
                if resp.status_code >= 300:
                    logger.warning(
                        "Agent delete failed for %s: HTTP %s %s",
                        agent_key, resp.status_code, resp.text[:300]
                    )
            except Exception:
                pass

    def _create_agent_raw(self, payload: dict[str, Any]) -> requests.Response:
        return requests.post(
            f"{self.base_url}{_AGENTS_CREATE_PATH}",
            headers=self.headers,
            json=payload,
            timeout=self.timeout,
        )

    def _create_agent_for_delete_test(
        self,
        *,
        name: str,
        seeded_model: SeededAIModel,
        created_agent_keys: list[str],
        description: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        payload = _build_payload(
            name=name,
            seeded_model=seeded_model,
            description=description,
            tags=tags,
        )
        resp = self._create_agent_raw(payload)
        assert resp.status_code == 201, f"Agent create failed: {resp.status_code}: {resp.text}"

        body = _response_json(resp)
        agent_key = TestCreateAgent._created_agent_key(body)
        created_agent_keys.append(agent_key)
        return agent_key

    def _get_agent_raw(
        self,
        agent_key: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return requests.get(
            f"{self.base_url}{_AGENTS_DETAIL_PATH.format(agent_key=agent_key)}",
            headers=headers if headers is not None else self.headers,
            timeout=self.timeout,
        )

    def _delete_agent_raw(
        self,
        agent_key: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return requests.delete(
            f"{self.base_url}{_AGENTS_DETAIL_PATH.format(agent_key=agent_key)}",
            headers=headers if headers is not None else self.headers,
            timeout=self.timeout,
        )

    def _list_agents_raw(
        self,
        *,
        params: dict[str, Any] | None = None,
    ) -> requests.Response:
        return requests.get(
            f"{self.base_url}{_AGENTS_LIST_PATH}",
            headers=self.headers,
            params=params,
            timeout=self.timeout,
        )

    @staticmethod
    def _assert_delete_agent_success_shape(body: dict[str, Any]) -> None:
        assert body.get("status") == "success", f"Expected success status, got: {body!r}"
        assert body.get("message") == "Agent deleted successfully", (
            f"Expected delete success message, got: {body!r}"
        )
        deleted = body.get("deleted")
        assert isinstance(deleted, dict), f"Expected deleted counts object, got: {body!r}"
        assert deleted.get("agents") == 1, f"Expected one agent deleted, got: {body!r}"
        for key in ("toolsets", "tools", "knowledge", "edges"):
            assert deleted.get(key) == 0, f"Expected deleted.{key}=0, got: {body!r}"

    @staticmethod
    def _assert_get_agent_not_available_after_delete(resp: requests.Response) -> None:
        """Document current gateway behavior for soft-deleted / missing agents."""
        assert resp.status_code == 500, (
            f"Expected current 500 after delete (same as unknown GET), "
            f"got {resp.status_code}: {resp.text}"
        )
        error_text = _response_text_fragments(resp)
        assert "not found" in error_text or "agent" in error_text, (
            f"unexpected error payload: {resp.text}"
        )

    def test_delete_agent_lifecycle(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        created_agent_keys: list[str],
    ) -> None:
        search_token = uuid4().hex[:10]
        unique_name = f"it-agent-delete-lifecycle-{search_token}"
        agent_key = self._create_agent_for_delete_test(
            name=unique_name,
            seeded_model=reasoning_multimodal_llm_model,
            created_agent_keys=created_agent_keys,
            description=f"delete lifecycle integration test {search_token}",
            tags=[f"delete-{search_token}"],
        )

        get_before = self._get_agent_raw(agent_key)
        assert get_before.status_code == 200, (
            f"Expected 200 before delete, got {get_before.status_code}: {get_before.text}"
        )
        before_body = _response_json(get_before)
        TestGetAgent._assert_get_agent_response_shape(
            before_body,
            expected_agent_key=agent_key,
            expected_name=unique_name,
        )

        del_resp = self._delete_agent_raw(agent_key)
        assert del_resp.status_code == 200, (
            f"Expected 200 on delete, got {del_resp.status_code}: {del_resp.text}"
        )
        del_body = _response_json(del_resp)
        self._assert_delete_agent_success_shape(del_body)

        if agent_key in created_agent_keys:
            created_agent_keys.remove(agent_key)

        get_after = self._get_agent_raw(agent_key)
        self._assert_get_agent_not_available_after_delete(get_after)

        list_resp = self._list_agents_raw(params={"search": search_token})
        assert list_resp.status_code == 200, (
            f"Expected 200 on list search, got {list_resp.status_code}: {list_resp.text}"
        )
        list_body = _response_json(list_resp)
        agents = list_body.get("agents")
        assert isinstance(agents, list), f"Expected agents list, got: {list_body!r}"
        matching_keys = [
            agent.get("_key")
            for agent in agents
            if isinstance(agent, dict) and agent.get("_key") == agent_key
        ]
        assert not matching_keys, (
            f"Deleted agent {agent_key!r} should not appear in search results: {list_body!r}"
        )

    def test_delete_agent_requires_auth(self, agent_session: dict[str, Any]) -> None:
        resp = self._delete_agent_raw(
            agent_session["primary_agent"],
            headers={},
        )
        assert resp.status_code == 401, (
            f"Expected 401 for missing auth, got {resp.status_code}: {resp.text}"
        )

        body = _response_json(resp)
        assert isinstance(body, dict) and "error" in body, f"Expected error body, got: {body!r}"
        assert body["error"]["message"] == "No token provided", (
            f"Expected 'No token provided', got {body['error']['message']!r}"
        )

    def test_delete_agent_unknown_key(self) -> None:
        """Document current gateway behavior for unknown agent key on DELETE."""
        missing_agent_key = f"missing-agent-{uuid4().hex[:12]}"

        resp = self._delete_agent_raw(missing_agent_key)
        assert resp.status_code == 500, (
            f"Expected current 500 error for unknown agent key, got {resp.status_code}: {resp.text}"
        )

        error_text = _response_text_fragments(resp)
        assert "not found" in error_text or "agent" in error_text, (
            f"unexpected error payload: {resp.text}"
        )

    def test_delete_agent_twice(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        created_agent_keys: list[str],
    ) -> None:
        agent_key = self._create_agent_for_delete_test(
            name=f"it-agent-delete-twice-{uuid4().hex[:8]}",
            seeded_model=reasoning_multimodal_llm_model,
            created_agent_keys=created_agent_keys,
        )

        first = self._delete_agent_raw(agent_key)
        assert first.status_code == 200, f"{first.status_code}: {first.text}"
        self._assert_delete_agent_success_shape(_response_json(first))

        if agent_key in created_agent_keys:
            created_agent_keys.remove(agent_key)

        second = self._delete_agent_raw(agent_key)
        assert second.status_code == 500, (
            f"Expected current 500 on second delete, got {second.status_code}: {second.text}"
        )
        error_text = _response_text_fragments(second)
        assert "not found" in error_text or "agent" in error_text, (
            f"unexpected error payload: {second.text}"
        )
