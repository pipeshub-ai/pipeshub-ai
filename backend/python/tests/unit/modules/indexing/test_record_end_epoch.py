"""A record is marked indexed only if every epoch its batches were written under is still current.

``VectorStore.apply`` collects the epoch tokens its batches were accepted under and carries them in ``ctx.settings`` (the sink
reads them after ``apply`` has returned, when its own collector is already reset). A reset that moved the epoch after the last
batch was confirmed drops those points, so such a record is handed back instead of being marked COMPLETED.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.configuration_service import ConfigurationService
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.modules.transformers import vectorstore as vs
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.modules.transformers.vectorstore import WRITTEN_UNDER
from app.services.messaging.precision_flip import FenceMoved
from app.services.vector_db.collection_epoch import Fence
from app.services.vector_db.collection_pin import CollectionPinStore
from app.services.vector_db.models import CollectionConfig
from app.services.vector_db.precision_guard import PrecisionGuard
from tests.support.in_memory_vector_db import InMemoryVectorDBService
from tests.support.index_world import COLL, ORG, RID, VRID, context_of, make_store, parse, record_of

log = logging.getLogger("record-end-epoch")


@pytest.fixture
async def store():  # noqa: ANN201
    db = InMemoryVectorDBService()
    await db.create_collection(COLL, CollectionConfig(embedding_size=2))
    s = make_store(db)
    s._guard = PrecisionGuard(CollectionPinStore(ConfigurationService(log, InMemoryKeyValueStore(log)), log), db, log)
    return s


async def test_apply_carries_the_tokens_it_wrote_under_after_it_returns(store) -> None:
    ctx = context_of(record_of(parse("alpha\n\nbeta")), "newRecord", prev=None)
    await store.apply(ctx)
    current = await store._guard._pins.epochs.current(COLL)
    assert ctx.settings[WRITTEN_UNDER] == {COLL: [current]}  # an empty set here would let the check pass for nothing


async def test_a_reset_after_the_last_batch_makes_the_check_fail(store) -> None:
    ctx = context_of(record_of(parse("alpha")), "newRecord", prev=None)
    await store.apply(ctx)
    await store.require_epochs_current(ctx.settings[WRITTEN_UNDER])  # still current: passes
    await store._guard._pins.epochs.begin_reset(COLL)
    with pytest.raises(FenceMoved):
        await store.require_epochs_current(ctx.settings[WRITTEN_UNDER])


async def test_every_token_a_record_wrote_under_must_be_current_not_just_the_last(store) -> None:
    """A summary written under the old epoch and the rest under the new one: the old one is retired, its points are gone."""
    epochs = store._guard._pins.epochs
    first = await store._guard.capture(COLL)
    ticket = await epochs.begin_reset(COLL)
    second = await store._guard.capture(COLL)
    assert first.token != second.token
    log_ = vs._PointLog()
    handle = vs._POINTED.set(log_)
    try:
        vs._note_fence(first)
        vs._note_fence(second)
    finally:
        vs._POINTED.reset(handle)
    del ticket
    with pytest.raises(FenceMoved):
        await store.require_epochs_current({c: sorted(t) for c, t in log_.tokens.items()})
    await store.require_epochs_current({COLL: [second.token]})


async def test_a_record_whose_epoch_moved_is_not_marked_indexed() -> None:
    from tests.unit.modules.transformers.test_sink_orchestrator import _make_ctx, _make_orchestrator

    orch = _make_orchestrator(graph_doc={"indexingStatus": "IN_PROGRESS"})
    orch._update_indexing_status = AsyncMock(return_value=True)

    async def apply(ctx):  # noqa: ANN001, ANN202
        ctx.settings[WRITTEN_UNDER] = {"records": ["t0"]}

    orch.vector_store.apply = AsyncMock(side_effect=apply)
    orch.vector_store.require_epochs_current = AsyncMock(side_effect=FenceMoved("the collection was reset"))
    with pytest.raises(FenceMoved):
        await orch.apply(_make_ctx())
    orch._update_indexing_status.assert_not_awaited()


async def test_a_record_that_wrote_nothing_reads_no_epoch() -> None:
    from tests.unit.modules.transformers.test_sink_orchestrator import _make_ctx, _make_orchestrator

    orch = _make_orchestrator(graph_doc={"indexingStatus": "IN_PROGRESS"})
    orch._update_indexing_status = AsyncMock(return_value=True)
    orch.vector_store.require_epochs_current = AsyncMock()
    await orch.apply(_make_ctx())
    orch.vector_store.require_epochs_current.assert_not_awaited()
    orch._update_indexing_status.assert_awaited_once()


async def test_the_record_summary_is_checked_against_the_epoch_it_was_written_under(store, monkeypatch) -> None:
    """The summary is written outside ``apply``: a reset during its write raises, so the stage writes it again."""
    store._build_record_summary_document = MagicMock(return_value=MagicMock())
    store._collection_with_model = AsyncMock(return_value=COLL)
    store.delete_blocks_by_ids = AsyncMock()
    store._bind_membership = AsyncMock(return_value=None)
    store._check_for_leaks = AsyncMock()
    epochs = store._guard._pins.epochs

    async def write(docs, record_id, collection, fence=None):  # noqa: ANN001, ANN202
        vs._note_fence(await store._guard.capture(collection))  # what the chunk writer does when it accepts a batch
        await epochs.begin_reset(collection)  # a cleanup moves the epoch while the summary is being embedded

    store._process_document_chunks = AsyncMock(side_effect=write)
    monkeypatch.setattr(vs, "reset_membership_context", lambda _t: None)
    with pytest.raises(FenceMoved):
        await store.index_record_summary(RID, VRID, ORG, MagicMock(), None)
    store._check_for_leaks.assert_not_awaited()
