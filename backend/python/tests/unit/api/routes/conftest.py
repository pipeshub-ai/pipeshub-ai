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
