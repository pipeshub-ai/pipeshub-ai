"""The record stages write the stored record only while the record is on the job's revision,
and each writes only its own section of it, whichever finishes first."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.events.processor import convert_record_dict_to_record
from app.models.blocks import BlocksContainer, SemanticMetadata
from app.models.entities import Record
from app.modules.named_entities.domain.models import NamedEntityExtraction
from app.modules.pipeline.leases import RecordLeases, RevisionSuperseded
from app.modules.pipeline.models import Priority, StageJob
from app.modules.pipeline.policy import DEFAULT_POLICY
from app.modules.pipeline.stage import Deadline
from app.modules.pipeline.stages.classify_io import GraphClassifyIO
from app.modules.pipeline.stages.entities_io import GraphEntitiesIO
from app.modules.pipeline.stages.record_io import StoredRecordMissing
from app.modules.transformers.transformer import TransformContext
from app.services.resource_governor.models import ParseTier


def _job(stage: str = "classify") -> StageJob:
    return StageJob.model_validate({
        "stage": stage, "stage_version": 1, "org_id": "org-1", "virtual_record_id": "vr-1",
        "rev": "rev-a", "record_ids": ("rec-1",), "connector_id": "conn-1", "tier": ParseTier.LIGHT,
        "priority": Priority.BULK, "trigger": "embed", "text_digest": "t", "blocks_digest": "b",
        "text_chars": 10, "has_tables": False, "has_images": False,
    })


class _Blob:
    """An in-memory stored record: ``save_semantic_metadata`` replaces its metadata, as the real one does."""

    def __init__(self, stored: dict[str, Any] | None) -> None:
        self.stored = stored
        self.saves = 0
        self.get_record_from_storage = AsyncMock(side_effect=self._get)
        self.save_semantic_metadata = AsyncMock(side_effect=self._save)

    async def _get(self, virtual_record_id: str, org_id: str) -> dict[str, Any] | None:
        return None if self.stored is None else {**self.stored}

    async def _save(self, org_id: str, record_id: str, virtual_record_id: str, metadata: dict[str, Any]) -> None:
        assert self.stored is not None
        self.stored = {**self.stored, "semantic_metadata": metadata}
        self.saves += 1


def _graph(rev: str) -> MagicMock:
    graph = MagicMock()
    graph.get_document = AsyncMock(
        return_value={
            "_key": "rec-1", "orgId": "org-1", "contentRev": rev, "recordName": "a.txt", "recordType": "FILE",
            "externalRecordId": "ext-1", "connectorId": "conn-1", "mimeType": "text/plain",
            "connectorName": "KB", "origin": "UPLOAD",
        }
    )
    graph.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])
    return graph


def _sink() -> MagicMock:
    sink = MagicMock()
    sink.prepare_enrichment = AsyncMock()
    sink.write_enrichment = AsyncMock()
    sink.write_summary_point = AsyncMock()
    sink.persist_named_entities = AsyncMock(return_value="written")
    sink.entity_resolution_mode = MagicMock(return_value="apply")
    return sink


def _record(document: dict[str, Any]) -> Record:
    return convert_record_dict_to_record(document)


def _classify_io(blob: _Blob, rev: str = "rev-a", sink: MagicMock | None = None) -> GraphClassifyIO:
    return GraphClassifyIO(
        _job(), Deadline(30), policy=DEFAULT_POLICY, graph=_graph(rev), blob_storage=blob,  # type: ignore[arg-type]
        sink=sink or _sink(), classifier=AsyncMock(), config_service=MagicMock(),
        record_from_document=_record, record_leases=RecordLeases(),
    )


def _entities_io(blob: _Blob, rev: str = "rev-a") -> GraphEntitiesIO:
    return GraphEntitiesIO(
        _job("entities"), Deadline(30), policy=DEFAULT_POLICY, graph=_graph(rev), blob_storage=blob,  # type: ignore[arg-type]
        sink=_sink(), config_service=MagicMock(), record_from_document=_record, record_leases=RecordLeases(),
    )


# The content the entities were extracted from, as the stage hands it to save().
_BLOCKS = BlocksContainer(blocks=[], block_groups=[])


def _extraction() -> NamedEntityExtraction:
    return NamedEntityExtraction(status="COMPLETED", strategy="deterministic")


@pytest.mark.asyncio
async def test_a_record_that_moved_to_a_newer_revision_is_not_written() -> None:
    blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}})
    io = _classify_io(blob, rev="rev-b")
    with pytest.raises(RevisionSuperseded):
        await io.save(_job(), ["rec-1"], SemanticMetadata(summary="s"))
    with pytest.raises(RevisionSuperseded):
        await io.restore_stored_metadata(_job(), SemanticMetadata(summary="s"))
    with pytest.raises(RevisionSuperseded):
        await _entities_io(blob, rev="rev-b").save(_job("entities"), ["rec-1"], _extraction(), _BLOCKS)
    assert blob.saves == 0


@pytest.mark.asyncio
async def test_a_kept_classification_is_restored_only_while_its_state_is_unchanged() -> None:
    blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}})
    io = _classify_io(blob)

    async def superseded() -> bool:
        return False

    async def unchanged() -> bool:
        return True

    with pytest.raises(RevisionSuperseded):
        await io.restore_stored_metadata(_job(), SemanticMetadata(summary="old"), still_current=superseded)
    assert blob.saves == 0
    await io.restore_stored_metadata(_job(), SemanticMetadata(summary="old"), still_current=unchanged)
    assert blob.saves == 1


@pytest.mark.asyncio
async def test_a_restored_classification_puts_its_summary_point_back() -> None:
    """A re-index of unchanged content deletes the record's vectors and rewrites the stored record
    without its classification. Restoring the kept classification must also restore the summary
    point, or the record silently drops out of summary-level search."""
    blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}})
    sink = _sink()
    io = _classify_io(blob, sink=sink)

    async def superseded() -> bool:
        return False

    with pytest.raises(RevisionSuperseded):
        await io.restore_stored_metadata(_job(), SemanticMetadata(summary="kept"), still_current=superseded)
    sink.write_summary_point.assert_not_awaited()

    await io.restore_stored_metadata(_job(), SemanticMetadata(summary="kept"))

    [call] = sink.write_summary_point.await_args_list
    ctx: TransformContext = call.args[0]
    assert ctx.record.id == "rec-1" and ctx.record.virtual_record_id == "vr-1"
    assert ctx.record.semantic_metadata.summary == "kept"
    sink.write_enrichment.assert_not_awaited()  # the taxonomy survives a re-index


@pytest.mark.asyncio
async def test_the_summary_point_is_written_before_the_classification_goes_back() -> None:
    """Once the stored record holds the classification the job counts as done, so a summary
    point that failed after that write would stay missing. Written first, a failure leaves the
    classification out and the next delivery restores both."""
    blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}})
    sink = _sink()
    saves_when_written: list[int] = []

    async def fail_once(ctx: TransformContext) -> None:
        saves_when_written.append(blob.saves)
        if len(saves_when_written) == 1:
            raise RuntimeError("embedding model not loaded")

    sink.write_summary_point = AsyncMock(side_effect=fail_once)
    io = _classify_io(blob, sink=sink)

    with pytest.raises(RuntimeError):
        await io.restore_stored_metadata(_job(), SemanticMetadata(summary="kept"))
    assert blob.saves == 0
    assert not await _classify_io(blob).stored_metadata_present(_job())

    await io.restore_stored_metadata(_job(), SemanticMetadata(summary="kept"))
    assert saves_when_written == [0, 0]
    assert blob.saves == 1


@pytest.mark.asyncio
async def test_no_stored_record_is_reported_as_missing() -> None:
    with pytest.raises(StoredRecordMissing):
        await _classify_io(_Blob(None)).restore_stored_metadata(_job(), SemanticMetadata(summary="s"))


@pytest.mark.asyncio
async def test_the_stored_record_is_read_once_per_job_before_writing() -> None:
    blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}})
    io = _classify_io(blob)
    assert not await io.stored_metadata_present(_job())
    assert await io.load_blocks(_job()) is not None
    blob.get_record_from_storage.assert_awaited_once()


@pytest.mark.asyncio
async def test_entities_never_writes_the_stored_record() -> None:
    """Classification is the stored record's only stage writer, so the two stages cannot drop
    each other's work there whichever finishes first."""
    blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}, "semantic_metadata": None})
    entities = _entities_io(blob)
    assert await entities.current_record_ids(_job("entities")) == ["rec-1"]

    assert await entities.save(_job("entities"), ["rec-1"], _extraction(), _BLOCKS) == "written"

    assert blob.saves == 0
    assert blob.stored["semantic_metadata"] is None  # type: ignore[index]


@pytest.mark.asyncio
async def test_classification_keeps_a_legacy_named_entity_section() -> None:
    """A record indexed before the entities stage keeps its extraction in the stored record;
    classifying it again must not drop it."""
    legacy = {"status": "COMPLETED", "entities": []}
    blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}, "semantic_metadata": {"named_entities": legacy}})
    classify = _classify_io(blob)
    assert await classify.current_record_ids(_job()) == ["rec-1"]

    await classify.save(_job(), ["rec-1"], SemanticMetadata(summary="s", topics=["VPN"], categories=["Security"]))

    stored = blob.stored["semantic_metadata"]  # type: ignore[index]
    assert stored["summary"] == "s" and stored["named_entities"] == legacy


@pytest.mark.asyncio
async def test_classify_writes_the_enrichment_after_the_stored_record() -> None:
    blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}})
    sink = _sink()
    io = _classify_io(blob, sink=sink)
    await io.current_record_ids(_job())
    await io.save(_job(), ["rec-1"], SemanticMetadata(summary="s"))
    sink.prepare_enrichment.assert_awaited_once()
    sink.write_enrichment.assert_awaited_once()
    assert blob.saves == 1


class _Leases:
    """A lease manager that records who holds what."""

    def __init__(self) -> None:
        self.held: set[str] = set()

    async def try_acquire(self, pool: str, holder: str, limit: int, lease_seconds: float) -> bool:
        self.held.add(pool)
        return True

    async def renew(self, pool: str, holder: str, lease_seconds: float) -> bool:
        return True

    async def release(self, pool: str, holder: str) -> None:
        self.held.discard(pool)


def _two_records(second_rev: str) -> MagicMock:
    """rec-1 on the job's revision; rec-2 (a bound copy) on ``second_rev``."""
    graph = _graph("rev-a")
    base = graph.get_document.return_value

    async def document(record_id: str, collection: str, **_: object) -> dict[str, Any]:
        rev = "rev-a" if record_id == "rec-1" else second_rev
        return {**base, "_key": record_id, "contentRev": rev}

    graph.get_document = AsyncMock(side_effect=document)
    return graph


def _bound_leases() -> tuple[RecordLeases, _Leases]:
    leases, manager = RecordLeases(), _Leases()
    leases.bind(manager)  # type: ignore[arg-type]
    return leases, manager


@pytest.mark.asyncio
@pytest.mark.parametrize("second_rev", ["rev-a", "rev-b"])
async def test_classification_outputs_are_written_under_each_records_lease(second_rev: str) -> None:  # review: revision fence
    leases, manager = _bound_leases()
    seen: list[tuple[str, bool]] = []
    sink = _sink()

    async def write_enrichment(ctx: TransformContext, index_summary: bool = True) -> None:
        seen.append((ctx.record.id, f"record:{ctx.record.id}" in manager.held))

    sink.write_enrichment = AsyncMock(side_effect=write_enrichment)
    blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}})
    io = GraphClassifyIO(
        _job(), Deadline(30), policy=DEFAULT_POLICY, graph=_two_records(second_rev), blob_storage=blob,  # type: ignore[arg-type]
        sink=sink, classifier=AsyncMock(), config_service=MagicMock(),
        record_from_document=_record, record_leases=leases,
    )
    await io.save(_job(), ["rec-1", "rec-2"], SemanticMetadata(summary="s"))
    # Each write ran holding that record's lease; a copy on a newer revision got nothing.
    expected = [("rec-1", True), ("rec-2", True)] if second_rev == "rev-a" else [("rec-1", True)]
    assert seen == expected
    assert manager.held == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("second_rev", ["rev-a", "rev-b"])
async def test_named_entities_are_persisted_under_each_records_lease(second_rev: str) -> None:
    leases, manager = _bound_leases()
    seen: list[tuple[str, bool]] = []
    blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}})
    io = GraphEntitiesIO(
        _job("entities"), Deadline(30), policy=DEFAULT_POLICY, graph=_two_records(second_rev), blob_storage=blob,  # type: ignore[arg-type]
        sink=_sink(), config_service=MagicMock(), record_from_document=_record, record_leases=leases,
    )

    async def persist(ctx: TransformContext, extraction: NamedEntityExtraction) -> str:
        seen.append((ctx.record.id, f"record:{ctx.record.id}" in manager.held))
        return "kept"

    io._sink.persist_named_entities = AsyncMock(side_effect=persist)  # type: ignore[attr-defined]
    assert await io.save(_job("entities"), ["rec-1", "rec-2"], _extraction(), _BLOCKS) == "kept"
    expected = [("rec-1", True), ("rec-2", True)] if second_rev == "rev-a" else [("rec-1", True)]
    assert seen == expected


@pytest.mark.asyncio
async def test_classification_and_entities_are_written_under_the_jobs_virtual_record() -> None:
    # The summary point is keyed by the record's virtual record id; written without one it
    # lands under "None", outside the record, where search filters and deletes never reach it.
    seen: list[str | None] = []
    sink = _sink()

    async def write_enrichment(ctx: TransformContext, index_summary: bool = True) -> None:
        seen.append(ctx.record.virtual_record_id)

    async def persist(ctx: TransformContext, extraction: NamedEntityExtraction) -> str:
        seen.append(ctx.record.virtual_record_id)
        return "kept"

    sink.write_enrichment = AsyncMock(side_effect=write_enrichment)
    sink.persist_named_entities = AsyncMock(side_effect=persist)
    blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}})
    await _classify_io(blob, sink=sink).save(_job(), ["rec-1"], SemanticMetadata(summary="s"))
    entities = GraphEntitiesIO(
        _job("entities"), Deadline(30), policy=DEFAULT_POLICY, graph=_graph("rev-a"), blob_storage=blob,  # type: ignore[arg-type]
        sink=sink, config_service=MagicMock(), record_from_document=_record, record_leases=RecordLeases(),
    )
    await entities.save(_job("entities"), ["rec-1"], _extraction(), _BLOCKS)
    assert seen == ["vr-1", "vr-1"]


class _Sidecars:
    """Records each sidecar write with whether the record's lease was held and how many stored
    record writes had landed by then."""

    def __init__(self, manager: _Leases, blob: _Blob) -> None:
        self.calls: list[dict[str, Any]] = []
        self._manager, self._blob = manager, blob

    async def write(
        self, org_id: str, virtual_record_id: str, rev: str | None, name: str, version: int, payload: dict[str, Any],
    ) -> bool:
        self.calls.append({
            "at": (org_id, virtual_record_id, rev, name, version), "payload": payload,
            "held": "record:rec-1" in self._manager.held, "saves": self._blob.saves,
        })
        return True


def _with_sidecars(
    blob: _Blob, *, rev: str = "rev-a", entities: bool = False, sink: MagicMock | None = None,
) -> tuple[GraphClassifyIO | GraphEntitiesIO, _Sidecars]:
    leases, manager = _bound_leases()
    sidecars = _Sidecars(manager, blob)
    common: dict[str, Any] = {
        "policy": DEFAULT_POLICY, "graph": _graph(rev), "blob_storage": blob, "sink": sink or _sink(),
        "config_service": MagicMock(), "record_from_document": _record, "record_leases": leases,
        "sidecars": sidecars,
    }
    if entities:
        return GraphEntitiesIO(_job("entities"), Deadline(30), **common), sidecars
    return GraphClassifyIO(_job(), Deadline(30), classifier=AsyncMock(), **common), sidecars


@pytest.mark.asyncio
async def test_classify_files_its_sidecar_under_the_lease_after_the_stored_record() -> None:
    blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}})
    io, sidecars = _with_sidecars(blob)
    metadata = SemanticMetadata(summary="s", topics=["VPN"], named_entities={"entities": []})

    await io.save(_job(), ["rec-1"], metadata)  # type: ignore[arg-type]

    [call] = sidecars.calls
    assert call["at"] == ("org-1", "vr-1", "rev-a", "classification", 1)
    assert call["held"] is True and call["saves"] == 1
    assert call["payload"] == metadata.model_dump(mode="json", exclude={"named_entities"})


@pytest.mark.asyncio
async def test_classify_files_no_sidecar_when_the_stored_record_was_not_written() -> None:
    superseded_blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}})
    io, superseded = _with_sidecars(superseded_blob, rev="rev-b")
    with pytest.raises(RevisionSuperseded):
        await io.save(_job(), ["rec-1"], SemanticMetadata(summary="s"))  # type: ignore[arg-type]

    io, missing = _with_sidecars(_Blob(None))
    with pytest.raises(StoredRecordMissing):
        await io.save(_job(), ["rec-1"], SemanticMetadata(summary="s"))  # type: ignore[arg-type]

    failing_blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}})
    failing_blob.save_semantic_metadata = AsyncMock(side_effect=RuntimeError("storage down"))
    io, failed = _with_sidecars(failing_blob)
    with pytest.raises(RuntimeError):
        await io.save(_job(), ["rec-1"], SemanticMetadata(summary="s"))  # type: ignore[arg-type]

    assert superseded.calls == missing.calls == failed.calls == []


@pytest.mark.asyncio
async def test_a_restored_classification_files_its_sidecar_for_the_jobs_revision() -> None:
    blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}})
    io, sidecars = _with_sidecars(blob)

    async def superseded() -> bool:
        return False

    with pytest.raises(RevisionSuperseded):
        await io.restore_stored_metadata(_job(), SemanticMetadata(summary="kept"), still_current=superseded)  # type: ignore[union-attr]
    assert sidecars.calls == []

    await io.restore_stored_metadata(_job(), SemanticMetadata(summary="kept"))  # type: ignore[union-attr]
    [call] = sidecars.calls
    assert call["at"] == ("org-1", "vr-1", "rev-a", "classification", 1)
    assert call["held"] is True and call["saves"] == 1 and call["payload"]["summary"] == "kept"


@pytest.mark.asyncio
@pytest.mark.parametrize(("outcome", "filed"), [("written", True), ("kept", False), ("disabled", False), ("failed", False)])
async def test_entities_files_its_sidecar_only_for_a_written_extraction(outcome: str, filed: bool) -> None:
    blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}})
    sink = _sink()
    sink.persist_named_entities = AsyncMock(return_value=outcome)
    io, sidecars = _with_sidecars(blob, entities=True, sink=sink)
    extraction = _extraction()

    assert await io.save(_job("entities"), ["rec-1"], extraction, _BLOCKS) == outcome  # type: ignore[arg-type]

    if not filed:
        assert sidecars.calls == []
        return
    [call] = sidecars.calls
    assert call["at"] == ("org-1", "vr-1", "rev-a", "entities", 1)
    assert call["held"] is True and call["payload"] == extraction.model_dump(mode="json")
    assert blob.saves == 0
