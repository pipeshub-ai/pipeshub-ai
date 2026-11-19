"""The classify and entities stages read and keep exactly what they always did, unless stage hooks are installed."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.models.blocks import (
    Block,
    BlocksContainer,
    BlockType,
    DataFormat,
    SemanticMetadata,
)
from app.modules.indexing import extension
from app.modules.indexing.extension import IndexingExtension, StageInput
from tests.unit.modules.pipeline.test_classify_io_writes import (
    _BLOCKS,
    _Blob,
    _classify_io,
    _entities_io,
    _extraction,
    _job,
)

STORED = {"block_containers": {"blocks": [], "block_groups": []}}
OWN = BlocksContainer(blocks=[Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="hooked")])


class Hooks:
    def __init__(
        self, *, keep: bool = False, save: bool = False, supply: bool = True, extraction: object = None, restore: bool = False
    ) -> None:
        self.keep, self.save, self.supply, self.extraction, self.restore = keep, save, supply, extraction, restore
        self.calls: list[str] = []

    async def classify_input(self, job: object, io: object) -> StageInput | None:
        self.calls.append("classify_input")
        return StageInput("hooked-digest", OWN) if self.supply else None

    async def entities_input(self, job: object, io: object) -> StageInput | None:
        self.calls.append("entities_input")
        return StageInput("hooked-digest", OWN, self.extraction) if self.supply else None

    async def save_classification(self, job: object, io: object, owner: object, metadata: object) -> bool:
        self.calls.append("save_classification")
        return self.save

    async def restore_classification(self, job: object, io: object, metadata: object) -> bool:
        self.calls.append("restore_classification")
        return self.restore

    async def keep_extraction(self, job: object, io: object, owner: object, extraction: object) -> bool:
        self.calls.append("keep_extraction")
        return self.keep


def _install(hooks: Hooks) -> None:
    extension.install(IndexingExtension(stage_hooks=hooks))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_without_hooks_the_classify_stage_reads_the_stored_blocks_and_the_jobs_digest() -> None:
    io = _classify_io(_Blob(STORED))

    assert await io.input_digest(_job()) == "t"
    assert (await io.load_blocks(_job())).model_dump() == BlocksContainer(blocks=[], block_groups=[]).model_dump()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_hooked_input_replaces_the_digest_and_the_blocks_and_is_read_once_per_job() -> None:
    hooks = Hooks()
    _install(hooks)
    io = _classify_io(_Blob(STORED))

    assert await io.input_digest(_job()) == "hooked-digest"
    assert await io.load_blocks(_job()) is OWN
    assert hooks.calls == ["classify_input"]


@pytest.mark.asyncio
async def test_hooks_that_supply_nothing_leave_the_stage_on_the_stored_blocks() -> None:
    _install(Hooks(supply=False))
    io = _classify_io(_Blob(STORED))

    assert await io.input_digest(_job()) == "t"
    assert await io.load_blocks(_job()) is not OWN


@pytest.mark.asyncio
async def test_a_save_the_hooks_took_over_writes_nothing_here() -> None:
    hooks = Hooks(save=True)
    _install(hooks)
    blob = _Blob(STORED)
    io = _classify_io(blob)

    await io.save(_job(), ["rec-1"], SemanticMetadata(summary="s"))

    assert hooks.calls == ["save_classification"]
    assert blob.saves == 0
    io._sink.prepare_enrichment.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_save_the_hooks_decline_is_written_as_always() -> None:
    _install(Hooks(save=False))
    blob = _Blob(STORED)
    io = _classify_io(blob)

    await io.save(_job(), ["rec-1"], SemanticMetadata(summary="s"))

    assert blob.saves == 1
    io._sink.prepare_enrichment.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_without_hooks_the_entities_stage_reads_the_stored_blocks_and_persists() -> None:
    io = _entities_io(_Blob(STORED))

    assert await io.input_digest(_job("entities")) == "t"
    assert await io.save(_job("entities"), ["rec-1"], _extraction(), _BLOCKS) == "written"


@pytest.mark.asyncio
async def test_hooked_entities_input_replaces_the_digest_and_the_blocks() -> None:
    hooks = Hooks()
    _install(hooks)
    io = _entities_io(_Blob(STORED))

    assert await io.input_digest(_job("entities")) == "hooked-digest"
    assert await io.load_blocks(_job("entities")) is OWN
    assert hooks.calls == ["entities_input"]


@pytest.mark.asyncio
async def test_an_extraction_the_hooks_keep_is_not_persisted_by_the_stage() -> None:
    hooks = Hooks(keep=True)
    _install(hooks)
    io = _entities_io(_Blob(STORED))
    io._sink.persist_named_entities = AsyncMock(return_value="written")  # type: ignore[attr-defined]

    outcome = await io.save(_job("entities"), ["rec-1"], _extraction(), _BLOCKS)

    assert outcome == "kept"
    io._sink.persist_named_entities.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_an_extraction_the_hooks_decline_is_persisted_as_always() -> None:
    _install(Hooks(keep=False))
    io = _entities_io(_Blob(STORED))

    assert await io.save(_job("entities"), ["rec-1"], _extraction(), _BLOCKS) == "written"


@pytest.mark.asyncio
async def test_an_extraction_the_hooks_hand_over_is_returned_without_running_the_model() -> None:
    handed = _extraction()
    hooks = Hooks(extraction=handed)
    _install(hooks)
    io = _entities_io(_Blob(STORED))
    io._sink.extract_named_entities = AsyncMock(return_value=_extraction())  # type: ignore[attr-defined]

    assert await io.extract(_job("entities"), OWN) is handed
    io._sink.extract_named_entities.assert_not_awaited()  # type: ignore[attr-defined]
    assert hooks.calls == ["entities_input"]


@pytest.mark.asyncio
async def test_hooks_that_hand_over_no_extraction_leave_the_model_to_run() -> None:
    _install(Hooks())
    io = _entities_io(_Blob(STORED))
    ran = _extraction()
    io._sink.extract_named_entities = AsyncMock(return_value=ran)  # type: ignore[attr-defined]

    assert await io.extract(_job("entities"), OWN) is ran
    io._sink.extract_named_entities.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_without_hooks_the_entities_stage_extracts_as_always() -> None:
    io = _entities_io(_Blob(STORED))
    ran = _extraction()
    io._sink.extract_named_entities = AsyncMock(return_value=ran)  # type: ignore[attr-defined]

    assert await io.extract(_job("entities"), _BLOCKS) is ran


@pytest.mark.asyncio
async def test_a_restore_the_hooks_took_over_writes_nothing_here() -> None:
    hooks = Hooks(restore=True)
    _install(hooks)
    blob = _Blob(STORED)
    io = _classify_io(blob)

    await io.restore_stored_metadata(_job(), SemanticMetadata(summary="kept"))

    assert hooks.calls == ["restore_classification"]
    assert blob.saves == 0
    io._sink.write_summary_point.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_restore_the_hooks_decline_is_written_as_always() -> None:
    _install(Hooks(restore=False))
    blob = _Blob(STORED)
    io = _classify_io(blob)

    await io.restore_stored_metadata(_job(), SemanticMetadata(summary="kept"))

    assert blob.saves == 1
    io._sink.write_summary_point.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_without_hooks_a_kept_classification_is_restored_as_always() -> None:
    blob = _Blob(STORED)
    io = _classify_io(blob)

    await io.restore_stored_metadata(_job(), SemanticMetadata(summary="kept"))

    assert blob.saves == 1
