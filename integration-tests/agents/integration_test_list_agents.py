from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
import requests

from openapi_search_validator import assert_matches_component_schema
from pipeshub_client import PipeshubClient


@pytest.mark.integration
class TestListAgentsIntegration:
    """GET /api/v1/agents integration coverage."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.base_url = pipeshub_client.base_url
        self.headers = pipeshub_client.auth_headers
        self.timeout = int(os.getenv("PIPESHUB_TEST_TIMEOUT", "60"))
        self.list_agents_url = f"{self.base_url}/api/v1/agents"

    @pytest.fixture
    def seeded_agents_dataset(
        self,
        agent_factory,
    ) -> dict[str, Any]:
        token = uuid.uuid4().hex[:10]
        created_agents = [
            agent_factory(
                name=f"it-agent-alpha-{token}",
                description=f"alpha integration agent {token}",
            ),
            agent_factory(
                name=f"it-agent-bravo-{token}",
                description=f"bravo integration agent {token}",
            ),
            agent_factory(
                name=f"it-agent-charlie-{token}",
                description=f"charlie integration agent {token}",
            ),
        ]
        return {
            "token": token,
            "agents": created_agents,
            "agent_keys": [agent["agentKey"] for agent in created_agents],
            "agent_names": [agent["name"] for agent in created_agents],
        }

    @staticmethod
    def _assert_list_envelope(body: Any) -> None:
        assert isinstance(body, dict), f"expected object response, got {type(body)!r}"
        assert body.get("success") is True, f"expected success=true: {body!r}"

        agents = body.get("agents")
        assert isinstance(agents, list), f"agents should be a list: {body!r}"
        for agent in agents:
            assert isinstance(agent, dict), f"agent entry should be object: {agent!r}"
            assert_matches_component_schema(agent, "Agent")

        pagination = body.get("pagination")
        assert isinstance(pagination, dict), f"pagination should be object: {body!r}"
        for key in (
            "currentPage",
            "limit",
            "totalItems",
            "totalPages",
            "hasNext",
            "hasPrev",
        ):
            assert key in pagination, f"pagination missing {key!r}: {pagination!r}"

    @staticmethod
    def _agent_keys(body: dict[str, Any]) -> list[str]:
        return [
            str(agent.get("agentKey") or agent.get("_key"))
            for agent in body.get("agents") or []
            if isinstance(agent, dict) and (agent.get("agentKey") or agent.get("_key"))
        ]

    def test_list_agents_returns_created_agents_with_search(
        self,
        seeded_agents_dataset: dict[str, Any],
    ) -> None:
        resp = requests.get(
            self.list_agents_url,
            headers=self.headers,
            params={
                "search": seeded_agents_dataset["token"],
                "page": 1,
                "limit": 100,
                "sort_by": "name",
                "sort_order": "asc",
            },
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        self._assert_list_envelope(body)

        returned_keys = set(self._agent_keys(body))
        expected_keys = set(seeded_agents_dataset["agent_keys"])
        assert expected_keys.issubset(returned_keys), (
            f"expected created agents in list response: expected={expected_keys!r} "
            f"actual={returned_keys!r}"
        )
        assert body["pagination"]["totalItems"] >= len(expected_keys), body

    def test_list_agents_supports_pagination_variations(
        self,
        seeded_agents_dataset: dict[str, Any],
    ) -> None:
        params = {
            "search": seeded_agents_dataset["token"],
            "sort_by": "name",
            "sort_order": "asc",
            "limit": 1,
        }

        page_1 = requests.get(
            self.list_agents_url,
            headers=self.headers,
            params={**params, "page": 1},
            timeout=self.timeout,
        )
        page_2 = requests.get(
            self.list_agents_url,
            headers=self.headers,
            params={**params, "page": 2},
            timeout=self.timeout,
        )

        assert page_1.status_code == 200, f"{page_1.status_code}: {page_1.text}"
        assert page_2.status_code == 200, f"{page_2.status_code}: {page_2.text}"

        body_1 = page_1.json()
        body_2 = page_2.json()
        self._assert_list_envelope(body_1)
        self._assert_list_envelope(body_2)

        keys_1 = self._agent_keys(body_1)
        keys_2 = self._agent_keys(body_2)
        assert len(keys_1) == 1, body_1
        assert len(keys_2) == 1, body_2
        assert keys_1[0] != keys_2[0], (
            f"expected different records across pages: page1={keys_1!r} page2={keys_2!r}"
        )
        assert body_1["pagination"]["currentPage"] == 1, body_1
        assert body_2["pagination"]["currentPage"] == 2, body_2
        assert body_1["pagination"]["totalItems"] >= len(seeded_agents_dataset["agent_keys"])
        assert body_1["pagination"]["hasNext"] is True, body_1
        assert body_2["pagination"]["hasPrev"] is True, body_2

    def test_list_agents_supports_sort_order_variations(
        self,
        seeded_agents_dataset: dict[str, Any],
    ) -> None:
        common = {
            "search": seeded_agents_dataset["token"],
            "page": 1,
            "limit": 100,
            "sort_by": "name",
        }
        asc_resp = requests.get(
            self.list_agents_url,
            headers=self.headers,
            params={**common, "sort_order": "asc"},
            timeout=self.timeout,
        )
        desc_resp = requests.get(
            self.list_agents_url,
            headers=self.headers,
            params={**common, "sort_order": "desc"},
            timeout=self.timeout,
        )

        assert asc_resp.status_code == 200, f"{asc_resp.status_code}: {asc_resp.text}"
        assert desc_resp.status_code == 200, f"{desc_resp.status_code}: {desc_resp.text}"

        asc_body = asc_resp.json()
        desc_body = desc_resp.json()
        self._assert_list_envelope(asc_body)
        self._assert_list_envelope(desc_body)

        asc_names = [
            agent["name"]
            for agent in asc_body["agents"]
            if isinstance(agent, dict) and seeded_agents_dataset["token"] in str(agent.get("name", ""))
        ]
        desc_names = [
            agent["name"]
            for agent in desc_body["agents"]
            if isinstance(agent, dict) and seeded_agents_dataset["token"] in str(agent.get("name", ""))
        ]
        expected_names = sorted(seeded_agents_dataset["agent_names"])
        assert asc_names[: len(expected_names)] == expected_names, asc_names
        assert desc_names[: len(expected_names)] == list(reversed(expected_names)), desc_names

    @pytest.mark.parametrize("path_suffix", ["", "/"])
    def test_list_agents_supports_path_variations(
        self,
        seeded_agents_dataset: dict[str, Any],
        path_suffix: str,
    ) -> None:
        resp = requests.get(
            f"{self.list_agents_url}{path_suffix}",
            headers=self.headers,
            params={"search": seeded_agents_dataset["token"], "page": 1, "limit": 100},
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        self._assert_list_envelope(body)
        returned_keys = set(self._agent_keys(body))
        assert set(seeded_agents_dataset["agent_keys"]).issubset(returned_keys), body

    def test_list_agents_ignores_unknown_query_params(
        self,
        seeded_agents_dataset: dict[str, Any],
    ) -> None:
        resp = requests.get(
            self.list_agents_url,
            headers=self.headers,
            params={
                "search": seeded_agents_dataset["token"],
                "page": 1,
                "limit": 100,
                "ignored": "value",
                "unexpected_flag": "true",
            },
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        self._assert_list_envelope(body)
        returned_keys = set(self._agent_keys(body))
        assert set(seeded_agents_dataset["agent_keys"]).issubset(returned_keys), body

    def test_list_agents_unknown_search_returns_empty_agents(
        self,
    ) -> None:
        resp = requests.get(
            self.list_agents_url,
            headers=self.headers,
            params={
                "search": f"no-agent-should-match-{uuid.uuid4().hex}",
                "page": 1,
                "limit": 20,
            },
            timeout=self.timeout,
        )
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        self._assert_list_envelope(body)
        assert body["agents"] == [], body
        assert body["pagination"]["totalItems"] == 0, body

    def test_list_agents_missing_auth_returns_401_or_403(self) -> None:
        resp = requests.get(
            self.list_agents_url,
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        assert resp.status_code in (401, 403), f"{resp.status_code}: {resp.text}"
