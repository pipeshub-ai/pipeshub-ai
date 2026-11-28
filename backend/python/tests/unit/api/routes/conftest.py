"""Shared setup for the route tests in this directory.

An edition may replace ``get_services``, ``_enrich_agent_models`` and
``sa_forces_org_sharing`` in ``app.api.routes.agent`` when it loads. These tests
exercise the base implementations against a plain mocked container and config
service, so they pin the base ones back for every test; an edition's own
replacements are covered by that edition's tests.
"""

import pytest

import app.api.routes.agent as agent_routes


@pytest.fixture(autouse=True)
def _base_agent_route_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_routes, "get_services", agent_routes._base_get_services)
    monkeypatch.setattr(agent_routes, "_enrich_agent_models", agent_routes._base_enrich_agent_models)
    monkeypatch.setattr(agent_routes, "sa_forces_org_sharing", agent_routes._base_sa_forces_org_sharing)


@pytest.fixture
def default_caller_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """The base caller policy (`AgentCallerPolicy`), for tests of what it decides: a service-account
    agent runs as itself, on the credentials kept for it. An edition may install its own policy."""
    from app.modules.agents.caller_policy import default_agent_caller_policy

    monkeypatch.setattr("app.edition_services.agent_caller_policy", default_agent_caller_policy)


@pytest.fixture
def agents_run_as_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller policy under which every agent, a service agent included, runs as the person asking, on
    their own credentials: the edition's own policy when it already does, else a minimal one."""
    from app.modules.agents.caller_policy import (
        AgentCallerPolicy,
        get_agent_caller_policy,
    )

    if not get_agent_caller_policy().service_agents_run_as_themselves():
        return

    class _RunsAgentsAsTheCaller(AgentCallerPolicy):
        def service_agents_run_as_themselves(self) -> bool:
            return False

    policy = _RunsAgentsAsTheCaller()
    monkeypatch.setattr("app.edition_services.agent_caller_policy", lambda: policy)
