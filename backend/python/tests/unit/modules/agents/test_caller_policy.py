"""The base caller policy (`AgentCallerPolicy`), and the shared code reading the edition's policy."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.agents.caller_policy import (
    AgentCallerPolicy,
    default_agent_caller_policy,
    get_agent_caller_policy,
    guard_agent_route,
)
from app.modules.agents.service.builders import (
    _assert_no_personal_mcp_servers,
    _assert_no_personal_toolsets,
)
from app.modules.agents.service.errors import InvalidRequestError

_PERSONAL = {"alice-jira": {"_id": "alice-jira", "name": "alice-jira", "scope": "personal", "createdBy": "u-alice"}}
_CHECKS = [(_assert_no_personal_toolsets, "Personal actions"), (_assert_no_personal_mcp_servers, "Personal MCP servers")]


class _AsTheCaller(AgentCallerPolicy):
    """A policy that runs every agent, a service agent included, as the person asking."""

    def service_agents_run_as_themselves(self) -> bool:
        return False


def _serve(monkeypatch: pytest.MonkeyPatch, policy: AgentCallerPolicy) -> None:
    monkeypatch.setattr("app.edition_services.agent_caller_policy", lambda: policy)


class TestTheBasePolicy:
    def test_a_service_agent_runs_as_itself(self) -> None:
        assert default_agent_caller_policy().service_agents_run_as_themselves() is True

    async def test_it_refuses_no_caller_and_admits_every_agent_and_person(self) -> None:
        policy = default_agent_caller_policy()
        await policy.guard_route(MagicMock())
        policy.check_caller({"userId": "u-1", "orgId": "org-1", "isScopedToken": True})
        await policy.admit_agent({"orgId": "org-2"}, graph_provider=AsyncMock(), org_id="org-1", agent_id="a1")
        policy.admit_person({"kind": "service", "isActive": False})

    def test_it_adds_nothing_to_the_caller_context_or_the_run(self) -> None:
        policy = default_agent_caller_policy()
        assert policy.caller_fields({"userId": "u-1", "principalKind": "app"}) == {}
        assert policy.run_query_fields(service_agent=True) == {}
        assert policy.run_loop_options(service_agent=True) == {}


class TestTheEditionsPolicyIsRead:
    def test_the_policy_is_the_one_the_edition_serves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        policy = _AsTheCaller()
        _serve(monkeypatch, policy)
        assert get_agent_caller_policy() is policy

    async def test_the_route_guard_asks_the_editions_policy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        guarded: list[object] = []

        class _Guarded(AgentCallerPolicy):
            async def guard_route(self, request: object) -> None:
                guarded.append(request)

        _serve(monkeypatch, _Guarded())
        request = MagicMock()
        await guard_agent_route(request)
        assert guarded == [request]

    async def test_a_refusing_guard_stops_the_route(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Refusing(AgentCallerPolicy):
            async def guard_route(self, request: object) -> None:
                raise PermissionError("refused")

        _serve(monkeypatch, _Refusing())
        with pytest.raises(PermissionError, match="refused"):
            await guard_agent_route(MagicMock())


class TestAPersonalToolOnAnAgentOthersRun:
    """A service agent refuses a personal tool in every edition; the refusal names service agents only
    where they run as themselves."""

    @pytest.mark.parametrize(("check", "what"), _CHECKS)
    def test_the_base_policy_names_service_agents(
        self, monkeypatch: pytest.MonkeyPatch, check: object, what: str,
    ) -> None:
        _serve(monkeypatch, default_agent_caller_policy())
        with pytest.raises(InvalidRequestError, match=f"{what} \\(alice-jira\\) can't be attached to an agent that is shared with the organization or runs as a service account\\.$"):
            check(_PERSONAL, shared_with_org=False, service_account=True)

    @pytest.mark.parametrize(("check", "what"), _CHECKS)
    def test_a_policy_that_runs_agents_as_the_caller_names_sharing_only(
        self, monkeypatch: pytest.MonkeyPatch, check: object, what: str,
    ) -> None:
        _serve(monkeypatch, _AsTheCaller())
        with pytest.raises(InvalidRequestError, match=f"{what} \\(alice-jira\\) can't be attached to an agent that is shared with the organization\\.$"):
            check(_PERSONAL, shared_with_org=False, service_account=True)

    @pytest.mark.parametrize(("check", "_what"), _CHECKS)
    def test_an_agent_only_its_owner_runs_may_keep_one(self, check: object, _what: str) -> None:
        check(_PERSONAL, shared_with_org=False, service_account=False)
