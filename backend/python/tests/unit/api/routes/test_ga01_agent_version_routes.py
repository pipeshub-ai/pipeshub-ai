"""GA-01 / OWD-14: publish, draft and rollback through the real agent routes, and the version a run
definition is pinned to. Only the graph and the config store are replaced (`tests/support/agent_routes.py`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from tests.support.agent_routes import (
    FakeConfigService,
    InMemoryGraph,
    as_user,
    make_client,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi.testclient import TestClient


class _NoDescriptors:
    def toolset_tool(self, *_: object) -> None:
        return None

    async def approved_mcp_tools(self, *_: object) -> None:
        return None


# The toolset instance the agent attaches, as the toolsets admin writes it.
_TOOLSET_INSTANCES = {
    "/services/toolset-instances": [{"_id": "inst-1", "orgId": "org-1", "instanceName": "Slack", "toolsetType": "slack"}],
}
_SLACK = {"name": "Slack", "instanceId": "inst-1", "tools": [{"name": "send", "description": "Send a message"}]}


@pytest.fixture(params=["arango", "neo4j"])
def graph(request: pytest.FixtureRequest) -> InMemoryGraph:
    g = InMemoryGraph(backend=request.param)
    # An agent from before versions: no version exists until the migration or its first read.
    g.add_agent("helper", "alice", share_with_org=True, defaultReasoningEffort="low", systemPrompt="v1 prompt")
    g.add_agent("private", "alice")
    return g


@pytest.fixture
def client(graph: InMemoryGraph) -> Iterator[TestClient]:
    c, _ = make_client(graph, FakeConfigService(_TOOLSET_INSTANCES))
    with patch("app.api.routes.agent._enrich_agent_models", new_callable=AsyncMock), \
         patch("app.services.featureflag.platform_settings.is_actions_enabled", new_callable=AsyncMock, return_value=True), \
         patch("app.agents.mcp.service.is_mcp_enabled", new_callable=AsyncMock, return_value=True), \
         patch("app.harness.mcp_hub.settings.hub_enabled", return_value=False), \
         patch("app.harness.assembly.descriptor_pins.ToolDescriptors", return_value=_NoDescriptors()):
        yield c


def _definition(client: TestClient, user: str = "bob", version: int | None = None) -> dict[str, Any]:
    query = f"?version={version}" if version is not None else ""
    response = client.get(f"/api/v1/agent/helper/run-definition{query}", headers=as_user(user))
    assert response.status_code == 200, response.text
    return response.json()["definition"]


def _save(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    response = client.put("/api/v1/agent/helper", headers=as_user("alice"), json=body)
    assert response.status_code == 200, response.text
    return response.json()


class TestGa01PinnedVersions:
    def test_ga01_an_agent_from_before_versions_runs_as_version_1(self, client: TestClient) -> None:
        definition = _definition(client)

        assert (definition["definitionVersion"], definition["defaultReasoningEffort"]) == (1, "low")

    def test_ga01_a_draft_does_not_reach_runs_and_a_schedule_keeps_its_pinned_version(
        self, client: TestClient,
    ) -> None:
        pinned = _definition(client)["definitionVersion"]

        draft = _save(client, {"defaultReasoningEffort": "high", "publish": False})
        assert draft["version"] is None
        assert _definition(client)["defaultReasoningEffort"] == "low"

        published = client.post("/api/v1/agent/helper/versions", headers=as_user("alice"), json={"note": "deeper"})
        assert published.status_code == 201
        assert published.json()["version"]["version"] == 2

        latest = _definition(client)
        kept = _definition(client, version=pinned)
        assert (latest["definitionVersion"], latest["defaultReasoningEffort"]) == (2, "high")
        assert (kept["definitionVersion"], kept["defaultReasoningEffort"]) == (1, "low")

    def test_ga01_rollback_restores_the_prior_behaviour_as_a_new_version(self, client: TestClient) -> None:
        _definition(client)
        _save(client, {"defaultReasoningEffort": "high"})

        rolled = client.post("/api/v1/agent/helper/versions/1/rollback", headers=as_user("alice"), json={})

        assert rolled.status_code == 201
        assert (rolled.json()["version"]["version"], rolled.json()["version"]["rolledBackFrom"]) == (3, 1)
        definition = _definition(client)
        assert (definition["definitionVersion"], definition["defaultReasoningEffort"]) == (3, "low")
        assert _definition(client, version=2)["defaultReasoningEffort"] == "high"

    def test_ga01_a_pinned_version_the_store_does_not_hold_runs_the_latest_never_refused(
        self, client: TestClient,
    ) -> None:
        _save(client, {"defaultReasoningEffort": "high"})

        definition = _definition(client, version=9)

        assert (definition["definitionVersion"], definition["defaultReasoningEffort"]) == (1, "high")

    def test_ga01_an_agent_with_no_versions_pinned_by_an_old_schedule_runs_as_version_1(
        self, client: TestClient, graph: InMemoryGraph,
    ) -> None:
        definition = _definition(client, version=1)

        assert (definition["definitionVersion"], definition["defaultReasoningEffort"]) == (1, "low")
        assert list(graph.nodes["agentVersions"]) == ["helper-v1"]

    def test_ga01_who_cannot_run_the_agent_is_refused_whatever_version_they_name(self, client: TestClient) -> None:
        response = client.get("/api/v1/agent/private/run-definition?version=9", headers=as_user("bob"))

        assert response.status_code == 404


def _rollback(client: TestClient, version: int) -> dict[str, Any]:
    response = client.post(f"/api/v1/agent/helper/versions/{version}/rollback", headers=as_user("alice"), json={})
    assert response.status_code in (200, 201), response.text
    return response.json()


def _draft(client: TestClient) -> dict[str, Any]:
    return client.get("/api/v1/agent/helper", headers=as_user("alice")).json()["agent"]


class TestRollbackResetsTheDraft:
    """A rollback rewrites the draft to the version it restores."""

    @pytest.fixture
    def three_versions(self, client: TestClient) -> None:
        _save(client, {"instructions": "v1 steps", "toolsets": [_SLACK]})
        _save(client, {"instructions": "v2 steps", "defaultReasoningEffort": "high", "toolsets": []})
        _save(client, {"instructions": "unpublished steps", "defaultReasoningEffort": "medium", "publish": False})

    @pytest.mark.usefixtures("three_versions")
    def test_owd14_after_a_rollback_the_draft_is_the_version_rolled_back_to(self, client: TestClient) -> None:
        v1 = client.get("/api/v1/agent/helper/versions/1", headers=as_user("alice")).json()["version"]

        rolled = _rollback(client, 1)

        draft = _draft(client)
        assert (draft["instructions"], draft["defaultReasoningEffort"]) == ("v1 steps", "low")
        assert [(t["name"], t["instanceId"]) for t in draft["toolsets"]] == [("slack", "inst-1")]
        assert (rolled["version"]["version"], rolled["version"]["rolledBackFrom"]) == (3, 1)
        assert rolled["version"]["digest"] == v1["digest"]
        assert draft["versioning"] == {"latestVersion": 3, "latestDigest": v1["digest"], "unpublishedChanges": False}

    @pytest.mark.usefixtures("three_versions")
    def test_owd14_an_unpublished_edit_is_discarded_and_the_answer_says_so(self, client: TestClient) -> None:
        assert _rollback(client, 1)["discardedUnpublishedChanges"] is True
        assert _draft(client)["instructions"] == "v1 steps"
        # Nothing was left unpublished this time.
        assert _rollback(client, 2)["discardedUnpublishedChanges"] is False

    @pytest.mark.usefixtures("three_versions")
    def test_owd14_a_save_straight_after_a_rollback_publishes_nothing_older(self, client: TestClient) -> None:
        _rollback(client, 1)

        saved = _save(client, {"tags": ["triage"]})

        assert saved["version"]["version"] == 4
        definition = _definition(client)
        assert (definition["definitionVersion"], definition["defaultReasoningEffort"]) == (4, "low")
        assert [t["name"] for t in definition["toolsets"]] == ["slack"]
        four = client.get("/api/v1/agent/helper/versions/4", headers=as_user("alice")).json()["version"]["definition"]
        assert four["instructions"] == "v1 steps"

    @pytest.mark.usefixtures("three_versions")
    def test_owd14_a_rollback_that_fails_writing_the_draft_leaves_the_live_version_and_finishes_on_retry(
        self, client: TestClient, graph: InMemoryGraph,
    ) -> None:
        graph.fail("batch_create_edges")
        failed = client.post("/api/v1/agent/helper/versions/1/rollback", headers=as_user("alice"), json={})
        assert failed.status_code == 500
        assert _definition(client)["definitionVersion"] == 2

        graph.failures.clear()
        assert _rollback(client, 1)["version"]["version"] == 3
        assert _draft(client)["instructions"] == "v1 steps"

    @pytest.mark.usefixtures("three_versions")
    def test_owd14_a_rollback_keeps_the_agents_owner_and_sharing(self, client: TestClient, graph: InMemoryGraph) -> None:
        _rollback(client, 1)

        agent = graph.nodes["agentInstances"]["helper"]
        assert agent["createdBy"] == "k-alice"
        assert _draft(client)["shareWithOrg"] is True
        assert client.get("/api/v1/agent/helper/run-definition", headers=as_user("bob")).status_code == 200


class TestOwd14SaveAndPublish:
    def test_owd14_a_save_publishes_unless_it_asks_to_stay_a_draft(self, client: TestClient) -> None:
        saved = _save(client, {"defaultReasoningEffort": "medium"})

        assert saved["version"]["version"] == 1
        assert _definition(client)["defaultReasoningEffort"] == "medium"

    def test_owd14_publishing_an_unchanged_draft_answers_the_latest_version(self, client: TestClient) -> None:
        _save(client, {"defaultReasoningEffort": "medium"})

        again = client.post("/api/v1/agent/helper/versions", headers=as_user("alice"))

        assert (again.status_code, again.json()["created"], again.json()["version"]["version"]) == (200, False, 1)

    def test_owd14_the_version_list_shows_no_definition_and_says_when_the_draft_differs(
        self, client: TestClient,
    ) -> None:
        _definition(client)
        _save(client, {"defaultReasoningEffort": "high", "publish": False})

        listed = client.get("/api/v1/agent/helper/versions", headers=as_user("bob")).json()
        agent = client.get("/api/v1/agent/helper", headers=as_user("alice")).json()["agent"]

        assert [v["version"] for v in listed["versions"]] == [1]
        assert "definition" not in listed["versions"][0]
        assert (listed["latestVersion"], listed["unpublishedChanges"]) == (1, True)
        assert agent["versioning"]["unpublishedChanges"] is True
        one = client.get("/api/v1/agent/helper/versions/1", headers=as_user("bob")).json()["version"]
        assert one["definition"]["systemPrompt"] == "v1 prompt"


class TestOwd14Outage:
    def test_owd14_the_builder_opens_the_draft_when_versions_cannot_be_read(
        self, client: TestClient, graph: InMemoryGraph,
    ) -> None:
        graph.fail("get_nodes_by_filters")

        opened = client.get("/api/v1/agent/helper", headers=as_user("alice"))
        run = client.get("/api/v1/agent/helper/run-definition", headers=as_user("bob"))

        assert opened.status_code == 200
        assert "versioning" not in opened.json()["agent"]
        assert run.status_code == 500


class TestOwd14Access:
    @pytest.mark.parametrize("path", ["/versions", "/versions/1"])
    def test_owd14_who_cannot_see_the_agent_cannot_see_its_versions(self, client: TestClient, path: str) -> None:
        client.get("/api/v1/agent/private", headers=as_user("alice"))

        for user in ("bob", "mallory"):
            assert client.get(f"/api/v1/agent/private{path}", headers=as_user(user)).status_code == 404

    def test_owd14_a_reader_cannot_publish_or_roll_back(self, client: TestClient, graph: InMemoryGraph) -> None:
        _definition(client)

        publish = client.post("/api/v1/agent/helper/versions", headers=as_user("bob"))
        rollback = client.post("/api/v1/agent/helper/versions/1/rollback", headers=as_user("bob"))

        assert (publish.status_code, rollback.status_code) == (403, 403)
        assert list(graph.nodes["agentVersions"]) == ["helper-v1"]

    def test_owd14_another_org_cannot_publish(self, client: TestClient) -> None:
        response = client.post("/api/v1/agent/helper/versions", headers=as_user("mallory"))

        assert response.status_code == 404
