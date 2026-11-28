"""An agent answers as the person asking, whatever kind of agent it is.

The effective authority of a run is the person's own access, narrowed by the
agent's configured sources and tools. It is never the identity of whoever made
the agent, and never wider than the person. These drive the real chat routes
through a FastAPI app (`tests/support/agent_routes.py`); only the LLM factory
and the agent loop are replaced, and the fake loop answers from a small access
table keyed by the identity it is handed, so a wrong identity shows up as data.

Runs with no verified person (a service-to-service token, a missing user, a
service account, a removed account) are refused with a plain message.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from app.agents.agent_loop.protocol.formatter import AGUI_FORMATTER
from app.agents.constants.mcp_server_constants import get_mcp_credentials_path
from app.api.middlewares.auth import (
    _normalize_regular_payload,
    _normalize_scoped_payload,
)
from app.api.middlewares.identified_caller import UNIDENTIFIED_CALLER_DETAIL
from app.api.middlewares.token_policy import AuthTokenType
from app.harness.credentials.tool_credentials import toolset_reference
from tests.support.agent_routes import (
    AGENTS,
    FakeConfigService,
    InMemoryGraph,
    as_user,
    make_client,
    user_key,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi.testclient import TestClient
    from httpx import Response

ORG = "org-1"

# What each identity may read. The agent's configured sources are {"hr", "wiki"}.
ACCESS = {
    "u-alice": {"hr", "wiki", "alice-notes"},
    "u-carol": {"hr", "wiki", "carol-private"},
    "u-bob": {"wiki"},
}
AGENT_SOURCES = {"hr", "wiki"}

SERVICE_ACCOUNT = {"userId": "u-svc", "orgId": ORG, "email": "svc@service.test"}
# What the auth layer builds for the unattended callers.
CHAT_BOT = {
    "userId": "u-chat-bot",
    "orgId": ORG,
    "isServiceAccount": True,
    "token_type": AuthTokenType.SCOPED.value,
    "scopes": ["conversation:create"],
}
EXTRA = {
    "svc": SERVICE_ACCOUNT,
    "chat-bot": CHAT_BOT,
    "scoped-only": {
        "userId": "u-bot2",
        "orgId": ORG,
        "token_type": AuthTokenType.SCOPED.value,
        "scopes": ["conversation:create"],
    },
    "flag-only": {"userId": "u-bot3", "orgId": ORG, "isServiceAccount": True},
    "no-user": {"orgId": ORG},
    "no-org": {"userId": "u-bob"},
    "ghost": {"userId": "u-ghost", "orgId": ORG},
}


class AnsweringLoop:
    """Stands in for the agent loop: answers with what its identity may read within the agent's sources."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, query_info: dict, user_info: dict, *_a: object, **kwargs: object
    ) -> AsyncIterator[str]:
        self.calls.append({"query_info": query_info, "user_info": user_info, **kwargs})

        async def gen() -> AsyncIterator[str]:
            ctx = SimpleNamespace(run_id="run-1", conversation_id="conv-1")
            for frame in AGUI_FORMATTER.answer_final(
                ctx, completion_data={"answer": "ok", "citations": []}
            ):
                yield f"event: {frame['event']}\ndata: {json.dumps(frame['data'])}\n\n"

        return gen()

    def readable(self, call: dict[str, Any]) -> set[str]:
        return ACCESS.get(call["user_info"]["userId"], set()) & AGENT_SOURCES


@pytest.fixture
def graph() -> InMemoryGraph:
    g = InMemoryGraph()
    g.add_node("users", {"_key": "k-svc", **SERVICE_ACCOUNT, "kind": "service"})
    g.add_node(
        "users",
        {
            "_key": "k-ghost",
            "userId": "u-ghost",
            "orgId": ORG,
            "email": "g@acme.test",
            "isActive": False,
        },
    )
    # Three flavours of one agent: made by a member, made by an administrator, a plain one.
    g.add_agent("member-svc", "alice", share_with_org=True, isServiceAccount=True)
    g.add_agent(
        "bound-svc",
        "alice",
        share_with_org=True,
        isServiceAccount=True,
        serviceAccountUserId="u-svc",
    )
    g.add_agent("plain", "alice", share_with_org=True, isServiceAccount=False)
    g.add_agent(
        "admin-svc",
        "carol",
        share_with_org=True,
        isServiceAccount=True,
        serviceAccountUserId="u-svc",
    )
    return g


@pytest.fixture
def loop(monkeypatch) -> AnsweringLoop:
    fake = AnsweringLoop()
    monkeypatch.setattr("app.api.routes.agent.run_agent_loop_stream", fake)

    async def llm(*_a: object, **_k: object) -> tuple[object, dict, dict]:
        return object(), {"isMultimodal": False, "provider": "openai"}, {}

    monkeypatch.setattr("app.api.routes.agent.get_llm_for_chat", llm)
    return fake


@pytest.fixture
def scope_users(monkeypatch) -> list[str]:
    """The user id each turn's source scope was resolved for."""
    seen: list[str] = []
    from app.api.routes import agent as agent_routes

    real = agent_routes.prepare_turn_scope

    async def spy(graph_provider: Any, user_id: str, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        seen.append(user_id)
        return await real(graph_provider, user_id, *args, **kwargs)

    monkeypatch.setattr(agent_routes, "prepare_turn_scope", spy)
    return seen


def _attach_sources(graph: InMemoryGraph, *agents: str) -> None:
    for agent in agents:
        for source in sorted(AGENT_SOURCES):
            key = f"{agent}-{source}"
            graph.add_node(
                "agentKnowledge", {"_key": key, "connectorId": source, "type": "APP"}
            )
            graph.add_edge(
                "agentHasKnowledge",
                {"_from": f"{AGENTS}/{agent}", "_to": f"agentKnowledge/{key}"},
            )


def _client(
    graph: InMemoryGraph, config: FakeConfigService | None = None
) -> TestClient:
    return make_client(graph, config, extra_users=EXTRA)[0]


def _stream(
    client: TestClient, agent: str, caller: str | None, body: dict | None = None
) -> Response:
    headers = as_user(caller) if caller else {}
    return client.post(
        f"/api/v1/agent/{agent}/chat/stream",
        headers=headers,
        json=body or {"query": "hi"},
    )


def _chat(
    client: TestClient, agent: str, caller: str | None, body: dict | None = None
) -> Response:
    headers = as_user(caller) if caller else {}
    return client.post(
        f"/api/v1/agent/{agent}/chat", headers=headers, json=body or {"query": "hi"}
    )


ALL_KINDS = ["member-svc", "bound-svc", "plain", "admin-svc"]


class TestColleagueGetsOnlyTheirOwnReach:
    @pytest.mark.parametrize("agent", ALL_KINDS)
    def test_the_run_and_its_source_scope_use_the_colleague(
        self, graph, loop, scope_users, agent
    ) -> None:
        _attach_sources(graph, agent)
        response = _stream(_client(graph), agent, "bob")

        assert response.status_code == 200
        (call,) = loop.calls
        assert call["user_info"]["userId"] == "u-bob"
        assert call["user_info"]["email"] == "bob@acme.test"
        assert scope_users == ["u-bob"]
        # What the colleague can read within the agent's sources, and nothing of the owner's.
        assert loop.readable(call) == {"wiki"}

    def test_the_owner_keeps_their_own_wider_reach(
        self, graph, loop, scope_users
    ) -> None:
        _attach_sources(graph, "member-svc")
        assert _stream(_client(graph), "member-svc", "alice").status_code == 200
        (call,) = loop.calls
        assert call["user_info"]["userId"] == "u-alice"
        assert scope_users == ["u-alice"]
        assert loop.readable(call) == {"hr", "wiki"}

    def test_an_administrators_agent_answers_a_member_with_the_members_reach(
        self, graph, loop
    ) -> None:
        _attach_sources(graph, "admin-svc")
        client = _client(graph)
        assert _stream(client, "admin-svc", "bob").status_code == 200
        assert _stream(client, "admin-svc", "carol").status_code == 200
        member, admin = loop.calls
        assert loop.readable(member) == {"wiki"}
        assert loop.readable(admin) == {"hr", "wiki"}
        assert "carol-private" not in loop.readable(member)

    def test_non_streaming_chat_is_the_same_run(self, graph, loop) -> None:
        _attach_sources(graph, "member-svc")
        assert _chat(_client(graph), "member-svc", "bob").status_code == 200
        (call,) = loop.calls
        assert call["user_info"]["userId"] == "u-bob"

    def test_the_run_is_owned_by_the_person_asking(self, graph, loop) -> None:
        assert _stream(_client(graph), "member-svc", "bob").status_code == 200
        (call,) = loop.calls
        assert call["query_info"]["is_service_account"] is False
        assert call["query_info"]["artifact_owner_user_id"] is None
        assert call.get("cancellation_owner") is None


class TestNothingInTheRequestOrTheAgentGrantsAuthority:
    @pytest.mark.parametrize("flag", [True, False])
    def test_the_agents_own_service_flag_changes_nothing(
        self, graph, loop, flag
    ) -> None:
        graph.nodes[AGENTS]["plain"]["isServiceAccount"] = flag
        assert _stream(_client(graph), "plain", "bob").status_code == 200
        (call,) = loop.calls
        assert call["user_info"]["userId"] == "u-bob"
        assert call["query_info"]["is_service_account"] is False

    def test_identity_fields_in_the_body_grant_nothing(
        self, graph, loop, scope_users
    ) -> None:
        body = {
            "query": "hi",
            "isServiceAccount": True,
            "serviceAccountUserId": "u-alice",
            "userId": "u-alice",
            "orgId": ORG,
            "callerEmail": "cfo@acme.test",
            "callerDisplayName": "The CFO",
        }
        _attach_sources(graph, "plain")
        response = _stream(_client(graph), "plain", "bob", body)

        assert response.status_code == 200
        (call,) = loop.calls
        assert call["user_info"]["userId"] == "u-bob"
        assert call["user_info"]["email"] == "bob@acme.test"
        assert call["user_info"]["fullName"] != "The CFO"
        assert scope_users == ["u-bob"]
        assert loop.readable(call) == {"wiki"}

    def test_a_body_that_claims_the_agent_is_a_service_agent_does_not_remove_the_permission_check(
        self,
        graph,
        loop,
    ) -> None:
        graph.add_agent(
            "private-svc", "alice", isServiceAccount=True
        )  # shared with nobody
        response = _stream(
            _client(graph),
            "private-svc",
            "bob",
            {"query": "hi", "isServiceAccount": True},
        )
        assert response.status_code == 404
        assert not loop.calls


class TestNothingPersonalIsLogged:
    def test_a_run_logs_neither_the_callers_nor_the_owners_name_or_email(
        self, graph, loop, caplog
    ) -> None:
        import logging

        caplog.set_level(logging.DEBUG, logger="test.agent_routes")
        body = {
            "query": "hi",
            "callerDisplayName": "Bob B.",
            "callerEmail": "bob@acme.test",
        }
        assert _stream(_client(graph), "member-svc", "bob", body).status_code == 200

        messages = [
            r.getMessage() for r in caplog.records if r.name == "test.agent_routes"
        ]
        for personal in ("Bob B.", "bob@acme.test", "Alice", "alice@acme.test"):
            assert not any(personal in m for m in messages), f"{personal!r} was logged"


class TestCreatorStatusHasNoEffect:
    @pytest.mark.parametrize("change", [{"isActive": False}, {"isDisabled": True}])
    @pytest.mark.parametrize("agent", ["member-svc", "bound-svc"])
    def test_a_removed_or_disabled_creator_changes_nothing_for_a_colleague(
        self,
        graph,
        loop,
        agent,
        change,
    ) -> None:
        graph.nodes["users"][user_key("alice")].update(change)
        _attach_sources(graph, agent)
        response = _stream(_client(graph), agent, "bob")

        assert response.status_code == 200
        (call,) = loop.calls
        assert call["user_info"]["userId"] == "u-bob"
        assert loop.readable(call) == {"wiki"}

    def test_a_run_never_takes_the_creators_identity(
        self, graph, loop, scope_users
    ) -> None:
        _attach_sources(graph, "member-svc")
        assert _stream(_client(graph), "member-svc", "bob").status_code == 200
        (call,) = loop.calls
        # Neither the run, nor its source scope, nor its owner is the creator's.
        assert call["user_info"]["userId"] == "u-bob"
        assert call["user_info"]["email"] != "alice@acme.test"
        assert scope_users == ["u-bob"]
        assert "u-alice" not in [
            args[0] for args, _ in graph.calls_to("get_user_by_user_id")
        ]

    def test_an_agent_that_cannot_be_placed_in_an_org_refuses(
        self, graph, loop
    ) -> None:
        del graph.nodes["users"][user_key("alice")]
        response = _stream(_client(graph), "member-svc", "bob")
        assert response.status_code == 404
        assert not loop.calls

    def test_the_orgs_named_on_the_agent_wins_over_the_creators(
        self, graph, loop
    ) -> None:
        graph.nodes[AGENTS]["member-svc"]["orgId"] = "org-2"
        response = _stream(_client(graph), "member-svc", "bob")
        assert response.status_code == 404
        assert not loop.calls


class TestCredentialsAreTheCallersOwn:
    @staticmethod
    def _with_toolset(graph: InMemoryGraph, agent: str) -> None:
        graph.add_node(
            "agentToolsets",
            {
                "_key": "ts",
                "name": "jira",
                "displayName": "Jira",
                "instanceId": "inst-1",
            },
        )
        graph.add_node(
            "agentTools", {"_key": "t1", "name": "search", "fullName": "jira.search"}
        )
        graph.add_edge(
            "agentHasToolset", {"_from": f"{AGENTS}/{agent}", "_to": "agentToolsets/ts"}
        )
        graph.add_edge(
            "toolsetHasTool", {"_from": "agentToolsets/ts", "_to": "agentTools/t1"}
        )

    @pytest.mark.parametrize("agent", ["member-svc", "bound-svc"])
    def test_the_callers_own_connection_is_used(self, graph, loop, agent) -> None:
        self._with_toolset(graph, agent)
        config = FakeConfigService(
            {
                "/services/toolsets/inst-1/u-alice": {
                    "isAuthenticated": True,
                    "who": "owner",
                },
                f"/services/toolsets/inst-1/{agent}": {
                    "isAuthenticated": True,
                    "who": "agent",
                },
                "/services/toolsets/inst-1/u-bob": {
                    "isAuthenticated": True,
                    "who": "bob",
                },
            }
        )
        assert _stream(_client(graph, config), agent, "bob").status_code == 200
        (call,) = loop.calls
        assert call["query_info"]["toolsetConfigs"] == {
            "inst-1": toolset_reference("inst-1", "u-bob")
        }
        assert call["query_info"]["credentialOwner"] == "u-bob"

    @pytest.mark.parametrize("agent", ["member-svc", "bound-svc"])
    def test_without_their_own_connection_the_run_stops_and_borrows_nobody_elses(
        self,
        graph,
        loop,
        agent,
    ) -> None:
        self._with_toolset(graph, agent)
        config = FakeConfigService(
            {
                "/services/toolsets/inst-1/u-alice": {
                    "isAuthenticated": True,
                    "who": "owner",
                },
                f"/services/toolsets/inst-1/{agent}": {
                    "isAuthenticated": True,
                    "who": "agent",
                },
            }
        )
        response = _stream(_client(graph, config), agent, "bob")

        assert "RUN_ERROR" in response.text
        assert "not configured: 'Jira'" in response.text
        assert "Workspace" in response.text
        assert not loop.calls

    def test_the_owner_uses_their_own_connection_as_before(self, graph, loop) -> None:
        self._with_toolset(graph, "member-svc")
        config = FakeConfigService(
            {
                "/services/toolsets/inst-1/u-alice": {
                    "isAuthenticated": True,
                    "who": "owner",
                }
            }
        )
        assert _stream(_client(graph, config), "member-svc", "alice").status_code == 200
        (call,) = loop.calls
        assert call["query_info"]["toolsetConfigs"] == {
            "inst-1": toolset_reference("inst-1", "u-alice")
        }

    @staticmethod
    def _mcp_config(**records: dict) -> FakeConfigService:
        from app.services.featureflag.platform_settings import PLATFORM_SETTINGS_KEY

        instance = {
            "_id": "inst-m",
            "orgId": ORG,
            "createdBy": "u-alice",
            "name": "GitHub",
            "transport": "streamable_http",
            "url": "https://mcp.example.com/mcp",
            "authMode": "oauth",
            "useAdminAuth": False,
            "scope": "org",
        }
        return FakeConfigService(
            {
                PLATFORM_SETTINGS_KEY: {"featureFlags": {"ENABLE_MCP": True}},
                "/services/mcp/instances/inst-m": instance,
                **records,
            }
        )

    @staticmethod
    def _attach_mcp(graph: InMemoryGraph, agent: str) -> None:
        graph.add_node(
            "agentMcpServers",
            {
                "_key": "ms",
                "instanceId": "inst-m",
                "name": "github",
                "displayName": "GitHub",
            },
        )
        graph.add_edge(
            "agentHasMcpServer",
            {"_from": f"{AGENTS}/{agent}", "_to": "agentMcpServers/ms"},
        )

    def test_an_mcp_connection_made_by_someone_else_is_not_borrowed(
        self, graph, loop
    ) -> None:
        self._attach_mcp(graph, "member-svc")
        config = self._mcp_config(
            **{
                get_mcp_credentials_path("inst-m", "u-alice"): {
                    "isAuthenticated": True
                },
                get_mcp_credentials_path("inst-m", "member-svc"): {
                    "isAuthenticated": True
                },
            }
        )
        response = _stream(_client(graph, config), "member-svc", "bob")

        assert "mcp_server_config_missing" in response.text
        assert '"serviceAccount": false' in response.text
        assert not loop.calls

    def test_an_mcp_connection_of_the_caller_is_the_one_used(self, graph, loop) -> None:
        self._attach_mcp(graph, "member-svc")
        config = self._mcp_config(
            **{
                get_mcp_credentials_path("inst-m", "u-bob"): {"isAuthenticated": True},
            }
        )
        assert _stream(_client(graph, config), "member-svc", "bob").status_code == 200
        (call,) = loop.calls
        assert call["query_info"]["mcpServerConfigs"]["inst-m"]["ownerId"] == "u-bob"


# A request with no principal at all is stopped by the auth layer (401) before the agent route.
_NO_PRINCIPAL = ["no-user", "no-org", None]


class TestEveryKindOfAgentRunsTheWholeChatPath:
    """Each kind of agent takes its own branch through the chat route (a service agent's source
    narrowing, a bound one's collections, the universal assistant). Each must reach the loop
    with no error frame, so a name left undefined in any branch fails here, not in production."""

    @pytest.mark.parametrize("agent", [*ALL_KINDS, "agentIdPlaceholder"])
    @pytest.mark.parametrize("route", [_stream, _chat], ids=["stream", "chat"])
    @pytest.mark.parametrize("caller", ["bob", "alice"])
    def test_the_run_completes_for_a_person(
        self, graph, loop, caplog, agent, route, caller
    ) -> None:
        if agent != "agentIdPlaceholder":
            _attach_sources(graph, agent)
        response = route(_client(graph), agent, caller)

        assert response.status_code == 200, response.text[:500]
        assert (
            "RUN_ERROR" not in response.text and "event: error" not in response.text
        ), response.text[:500]
        (call,) = loop.calls
        assert call["user_info"]["userId"] == f"u-{caller}"
        assert not [
            r
            for r in caplog.records
            if r.exc_info and r.exc_info[0] in (NameError, UnboundLocalError)
        ]


class TestRunsWithNoVerifiedPersonAreRefused:
    @pytest.mark.parametrize("agent", ALL_KINDS)
    @pytest.mark.parametrize("caller", ["chat-bot", "scoped-only", "flag-only"])
    def test_a_service_principal_gets_the_refusal_and_no_data(
        self, graph, loop, agent, caller
    ) -> None:
        _attach_sources(graph, agent)
        response = _stream(_client(graph), agent, caller)

        assert response.status_code == 403
        # A service token is stopped at the scope check; a flagged regular token, by the agent's own refusal.
        expected = (
            "This route does not accept service tokens"
            if caller != "flag-only"
            else UNIDENTIFIED_CALLER_DETAIL
        )
        assert response.json()["detail"] == expected
        assert not loop.calls

    @pytest.mark.parametrize("route", [_stream, _chat], ids=["stream", "chat"])
    @pytest.mark.parametrize("caller", [*_NO_PRINCIPAL, "chat-bot", "flag-only"])
    def test_a_run_with_no_user_behind_it_never_reaches_the_agent(
        self, graph, loop, route, caller
    ) -> None:
        response = route(_client(graph), "member-svc", caller)
        assert response.status_code in (401, 403)
        assert not loop.calls
        assert not graph.calls_to("get_agent")

    @pytest.mark.parametrize("route", [_stream, _chat], ids=["stream", "chat"])
    @pytest.mark.parametrize("caller", ["svc", "ghost"])
    def test_a_service_account_or_a_removed_account_is_refused(
        self, graph, loop, route, caller
    ) -> None:
        response = route(_client(graph), "member-svc", caller)
        assert response.status_code == 403
        assert response.json()["detail"] == UNIDENTIFIED_CALLER_DETAIL
        assert not loop.calls

    def test_the_refusal_names_neither_the_creator_nor_the_agent_identity(
        self, graph, loop
    ) -> None:
        response = _stream(_client(graph), "bound-svc", "chat-bot")
        for hidden in ("alice", "u-alice", "u-svc", "bound-svc"):
            assert hidden not in response.text

    def test_the_agent_is_not_reached_before_the_refusal(self, graph, loop) -> None:
        _stream(_client(graph), "member-svc", "chat-bot")
        assert not graph.calls_to("get_agent")
        assert not graph.calls_to("get_user_by_user_id")

    def test_a_forged_flag_in_the_body_does_not_admit_an_unidentified_principal(
        self, graph, loop
    ) -> None:
        body = {
            "query": "hi",
            "isServiceAccount": False,
            "userId": "u-alice",
            "callerEmail": "alice@acme.test",
        }
        assert (
            _stream(_client(graph), "member-svc", "chat-bot", body).status_code == 403
        )
        assert not loop.calls


class TestAnotherOrgIsRefused:
    @pytest.mark.parametrize("agent", ALL_KINDS)
    def test_it_reads_the_same_as_a_missing_agent(self, graph, loop, agent) -> None:
        client = _client(graph)
        other = _stream(client, agent, "mallory")
        missing = _stream(client, "missing", "mallory")
        assert other.status_code == missing.status_code == 404
        assert other.json() == missing.json()
        assert not loop.calls

    def test_a_stray_share_edge_does_not_cross_orgs(self, graph, loop) -> None:
        graph.add_edge(
            "permission",
            {
                "_from": f"users/{user_key('mallory')}",
                "_to": f"{AGENTS}/member-svc",
                "type": "USER",
                "role": "READER",
            },
        )
        response = _stream(_client(graph), "member-svc", "mallory")
        assert response.status_code == 404
        assert not loop.calls

    def test_the_orgs_other_member_is_unaffected(self, graph, loop) -> None:
        assert _stream(_client(graph), "member-svc", "bob").status_code == 200


class TestSourcesTheCallerCannotReadStayOut:
    """A source the agent lists but the person cannot read is not searched for them."""

    def test_the_scope_is_resolved_for_the_person_for_every_kind_of_agent(
        self, graph, loop, scope_users
    ) -> None:
        for agent in ALL_KINDS:
            _attach_sources(graph, agent)
        client = _client(graph)
        for agent in ALL_KINDS:
            assert _stream(client, agent, "bob").status_code == 200
        assert scope_users == ["u-bob"] * len(ALL_KINDS)
        assert all(loop.readable(call) == {"wiki"} for call in loop.calls)


def _token(**claims: Any) -> dict[str, Any]:  # noqa: ANN401
    """An OAuth access token as it reaches a route, through the real normaliser."""
    raw = {
        "orgId": ORG,
        "tokenType": "oauth",
        "scope": "agent:execute conversation:chat",
        **claims,
    }
    return _normalize_regular_payload(raw, "tok")


class TestRealTokenShapes:
    """Tokens are built by the auth layer's own normalisers, so the shapes are what a route really sees."""

    def _client_with(
        self, graph: InMemoryGraph, name: str, user: dict[str, Any]
    ) -> TestClient:
        return make_client(graph, extra_users={name: user})[0]

    @pytest.mark.parametrize("agent", ALL_KINDS)
    @pytest.mark.parametrize("identity", ["u-carol", "u-alice", "u-svc"])
    def test_a_client_credentials_app_is_refused_whoever_it_acts_as(
        self, graph, loop, agent, identity
    ) -> None:
        # Node mints `userId == client_id` and puts the identity the app acts as in `createdBy`.
        user = _token(
            userId="client-123",
            client_id="client-123",
            createdBy=identity,
            role="admin",
        )
        client = self._client_with(graph, "app", user)
        for route in (_stream, _chat):
            response = route(client, agent, "app")
            assert response.status_code == 403
            assert response.json()["detail"] == UNIDENTIFIED_CALLER_DETAIL
        assert not loop.calls

    def test_the_auth_layer_marks_the_principal_kind_and_the_apps_own_id(self) -> None:
        app = _token(userId="client-123", client_id="client-123", createdBy="u-carol")
        assert (app["principalKind"], app["appId"]) == ("app", "client-123")
        person = _token(
            userId="u-bob", client_id="client-123", createdBy="client-identity"
        )
        assert person["principalKind"] == "human"
        assert "appId" not in person
        scoped = _normalize_scoped_payload(
            {"userId": "u1", "orgId": ORG, "scopes": ["conversation:create"]}, "tok"
        )
        assert scoped["principalKind"] == "service"

    def test_a_kind_claim_in_the_token_is_never_believed(self) -> None:
        forged = _token(
            userId="client-123",
            client_id="client-123",
            createdBy="u-carol",
            principalKind="human",
        )
        assert forged["principalKind"] == "app"
        person = _token(
            userId="u-bob", client_id="client-123", principalKind="app", appId="x"
        )
        assert person["principalKind"] == "human"
        assert "appId" not in person

    def test_an_app_owner_using_their_own_authorization_code_token_is_a_person(
        self, graph, loop
    ) -> None:
        # Authorization-code tokens also carry `createdBy` (the app's identity), but `userId` is the person's.
        user = _token(userId="u-bob", client_id="client-123", createdBy="u-carol")
        user["email"] = "bob@acme.test"
        _attach_sources(graph, "member-svc")
        response = _stream(
            self._client_with(graph, "bob-app", user), "member-svc", "bob-app"
        )

        assert response.status_code == 200
        (call,) = loop.calls
        assert call["user_info"]["userId"] == "u-bob"
        assert loop.readable(call) == {"wiki"}

    def test_a_personal_access_token_is_a_person(self, graph, loop) -> None:
        user = _token(userId="u-bob", scope="agent:execute")
        assert (
            _stream(self._client_with(graph, "pat", user), "plain", "pat").status_code
            == 200
        )
        assert loop.calls[0]["user_info"]["userId"] == "u-bob"


class TestTheStreamRouteAdmitsNoServiceToken:
    def test_the_assistant_stream_refuses_a_service_token_that_names_a_person(
        self, graph, loop
    ) -> None:
        # A service-to-service token naming a real user must not run the assistant as that user.
        named = {
            "userId": "u-bob",
            "orgId": ORG,
            "token_type": AuthTokenType.SCOPED.value,
            "scopes": ["conversation:create"],
        }
        client = make_client(graph, extra_users={"named": named})[0]
        response = client.post(
            "/api/v1/agent/agentIdPlaceholder/chat/stream",
            headers=as_user("named"),
            json={"query": "hi"},
        )
        assert response.status_code in (401, 403)
        assert not loop.calls

    def test_no_chat_route_lists_a_service_scope(self) -> None:
        from app.api.middlewares.auth import AUTH_POLICY_ATTR
        from app.api.routes.agent import router

        for route in router.routes:
            if getattr(route, "path", "").endswith(("/chat", "/chat/stream")):
                pending = [route.dependant]
                while pending:
                    d = pending.pop()
                    policy = getattr(d.call, AUTH_POLICY_ATTR, None)
                    assert policy is None or not policy.service_scopes, route.path
                    pending.extend(d.dependencies)


class TestEveryAgentLoopEntryRefusesAnAppOrServiceToken:
    """The assistant and the assistant chat run the agent loop too, so they refuse what a named agent refuses."""

    @pytest.mark.parametrize("path", ["chat", "chat/stream"])
    @pytest.mark.parametrize("identity", ["u-carol", "u-alice"])
    def test_the_universal_assistant_refuses_a_client_credentials_app(
        self, graph, loop, path, identity
    ) -> None:
        user = _token(
            userId="client-123",
            client_id="client-123",
            createdBy=identity,
            role="admin",
        )
        client = make_client(graph, extra_users={"app": user})[0]
        response = client.post(
            f"/api/v1/agent/agentIdPlaceholder/{path}",
            headers=as_user("app"),
            json={"query": "hi"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == UNIDENTIFIED_CALLER_DETAIL
        assert "personal access token" in response.json()["detail"]
        assert not loop.calls

    @pytest.mark.parametrize("path", ["chat", "chat/stream"])
    @pytest.mark.parametrize("who", ["app", "service"])
    def test_the_assistant_chat_refuses_an_app_and_a_service_principal(
        self, path, who
    ) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.routes.chatbot import router as chatbot_router
        from tests.support.agent_routes import _TestAuth

        users = {
            "app": _token(
                userId="client-123",
                client_id="client-123",
                createdBy="u-carol",
                role="admin",
            ),
            "service": {
                "userId": "u-bot",
                "orgId": ORG,
                "principalKind": "service",
                "token_type": "regular",
            },
        }
        app = FastAPI()
        app.include_router(chatbot_router, prefix="/api/v1")
        app.add_middleware(_TestAuth, users=users)
        response = TestClient(app, raise_server_exceptions=False).post(
            f"/api/v1/{path}",
            headers=as_user(who),
            json={"query": "hi"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == UNIDENTIFIED_CALLER_DETAIL
