"""Each version of an artifact records the run that wrote it.

Who may open a version is judged by the answer that version's run wrote, so the bookkeeping must say, for every
version, which run that was: version 1 the creating run, every later one the run that bumped it. A file versioned
before that was kept has no such record for its later versions, and they read as not known.
"""

from __future__ import annotations

from app.config.constants.arangodb import CollectionNames
from app.models.entities import (
    ARTIFACT_VERSION_RUN_KEY,
    ArtifactType,
    ArtifactVersionWriter,
    artifact_version_writer,
    deserialize_artifact_versions,
    serialize_artifact_versions,
)
from app.services.artifact_registry.access import AccessPolicy
from app.services.artifact_registry.models import Actor
from app.services.artifact_registry.versioning import (
    VersionManager,
    rebuild_version_bookkeeping,
    recover_and_persist_version_mapping,
)

from .fakes import FakeBlobStore, FakeGraphProvider

ORG = "org-1"
USER = "user-1"


def _manager() -> tuple[VersionManager, FakeGraphProvider, FakeBlobStore]:
    graph = FakeGraphProvider()
    graph.add_user(USER, key="ukey-1")
    blob = FakeBlobStore()
    return VersionManager(graph, blob, AccessPolicy(graph)), graph, blob


def _actor(run_id: str | None) -> Actor:
    return Actor(org_id=ORG, user_id=USER, run_id=run_id)


async def _file(manager: VersionManager, *runs: str | None) -> str:
    """A file the first run made and each later run bumped once."""
    meta = await manager.create(
        actor=_actor(runs[0]), name="plan.md", artifact_type=ArtifactType.DOCUMENT, mime_type="text/markdown",
        content=b"v1", conversation_id="conv-1",
    )
    for n, run in enumerate(runs[1:], start=2):
        await manager.add_version(actor=_actor(run), artifact_id=meta.artifact_id, content=f"v{n}".encode())
    return meta.artifact_id


def _doc(graph: FakeGraphProvider, artifact_id: str) -> dict:
    return graph.nodes[CollectionNames.ARTIFACTS.value][artifact_id]


class TestEachVersionNamesItsRun:
    async def test_version_one_is_the_creating_runs_and_each_later_one_the_run_that_bumped_it(self) -> None:
        manager, graph, _ = _manager()
        artifact_id = await _file(manager, "run-1", "run-2", "run-3")

        doc = _doc(graph, artifact_id)
        assert [artifact_version_writer(doc, v) for v in (1, 2, 3)] == [
            ArtifactVersionWriter(run_id="run-1"), ArtifactVersionWriter(run_id="run-2"), ArtifactVersionWriter(run_id="run-3"),
        ]
        # The creating run is still the file's own: a later version never changes it.
        assert doc["runId"] == "run-1"
        # Written in the shape Neo4j can store and the schema allows (the fake checks both).
        assert isinstance(doc["versions"], str)
        assert [e[ARTIFACT_VERSION_RUN_KEY] for e in deserialize_artifact_versions(doc["versions"])] == ["run-1", "run-2", "run-3"]

    async def test_a_version_no_run_wrote_is_known_to_have_none(self) -> None:
        manager, graph, _ = _manager()
        artifact_id = await _file(manager, "run-1", None)

        assert artifact_version_writer(_doc(graph, artifact_id), 2) == ArtifactVersionWriter(run_id=None)

    async def test_a_caller_may_name_the_writer_or_record_none(self) -> None:
        manager, graph, _ = _manager()
        artifact_id = await _file(manager, "run-1")
        await manager.add_version(
            actor=_actor("run-copying"), artifact_id=artifact_id, content=b"v2",
            writer=ArtifactVersionWriter(run_id="run-source"),
        )
        await manager.add_version(actor=_actor("run-copying"), artifact_id=artifact_id, content=b"v3", writer=None)

        doc = _doc(graph, artifact_id)
        assert artifact_version_writer(doc, 2) == ArtifactVersionWriter(run_id="run-source")
        assert artifact_version_writer(doc, 3) is None

    async def test_identical_content_writes_no_version_and_keeps_its_writer(self) -> None:
        manager, graph, _ = _manager()
        artifact_id = await _file(manager, "run-1")

        version, _ = await manager.add_version(actor=_actor("run-2"), artifact_id=artifact_id, content=b"v1")

        assert version.deduplicated
        assert artifact_version_writer(_doc(graph, artifact_id), 1) == ArtifactVersionWriter(run_id="run-1")
        assert artifact_version_writer(_doc(graph, artifact_id), 2) is None


class TestFilesVersionedBefore:
    def test_a_later_version_without_a_recorded_run_is_not_known(self) -> None:
        doc = {
            "runId": "run-1",
            "versions": serialize_artifact_versions([
                {"registryVersion": 1, "storageVersion": 0},
                {"registryVersion": 2, "storageVersion": 1},
            ]),
        }
        assert artifact_version_writer(doc, 1) == ArtifactVersionWriter(run_id="run-1")
        assert artifact_version_writer(doc, 2) is None
        # A version with no entry at all, or out of range, neither.
        assert artifact_version_writer(doc, 3) is None
        assert artifact_version_writer(doc, 0) is None
        assert artifact_version_writer(None, 2) is None

    def test_rebuilt_bookkeeping_keeps_the_runs_recorded(self) -> None:
        existing = serialize_artifact_versions([
            {"registryVersion": 1, "storageVersion": 0, ARTIFACT_VERSION_RUN_KEY: "run-1"},
            {"registryVersion": 3, "storageVersion": 2, ARTIFACT_VERSION_RUN_KEY: "run-3"},
        ])
        rebuilt = rebuild_version_bookkeeping(3, existing)
        assert rebuilt == [
            {"registryVersion": 1, "storageVersion": 0, ARTIFACT_VERSION_RUN_KEY: "run-1"},
            {"registryVersion": 2, "storageVersion": 1},
            {"registryVersion": 3, "storageVersion": 2, ARTIFACT_VERSION_RUN_KEY: "run-3"},
        ]

    async def test_recovering_a_lost_mapping_keeps_the_runs_recorded(self) -> None:
        manager, graph, blob = _manager()
        artifact_id = await _file(manager, "run-1", "run-2", "run-3")
        doc = _doc(graph, artifact_id)
        # The entry for version 2 was lost; the others still name their runs.
        doc["versions"] = serialize_artifact_versions(
            [e for e in deserialize_artifact_versions(doc["versions"]) if e["registryVersion"] != 2]
        )
        record = graph.nodes[CollectionNames.RECORDS.value][artifact_id]

        storage = await recover_and_persist_version_mapping(
            graph_provider=graph, blob_store=blob, artifact_id=artifact_id, org_id=ORG,
            document_id=record["externalRecordId"], current_version=3, version=2,
        )

        assert storage == 1
        assert artifact_version_writer(_doc(graph, artifact_id), 3) == ArtifactVersionWriter(run_id="run-3")
        assert artifact_version_writer(_doc(graph, artifact_id), 2) is None
