"""Agent create integration tests.

``POST /api/v1/agents/create``

Exercises the Node gateway + Zod validation layer with positive and negative
request bodies using the existing enterprise-search fixtures.
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
        reasoning_llm_model: SeededAIModel,
        created_agent_keys: list[str],
    ) -> None:
        payload = _build_payload(
            name=f"it-agent-minimal-{uuid4().hex[:8]}",
            seeded_model=reasoning_llm_model,
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
        reasoning_llm_model: SeededAIModel,
        created_agent_keys: list[str],
    ) -> None:
        payload = _build_payload(
            name=f"it-agent-dialog-{uuid4().hex[:8]}",
            seeded_model=reasoning_llm_model,
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
        reasoning_llm_model: SeededAIModel,
        created_agent_keys: list[str],
    ) -> None:
        payload = _build_payload(
            name=f"it-agent-openapi-{uuid4().hex[:8]}",
            seeded_model=reasoning_llm_model,
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
        reasoning_llm_model: SeededAIModel,
        session_kb: dict[str, str],
        created_agent_keys: list[str],
        web_search_value: str | dict[str, Any],
        expected_provider: str,
    ) -> None:
        payload = _build_payload(
            name=f"it-agent-optional-{uuid4().hex[:8]}",
            seeded_model=reasoning_llm_model,
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

    def test_create_agent_rejects_missing_name(
        self,
        reasoning_llm_model: SeededAIModel,
    ) -> None:
        payload = _build_payload(
            name="placeholder",
            seeded_model=reasoning_llm_model,
        )
        payload.pop("name")

        resp = self._create_agent_raw(payload)
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"

        error_text = _response_text_fragments(resp)
        assert "name is required" in error_text, f"unexpected error payload: {resp.text}"

    def test_create_agent_rejects_blank_name(
        self,
        reasoning_llm_model: SeededAIModel,
    ) -> None:
        payload = _build_payload(
            name="   ",
            seeded_model=reasoning_llm_model,
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
        reasoning_llm_model: SeededAIModel,
    ) -> None:
        payload = {
            "name": f"it-agent-no-reasoning-{uuid4().hex[:8]}",
            "models": [
                {
                    "modelKey": reasoning_llm_model.model_key,
                    "modelName": reasoning_llm_model.model_name,
                    "provider": reasoning_llm_model.provider,
                },
            ],
        }

        resp = self._create_agent_raw(payload)
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"

        error_text = _response_text_fragments(resp)
        assert "reasoning model" in error_text, f"unexpected error payload: {resp.text}"
