"""A service-account agent is given only sources shared with the whole org, checked when it
is saved (`app/api/routes/agent.py`). Anyone in the org runs it on one identity, so a personal
connector is refused, and a collection is refused unless it runs as a bound service account.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.support.agent_routes import AGENTS, InMemoryGraph, as_user, make_client

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


@pytest.fixture
def graph() -> InMemoryGraph:
    g = InMemoryGraph()
    for key, kind, scope, owner in (
        ("drive-alice", "DRIVE", "personal", "u-alice"),
        ("pg-alice", "POSTGRESQL", "personal", "u-alice"),
        ("drive-bob", "DRIVE", "personal", "u-bob"),
        ("drive-team", "DRIVE", "team", "u-alice"),
        ("kb-1", "KB", "team", "u-alice"),
    ):
        g.add_node("apps", {"_key": key, "name": f"{key} name", "type": kind, "scope": scope, "orgId": "org-1",
                            "createdBy": owner})
    for key, kind in (("drive-org2", "DRIVE"), ("kb-org2", "KB")):
        g.add_node("apps", {"_key": key, "name": f"{key} name", "type": kind, "scope": "team", "orgId": "org-2",
                            "createdBy": "u-mallory"})
    g.add_agent("private", "alice")
    g.add_agent("sa", "alice", share_with_org=True, isServiceAccount=True)
    g.add_agent("sa-bound", "alice", share_with_org=True, isServiceAccount=True, serviceAccountUserId="u-svc")
    return g


@pytest.fixture
def client(graph: InMemoryGraph) -> TestClient:
    c, _ = make_client(graph)
    return c


def _knowledge(*ids: str) -> list[dict]:
    return [{"connectorId": i} for i in ids]


def _attach(graph: InMemoryGraph, agent: str, *ids: str) -> None:
    for i in ids:
        graph.add_node("agentKnowledge", {"_key": f"k-{agent}-{i}", "connectorId": i})
        graph.add_edge("agentHasKnowledge", {"_from": f"{AGENTS}/{agent}", "_to": f"agentKnowledge/k-{agent}-{i}"})


class TestCreate:
    @pytest.mark.parametrize("source", ["drive-alice", "pg-alice"])
    def test_a_personal_source_is_refused(self, client, graph, source) -> None:
        response = client.post("/api/v1/agent/create", headers=as_user("alice"), json={
            "name": "Helpdesk", "isServiceAccount": True, "knowledge": _knowledge("drive-team", source),
        })
        assert response.status_code == 400
        assert f"Personal sources ({source} name)" in response.json()["detail"]
        assert not graph.calls_to("begin_transaction")

    def test_someone_elses_source_is_not_named(self, client) -> None:
        response = client.post("/api/v1/agent/create", headers=as_user("alice"), json={
            "name": "Helpdesk", "isServiceAccount": True, "knowledge": _knowledge("drive-bob"),
        })
        assert response.status_code == 400
        assert "Personal sources (one that someone else owns)" in response.json()["detail"]
        assert "drive-bob" not in response.json()["detail"]

    def test_a_collection_is_refused_without_a_binding(self, client) -> None:
        response = client.post("/api/v1/agent/create", headers=as_user("alice"), json={
            "name": "Helpdesk", "isServiceAccount": True, "knowledge": _knowledge("kb-1"),
        })
        assert response.status_code == 400
        assert "Collections (kb-1 name)" in response.json()["detail"]

    def test_the_orgs_connectors_are_fine(self, client) -> None:
        response = client.post("/api/v1/agent/create", headers=as_user("alice"), json={
            "name": "Helpdesk", "isServiceAccount": True, "knowledge": _knowledge("drive-team"),
        })
        assert response.status_code == 200

    def test_an_agent_the_owner_runs_may_use_their_own_sources(self, client) -> None:
        response = client.post("/api/v1/agent/create", headers=as_user("alice"), json={
            "name": "Mine", "knowledge": _knowledge("drive-alice", "pg-alice", "kb-1"),
        })
        assert response.status_code == 200

    def test_sources_that_cannot_be_checked_refuse_the_save(self, client, graph) -> None:
        graph.fail("get_org_apps")
        response = client.post("/api/v1/agent/create", headers=as_user("alice"), json={
            "name": "Helpdesk", "isServiceAccount": True, "knowledge": _knowledge("drive-team"),
        })
        assert response.status_code == 503
        assert "couldn't be checked" in response.json()["detail"]
        assert not graph.calls_to("begin_transaction")


    @pytest.mark.parametrize("source", ["drive-org2", "no-such-source"])
    def test_a_source_outside_the_organization_is_refused(self, client, graph, source) -> None:
        response = client.post("/api/v1/agent/create", headers=as_user("alice"), json={
            "name": "Helpdesk", "isServiceAccount": True, "knowledge": _knowledge("drive-team", source),
        })
        assert response.status_code == 400
        assert "1 knowledge source(s) of this agent were not found in your organization" in response.json()["detail"]
        assert source not in response.json()["detail"]
        assert not graph.calls_to("begin_transaction")


class TestUpdate:
    def test_adding_a_personal_source_is_refused(self, client, graph) -> None:
        response = client.put("/api/v1/agent/sa", headers=as_user("alice"), json={"knowledge": _knowledge("drive-alice")})
        assert response.status_code == 400
        assert not graph.calls_to("update_agent")

    def test_a_bound_agent_may_be_given_a_collection(self, client) -> None:
        response = client.put("/api/v1/agent/sa-bound", headers=as_user("alice"), json={"knowledge": _knowledge("kb-1")})
        assert response.status_code == 200

    def test_a_bound_agent_may_not_be_given_another_organizations_collection(self, client, graph) -> None:
        response = client.put("/api/v1/agent/sa-bound", headers=as_user("alice"), json={"knowledge": _knowledge("kb-org2")})
        assert response.status_code == 400
        assert "not found in your organization" in response.json()["detail"]
        assert not graph.calls_to("update_agent")

    def test_turning_an_agent_with_personal_sources_into_a_service_agent_is_refused(self, client, graph) -> None:
        _attach(graph, "private", "drive-alice")
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={"isServiceAccount": True})
        assert response.status_code == 400
        assert "Personal sources (drive-alice name)" in response.json()["detail"]
        assert graph.nodes[AGENTS]["private"].get("isServiceAccount") is not True

    def test_other_edits_to_an_existing_agent_still_save(self, client, graph) -> None:
        # Its personal sources are left out on every run; renaming it needs no fix first.
        _attach(graph, "sa", "drive-alice")
        response = client.put("/api/v1/agent/sa", headers=as_user("alice"), json={"name": "Renamed"})
        assert response.status_code == 200
        assert graph.nodes[AGENTS]["sa"]["name"] == "Renamed"
