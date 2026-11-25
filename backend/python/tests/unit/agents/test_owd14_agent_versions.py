"""OWD-14 (ADR-OWD-14): the agent version store. Versions are immutable, numbered per agent, never
renumbered by a race, and read only within their org; rollback republishes, it never rewrites."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.agents.versions import (
    AgentVersionStore,
    VersionSource,
    agent_definition,
    definition_digest,
    pinned_agent,
    version_key,
)
from app.migrations.agent_versions_migration import (
    migrate_agent_versions,
    run_agent_versions_migration,
)
from app.services.org_erasure import erase_org_page
from tests.support.agent_routes import (
    AGENTS,
    FakeConfigService,
    InMemoryGraph,
    user_key,
)

VERSIONS = "agentVersions"


def _agent(**fields: object) -> dict[str, Any]:
    return {
        "_key": "a1", "createdBy": "k-alice", "updatedAtTimestamp": 5, "isDeleted": False, "shareWithOrg": True,
        "name": "Helper", "systemPrompt": "Be brief.", "models": ["m1_gpt"],
        "toolsets": [{"_key": "ts-1", "name": "slack", "tools": [{"_key": "t-1", "name": "send", "fullName": "slack.send"}]}],
        **fields,
    }


class _YieldingGraph(InMemoryGraph):
    """Gives the event loop a turn at every read and write, so concurrent publishers interleave."""

    async def ensure_nodes(self, *args: object, **kwargs: object) -> None:
        await asyncio.sleep(0)
        await super().ensure_nodes(*args, **kwargs)

    async def get_nodes_by_filters(self, *args: object, **kwargs: object) -> list[dict]:
        await asyncio.sleep(0)
        return await super().get_nodes_by_filters(*args, **kwargs)


@pytest.fixture
def store() -> AgentVersionStore:
    return AgentVersionStore(InMemoryGraph())


class TestOwd14Publish:
    async def test_owd14_versions_are_numbered_from_one_and_an_unchanged_draft_publishes_nothing(
        self, store: AgentVersionStore,
    ) -> None:
        v1, created1 = await store.publish("org-1", "a1", _agent(), published_by="u-alice")
        same, created_again = await store.publish("org-1", "a1", _agent(updatedAtTimestamp=9), published_by="u-alice")
        v2, created2 = await store.publish("org-1", "a1", _agent(systemPrompt="Be thorough."), published_by="u-bob")

        assert (v1.version, created1) == (1, True)
        assert (same.version, created_again) == (1, False)
        assert (v2.version, created2, v2.published_by) == (2, True, "u-bob")
        assert [v.version for v in await store.history("org-1", "a1")] == [2, 1]

    async def test_owd14_a_version_freezes_the_definition_never_the_identity(self, store: AgentVersionStore) -> None:
        v1, _ = await store.publish("org-1", "a1", _agent(), published_by="u-alice")

        assert v1.definition["systemPrompt"] == "Be brief."
        for identity in ("_key", "createdBy", "updatedAtTimestamp", "isDeleted", "shareWithOrg"):
            assert identity not in v1.definition

    async def test_owd14_a_relinked_node_with_the_same_content_is_not_a_change(self) -> None:
        relinked = _agent(toolsets=[{"_key": "ts-9", "name": "slack", "tools": [
            {"_key": "t-9", "name": "send", "fullName": "slack.send"},
        ]}])
        assert definition_digest(agent_definition(relinked)) == definition_digest(agent_definition(_agent()))

    async def test_owd14_a_stored_version_is_never_overwritten(self) -> None:
        graph = InMemoryGraph()
        store = AgentVersionStore(graph)
        v1, _ = await store.publish("org-1", "a1", _agent(), published_by="u-alice")
        before = json.dumps(graph.nodes[VERSIONS][version_key("a1", 1)], sort_keys=True)

        await graph.ensure_nodes(
            [{"id": version_key("a1", 1), "orgId": "org-1", "agentKey": "a1", "version": 1, "definition": "{}"}],
            VERSIONS,
        )
        await store.publish("org-1", "a1", _agent(systemPrompt="changed"), published_by="u-alice")

        assert json.dumps(graph.nodes[VERSIONS][version_key("a1", 1)], sort_keys=True) == before
        assert (await store.get("org-1", "a1", 1)) == v1

    async def test_owd14_racing_publishers_each_get_their_own_number(self) -> None:
        store = AgentVersionStore(_YieldingGraph())
        await store.publish("org-1", "a1", _agent(), published_by="u-alice")

        results = await asyncio.gather(
            store.publish("org-1", "a1", _agent(systemPrompt="A"), published_by="u-alice"),
            store.publish("org-1", "a1", _agent(systemPrompt="B"), published_by="u-bob"),
        )

        assert sorted(v.version for v, created in results if created) == [2, 3]
        prompts = {v.version: v.definition["systemPrompt"] for v in await store.history("org-1", "a1")}
        assert sorted(prompts.values()) == ["A", "B", "Be brief."]

    async def test_owd14_another_org_reads_no_version_by_its_key(self, store: AgentVersionStore) -> None:
        await store.publish("org-1", "a1", _agent(), published_by="u-alice")

        assert await store.get("org-2", "a1", 1) is None
        assert await store.numbers("org-2", "a1") == []
        assert await store.latest("org-2", "a1") is None


class TestOwd14Rollback:
    async def test_owd14_a_rollback_is_recorded_as_the_next_version_naming_the_one_it_restores(
        self, store: AgentVersionStore,
    ) -> None:
        v1, _ = await store.publish("org-1", "a1", _agent(), published_by="u-alice")
        await store.publish("org-1", "a1", _agent(systemPrompt="Regressed."), published_by="u-alice")

        v3, created = await store.publish(
            "org-1", "a1", v1.definition, published_by="u-bob", note="v2 regressed",
            source=VersionSource.ROLLBACK, rolled_back_from=1,
        )

        assert (v3.version, created, v3.source, v3.rolled_back_from, v3.note) == (
            3, True, VersionSource.ROLLBACK, 1, "v2 regressed",
        )
        assert v3.digest == v1.digest
        assert (await store.get("org-1", "a1", 2)).definition["systemPrompt"] == "Regressed."


class TestOwd14PinnedAgent:
    async def test_owd14_a_pinned_run_sees_the_version_and_nothing_the_draft_added(
        self, store: AgentVersionStore,
    ) -> None:
        v1, _ = await store.publish("org-1", "a1", _agent(), published_by="u-alice")
        draft = _agent(systemPrompt="Draft prompt.", engineProfile={"engines": ["codex"]}, isDeleted=False)

        seen = pinned_agent(draft, v1)

        assert seen["systemPrompt"] == "Be brief."
        assert "engineProfile" not in seen
        assert (seen["_key"], seen["createdBy"], seen["shareWithOrg"]) == ("a1", "k-alice", True)


class TestGa01Migration:
    async def test_ga01_every_existing_agent_gets_version_1_once(self) -> None:
        graph = InMemoryGraph()
        graph.add_agent("old-1", "alice", systemPrompt="Old one.")
        graph.add_agent("old-2", "mallory", systemPrompt="Other org.")
        graph.add_agent("gone", "alice", isDeleted=True)
        graph.add_node(AGENTS, {"_key": "orphan", "name": "x", "createdBy": "k-nobody", "isDeleted": False})

        first = await migrate_agent_versions(graph)
        again = await migrate_agent_versions(graph)

        store = AgentVersionStore(graph)
        v = await store.get("org-1", "old-1", 1)
        assert v is not None and v.source == VersionSource.MIGRATION and v.definition["systemPrompt"] == "Old one."
        assert await store.numbers("org-2", "old-2") == [1]
        assert await store.numbers("org-1", "gone") == []
        assert (first["versioned"], first["no_org"], first["failures"]) == (2, 1, 0)
        assert (again["versioned"], again["already"]) == (0, 2)

    async def test_ga01_the_migration_flag_is_set_only_after_a_clean_pass(self) -> None:
        graph = InMemoryGraph()
        graph.add_agent("old-1", "alice")
        config = FakeConfigService()

        result = await run_agent_versions_migration(config, graph, _Logger())
        skipped = await run_agent_versions_migration(config, graph, _Logger())

        assert result["versioned"] == 1
        assert skipped == {"skipped": True}

    async def test_ga01_a_first_read_and_the_migration_agree_on_version_1(self) -> None:
        graph = InMemoryGraph()
        graph.add_agent("old-1", "alice", systemPrompt="As it was.")
        store = AgentVersionStore(graph)
        live = await graph.get_agent("old-1", "org-1")

        read_first = await store.current("org-1", "old-1", live)
        await migrate_agent_versions(graph)

        assert read_first.version == 1
        assert await store.numbers("org-1", "old-1") == [1]


class TestOwd14Erasure:
    async def test_owd14_an_erased_orgs_versions_go_page_by_page_and_no_other_orgs(self) -> None:
        graph = InMemoryGraph()
        store = AgentVersionStore(graph)
        for n in range(3):
            await store.publish("org-1", "a1", _agent(systemPrompt=f"p{n}"), published_by=user_key("alice"))
        await store.publish("org-2", "b1", _agent(), published_by=user_key("mallory"))

        pages = []
        while True:
            report = await erase_org_page(graph, _NoBlobs(), "org-1", page=2)
            pages.append(report.agent_versions)
            if not report.remaining:
                break

        assert sum(pages) == 3
        assert await store.numbers("org-1", "a1") == []
        assert await store.numbers("org-2", "b1") == [1]


class _Logger:
    def info(self, *_: object, **__: object) -> None:
        pass


class _NoBlobs:
    async def delete(self, *_: object, **__: object) -> None:
        pass
