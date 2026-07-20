"""`GraphSkillStore`/`GraphUsageTracker` (Phase 2 adapter layer) — revision
round-trip via `SkillHistoryReader`, org isolation (the tenant boundary
every read/write claims to enforce), and `SkillCandidateStore` delegation
through a real `SkillManager` (mirrors how `manager.py` picks up the extra
surface via `isinstance` — see `manager.py`'s `_history`/`_candidate_store`
docstrings).

Uses a minimal in-memory `FakeGraphProvider` implementing only the
`IGraphDBProvider` surface `GraphSkillStore`/`GraphUsageTracker` actually
call (`get_document`, `get_nodes_by_filters`, `batch_upsert_nodes`,
`update_node`, `delete_nodes`, `delete_nodes_and_edges`,
`batch_create_edges`, `delete_edges_from`) — not the full interface.
"""

from __future__ import annotations

from typing import Any

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.base import SkillCandidate
from app.agent_loop_lib.modules.providers.skills.evaluator import RubricSkillEvaluator
from app.agent_loop_lib.modules.providers.skills.governor import AutoApproveGovernor
from app.agent_loop_lib.modules.providers.skills.manager import SkillManager, SkillManagerConfig
from app.agent_loop_lib.modules.providers.skills.validator import SkillValidator
from app.agents.agent_loop.skills.audit_governor import AuditGovernor
from app.agents.agent_loop.skills.graph_store import GraphSkillStore
from app.agents.agent_loop.skills.graph_tracker import GraphUsageTracker

_NEO4J_PRIMITIVES = (str, int, float, bool)


def _assert_neo4j_safe(doc: dict[str, Any], context: str) -> None:
    """Neo4j node properties may only be primitives or arrays of primitives
    — a nested dict (or a dict/None inside a list) throws
    `Neo.ClientError.Statement.TypeError` at runtime. Enforced here on every
    write the fakes see, so no store field can regress to a map shape."""
    for key, value in doc.items():
        if value is None or isinstance(value, _NEO4J_PRIMITIVES):
            continue
        assert isinstance(value, list), f"{context}: property {key!r} is {type(value).__name__}, not primitive/array"
        for item in value:
            assert isinstance(item, _NEO4J_PRIMITIVES), (
                f"{context}: array property {key!r} contains {type(item).__name__} "
                f"— Neo4j arrays must hold primitives only (no maps, no nulls)"
            )


class FakeGraphProvider:
    """In-memory stand-in for `IGraphDBProvider` — only the methods this
    adapter layer's stores actually use."""

    def __init__(self) -> None:
        self._collections: dict[str, dict[str, dict[str, Any]]] = {}
        self._edges: dict[str, list[dict[str, Any]]] = {}

    def _col(self, name: str) -> dict[str, dict[str, Any]]:
        return self._collections.setdefault(name, {})

    async def get_document(self, document_key: str, collection: str, transaction: str | None = None) -> dict | None:
        doc = self._col(collection).get(document_key)
        return dict(doc) if doc is not None else None

    async def get_nodes_by_filters(
        self, collection: str, filters: dict[str, Any],
        return_fields: list[str] | None = None, transaction: str | None = None,
    ) -> list[dict[str, Any]]:
        return [
            dict(doc) for doc in self._col(collection).values()
            if all(doc.get(k) == v for k, v in filters.items())
        ]

    async def batch_upsert_nodes(self, nodes: list[dict[str, Any]], collection: str, transaction: str | None = None) -> bool:
        col = self._col(collection)
        for node in nodes:
            _assert_neo4j_safe(node, f"batch_upsert_nodes({collection})")
            key = str(node.get("id") or node.get("_key"))
            col[key] = dict(node)
        return True

    async def update_node(self, key: str, collection: str, node_updates: dict[str, Any], transaction: str | None = None) -> bool:
        _assert_neo4j_safe(node_updates, f"update_node({collection})")
        col = self._col(collection)
        if key not in col:
            return False
        col[key].update(node_updates)
        return True

    async def delete_nodes(self, keys: list[str], collection: str, transaction: str | None = None) -> bool:
        col = self._col(collection)
        for key in keys:
            col.pop(str(key), None)
        return True

    async def delete_nodes_and_edges(
        self, keys: list[str], collection: str, graph_name: str = "knowledgeGraph", transaction: str | None = None,
    ) -> None:
        await self.delete_nodes(keys, collection)

    async def batch_create_edges(self, edges: list[dict[str, Any]], collection: str, transaction: str | None = None) -> bool:
        self._edges.setdefault(collection, []).extend(dict(e) for e in edges)
        return True

    async def delete_edges_from(self, from_id: str, from_collection: str, collection: str, transaction: str | None = None) -> int:
        edges = self._edges.setdefault(collection, [])
        before = len(edges)
        self._edges[collection] = [e for e in edges if e.get("from_id") != from_id]
        return before - len(self._edges[collection])


_SKILL_MD = """---
name: deploy-service
description: Use when deploying a service to the cluster
---

Step 1. Build the image.
Step 2. Push it.
"""


def _store(graph: FakeGraphProvider, org_id: str = "org-1", user_id: str = "user-1") -> GraphSkillStore:
    return GraphSkillStore(graph, org_id, user_id)


class TestRevisionRoundTrip:
    async def test_create_update_list_versions_get_version_rollback(self) -> None:
        graph = FakeGraphProvider()
        store = _store(graph)

        created = await store.create_skill("deploy-service", _SKILL_MD)
        assert created.version == "1.0.0"

        updated_md = _SKILL_MD.replace("Step 2. Push it.", "Step 2. Push it.\nStep 3. Verify health.")
        updated = await store.update_skill("deploy-service", updated_md)
        assert updated.version == "1.0.1"

        versions = await store.list_versions("deploy-service")
        assert len(versions) == 1
        assert versions[0].version == "1.0.0"

        archived_skill = await store.get_version("deploy-service", "1.0.0")
        assert archived_skill is not None
        assert "Verify health" not in archived_skill.body

        current = await store.get_skill("deploy-service")
        assert "Verify health" in current.body

        rolled_back = await store.rollback("deploy-service", "1.0.0")
        # Rollback creates a NEW revision rather than reusing the archived
        # version number — history stays monotonic.
        assert rolled_back.version == "1.0.2"
        restored = await store.get_skill("deploy-service")
        assert "Verify health" not in restored.body

        versions_after_rollback = await store.list_versions("deploy-service")
        assert {v.version for v in versions_after_rollback} == {"1.0.0", "1.0.1"}

    async def test_rollback_unknown_version_raises(self) -> None:
        graph = FakeGraphProvider()
        store = _store(graph)
        await store.create_skill("deploy-service", _SKILL_MD)

        try:
            await store.rollback("deploy-service", "9.9.9")
        except RegistryError:
            pass
        else:
            raise AssertionError("expected RegistryError for unknown version")

    async def test_update_preserves_usage_counters(self) -> None:
        """Regression test: `update_skill`'s full-document overwrite must
        carry forward `GraphUsageTracker`'s counters (see `_extract_usage`
        in graph_store.py) — a naive overwrite would silently reset them."""
        graph = FakeGraphProvider()
        store = _store(graph)
        tracker = GraphUsageTracker(graph, "org-1", "user-1")

        await store.create_skill("deploy-service", _SKILL_MD)
        await tracker.record_activation("deploy-service", "session-1")
        await tracker.record_activation("deploy-service", "session-1")
        await tracker.record_outcome("deploy-service", "session-1", success=True)

        await store.update_skill("deploy-service", _SKILL_MD.replace("Build the image.", "Build the image (v2)."))

        experience = await tracker.get_experience("deploy-service")
        assert experience.total_activations == 2
        assert experience.successful_outcomes == 1


class TestNeo4jSafeEncoding:
    """The regression behind these: `resources` as a nested {path: content}
    map and `auditLog` as an array of objects both work on Arango but throw
    `Neo.ClientError.Statement.TypeError` on Neo4j (properties must be
    primitives or arrays thereof). `FakeGraphProvider` now asserts
    primitive-shape on every write, so simply exercising the paths below
    proves the encoding — these tests additionally prove the round-trip
    reads back correctly."""

    async def test_resource_write_read_remove_round_trip(self) -> None:
        graph = FakeGraphProvider()
        store = _store(graph)
        await store.create_skill("deploy-service", _SKILL_MD)

        assert await store.write_resource("deploy-service", "scripts/run.py", "print('hi')") is True
        assert await store.write_resource("deploy-service", "references/notes.md", "# notes") is True

        assert await store.get_resource("deploy-service", "scripts/run.py") == "print('hi')"
        skill = await store.get_skill("deploy-service")
        assert skill.resources == {"references": ["references/notes.md"], "scripts": ["scripts/run.py"]}

        assert await store.remove_resource("deploy-service", "scripts/run.py") is True
        assert await store.get_resource("deploy-service", "scripts/run.py") is None
        assert await store.get_resource("deploy-service", "references/notes.md") == "# notes"

    async def test_resources_survive_update_and_rollback(self) -> None:
        graph = FakeGraphProvider()
        store = _store(graph)
        await store.create_skill("deploy-service", _SKILL_MD)
        await store.write_resource("deploy-service", "scripts/run.py", "print('hi')")

        await store.update_skill("deploy-service", _SKILL_MD.replace("Push it.", "Push it hard."))
        assert await store.get_resource("deploy-service", "scripts/run.py") == "print('hi')"

        await store.rollback("deploy-service", "1.0.0")
        # The 1.0.0 snapshot was taken at update time — after the resource
        # write — so rollback restores the resource along with the content.
        assert await store.get_resource("deploy-service", "scripts/run.py") == "print('hi')"

    async def test_legacy_nested_resources_map_still_readable(self) -> None:
        """Docs written before the parallel-array encoding carry a real
        nested `resources` map (valid on Arango) — reads must fall back to
        it, and the next write migrates to the primitive shape."""
        graph = FakeGraphProvider()
        store = _store(graph)
        await store.create_skill("deploy-service", _SKILL_MD)
        # Simulate a legacy doc: inject the old map shape directly.
        doc = graph._col("agentSkills")["org-1_deploy-service"]
        doc.pop("resourcePaths"), doc.pop("resourceContents")
        doc["resources"] = {"scripts/old.py": "legacy content"}

        assert await store.get_resource("deploy-service", "scripts/old.py") == "legacy content"

        # A write re-encodes everything (legacy entry included) as arrays.
        await store.write_resource("deploy-service", "scripts/new.py", "new content")
        migrated = graph._col("agentSkills")["org-1_deploy-service"]
        assert migrated["resourcePaths"] == ["scripts/new.py", "scripts/old.py"]
        assert await store.get_resource("deploy-service", "scripts/old.py") == "legacy content"

    async def test_audit_governor_appends_parallel_arrays(self) -> None:
        graph = FakeGraphProvider()
        store = _store(graph)
        created = await store.create_skill("deploy-service", _SKILL_MD)

        governor = AuditGovernor(AutoApproveGovernor(), graph, "org-1", "user-1")
        await governor.on_skill_created(created)
        await governor.on_skill_deprecated("deploy-service", reason="superseded")

        doc = graph._col("agentSkills")["org-1_deploy-service"]
        assert doc["auditActions"] == ["created", "deprecated"]
        assert doc["auditActorIds"] == ["user-1", "user-1"]
        # "" (not None) encodes "no reason" — Neo4j rejects nulls in arrays.
        assert doc["auditReasons"] == ["", "superseded"]
        assert len(doc["auditTimestamps"]) == 2


class TestOrgIsolation:
    async def test_second_org_cannot_see_or_load_first_orgs_skill(self) -> None:
        graph = FakeGraphProvider()
        org1_store = _store(graph, org_id="org-1")
        org2_store = _store(graph, org_id="org-2")

        await org1_store.create_skill("deploy-service", _SKILL_MD)

        assert await org1_store.exists("deploy-service") is True
        assert await org2_store.exists("deploy-service") is False
        assert await org2_store.get_skill("deploy-service") is None

        org1_names = {m.name for m in await org1_store.list_skills()}
        org2_names = {m.name for m in await org2_store.list_skills()}
        assert org1_names == {"deploy-service"}
        assert org2_names == set()

    async def test_second_org_can_create_a_same_named_skill_independently(self) -> None:
        graph = FakeGraphProvider()
        org1_store = _store(graph, org_id="org-1")
        org2_store = _store(graph, org_id="org-2")

        await org1_store.create_skill("deploy-service", _SKILL_MD)
        # Different org, same name, same underlying `agentSkills` collection
        # — the composite `{org_id}_{name}` key must keep these from
        # colliding.
        await org2_store.create_skill("deploy-service", _SKILL_MD)

        await org1_store.deprecate_skill("deploy-service", reason="superseded")
        org1_skill = await org1_store.get_skill("deploy-service")
        org2_skill = await org2_store.get_skill("deploy-service")
        assert org1_skill.metadata.status.value == "deprecated"
        assert org2_skill.metadata.status.value == "active"

    async def test_usage_tracking_is_org_scoped(self) -> None:
        graph = FakeGraphProvider()
        org1_store = _store(graph, org_id="org-1")
        org2_store = _store(graph, org_id="org-2")
        await org1_store.create_skill("deploy-service", _SKILL_MD)
        await org2_store.create_skill("deploy-service", _SKILL_MD)

        org1_tracker = GraphUsageTracker(graph, "org-1", "user-1")
        org2_tracker = GraphUsageTracker(graph, "org-2", "user-1")
        await org1_tracker.record_activation("deploy-service", "s1")

        assert (await org1_tracker.get_experience("deploy-service")).total_activations == 1
        assert (await org2_tracker.get_experience("deploy-service")).total_activations == 0


class TestSkillManagerCandidateDelegation:
    """`SkillManager` delegates its candidate queue to the store when the
    store implements `SkillCandidateStore` (an `isinstance` check, not a
    config flag — see manager.py). `GraphSkillStore` does; this proves the
    delegation actually reaches the graph rather than manager.py's
    filesystem-JSON fallback."""

    def _manager(self, graph: FakeGraphProvider, org_id: str = "org-1") -> SkillManager:
        store = _store(graph, org_id=org_id)
        tracker = GraphUsageTracker(graph, org_id, "user-1")
        return SkillManager(
            store=store, index=_NullIndex(), tracker=tracker, validator=SkillValidator(),
            evaluator=RubricSkillEvaluator(),
            config=SkillManagerConfig(write_approval=True),
        )

    async def test_queue_get_and_remove_candidate_round_trip(self) -> None:
        graph = FakeGraphProvider()
        manager = self._manager(graph)
        assert manager.supports_history is True

        candidate = SkillCandidate(
            candidate_id="cand-1", name="new-skill", description="when to use it",
            body="do the thing", status="pending", created_at="2026-01-01T00:00:00+00:00",
        )
        await manager.queue_candidate(candidate)

        # Proves this landed in the graph's agentSkillCandidates collection,
        # not manager.py's `_meta/candidates/` filesystem fallback.
        assert "cand-1" in graph._col("agentSkillCandidates")

        pending = await manager.get_pending_candidates()
        assert [c.candidate_id for c in pending] == ["cand-1"]

        await manager.reject_candidate("cand-1")
        assert await manager.get_pending_candidates() == []

    async def test_candidates_are_org_scoped(self) -> None:
        graph = FakeGraphProvider()
        org1_manager = self._manager(graph, org_id="org-1")
        org2_manager = self._manager(graph, org_id="org-2")

        await org1_manager.queue_candidate(SkillCandidate(
            candidate_id="cand-1", name="new-skill", description="d", body="b", status="pending",
            created_at="2026-01-01T00:00:00+00:00",
        ))

        assert len(await org1_manager.get_pending_candidates()) == 1
        assert len(await org2_manager.get_pending_candidates()) == 0


class _NullIndex:
    """Minimal `SkillIndex` stub — these tests exercise the store/tracker/
    candidate-queue paths only, never search."""

    async def rebuild(self, skills: list) -> None:
        return None

    async def search(self, query: str, filter=None, limit: int = 10) -> list:
        return []

    async def get_categories(self) -> dict:
        return {}

    async def get_tags(self) -> list:
        return []

    async def add_entry(self, metadata) -> None:
        return None

    async def remove_entry(self, name: str) -> None:
        return None

    async def update_entry(self, metadata) -> None:
        return None
