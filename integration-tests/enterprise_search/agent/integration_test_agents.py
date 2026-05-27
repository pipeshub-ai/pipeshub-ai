"""Agent integration tests.

``POST /api/v1/agents/create``
``GET /api/v1/agents``
``GET /api/v1/agents/{agentKey}``

Exercises the Node gateway + Zod validation layer with positive and negative
request shapes using the existing enterprise-search fixtures.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
import requests

from ai_models_setup import SeededAIModel
from openapi_search_validator import assert_response_matches_spec
from pipeshub_client import PipeshubClient

_AGENTS_CREATE_PATH = "/api/v1/agents/create"
_AGENTS_LIST_PATH = "/api/v1/agents"
_AGENTS_DETAIL_PATH = "/api/v1/agents/{agent_key}"


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
                assert resp.status_code < 300, (
                    f"Agent delete failed for {agent_key}: "
                    f"HTTP {resp.status_code} {resp.text[:300]}"
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

        resp = self._create_agent_raw(payload)
        assert resp.status_code == 201, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        agent_key = self._created_agent_key(body)
        created_agent_keys.append(agent_key)

        agent = body["agent"]
        assert agent.get("name") == payload["name"]
        assert agent.get("isServiceAccount") is False
        assert agent.get("webSearch") is None

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

        assert_response_matches_spec(body, "/agents/create", "post", status_code=201)

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
        assert isinstance(first_model, dict), f"Expected model object, got: {first_model!r}"
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

    def test_create_agent_rejects_blank_name(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
    ) -> None:
        payload = _build_payload(
            name="   ",
            seeded_model=reasoning_multimodal_llm_model,
        )

        resp = self._create_agent_raw(payload)
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"

        error_text = _response_text_fragments(resp)
        assert "name is required" in error_text, f"unexpected error payload: {resp.text}"

    def test_create_agent_rejects_empty_models_array(self) -> None:
        payload = {
            "name": f"it-agent-empty-models-{uuid4().hex[:8]}",
            "models": [],
        }

        resp = self._create_agent_raw(payload)
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"

        error_text = _response_text_fragments(resp)
        assert "at least one ai model is required" in error_text, (
            f"unexpected error payload: {resp.text}"
        )

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
                assert resp.status_code < 300, (
                    f"Agent delete failed for {agent_key}: "
                    f"HTTP {resp.status_code} {resp.text[:300]}"
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
                assert resp.status_code < 300, (
                    f"Agent delete failed for {agent_key}: "
                    f"HTTP {resp.status_code} {resp.text[:300]}"
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
        assert_response_matches_spec(body, "/agents/{agentKey}", "get", status_code=200)

    def test_get_agent_returns_current_error_status_for_unknown_agent_key(self) -> None:
        missing_agent_key = f"missing-agent-{uuid4().hex[:12]}"

        resp = self._get_agent_raw(missing_agent_key)
        assert resp.status_code == 500, (
            f"Expected current 500 error for unknown agent key, got {resp.status_code}: {resp.text}"
        )

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
