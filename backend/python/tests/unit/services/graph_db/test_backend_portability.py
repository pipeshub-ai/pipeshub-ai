"""Every store the task and workflow engines persist through must round-trip
identically on ArangoDB and on Neo4j.

These engines were added against an Arango deployment, and Arango accepts any
JSON the adapter hands it -- so an adapter that writes a nested map, a `set`,
or a null for "empty" looks correct locally and fails only on a Neo4j install.
Each test below therefore runs the same round-trip against both semantics
fakes; see `tests/unit/services/graph_provider_fakes` for what "semantics"
means concretely.
"""
from __future__ import annotations

import pytest

from app.config.constants.arangodb import CollectionNames
from app.config.constants.neo4j import COLLECTION_TO_LABEL
from app.schema.node_schema_registry import NODE_SCHEMA_REGISTRY
from app.services.tasks.adapters.graph.run_archive import GraphRunArchive
from app.services.tasks.adapters.graph.task_store import GraphTaskStore
from app.services.tasks.domain.models import (
    RunStatus,
    TaskDefinition,
    TaskPrincipal,
    TaskQuery,
    TaskRun,
)
from app.services.workflows.adapters.graph.code_store import GraphWorkflowCodeStore
from app.services.workflows.adapters.graph.version_store import (
    GraphWorkflowVersionStore,
)
from app.services.workflows.domain.models import (
    ArtifactRef,
    IRNode,
    IRNodeKind,
    WorkflowIR,
    WorkflowVersion,
)
from tests.unit.services.graph_provider_fakes import (
    GRAPH_PROVIDER_FAKES,
    ArangoSemanticsGraphProvider,
    BaseFakeGraphProvider,
    Neo4jSemanticsGraphProvider,
    assert_neo4j_safe,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(params=GRAPH_PROVIDER_FAKES, ids=lambda cls: cls.__name__)
def provider(request: pytest.FixtureRequest) -> BaseFakeGraphProvider:
    return request.param()


def _task(**overrides) -> TaskDefinition:
    defaults = {
        "org_id": "org-1",
        "created_by_user_id": "user-1",
        "principal": TaskPrincipal(org_id="org-1", user_id="user-1", user_email="a@b.com"),
        "title": "Daily digest",
        "description": "every morning summarize tickets",
        "instructions": "Summarize yesterday's tickets",
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    defaults.update(overrides)
    return TaskDefinition(**defaults)


def _run(**overrides) -> TaskRun:
    defaults = {
        "task_id": "task-1",
        "org_id": "org-1",
        "idempotency_key": "idem-1",
        "status": RunStatus.SUCCEEDED,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    defaults.update(overrides)
    return TaskRun(**defaults)


def _version(**overrides) -> WorkflowVersion:
    defaults = {
        "version_id": "ver-1",
        "version_number": 1,
        "workflow_id": "wf-1",
        "org_id": "org-1",
        "content_hash": "h",
        "created_by_user_id": "user-1",
    }
    defaults.update(overrides)
    return WorkflowVersion(**defaults)


class TestMetaTheFakesActuallyDiffer:
    """If the fakes silently accept everything, every test in this file is
    vacuous. These pin the two behaviours the rest of the suite relies on."""

    async def test_a_nested_map_property_is_rejected(self, provider) -> None:
        with pytest.raises(AssertionError, match="not primitive/array"):
            await provider.batch_upsert_nodes([{"id": "x", "nested": {"a": 1}}], "c")

    async def test_an_array_of_maps_is_rejected(self, provider) -> None:
        with pytest.raises(AssertionError, match="primitives only"):
            await provider.batch_upsert_nodes([{"id": "x", "rows": [{"a": 1}]}], "c")

    async def test_neo4j_drops_null_properties_and_arango_keeps_them(self) -> None:
        neo4j, arango = Neo4jSemanticsGraphProvider(), ArangoSemanticsGraphProvider()
        for store in (neo4j, arango):
            await store.batch_upsert_nodes([{"id": "x", "maybe": None}], "c")

        assert "maybe" not in await neo4j.get_document("x", "c")
        assert (await arango.get_document("x", "c"))["maybe"] is None


class TestTaskStore:
    async def test_task_with_nested_principal_and_policies_round_trips(self, provider) -> None:
        store = GraphTaskStore(provider)
        await store.create(_task(task_id="t-1", tool_names=["a", "b"]))

        fetched = await store.get("t-1", "org-1")

        assert fetched is not None
        assert fetched.principal.user_email == "a@b.com"
        assert fetched.tool_names == ["a", "b"]
        assert fetched.retry_policy == _task().retry_policy

    async def test_listing_filters_survive_the_round_trip(self, provider) -> None:
        store = GraphTaskStore(provider)
        await store.create(_task(task_id="t-1", created_from_conversation_id="conv-1"))
        await store.create(_task(task_id="t-2"))

        page = await store.list(TaskQuery(org_id="org-1", created_from_conversation_id="conv-1"))

        assert [t.task_id for t in page.items] == ["t-1"]


class TestRunArchive:
    async def test_run_with_empty_dicts_round_trips(self, provider) -> None:
        """Regression: `usage` and `trigger_payload` default to `{}` and are
        not Optional, so encoding empty as null made the archived run fail
        validation on read -- i.e. every schedule-driven run's history."""
        archive = GraphRunArchive(provider)
        run = _run(run_id="r-1")
        assert run.usage == {} and run.trigger_payload == {}

        await archive.archive(run)
        fetched = await archive.get("r-1")

        assert fetched is not None
        assert fetched.usage == {}
        assert fetched.trigger_payload == {}

    async def test_empty_dicts_are_stored_as_json_not_null(self, provider) -> None:
        """The read side tolerates a null now, but writing one is still wrong:
        Arango stores it and Neo4j deletes the property, so the two backends
        stop agreeing on what an archived run looks like."""
        archive = GraphRunArchive(provider)
        await archive.archive(_run(run_id="r-1"))

        stored = provider._col(CollectionNames.TASK_RUNS.value)["r-1"]

        assert stored["usage"] == "{}"
        assert stored["trigger_payload"] == "{}"

    async def test_run_with_populated_nested_dicts_round_trips(self, provider) -> None:
        archive = GraphRunArchive(provider)
        await archive.archive(
            _run(
                run_id="r-2",
                usage={"tokens": {"in": 10, "out": 20}},
                trigger_payload={"action": "opened", "issue": {"number": 7}},
            )
        )

        fetched = await archive.get("r-2")

        assert fetched is not None
        assert fetched.usage == {"tokens": {"in": 10, "out": 20}}
        assert fetched.trigger_payload["issue"]["number"] == 7

    async def test_listing_counts_runs_past_the_first_page(self, provider) -> None:
        """Exercises the `return_fields` count path, which asked for `_key` --
        a property that exists on Arango and on no Neo4j node."""
        archive = GraphRunArchive(provider)
        for i in range(5):
            await archive.archive(_run(run_id=f"r-{i}", created_at=f"2024-01-0{i + 1}T00:00:00+00:00"))

        page = await archive.list_for_task("task-1", limit=2)

        assert page.total == 5
        assert [r.run_id for r in page.items] == ["r-4", "r-3"]


class TestWorkflowVersionStore:
    async def test_version_with_ir_pins_and_bundle_ref_round_trips(self, provider) -> None:
        """Regression: `ir` is a map holding an array of maps, `tool_pins` is a
        map and `agent_pins` a `set` -- none of which Neo4j accepts, and the
        `set` is not even JSON-serialisable for Arango's HTTP client."""
        store = GraphWorkflowVersionStore(provider)
        await store.save(
            _version(
                bundle_ref=ArtifactRef(artifact_id="art-1", version="1"),
                tool_pins={"slack__post": "slack.post"},
                agent_pins={"agent-a", "agent-b"},
                ir=WorkflowIR(
                    nodes=[IRNode(node_id="n1", kind=IRNodeKind.TOOL_CALL, label="slack__post")],
                    entry_node_id="n1",
                ),
            )
        )

        fetched = await store.get("ver-1", "org-1")

        assert fetched is not None
        assert fetched.bundle_ref is not None
        assert fetched.bundle_ref.artifact_id == "art-1"
        assert fetched.tool_pins == {"slack__post": "slack.post"}
        assert fetched.agent_pins == {"agent-a", "agent-b"}
        assert [n.node_id for n in fetched.ir.nodes] == ["n1"]
        assert fetched.ir.entry_node_id == "n1"

    async def test_version_with_no_bundle_ref_round_trips(self, provider) -> None:
        store = GraphWorkflowVersionStore(provider)
        await store.save(_version(bundle_ref=None))

        fetched = await store.get("ver-1", "org-1")

        assert fetched is not None
        assert fetched.bundle_ref is None
        assert fetched.ir.nodes == []
        assert fetched.tool_pins == {}

    async def test_latest_is_the_highest_version_number(self, provider) -> None:
        store = GraphWorkflowVersionStore(provider)
        for i in range(1, 4):
            await store.save(_version(version_id=f"ver-{i}", version_number=0))

        latest = await store.get_latest("wf-1", "org-1")

        assert latest is not None
        assert latest.version_number == 3


class TestWorkflowCodeStore:
    async def test_source_round_trips_as_utf8(self, provider) -> None:
        store = GraphWorkflowCodeStore(provider)
        source = "def run(ctx):\n    return '\u00e9\u2713'\n".encode()

        ref = await store.put("wf-1", "org-1", source)

        assert await store.get(ref) == source


class TestReadingLegacyDocuments:
    """Rows already in a deployed database were written by the unencoded
    adapters, so the new read path has to accept both shapes -- there is no
    migration step between the two.

    Arango-only by construction: these shapes are exactly the ones Neo4j
    rejected, which is why the bug was invisible until now.
    """

    @pytest.fixture
    def arango(self) -> ArangoSemanticsGraphProvider:
        return ArangoSemanticsGraphProvider()

    async def test_a_version_stored_with_raw_nested_maps_still_loads(self, arango) -> None:
        arango.seed(
            CollectionNames.WORKFLOW_VERSIONS.value,
            {
                "id": "ver-legacy",
                "version_id": "ver-legacy",
                "version_number": 1,
                "workflow_id": "wf-1",
                "org_id": "org-1",
                "content_hash": "h",
                "created_by_user_id": "user-1",
                "created_at": "2024-01-01T00:00:00+00:00",
                "sdk_version": "0.1.0",
                "bundle_ref": {"artifact_id": "art-legacy", "version": "1"},
                "tool_pins": {"slack__post": "slack.post"},
                "ir": {"schema_version": 1, "nodes": [], "edges": [], "entry_node_id": None},
            },
        )

        fetched = await GraphWorkflowVersionStore(arango).get("ver-legacy", "org-1")

        assert fetched is not None
        assert fetched.bundle_ref is not None
        assert fetched.bundle_ref.artifact_id == "art-legacy"
        assert fetched.tool_pins == {"slack__post": "slack.post"}

    async def test_a_run_archived_with_null_usage_still_loads(self, arango) -> None:
        doc = _run(run_id="r-legacy").model_dump(mode="json")
        doc["id"] = "r-legacy"
        doc["usage"] = None
        doc["trigger_payload"] = None
        arango.seed(CollectionNames.TASK_RUNS.value, doc)

        fetched = await GraphRunArchive(arango).get("r-legacy")

        assert fetched is not None
        assert fetched.usage == {}
        assert fetched.trigger_payload == {}


class TestSchemaBootstrap:
    """Both backends need the new collections declared, for different reasons:
    Arango creates the container, Neo4j creates the unique-id constraint that
    makes `MERGE (n {id}) ` atomic. `GraphTaskStore`'s read-then-write
    optimistic concurrency is unsound without the latter."""

    NEW_COLLECTIONS = (
        CollectionNames.TASKS,
        CollectionNames.TASK_RUNS,
        CollectionNames.WORKFLOW_VERSIONS,
        CollectionNames.WORKFLOW_SOURCES,
        CollectionNames.AGENT_CHECKPOINTS,
        CollectionNames.AGENT_TIMELINE_ENTRIES,
    )

    @pytest.mark.parametrize("collection", NEW_COLLECTIONS, ids=lambda c: c.value)
    async def test_collection_gets_a_neo4j_unique_id_constraint(self, collection) -> None:
        assert collection.value in NODE_SCHEMA_REGISTRY

    @pytest.mark.parametrize("collection", NEW_COLLECTIONS, ids=lambda c: c.value)
    async def test_collection_has_an_explicit_neo4j_label(self, collection) -> None:
        """Without a mapping the label falls back to `str.capitalize()`, which
        mangles camelCase into `Taskruns` -- consistent, but not what any
        hand-written Cypher or migration would look for."""
        assert collection.value in COLLECTION_TO_LABEL

    async def test_documents_written_by_every_store_are_neo4j_safe(self, provider) -> None:
        """A belt-and-braces sweep: whatever the individual suites above did or
        did not cover, nothing any store wrote may be a nested map."""
        await GraphTaskStore(provider).create(_task(task_id="t-1"))
        await GraphRunArchive(provider).archive(_run(run_id="r-1"))
        await GraphWorkflowVersionStore(provider).save(_version())
        await GraphWorkflowCodeStore(provider).put("wf-1", "org-1", b"x = 1")

        for name, docs in provider._collections.items():
            for key, doc in docs.items():
                assert_neo4j_safe(doc, f"{name}/{key}")
