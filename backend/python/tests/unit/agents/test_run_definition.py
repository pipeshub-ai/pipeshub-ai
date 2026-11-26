"""P-3: the execute-scoped run definition Node admits durable agent runs with."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

import app.edition_config  # noqa: F401  (edition seams load before any router)
from app.agents.run_definition import build_run_definition
from app.api.middlewares.auth import AUTH_POLICY_ATTR
from tests.support.agent_versions import versions_beside
from tests.support.bound_org import bind_test_org
from tests.support.mcp_tool_admission import (  # noqa: F401 - the fixtures, used by name
    mcp_approvals_open,
    mcp_approvals_required,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _bound_org() -> Iterator[str]:
    """``authMiddleware`` binds the caller's org (every caller here is in ``org-1``); these tests call the route
    functions directly."""
    yield from bind_test_org("org-1")



@pytest.fixture(autouse=True)
def _agent_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    """The routes' agent versions (OWD-14) beside this file's mocked graph."""
    versions_beside(monkeypatch, "app.api.routes.agent")


def _agent() -> dict[str, Any]:
    return {
        "_key": "agent-1",
        "systemPrompt": "never leaves Python",
        "updatedAtTimestamp": 1_700_000_000_123,
        "models": [
            {"modelKey": "m1", "modelName": "gpt-x", "provider": "openAI", "isReasoning": True},
            {"modelKey": "", "modelName": "dropped"},
        ],
        "defaultReasoningEffort": "low",
        "toolsets": [
            {
                "name": "jira",
                "instanceId": "inst-1",
                "tools": [
                    {"name": "search_issues", "fullName": "jira.search_issues"},
                    {"name": "", "fullName": "jira.nameless"},
                ],
            }
        ],
        "mcpServers": [
            {
                "instanceId": "mcp-9",
                "name": "github",
                "typeId": "github",
                "tools": [{"name": "list_prs", "fullName": "mcp_github_list_prs"}],
            },
            {"name": "no-instance"},
        ],
        "knowledge": [
            {"connectorId": "app-drive", "type": "APP"},
            {"connectorId": "kb-1", "type": "KB"},
        ],
        "skills": [{"name": "xlsx", "version": "3"}],
    }


class TestBuildRunDefinition:
    def test_projects_grants_without_prompts(self) -> None:
        definition = build_run_definition(
            _agent(),
            agent_key="agent-1",
            actions_enabled=True,
            mcp_enabled=True,
            web_search_provider="serper",
        )
        wire = definition.model_dump(by_alias=True, mode="json")
        assert wire == {
            "agentKey": "agent-1",
            "definitionVersion": 1_700_000_000_123,
            "models": [
                {"modelKey": "m1", "modelName": "gpt-x", "provider": "openAI", "isReasoning": True}
            ],
            "defaultReasoningEffort": "low",
            "toolsets": [
                {
                    "name": "jira",
                    "instanceId": "inst-1",
                    "tools": [{"name": "search_issues", "fullName": "jira.search_issues"}],
                }
            ],
            "mcpServers": [
                {
                    "instanceId": "mcp-9",
                    "name": "github",
                    "typeId": "github",
                    "tools": [{"name": "list_prs", "fullName": "mcp_github_list_prs"}],
                }
            ],
            "knowledge": [
                {"connectorId": "app-drive", "isKb": False},
                {"connectorId": "kb-1", "isKb": True},
            ],
            "skills": [{"name": "xlsx", "version": "3", "bundleDigest": None}],
            "webSearch": "serper",
        }
        assert "never leaves Python" not in json.dumps(wire)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(None, None), (3, 3), (0, 0), (-2, 0), (99, 16), ("4", 0), (True, 0), (2.5, 0)],
    )
    def test_ad1_max_children_is_the_agents_bounded_delegation(
        self, raw: object, expected: int | None
    ) -> None:
        agent = {**_agent(), **({} if raw is None else {"maxChildren": raw})}
        definition = build_run_definition(
            agent,
            agent_key="agent-1",
            actions_enabled=True,
            mcp_enabled=True,
            web_search_provider=None,
        )
        assert definition.max_children == expected
        wire = definition.model_dump(by_alias=True, mode="json")
        # Unset: absent from the wire, so Node pins the chat mode's default (K-L).
        assert wire.get("maxChildren", None) == expected
        assert ("maxChildren" in wire) is (expected is not None)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, None),
            ({"engines": ["opencode"], "mode": "plan"}, {"engines": ["opencode"], "mode": "plan"}),
            ({}, {}),
            ("plan", {"invalid": True}),
        ],
    )
    def test_ep17_the_engine_profile_is_carried_as_stored(self, raw: object, expected: object) -> None:
        agent = {**_agent(), **({} if raw is None else {"engineProfile": raw})}
        definition = build_run_definition(
            agent, agent_key="agent-1", actions_enabled=True, mcp_enabled=True, web_search_provider=None
        )
        wire = definition.model_dump(by_alias=True, mode="json")
        # Unset: absent from the wire, so Node runs the agent on the loop and never reads a ceiling.
        assert wire.get("engineProfile") == expected
        assert ("engineProfile" in wire) is (expected is not None)

    def test_disabled_platform_features_grant_nothing(self) -> None:
        definition = build_run_definition(
            _agent(),
            agent_key="agent-1",
            actions_enabled=False,
            mcp_enabled=False,
            web_search_provider=None,
        )
        assert definition.toolsets == ()
        assert definition.mcp_servers == ()
        assert definition.web_search is None
        assert len(definition.knowledge) == 2

    def test_an_agent_without_timestamps_is_version_one(self) -> None:
        definition = build_run_definition(
            {"toolsets": None, "knowledge": None},
            agent_key="a",
            actions_enabled=True,
            mcp_enabled=True,
            web_search_provider=None,
        )
        assert definition.definition_version == 1


def _route(path: str, method: str) -> APIRoute:
    from app.api.routes.agent import router

    for route in router.routes:
        if isinstance(route, APIRoute) and route.path == path and method in route.methods:
            return route
    raise AssertionError(f"{method} {path} is not mounted")


async def _run_scope_checks(route: APIRoute, user: dict[str, Any]) -> None:
    request = SimpleNamespace(state=SimpleNamespace(user=user))
    for dependency in route.dependant.dependencies:
        if getattr(dependency.call, AUTH_POLICY_ATTR, None) is not None:
            await dependency.call(request)


_EXECUTE_ONLY_PAT = {"userId": "u1", "orgId": "org-1", "isOAuth": True, "oauthScopes": ["agent:execute"]}
# What Node's Slack hydration mints for a Slack user without a PipesHub account.
_SLACK_SERVICE_TOKEN = {
    "orgId": "org-1",
    "userId": "5f0c0ffee0ddba11ab5e0001",
    "email": "someone@slack.example",
    "token_type": "scoped",
    "scopes": ["conversation:create"],
    "isServiceAccount": True,
}
_READ_ONLY_PAT = {"userId": "u1", "orgId": "org-1", "isOAuth": True, "oauthScopes": ["agent:read"]}


class TestRunDefinitionRouteScopes:
    @pytest.mark.asyncio
    async def test_an_execute_only_token_is_admitted(self) -> None:
        await _run_scope_checks(_route("/{agent_id}/run-definition", "GET"), _EXECUTE_ONLY_PAT)

    @pytest.mark.asyncio
    async def test_a_read_only_token_is_refused(self) -> None:
        with pytest.raises(HTTPException) as refused:
            await _run_scope_checks(_route("/{agent_id}/run-definition", "GET"), _READ_ONLY_PAT)
        assert refused.value.status_code == 403

    @pytest.mark.asyncio
    async def test_the_slack_service_token_is_admitted_and_no_other(self) -> None:
        # SF-6: the Slack bot admits service-account agent runs; the route decides which agents.
        await _run_scope_checks(_route("/{agent_id}/run-definition", "GET"), _SLACK_SERVICE_TOKEN)
        other = {**_SLACK_SERVICE_TOKEN, "scopes": ["fetch:config"]}
        with pytest.raises(HTTPException) as refused:
            await _run_scope_checks(_route("/{agent_id}/run-definition", "GET"), other)
        assert refused.value.status_code == 403

    @pytest.mark.asyncio
    async def test_the_full_agent_read_still_needs_agent_read(self) -> None:
        # Why admission cannot use GET /{agent_id} (P0.6 follow-up).
        with pytest.raises(HTTPException):
            await _run_scope_checks(_route("/{agent_id}", "GET"), _EXECUTE_ONLY_PAT)


def _services(permission: object, agent: dict[str, Any] | None) -> dict[str, Any]:
    graph_provider = AsyncMock()
    graph_provider.check_agent_permission = AsyncMock(return_value=permission)
    graph_provider.get_agent = AsyncMock(return_value=agent)
    return {"graph_provider": graph_provider, "config_service": AsyncMock(), "logger": MagicMock()}


def _request(user: dict[str, Any] | None = None) -> MagicMock:
    request = MagicMock()
    request.state.user = dict(user if user is not None else _EXECUTE_ONLY_PAT)
    request.query_params = {}
    return request


def _service_account_agent() -> dict[str, Any]:
    return {**_agent(), "isServiceAccount": True, "createdBy": "creator-key"}


def _defining():  # noqa: ANN202
    return (
        patch("app.api.routes.agent._enrich_agent_models", new_callable=AsyncMock),
        patch("app.services.featureflag.platform_settings.is_actions_enabled", new_callable=AsyncMock, return_value=True),
        patch("app.agents.mcp.service.is_mcp_enabled", new_callable=AsyncMock, return_value=False),
    )


class TestRunDefinitionRoute:
    @pytest.mark.asyncio
    async def test_returns_the_definition_for_the_callers_own_access(self) -> None:
        from app.api.routes.agent import get_agent_run_definition

        agent = _agent()
        agent["webSearch"] = {"provider": "tavily"}
        services = _services({"role": "READER"}, agent)
        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}), \
             patch("app.api.routes.agent._enrich_agent_models", new_callable=AsyncMock), \
             patch("app.services.featureflag.platform_settings.is_actions_enabled", new_callable=AsyncMock, return_value=True), \
             patch("app.agents.mcp.service.is_mcp_enabled", new_callable=AsyncMock, return_value=False):
            response = await get_agent_run_definition(_request(), "agent-1")

        services["graph_provider"].check_agent_permission.assert_awaited_once_with("agent-1", "uk1", "org-1")
        body = json.loads(response.body)["definition"]
        assert body["agentKey"] == "agent-1"
        assert body["webSearch"] == "tavily"
        assert body["mcpServers"] == []
        assert [t["name"] for t in body["toolsets"]] == ["jira"]

    @pytest.mark.asyncio
    async def test_no_access_is_not_found_and_defines_nothing(self) -> None:
        from app.api.routes.agent import get_agent_run_definition

        services = _services(None, _agent())
        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}), \
             patch("app.api.routes.agent._enrich_agent_models", new_callable=AsyncMock) as enrich:
            with pytest.raises(HTTPException) as refused:
                await get_agent_run_definition(_request(), "agent-1")
        assert refused.value.status_code == 404
        enrich.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sf6_a_service_token_runs_a_service_account_agent_of_its_org(self) -> None:
        from app.api.routes.agent import get_agent_run_definition

        services = _services(None, _service_account_agent())
        services["graph_provider"].get_document = AsyncMock(return_value={"orgId": "org-1"})
        enrich, actions, mcp = _defining()
        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock) as user_doc, \
             enrich, actions, mcp:
            response = await get_agent_run_definition(_request(_SLACK_SERVICE_TOKEN), "agent-1")
        assert json.loads(response.body)["definition"]["agentKey"] == "agent-1"
        # The synthetic Slack identity has no user document and no share, as in legacy.
        user_doc.assert_not_awaited()
        services["graph_provider"].check_agent_permission.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sf6_a_service_token_is_refused_another_orgs_agent(self) -> None:
        from app.api.routes.agent import get_agent_run_definition

        services = _services(None, _service_account_agent())
        services["graph_provider"].get_document = AsyncMock(return_value={"orgId": "org-2"})
        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services):
            with pytest.raises(HTTPException) as refused:
                await get_agent_run_definition(_request(_SLACK_SERVICE_TOKEN), "agent-1")
        assert refused.value.status_code == 404

    @pytest.mark.asyncio
    async def test_sf6_a_service_token_is_refused_an_ordinary_agent(self) -> None:
        from app.api.routes.agent import get_agent_run_definition

        services = _services({"role": "OWNER"}, _agent())
        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}):
            with pytest.raises(HTTPException) as refused:
                await get_agent_run_definition(_request(_SLACK_SERVICE_TOKEN), "agent-1")
        assert refused.value.status_code == 404
        services["graph_provider"].check_agent_permission.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sf6_a_user_of_the_org_runs_a_service_account_agent_without_a_share(self) -> None:
        # Legacy's chat route never checks a share for a service-account agent.
        from app.api.routes.agent import get_agent_run_definition

        services = _services(None, _service_account_agent())
        services["graph_provider"].get_document = AsyncMock(return_value={"orgId": "org-1"})
        enrich, actions, mcp = _defining()
        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             enrich, actions, mcp:
            response = await get_agent_run_definition(_request(), "agent-1")
        assert json.loads(response.body)["definition"]["agentKey"] == "agent-1"

    @pytest.mark.asyncio
    async def test_a_store_failure_is_an_error_not_a_denial(self) -> None:
        from app.api.routes.agent import get_agent_run_definition

        services = _services({"role": "OWNER"}, _agent())
        services["graph_provider"].check_agent_permission = AsyncMock(side_effect=RuntimeError("graph down"))
        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}):
            with pytest.raises(HTTPException) as failed:
                await get_agent_run_definition(_request(), "agent-1")
        assert failed.value.status_code == 500


class TestEngineAgentDefinition:
    """AG1 RD: the run definition reads the engine settings the agents module stored."""

    def test_rd1_the_stored_narrowing_and_cloud_pin_are_on_the_wire(self) -> None:
        agent = {**_agent(), "engineProfile": {"accountKinds": ["org_cloud"]}, "modelAccountId": "cloud/acc-1"}
        wire = build_run_definition(
            agent, agent_key="agent-1", actions_enabled=True, mcp_enabled=True, web_search_provider=None
        ).model_dump(by_alias=True, mode="json")
        assert (wire["engineProfile"], wire["modelAccountId"]) == ({"accountKinds": ["org_cloud"]}, "cloud/acc-1")
        plain = build_run_definition(
            _agent(), agent_key="agent-1", actions_enabled=True, mcp_enabled=True, web_search_provider=None
        ).model_dump(by_alias=True, mode="json")
        assert "engineProfile" not in plain and "modelAccountId" not in plain

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("stored", "expected"),
        [('{"accountKinds":["user_api_key"]}', {"accountKinds": ["user_api_key"]}), ("{not json", {"invalid": True})],
        ids=["stored-text", "unreadable-text"],
    )
    async def test_rd2_the_route_serves_what_the_provider_decoded(self, stored: str, expected: object) -> None:
        from app.api.routes.agent import get_agent_run_definition
        from app.services.graph_db.common.utils import decode_agent_engine_profile

        services = _services({"role": "READER"}, decode_agent_engine_profile({**_agent(), "engineProfile": stored}))
        enrich, actions, mcp = _defining()
        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}), \
             enrich, actions, mcp:
            response = await get_agent_run_definition(_request(), "agent-1")
        assert json.loads(response.body)["definition"]["engineProfile"] == expected


class TestRunDefinitionOwner:
    """CPD-7 ESR-10: the definition names the agent's owner by user id, so the control plane can tell what the
    owner shared through the agent; an unreadable creator names none."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("creator", "owner"), [({"_key": "ck", "userId": "mongo-7"}, "mongo-7"), (None, None)])
    async def test_the_owner_is_the_creators_user_id(self, creator: dict[str, Any] | None, owner: str | None) -> None:
        from app.api.routes.agent import get_agent_run_definition

        services = _services({"role": "READER"}, {**_agent(), "createdBy": "ck"})
        services["graph_provider"].get_document = AsyncMock(return_value=creator)
        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}), \
             patch("app.api.routes.agent._enrich_agent_models", new_callable=AsyncMock), \
             patch("app.services.featureflag.platform_settings.is_actions_enabled", new_callable=AsyncMock, return_value=True), \
             patch("app.agents.mcp.service.is_mcp_enabled", new_callable=AsyncMock, return_value=False):
            response = await get_agent_run_definition(_request(), "agent-1")
        body = json.loads(response.body)["definition"]
        assert body.get("owner") == owner
        assert ("owner" in body) is (owner is not None)


_LIVE_DIGEST = "sha256:" + "d" * 64


class TestHubOffSavedAgentPins:
    """WP84-FU-1: with the MCP hub off a saved agent's MCP tools are pinned at admission to the listing its server
    gives the run's credential owner now, as the assistant's are, so the claim withholds a tool whose schema changed
    since instead of registering whatever the server lists then."""

    @staticmethod
    async def _define(agent: dict[str, Any], *, hub: bool, user: dict[str, Any] | None = None) -> tuple[dict, AsyncMock]:
        from app.api.routes.agent import get_agent_run_definition

        services = _services({"role": "READER"}, agent)
        services["graph_provider"].get_document = AsyncMock(return_value={"orgId": "org-1"})
        listing = AsyncMock(return_value=({"mcp-9": {"list_prs": _LIVE_DIGEST}}, frozenset({"mcp-9"})))
        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock,
                   return_value={"_key": "uk1", "email": "u1@x"}), \
             patch("app.api.routes.agent._enrich_agent_models", new_callable=AsyncMock), \
             patch("app.services.featureflag.platform_settings.is_actions_enabled", new_callable=AsyncMock,
                   return_value=True), \
             patch("app.agents.mcp.service.is_mcp_enabled", new_callable=AsyncMock, return_value=True), \
             patch("app.harness.mcp_hub.settings.hub_enabled", return_value=hub), \
             patch("app.api.routes.agent._live_mcp_listing", listing):
            response = await get_agent_run_definition(_request(user), "agent-1")
        return json.loads(response.body)["definition"], listing

    @pytest.mark.asyncio
    async def test_hub_off_a_saved_agents_mcp_tool_is_pinned_to_its_live_listing(self) -> None:
        definition, listing = await self._define(_agent(), hub=False)
        [server] = definition["mcpServers"]
        assert [(t["name"], t["descriptorDigest"]) for t in server["tools"]] == [("list_prs", _LIVE_DIGEST)]
        listing.assert_awaited_once()
        assert listing.await_args.kwargs == {"agent_id": "agent-1", "is_service_account": False, "failures": []}

    @pytest.mark.asyncio
    async def test_hub_off_a_service_account_agent_lists_with_the_agents_own_credentials(self) -> None:
        _, listing = await self._define(_service_account_agent(), hub=False)
        assert listing.await_args.kwargs == {"agent_id": "agent-1", "is_service_account": True, "failures": []}

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mcp_approvals_open")
    async def test_d_mcp_5_hub_on_with_no_approval_required_it_is_pinned_to_its_live_listing(self) -> None:
        definition, listing = await self._define(_agent(), hub=True)
        [server] = definition["mcpServers"]
        assert [(t["name"], t["descriptorDigest"]) for t in server["tools"]] == [("list_prs", _LIVE_DIGEST)]
        listing.assert_awaited_once()

class TestLiveListingCredentials:
    @pytest.mark.asyncio
    async def test_a_service_account_agent_lists_with_the_agents_credential_record(self) -> None:
        from app.api.routes.agent import _live_mcp_listing

        servers = [{"instanceId": "mcp-9", "name": "github"}]
        with patch("app.api.routes.agent._resolve_mcp_server_configs", new_callable=AsyncMock,
                   return_value=({}, [], [], [])) as resolve:
            assert await _live_mcp_listing(
                servers, AsyncMock(), {"userId": "u1"}, {}, MagicMock(), agent_id="agent-1", is_service_account=True,
            ) == ({}, frozenset())
        assert resolve.await_args.kwargs["credential_lookup_id"] == "agent-1"
        assert resolve.await_args.kwargs["executing_user_id"] == "u1"
        assert resolve.await_args.kwargs["agent_id"] == "agent-1"
        assert resolve.await_args.kwargs["is_service_account"] is True
