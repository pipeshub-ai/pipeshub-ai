from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
import requests

from openapi_search_validator import (
    assert_matches_component_schema,
    assert_response_matches_spec,
)
from pipeshub_client import PipeshubClient
from enterprise_search.conversation_test_utils import AGENT_DETAIL_PATH


@pytest.mark.integration
class TestGetAgentIntegration:
    """GET /api/v1/agents/{agentKey} integration coverage."""

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        provisioned_agent_keys: dict[str, Any],
    ) -> None:
        self.base_url = pipeshub_client.base_url
        self.headers = pipeshub_client.auth_headers
        self.timeout = int(os.getenv("PIPESHUB_TEST_TIMEOUT", "60"))
        self.primary_agent_key = provisioned_agent_keys["primary_agent_key"]
        self.extra_agent_key = provisioned_agent_keys["extra_agent_key"]
        self.all_agent_keys = provisioned_agent_keys["all_agent_keys"]
        self.agent_detail_url_tpl = f"{self.base_url}/api/v1/agents/{{agentKey}}"

    @staticmethod
    def _assert_agent_detail_200(body: Any) -> None:
        assert_response_matches_spec(body, AGENT_DETAIL_PATH, "GET", 200)
        assert isinstance(body, dict), f"expected JSON object, got {type(body)!r}"
        assert body.get("status") == "success", f"expected success status: {body!r}"
        assert isinstance(body.get("message"), str) and body["message"].strip(), (
            f"expected success message: {body!r}"
        )
        agent = body.get("agent")
        assert isinstance(agent, dict), f"expected nested agent object: {body!r}"
        assert_matches_component_schema(agent, "Agent")
        agent_key = agent.get("agentKey") or agent.get("_key")
        assert isinstance(agent_key, str) and agent_key.strip(), (
            f"agent detail missing key: {body!r}"
        )
        name = agent.get("name")
        assert isinstance(name, str) and name.strip(), (
            f"agent detail missing name: {body!r}"
        )

    @pytest.mark.parametrize("agent_key_attr", ["primary_agent_key", "extra_agent_key"])
    def test_get_agent_returns_expected_agent_for_valid_key(
        self,
        agent_key_attr: str,
    ) -> None:
        agent_key = getattr(self, agent_key_attr)
        resp = requests.get(
            self.agent_detail_url_tpl.format(agentKey=agent_key),
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        self._assert_agent_detail_200(body)
        returned_key = body["agent"].get("agentKey") or body["agent"].get("_key")
        assert returned_key == agent_key, (
            f"returned agent key mismatch: expected={agent_key!r} actual={returned_key!r}"
        )

    @pytest.mark.parametrize("path_suffix", ["", "/"])
    def test_get_agent_supports_path_variations(self, path_suffix: str) -> None:
        resp = requests.get(
            f"{self.agent_detail_url_tpl.format(agentKey=self.primary_agent_key)}{path_suffix}",
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        self._assert_agent_detail_200(body)
        returned_key = body["agent"].get("agentKey") or body["agent"].get("_key")
        assert returned_key == self.primary_agent_key, body

    @pytest.mark.parametrize(
        "query_params",
        [
            {"ignored": "value"},
            {"unexpected_flag": "true", "view": "full"},
        ],
    )
    def test_get_agent_ignores_unknown_query_params(
        self,
        query_params: dict[str, str],
    ) -> None:
        resp = requests.get(
            self.agent_detail_url_tpl.format(agentKey=self.primary_agent_key),
            headers=self.headers,
            params=query_params,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        self._assert_agent_detail_200(body)
        returned_key = body["agent"].get("agentKey") or body["agent"].get("_key")
        assert returned_key == self.primary_agent_key, body

    def test_get_agent_unknown_key_returns_404(self) -> None:
        resp = requests.get(
            self.agent_detail_url_tpl.format(
                agentKey=f"missing-agent-{uuid.uuid4().hex}"
            ),
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

    def test_get_agent_missing_auth_returns_401_or_403(self) -> None:
        resp = requests.get(
            self.agent_detail_url_tpl.format(agentKey=self.primary_agent_key),
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        assert resp.status_code in (401, 403), f"{resp.status_code}: {resp.text}"
