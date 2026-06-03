"""Integration tests for agent usage lookup routes.

Routes covered:
- ``GET /api/v1/agents/web-search-usage/{provider}``
- ``GET /api/v1/agents/model-usage/{model_key}``
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
from openapi_schema_validator import assert_response_matches_openapi_operation
from pipeshub_client import PipeshubClient

logger = logging.getLogger(__name__)

_AGENTS_CREATE_PATH = "/api/v1/agents/create"
_AGENTS_WEB_SEARCH_USAGE_PATH = "/api/v1/agents/web-search-usage/{provider}"
_AGENTS_MODEL_USAGE_PATH = "/api/v1/agents/model-usage/{model_key}"
_AGENTS_DETAIL_PATH = "/api/v1/agents/{agent_key}"


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


def _response_json(resp: requests.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError as exc:
        raise AssertionError(
            f"Expected JSON response, got status={resp.status_code}: {resp.text[:500]}"
        ) from exc
    assert isinstance(data, dict), f"Expected dict JSON body, got: {data!r}"
    return data


def _build_agent_create_payload(*, name: str, seeded_model: SeededAIModel) -> dict[str, Any]:
    return {
        "name": name,
        "models": [
            {
                "modelKey": seeded_model.model_key,
                "modelName": seeded_model.model_name,
                "provider": seeded_model.provider,
                "isReasoning": True,
            },
        ],
        "webSearch": "tavily",
    }


@pytest.mark.integration
class TestGetWebSearchProviderUsage:

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
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
                    f"{self.base_url}{_AGENTS_DETAIL_PATH.format(agent_key=agent_key)}",
                    headers=self.headers,
                    timeout=self.timeout,
                )
                if resp.status_code >= 300:
                    logger.warning(
                        "Agent delete failed for %s: HTTP %s %s",
                        agent_key, resp.status_code, resp.text[:300]
                    )
            except Exception:
                # best-effort test cleanup
                pass

    def _create_agent_raw(self, payload: dict[str, Any]) -> requests.Response:
        return requests.post(
            f"{self.base_url}{_AGENTS_CREATE_PATH}",
            headers=self.headers,
            json=payload,
            timeout=self.timeout,
        )

    def _web_search_usage_raw(
        self,
        provider: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> requests.Response:
        return requests.get(
            f"{self.base_url}{_AGENTS_WEB_SEARCH_USAGE_PATH.format(provider=provider)}",
            headers=self.headers if headers is None else headers,
            params=params,
            timeout=self.timeout,
        )

    @staticmethod
    def _extract_agent_key(create_body: dict[str, Any]) -> str:
        agent = create_body.get("agent")
        assert isinstance(agent, dict), f"Expected response.agent object, got: {create_body!r}"
        agent_key = agent.get("_key")
        assert isinstance(agent_key, str) and agent_key, (
            f"Expected response.agent._key, got: {create_body!r}"
        )
        return agent_key

    def test_get_web_search_provider_usage_returns_agents_and_matches_openapi(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        created_agent_keys: list[str],
    ) -> None:
        payload = _build_agent_create_payload(
            name=f"it-web-search-usage-{uuid4().hex[:8]}",
            seeded_model=reasoning_multimodal_llm_model,
        )
        create_resp = self._create_agent_raw(payload)
        assert create_resp.status_code == 201, (
            f"Agent create failed: {create_resp.status_code}: {create_resp.text}"
        )
        create_body = _response_json(create_resp)
        created_agent_key = self._extract_agent_key(create_body)
        created_agent_keys.append(created_agent_key)

        usage_resp = self._web_search_usage_raw("tavily")
        assert usage_resp.status_code == 200, (
            f"Expected 200, got {usage_resp.status_code}: {usage_resp.text}"
        )
        usage_body = _response_json(usage_resp)
        assert_response_matches_openapi_operation(
            usage_body, "getWebSearchProviderUsage", status_code="200"
        )

        agents = usage_body.get("agents")
        assert isinstance(agents, list), f"Expected agents list, got: {usage_body!r}"
        assert any(
            isinstance(agent, dict) and agent.get("_key") == created_agent_key
            for agent in agents
        ), f"Expected created agent in web-search usage response: {usage_body!r}"

    def test_get_web_search_provider_usage_returns_empty_agents_for_unknown_provider(self) -> None:
        usage_resp = self._web_search_usage_raw(f"unknown-provider-{uuid4().hex[:8]}")
        assert usage_resp.status_code == 200, (
            f"Expected 200, got {usage_resp.status_code}: {usage_resp.text}"
        )
        usage_body = _response_json(usage_resp)
        assert_response_matches_openapi_operation(
            usage_body, "getWebSearchProviderUsage", status_code="200"
        )
        assert usage_body.get("success") is True, f"Expected success=true, got: {usage_body!r}"
        assert usage_body.get("agents") == [], (
            f"Expected empty agents for unknown provider, got: {usage_body!r}"
        )

    def test_get_web_search_provider_usage_rejects_whitespace_only_provider(self) -> None:
        usage_resp = self._web_search_usage_raw("%20%20%20")
        assert usage_resp.status_code == 400, (
            f"Expected 400 for whitespace-only provider, got {usage_resp.status_code}: {usage_resp.text}"
        )
        error_text = _response_text_fragments(usage_resp)
        assert "provider is required" in error_text, (
            f"unexpected error payload: {usage_resp.text}"
        )

    def test_get_web_search_provider_usage_rejects_unexpected_query_params(self) -> None:
        usage_resp = self._web_search_usage_raw("tavily", params={"page": "1"})
        assert usage_resp.status_code == 400, (
            f"Expected 400 for unexpected query param, got {usage_resp.status_code}: {usage_resp.text}"
        )
        error_text = _response_text_fragments(usage_resp)
        assert "validation failed" in error_text, (
            f"unexpected error payload: {usage_resp.text}"
        )

    def test_get_web_search_provider_usage_requires_auth(self) -> None:
        usage_resp = self._web_search_usage_raw("tavily", headers={})
        assert usage_resp.status_code == 401, (
            f"Expected 401 for missing auth, got {usage_resp.status_code}: {usage_resp.text}"
        )
        usage_body = _response_json(usage_resp)
        assert isinstance(usage_body, dict) and "error" in usage_body, (
            f"Expected error body, got: {usage_body!r}"
        )
        assert usage_body["error"]["message"] == "No token provided", (
            f"Expected 'No token provided', got {usage_body['error']['message']!r}"
        )


@pytest.mark.integration
class TestGetModelUsage:

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.base_url = pipeshub_client.base_url
        self.headers = pipeshub_client.auth_headers
        self.timeout = pipeshub_client.timeout_seconds

    def _model_usage_raw(
        self,
        model_key: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        return requests.get(
            f"{self.base_url}{_AGENTS_MODEL_USAGE_PATH.format(model_key=model_key)}",
            headers=self.headers if headers is None else headers,
            timeout=self.timeout,
        )

    def test_get_model_usage_returns_agents_and_matches_openapi(
        self,
        reasoning_multimodal_llm_model: SeededAIModel,
        agent_session: dict[str, Any],
    ) -> None:
        del agent_session  # fixture ensures agents already exist for this model key

        usage_resp = self._model_usage_raw(reasoning_multimodal_llm_model.model_key)
        assert usage_resp.status_code == 200, (
            f"Expected 200, got {usage_resp.status_code}: {usage_resp.text}"
        )

        usage_body = _response_json(usage_resp)
        assert_response_matches_openapi_operation(usage_body, "getModelUsage", status_code="200")
        agents = usage_body.get("agents")
        assert isinstance(agents, list), f"Expected agents list, got: {usage_body!r}"
        assert len(agents) >= 1, f"Expected at least one agent using seeded model, got: {usage_body!r}"

    def test_get_model_usage_returns_empty_agents_for_unknown_model_key(self) -> None:
        usage_resp = self._model_usage_raw(f"missing-model-key-{uuid4().hex[:8]}")
        assert usage_resp.status_code == 200, (
            f"Expected 200, got {usage_resp.status_code}: {usage_resp.text}"
        )
        usage_body = _response_json(usage_resp)
        assert_response_matches_openapi_operation(usage_body, "getModelUsage", status_code="200")
        assert usage_body.get("success") is True, f"Expected success=true, got: {usage_body!r}"
        assert usage_body.get("agents") == [], (
            f"Expected empty agents for unknown model key, got: {usage_body!r}"
        )

    def test_get_model_usage_requires_auth(self) -> None:
        usage_resp = self._model_usage_raw("any-model-key", headers={})
        assert usage_resp.status_code == 401, (
            f"Expected 401 for missing auth, got {usage_resp.status_code}: {usage_resp.text}"
        )
        usage_body = _response_json(usage_resp)
        assert isinstance(usage_body, dict) and "error" in usage_body, (
            f"Expected error body, got: {usage_body!r}"
        )
        assert usage_body["error"]["message"] == "No token provided", (
            f"Expected 'No token provided', got {usage_body['error']['message']!r}"
        )
