"""A re-embed from stored blocks, with no plan installed, is the whole-file re-embed it always was.

Deletes the points of the virtual record, indexes, reprojects the named entities, and hands the index the settings
``{"skip_blob": True}`` and nothing else. A plan from the lifecycle changes exactly those four things.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import Connectors, OriginTypes
from app.models.entities import Record, RecordType
from app.modules.indexing import extension
from app.modules.indexing.extension import (
    DefaultIndexingLifecycle,
    IndexingExtension,
    ReembedPlan,
)
from tests.support.record_handler import make_record_handler

RECORD = {"_key": "r1", "orgId": "o1", "contentRev": "rev-1", "extractionStatus": "NOT_STARTED"}
BLOB = {"block_containers": {"blocks": [], "block_groups": []}}


@pytest.fixture
def rig(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    handler = make_record_handler()
    sink = MagicMock()
    sink.index = AsyncMock()
    sink.reproject_named_entities = AsyncMock()
    sink.blob_storage.get_record_from_storage = AsyncMock(return_value=BLOB)
    handler.event_processor.sink_orchestrator = sink
    pipeline = handler.event_processor.processor.indexing_pipeline
    pipeline.delete_points_for_virtual_record = AsyncMock()
    monkeypatch.setattr(handler, "_blob_has_blocks", lambda _blob: True)
    record_obj = Record(
        id="r1", org_id="o1", record_name="a.pdf", record_type=RecordType.FILE, external_record_id="e", version=0,
        origin=OriginTypes.UPLOAD, connector_name=Connectors.KNOWLEDGE_BASE, connector_id="c",
    )
    monkeypatch.setattr(
        "app.services.messaging.kafka.handlers.record.convert_record_dict_to_record", lambda _doc: record_obj
    )
    monkeypatch.setattr(
        "app.services.messaging.kafka.handlers.record.RecordContext.from_record", staticmethod(lambda *_a: "context")
    )
    return SimpleNamespace(handler=handler, sink=sink, pipeline=pipeline)


async def _run(rig: SimpleNamespace) -> list:
    return [e async for e in rig.handler._index_from_blob("r1", dict(RECORD), {"orgId": "o1"}, "v1", "reindexRecord")]


async def test_with_no_plan_the_whole_file_is_deleted_indexed_and_reprojected(rig: SimpleNamespace) -> None:
    await _run(rig)

    rig.pipeline.delete_points_for_virtual_record.assert_awaited_once_with("v1", "context")
    rig.sink.index.assert_awaited_once()
    assert rig.sink.index.await_args.args[0].settings == {"skip_blob": True}
    rig.sink.reproject_named_entities.assert_awaited_once()
    assert rig.sink.reproject_named_entities.await_args.kwargs == {"content_rev": "rev-1"}


async def test_a_plan_can_keep_the_points_skip_the_reprojection_add_settings_and_run_after(rig: SimpleNamespace) -> None:
    after_ran: list[bool] = []

    async def after() -> None:
        after_ran.append(True)

    class Life(DefaultIndexingLifecycle):
        async def reembed_plan(self, record: object) -> ReembedPlan:
            return ReembedPlan(delete_whole=False, reproject_entities=False, settings={"extra": 1}, after=after)

    extension.install(IndexingExtension(lifecycle=Life()))

    await _run(rig)

    rig.pipeline.delete_points_for_virtual_record.assert_not_awaited()
    rig.sink.reproject_named_entities.assert_not_awaited()
    assert rig.sink.index.await_args.args[0].settings == {"skip_blob": True, "extra": 1}
    assert after_ran == [True]


async def test_the_after_step_runs_after_the_index_not_before(rig: SimpleNamespace) -> None:
    order: list[str] = []
    rig.sink.index.side_effect = lambda *_a: order.append("index")

    async def after() -> None:
        order.append("after")

    class Life(DefaultIndexingLifecycle):
        async def reembed_plan(self, record: object) -> ReembedPlan:
            return ReembedPlan(after=after)

    extension.install(IndexingExtension(lifecycle=Life()))

    await _run(rig)

    assert order == ["index", "after"]
