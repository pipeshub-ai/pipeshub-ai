"""Per-version provenance (P3.5): `record_derivation(provenance=…)`, `LineageTracker.sources_of`,
`RecordedProvenance`, and the registry's lifecycle and discard used by the publisher."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app.models.entities import ArtifactType, LifecycleStatus
from app.services.artifact_registry.models import (
    Actor,
    ArtifactProvenance,
    ArtifactVersionPin,
)
from app.services.artifact_registry.registry import ArtifactRegistryService
from app.services.artifact_registry.viewer_access import RecordedProvenance
from harness_core.artifacts.types import MAX_PROVENANCE_SOURCES

from .fakes import FakeBlobStore, FakeGraphProvider

if TYPE_CHECKING:
    import pytest

ORG = "org-1"
ACTOR = Actor(org_id=ORG, user_id="user-1")


def _service() -> tuple[ArtifactRegistryService, FakeGraphProvider]:
    graph = FakeGraphProvider()
    graph.add_user("user-1", key="ukey-1")
    return ArtifactRegistryService(graph, FakeBlobStore()), graph


def _provenance(sources: list[str] | None, **extra: object) -> ArtifactProvenance:
    return ArtifactProvenance(version=0, run_id="run-1", task_id="task-1", source_record_ids=sources, **extra)  # type: ignore[arg-type]


async def _artifact(service: ArtifactRegistryService, content: bytes = b"v1") -> str:
    made, _ = await service.register_output(
        actor=ACTOR, name="out.csv", artifact_type=ArtifactType.SPREADSHEET, mime_type="text/csv",
        content=content, conversation_id="conv-1",
    )
    return made.artifact_id


async def test_pv1_provenance_is_stored_per_version_and_code_lineage_is_unchanged() -> None:
    service, graph = _service()
    output = await _artifact(service)
    code = await _artifact_named(service, "code_1.py")
    await service.record_derivation(
        output_artifact_id=output, output_version=1,
        provenance=_provenance(
            ["rec-2", "rec-1"], workspace_id="ws:conv-1", workspace_revision=7, command="python make.py",
            input_paths=["data.csv"], input_artifacts=[ArtifactVersionPin(artifact_id="art-a", version=3)],
        ),
    )
    await service.record_derivation(output_artifact_id=output, output_version=1, code_artifact_id=code, code_version=1)

    stored = json.loads(graph.nodes["artifacts"][output]["provenance"])
    assert stored == [{
        "version": 1, "runId": "run-1", "taskId": "task-1", "workspaceId": "ws:conv-1", "workspaceRevision": 7,
        "command": "python make.py", "inputPaths": ["data.csv"],
        "inputArtifacts": [{"artifactId": "art-a", "version": 3}],
        "sourceRecordIds": ["rec-1", "rec-2"], "recordedAt": stored[0]["recordedAt"],
    }]
    lineage = await service.lineage.get_lineage_for_output(output)
    assert lineage is not None and (lineage.code_artifact_id, lineage.code_version) == (code, 1)


async def _artifact_named(service: ArtifactRegistryService, name: str) -> str:
    made, _ = await service.register_output(
        actor=ACTOR, name=name, artifact_type=ArtifactType.CODE, mime_type="text/x-python",
        content=b"print(1)", conversation_id="conv-1",
    )
    return made.artifact_id


async def test_pv2_sources_are_known_only_when_every_version_is() -> None:
    service, graph = _service()
    aid = await _artifact(service)
    tracker = service.lineage
    assert (await tracker.sources_of(ORG, [aid])) == {aid: None}
    await service.record_derivation(output_artifact_id=aid, output_version=1, provenance=_provenance(["rec-1"]))
    assert (await tracker.sources_of(ORG, [aid])) == {aid: frozenset({"rec-1"})}
    assert (await tracker.sources_of("org-2", [aid])) == {aid: None}
    assert (await tracker.sources_of(ORG, ["missing"])) == {"missing": None}

    await service.add_version(actor=ACTOR, artifact_id=aid, content=b"v2 by update_artifact")
    assert (await tracker.sources_of(ORG, [aid])) == {aid: None}
    await service.record_derivation(output_artifact_id=aid, output_version=2, provenance=_provenance([]))
    assert (await tracker.sources_of(ORG, [aid])) == {aid: frozenset({"rec-1"})}

    graph.nodes["records"][aid]["isDeleted"] = True
    assert (await tracker.sources_of(ORG, [aid])) == {aid: None}


async def test_pv5_unknown_sources_win_a_merge() -> None:
    service, _ = _service()
    aid = await _artifact(service)
    await service.record_derivation(output_artifact_id=aid, output_version=1, provenance=_provenance(["rec-1"]))
    await service.record_derivation(output_artifact_id=aid, output_version=1, provenance=_provenance(["rec-2"]))
    assert (await service.lineage.sources_of(ORG, [aid]))[aid] == frozenset({"rec-1", "rec-2"})
    await service.record_derivation(output_artifact_id=aid, output_version=1, provenance=_provenance(None))
    await service.record_derivation(output_artifact_id=aid, output_version=1, provenance=_provenance(["rec-3"]))
    assert (await service.lineage.sources_of(ORG, [aid]))[aid] is None


async def test_pv6_too_many_sources_are_recorded_as_unknown() -> None:
    service, _ = _service()
    aid = await _artifact(service)
    many = [f"rec-{i}" for i in range(MAX_PROVENANCE_SOURCES + 1)]
    await service.record_derivation(output_artifact_id=aid, output_version=1, provenance=_provenance(many))
    assert (await service.lineage.sources_of(ORG, [aid]))[aid] is None


async def test_pv7_a_malformed_field_reads_as_unknown(caplog: pytest.LogCaptureFixture) -> None:
    service, graph = _service()
    aid = await _artifact(service)
    graph.nodes["artifacts"][aid]["provenance"] = "{not json"
    caplog.set_level(logging.WARNING, logger="app.services.artifact_registry.lineage")
    assert (await RecordedProvenance(graph).sources_of(ORG, [aid])) == {aid: None}
    assert any("artifacts.provenance_unreadable" in r.getMessage() for r in caplog.records)


async def test_register_output_names_the_artifact_and_writes_its_lifecycle() -> None:
    service, graph = _service()
    made, version = await service.register_output(
        actor=ACTOR, name="d.md", artifact_type=ArtifactType.DOCUMENT, mime_type="text/markdown",
        content=b"1", conversation_id="conv-1", artifact_id="11111111-2222-4333-8444-555555555555",
        lifecycle_status=LifecycleStatus.DRAFT,
    )
    assert version is None and made.artifact_id == "11111111-2222-4333-8444-555555555555"
    assert made.lifecycle_status == LifecycleStatus.DRAFT
    finalized = await service.set_lifecycle(actor=ACTOR, artifact_id=made.artifact_id, lifecycle_status=LifecycleStatus.PUBLISHED)
    assert finalized.lifecycle_status == LifecycleStatus.PUBLISHED
    bumped, _ = await service.register_output(
        actor=ACTOR, name="d.md", artifact_type=ArtifactType.DOCUMENT, mime_type="text/markdown",
        content=b"22", conversation_id="conv-1", lifecycle_status=LifecycleStatus.DRAFT,
    )
    assert bumped.lifecycle_status == LifecycleStatus.DRAFT
    same, dedup = await service.register_output(
        actor=ACTOR, name="d.md", artifact_type=ArtifactType.DOCUMENT, mime_type="text/markdown",
        content=b"22", conversation_id="conv-1", lifecycle_status=LifecycleStatus.PUBLISHED,
    )
    assert dedup is not None and dedup.deduplicated and same.lifecycle_status == LifecycleStatus.DRAFT
    await service.discard(actor=ACTOR, artifact_id=made.artifact_id)
    assert graph.nodes["records"][made.artifact_id]["isDeleted"] is True
    assert await service.resolve_by_logical_name(ACTOR, "d.md", conversation_id="conv-1") is None
