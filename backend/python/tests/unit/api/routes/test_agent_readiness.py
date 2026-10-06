"""`GET /agent/{id}/readiness` and `compute_agent_readiness`, driven through the real route."""

from __future__ import annotations

import pytest

from app.agents.constants.toolset_constants import get_toolset_config_path
from app.modules.agents.readiness import compute_agent_readiness
from tests.support.agent_routes import (
    FakeConfigService,
    InMemoryGraph,
    as_user,
    make_client,
)

TOOLSETS = "agentToolsets"
HAS_TOOLSET = "agentHasToolset"


def _graph_with_toolsets() -> InMemoryGraph:
    graph = InMemoryGraph()
    graph.add_agent("a1", "alice", share_with_org=True)
    for key, name in (("ts-x", "Slack"), ("ts-y", "Gmail")):
        graph.add_node(TOOLSETS, {"_key": key, "instanceId": f"inst-{key}", "name": name.lower(), "instanceName": name})
        graph.add_edge(HAS_TOOLSET, {"_from": "agentInstances/a1", "_to": f"{TOOLSETS}/{key}"})
    return graph


def _config(user_id: str, **by_instance: dict) -> FakeConfigService:
    return FakeConfigService({get_toolset_config_path(i, user_id): c for i, c in by_instance.items()})


class TestReadinessRoute:
    def test_reports_the_toolset_the_caller_has_not_configured(self) -> None:
        config = _config("u-bob", **{"inst-ts-x": {"isAuthenticated": True, "token": "s3cret"}})
        client, _ = make_client(_graph_with_toolsets(), config)
        response = client.get("/api/v1/agent/a1/readiness", headers=as_user("bob"))
        assert response.status_code == 200
        assert response.json() == {"canSend": False, "missingToolsets": ["Gmail"], "unauthenticatedToolsets": []}
        assert "s3cret" not in response.text

    def test_configured_but_unauthenticated_is_not_ready(self) -> None:
        config = _config(
            "u-bob",
            **{"inst-ts-x": {"isAuthenticated": True}, "inst-ts-y": {"isAuthenticated": False}},
        )
        client, _ = make_client(_graph_with_toolsets(), config)
        body = client.get("/api/v1/agent/a1/readiness", headers=as_user("bob")).json()
        assert body == {"canSend": False, "missingToolsets": [], "unauthenticatedToolsets": ["Gmail"]}

    def test_ready_when_every_toolset_is_authenticated(self) -> None:
        config = _config(
            "u-bob",
            **{"inst-ts-x": {"isAuthenticated": True}, "inst-ts-y": {"isAuthenticated": True}},
        )
        client, _ = make_client(_graph_with_toolsets(), config)
        body = client.get("/api/v1/agent/a1/readiness", headers=as_user("bob")).json()
        assert body == {"canSend": True, "missingToolsets": [], "unauthenticatedToolsets": []}

    def test_agent_without_toolsets_is_ready(self) -> None:
        graph = InMemoryGraph()
        graph.add_agent("plain", "alice")
        client, _ = make_client(graph)
        assert client.get("/api/v1/agent/plain/readiness", headers=as_user("alice")).json()["canSend"] is True

    @pytest.mark.parametrize("caller", ["bob", "mallory"])
    def test_caller_without_access_gets_404(self, caller: str) -> None:
        graph = InMemoryGraph()
        graph.add_agent("private", "alice")
        client, _ = make_client(graph)
        assert client.get("/api/v1/agent/private/readiness", headers=as_user(caller)).status_code == 404

    def test_unknown_agent_is_404(self) -> None:
        client, _ = make_client(InMemoryGraph())
        assert client.get("/api/v1/agent/nope/readiness", headers=as_user("alice")).status_code == 404

    def test_oauth_token_without_agent_execute_is_rejected(self) -> None:
        client, _ = make_client(
            _graph_with_toolsets(),
            extra_users={"app": {"userId": "u-bob", "orgId": "org-1", "isOAuth": True, "oauthScopes": ["agent:read"]}},
        )
        assert client.get("/api/v1/agent/a1/readiness", headers=as_user("app")).status_code == 403

    def test_request_without_identity_is_rejected(self) -> None:
        client, _ = make_client(_graph_with_toolsets())
        assert client.get("/api/v1/agent/a1/readiness").status_code == 401


class TestComputeAgentReadiness:
    async def test_service_account_agent_reads_credentials_under_the_agent_key(self) -> None:
        agent = {"isServiceAccount": True, "toolsets": [{"instanceId": "i1", "name": "slack"}]}
        config = _config("agent-7", i1={"isAuthenticated": True, "token": "t"})
        result = await compute_agent_readiness(agent, {"userId": "u-bob"}, config_service=config, agent_id="agent-7")
        assert result.can_send is True
        assert result.toolset_configs == {"i1": {"isAuthenticated": True, "token": "t"}}
        assert "toolsetConfigs" not in result.model_dump(by_alias=True)

    async def test_blocked_message_names_the_problem_toolsets(self) -> None:
        agent = {"toolsets": [{"instanceId": "i1", "name": "gmail_app"}]}
        result = await compute_agent_readiness(
            agent, {"userId": "u-bob"}, config_service=FakeConfigService(), agent_id="a",
        )
        assert result.blocked_message == (
            "This agent requires the following actions to be set up — not configured: 'Gmail App'. "
            "Please connect your actions in Workspace → Actions before using this agent."
        )

    async def test_prefetched_auth_skips_the_config_read(self) -> None:
        agent = {"toolsets": [{"instanceId": "i1", "name": "x"}]}
        result = await compute_agent_readiness(
            agent, {"userId": "u-bob"}, config_service=FakeConfigService(), agent_id="a",
            prefetched_auth={"i1": {"isAuthenticated": True}},
        )
        assert result.can_send is True

    async def test_config_read_failure_counts_as_missing(self) -> None:
        class Broken:
            async def get_config(self, *_: object, **__: object) -> None:
                raise RuntimeError("etcd down")

        agent = {"toolsets": [{"instanceId": "i1", "name": "x"}]}
        result = await compute_agent_readiness(agent, {"userId": "u-bob"}, config_service=Broken(), agent_id="a")  # type: ignore[arg-type]
        assert result.can_send is False and result.missing_toolsets == ["X"]

    async def test_nothing_to_check_leaves_toolsets_untouched(self) -> None:
        result = await compute_agent_readiness({"toolsets": [{}]}, {"userId": "u"}, config_service=FakeConfigService())
        assert result.can_send is True and result.configured_toolsets is None
