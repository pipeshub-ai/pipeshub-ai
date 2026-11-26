"""C-8: provenance end to end, P3.5 publisher → recorded provenance → SS0's per-viewer artifact check.

An engine-made artifact is shown to a reader who does not own it only when
that reader can read every source record it was derived from; anything with
unknown provenance stays hidden, as SS0 made every artifact before P3.5.
Everything runs over the real registry, viewer access and record authorizer;
only the graph and blob are the registry's fakes.
"""

from __future__ import annotations

import pytest

from app.harness.artifacts.registry_store import RegistryArtifactStore
from app.models.entities import ArtifactType
from app.services.artifact_registry.models import Actor
from app.services.artifact_registry.registry import ArtifactRegistryService
from app.services.artifact_registry.viewer_access import artifact_viewer_access
from harness_core.artifacts.memory import InMemoryPublicationLog
from harness_core.artifacts.publisher import WorkspaceArtifactPublisher
from harness_core.artifacts.types import ArtifactVersionRef, Derivation, OutputFile
from harness_core.interactions.ports import RunBinding
from harness_core.workspace.memory import InMemoryBinaryObjectStore
from harness_core.workspace.types import ObjectScope, build_manifest
from tests.unit.services.artifact_registry.fakes import FakeBlobStore, FakeGraphProvider

ORG = "org-1"
OWNER = Actor(org_id=ORG, user_id="u-owner")
VIEWER = Actor(org_id=ORG, user_id="u-viewer")
RUN = RunBinding(ORG, "conv-1", "run-1")


class World:
    def __init__(self) -> None:
        self.graph = FakeGraphProvider()
        self.graph.add_user("u-owner", key="k-owner")
        self.graph.add_user("u-viewer", key="k-viewer")
        self.graph.add_record("kb-open", org_id=ORG, readers=("u-owner", "u-viewer"))
        self.graph.add_record("kb-closed", org_id=ORG, readers=("u-owner",))
        self.registry = ArtifactRegistryService(self.graph, FakeBlobStore())
        self.objects = InMemoryBinaryObjectStore()
        self.publisher = WorkspaceArtifactPublisher(
            RegistryArtifactStore(self.registry, OWNER), InMemoryPublicationLog(), self.objects,
            object_scope=lambda b: ObjectScope(b.tenant, b.scope),
        )
        self.viewer_access = artifact_viewer_access(self.graph)

    async def publish(self, path: str, text: str, derivation: Derivation) -> str:
        ref = await self.objects.put(ObjectScope(ORG, "conv-1"), text.encode())
        manifest = build_manifest("ws:conv-1", revision=3, parent_revision=2, files={}, created_at_ms=0)
        report = await self.publisher.publish_changes(
            RUN, manifest, [OutputFile(path, ref, "text/csv")], draft=False, derivation=derivation
        )
        assert report.refused == ()
        return report.published[0].artifact_id


@pytest.fixture
def world() -> World:
    return World()


async def test_c8_1_a_viewer_who_reads_every_source_sees_the_engine_artifact(world: World) -> None:
    aid = await world.publish("q3.csv", "a,b", Derivation(task_id="t", source_records=frozenset({"kb-open"})))
    assert await world.viewer_access.decide(VIEWER, [aid]) == {aid: "readable"}
    assert await world.viewer_access.may_stream(VIEWER, aid) is True


async def test_c8_2_one_unreadable_source_hides_it(world: World) -> None:
    aid = await world.publish(
        "q3.csv", "a,b", Derivation(task_id="t", source_records=frozenset({"kb-open", "kb-closed"}))
    )
    assert await world.viewer_access.decide(VIEWER, [aid]) == {aid: "denied"}
    assert await world.viewer_access.may_stream(VIEWER, aid) is False
    assert await world.viewer_access.may_stream(OWNER, aid) is True


async def test_c8_3_an_artifact_without_recorded_provenance_stays_hidden(world: World) -> None:
    legacy, _ = await world.registry.register_output(
        actor=OWNER, name="chart.png", artifact_type=ArtifactType.IMAGE, mime_type="image/png",
        content=b"png", conversation_id="conv-1",
    )
    assert await world.viewer_access.decide(VIEWER, [legacy.artifact_id]) == {legacy.artifact_id: "unresolved"}


async def test_c8_4_sources_of_an_input_artifact_count(world: World) -> None:
    secret = await world.publish("raw.csv", "s", Derivation(task_id="t", source_records=frozenset({"kb-closed"})))
    summary = await world.publish(
        "summary.csv", "sum",
        Derivation(
            task_id="t", input_artifacts=(ArtifactVersionRef(secret, 1),), source_records=frozenset({"kb-open"})
        ),
    )
    assert await world.viewer_access.decide(VIEWER, [summary]) == {summary: "denied"}


async def test_c8_5_a_later_version_without_provenance_hides_it_again(world: World) -> None:
    aid = await world.publish("q3.csv", "a,b", Derivation(task_id="t", source_records=frozenset({"kb-open"})))
    await world.registry.add_version(actor=OWNER, artifact_id=aid, content=b"edited by update_artifact")
    assert await world.viewer_access.decide(VIEWER, [aid]) == {aid: "unresolved"}
