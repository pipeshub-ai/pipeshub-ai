"""E7 (PA-1..PA-4, PA-6): erasing a deleted conversation's artifacts, and collecting discarded drafts."""

from __future__ import annotations

import pytest

from app.config.constants.arangodb import CollectionNames
from app.models.entities import ArtifactType
from app.services.artifact_registry.erasure import ArtifactEraser
from app.services.artifact_registry.models import Actor
from app.services.artifact_registry.registry import (
    DRAFT_DISCARDED_REASON,
    ArtifactRegistryService,
)

from .fakes import FakeBlobStore, FakeGraphProvider

ORG = "org-1"
OTHER_ORG = "org-2"
USER = "user-1"
RECORDS = CollectionNames.RECORDS.value
ARTIFACTS = CollectionNames.ARTIFACTS.value


def _setup() -> tuple[ArtifactRegistryService, FakeGraphProvider, FakeBlobStore]:
    graph = FakeGraphProvider()
    graph.add_user(USER, key="ukey-1")
    blob = FakeBlobStore()
    return ArtifactRegistryService(graph, blob), graph, blob


async def _artifact(service: ArtifactRegistryService, conversation: str, name: str, org: str = ORG) -> str:
    metadata = await service.register(
        actor=Actor(org_id=org, user_id=USER),
        name=name,
        artifact_type=ArtifactType.DOCUMENT,
        mime_type="text/plain",
        content=f"content of {name}".encode(),
        conversation_id=conversation,
    )
    return metadata.artifact_id


class FailingPurger:
    def __init__(self, inner: FakeBlobStore) -> None:
        self.inner = inner
        self.fail = True

    async def purge_document(self, org_id: str, document_id: str) -> bool:
        if self.fail:
            raise RuntimeError("storage down")
        return await self.inner.purge_document(org_id, document_id)


async def test_pa1_blobs_then_records_and_edges_of_only_that_conversation() -> None:
    service, graph, blob = _setup()
    erased = [await _artifact(service, "conv-a", f"a{i}.txt") for i in range(2)]
    kept = await _artifact(service, "conv-b", "b.txt")
    report = await ArtifactEraser(graph, blob).erase_conversation(ORG, "conv-a")
    assert (report.artifacts, report.blobs, report.remaining) == (2, 2, False)
    for artifact_id in erased:
        assert artifact_id not in graph.nodes[RECORDS]
        assert artifact_id not in graph.nodes[ARTIFACTS]
        assert all(artifact_id not in (e.get("from_id"), e.get("to_id")) for edges in graph.edges.values() for e in edges)
    assert kept in graph.nodes[RECORDS]
    assert len(blob.documents) == 1


async def test_pa1_another_orgs_conversation_of_the_same_id_is_untouched() -> None:
    service, graph, blob = _setup()
    theirs = await _artifact(service, "conv-a", "theirs.txt", org=OTHER_ORG)
    report = await ArtifactEraser(graph, blob).erase_conversation(ORG, "conv-a")
    assert report.artifacts == 0
    assert theirs in graph.nodes[ARTIFACTS]


async def test_pa2_a_second_call_reports_nothing_left() -> None:
    service, graph, blob = _setup()
    await _artifact(service, "conv-a", "a.txt")
    eraser = ArtifactEraser(graph, blob)
    await eraser.erase_conversation(ORG, "conv-a")
    again = await eraser.erase_conversation(ORG, "conv-a")
    assert (again.artifacts, again.blobs, again.remaining) == (0, 0, False)


async def test_pa2_a_crash_between_purge_and_graph_delete_is_finished_by_the_retry() -> None:
    service, graph, blob = _setup()
    artifact_id = await _artifact(service, "conv-a", "a.txt")
    real_delete = graph.delete_nodes_and_edges
    calls = {"n": 0}

    async def crash_once(keys: list[str], collection: str, **kwargs: object) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("crash after the purge")
        await real_delete(keys, collection, **kwargs)

    graph.delete_nodes_and_edges = crash_once  # type: ignore[method-assign]
    eraser = ArtifactEraser(graph, blob)
    with pytest.raises(RuntimeError):
        await eraser.erase_conversation(ORG, "conv-a")
    assert blob.documents == {}
    assert artifact_id in graph.nodes[ARTIFACTS]
    retry = await eraser.erase_conversation(ORG, "conv-a")
    assert (retry.artifacts, retry.blobs) == (1, 0)
    assert artifact_id not in graph.nodes[ARTIFACTS]


async def test_pa3_a_failed_purge_raises_and_keeps_the_graph_for_the_retry() -> None:
    service, graph, blob = _setup()
    artifact_id = await _artifact(service, "conv-a", "a.txt")
    purger = FailingPurger(blob)
    with pytest.raises(RuntimeError, match="storage down"):
        await ArtifactEraser(graph, purger).erase_conversation(ORG, "conv-a")
    assert artifact_id in graph.nodes[RECORDS]
    purger.fail = False
    assert (await ArtifactEraser(graph, purger).erase_conversation(ORG, "conv-a")).blobs == 1


async def test_pa4_one_page_per_call_while_more_remain() -> None:
    service, graph, blob = _setup()
    for i in range(5):
        await _artifact(service, "conv-a", f"a{i}.txt")
    eraser = ArtifactEraser(graph, blob, page=2)
    pages = []
    while True:
        report = await eraser.erase_conversation(ORG, "conv-a")
        pages.append(report.artifacts)
        if not report.remaining:
            break
    assert pages == [2, 2, 1]
    assert graph.nodes[ARTIFACTS] == {}


async def test_pa6_only_drafts_discarded_before_the_cut_off_are_collected() -> None:
    service, graph, blob = _setup()
    old = await _artifact(service, "conv-a", "old.txt")
    young = await _artifact(service, "conv-a", "young.txt")
    live = await _artifact(service, "conv-a", "live.txt")
    for artifact_id, at in ((old, 1_000), (young, 9_000)):
        graph.nodes[RECORDS][artifact_id].update(
            {"isDeleted": True, "reason": DRAFT_DISCARDED_REASON, "updatedAtTimestamp": at}
        )
    report = await ArtifactEraser(graph, blob).collect_discarded(ORG, before_ms=5_000)
    assert (report.artifacts, report.blobs, report.remaining) == (1, 1, False)
    assert old not in graph.nodes[RECORDS]
    assert young in graph.nodes[RECORDS]
    assert live in graph.nodes[RECORDS]


async def test_discard_stamps_the_discard_time_the_collector_reads() -> None:
    service, graph, _ = _setup()
    artifact_id = await _artifact(service, "conv-a", "draft.txt")
    await service.discard(actor=Actor(org_id=ORG, user_id=USER), artifact_id=artifact_id)
    record = graph.nodes[RECORDS][artifact_id]
    assert record["reason"] == DRAFT_DISCARDED_REASON
    assert isinstance(record["updatedAtTimestamp"], int)
