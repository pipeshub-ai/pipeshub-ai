"""OWD-14 / GA-01 on the worker: a run reads the definition of the version it pinned, never the draft,
while who owns and shares the agent stays the live agent's. A pin the store cannot answer never refuses a
run that ran before versions: it takes the latest published version, made from the live agent if none exists."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.agents.versions import AgentVersionStore, VersionSource
from app.harness.assembly.context import AgentRef
from app.harness.assembly.tool_environment import EDIT_STAMP_FLOOR, pinned_agent_version
from tests.support.agent_routes import InMemoryGraph


def _run(org: str = "org-1") -> Any:  # noqa: ANN401 - the RunContext fields the read uses
    return SimpleNamespace(org_id=org, session_id="ses-1", run_id="run-1")


def _ref(version: int) -> AgentRef:
    return AgentRef(agent_key="helper", definition_version=version, mode="auto")


@pytest.fixture(params=["arango", "neo4j"])
async def graph(request: pytest.FixtureRequest) -> InMemoryGraph:
    g = InMemoryGraph(backend=request.param)
    g.add_agent("helper", "alice", systemPrompt="v1 prompt", instructions="v1 steps")
    await AgentVersionStore(g).publish("org-1", "helper", await g.get_agent("helper", "org-1"), published_by="u-alice")
    g.nodes["agentInstances"]["helper"].update(systemPrompt="draft prompt", instructions="draft steps")
    return g


class TestOwd14WorkerPin:
    async def test_ga01_a_run_pinned_to_version_1_reads_version_1_not_the_draft(self, graph: InMemoryGraph) -> None:
        live = await graph.get_agent("helper", "org-1")

        seen = await pinned_agent_version(graph, _run(), _ref(1), live)

        assert (seen["systemPrompt"], seen["instructions"]) == ("v1 prompt", "v1 steps")
        assert seen["createdBy"] == live["createdBy"]

    async def test_owd14_a_pin_the_store_does_not_hold_runs_the_latest_published_version(
        self, graph: InMemoryGraph,
    ) -> None:
        live = await graph.get_agent("helper", "org-1")

        seen = await pinned_agent_version(graph, _run(), _ref(5), live)

        assert seen["systemPrompt"] == "v1 prompt"
        assert await AgentVersionStore(graph).numbers("org-1", "helper") == [1]

    async def test_ga01_an_agent_with_no_versions_gets_version_1_from_its_live_definition(self) -> None:
        graph = InMemoryGraph()
        graph.add_agent("helper", "alice", systemPrompt="as it stands")
        live = await graph.get_agent("helper", "org-1")

        seen = await pinned_agent_version(graph, _run(), _ref(1), live)

        assert seen["systemPrompt"] == "as it stands"
        stored = await AgentVersionStore(graph).get("org-1", "helper", 1)
        assert stored is not None and stored.source == VersionSource.MIGRATION

    async def test_owd14_a_run_admitted_before_versions_runs_the_live_agent(self, graph: InMemoryGraph) -> None:
        live = await graph.get_agent("helper", "org-1")

        seen = await pinned_agent_version(graph, _run(), _ref(EDIT_STAMP_FLOOR + 5), live)

        assert seen["systemPrompt"] == "draft prompt"
