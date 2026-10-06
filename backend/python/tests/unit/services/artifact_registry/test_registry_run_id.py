"""PH07-11: artifacts are stamped with the Node-minted runId of the creating turn."""

from __future__ import annotations

from app.models.entities import ArtifactType
from app.services.artifact_registry.models import Actor
from app.services.artifact_registry.registry import ArtifactRegistryService

from .fakes import FakeBlobStore, FakeGraphProvider

ORG, USER = "org-1", "user-1"


def _service() -> tuple[ArtifactRegistryService, FakeGraphProvider]:
    graph = FakeGraphProvider()
    graph.add_user(USER, key="ukey-1")
    return ArtifactRegistryService(graph, FakeBlobStore()), graph


class TestRunIdStamped:
    async def test_register_stamps_run_id_on_doc_and_metadata(self) -> None:
        service, graph = _service()
        metadata = await service.register(
            actor=Actor(org_id=ORG, user_id=USER, run_id="r1"), name="a.csv",
            artifact_type=ArtifactType.DATA_FILE, mime_type="text/csv", content=b"x", conversation_id="conv-1",
        )
        assert metadata.run_id == "r1"
        assert graph.nodes["artifacts"][metadata.artifact_id]["runId"] == "r1"

    async def test_register_output_stamps_run_id(self) -> None:
        service, graph = _service()
        metadata, _ = await service.register_output(
            actor=Actor(org_id=ORG, user_id=USER, run_id="r1"), name="a.png",
            artifact_type=ArtifactType.IMAGE, mime_type="image/png", content=b"x", conversation_id="conv-1",
        )
        assert graph.nodes["artifacts"][metadata.artifact_id]["runId"] == "r1"

    async def test_register_existing_stamps_run_id(self) -> None:
        service, graph = _service()
        metadata = await service.register_existing(
            actor=Actor(org_id=ORG, user_id=USER, run_id="r1"), document_id="doc-9", name="a.csv",
            artifact_type=ArtifactType.DATA_FILE, mime_type="text/csv", size_bytes=1, conversation_id="conv-1",
        )
        assert graph.nodes["artifacts"][metadata.artifact_id]["runId"] == "r1"

    async def test_a_version_bump_keeps_the_creating_run(self) -> None:
        service, graph = _service()
        first, _ = await service.register_output(
            actor=Actor(org_id=ORG, user_id=USER, run_id="r1"), name="a.png",
            artifact_type=ArtifactType.IMAGE, mime_type="image/png", content=b"v1", conversation_id="conv-1",
        )
        await service.register_output(
            actor=Actor(org_id=ORG, user_id=USER, run_id="r2"), name="a.png",
            artifact_type=ArtifactType.IMAGE, mime_type="image/png", content=b"v2-longer", conversation_id="conv-1",
        )
        assert graph.nodes["artifacts"][first.artifact_id]["runId"] == "r1"

    async def test_no_run_id_is_stored_as_none(self) -> None:
        service, graph = _service()
        metadata = await service.register(
            actor=Actor(org_id=ORG, user_id=USER), name="a.csv",
            artifact_type=ArtifactType.DATA_FILE, mime_type="text/csv", content=b"x", conversation_id="conv-1",
        )
        assert metadata.run_id is None
        assert graph.nodes["artifacts"][metadata.artifact_id]["runId"] is None


class TestActorConstructors:
    def test_from_state(self) -> None:
        actor = Actor.from_state({"org_id": ORG, "user_id": USER, "run_id": "r9", "acl_version": 4})
        assert (actor.run_id, actor.acl_version) == ("r9", 4)

    def test_from_state_defaults(self) -> None:
        actor = Actor.from_state({"org_id": ORG, "user_id": USER})
        assert (actor.run_id, actor.acl_version) == (None, None)
