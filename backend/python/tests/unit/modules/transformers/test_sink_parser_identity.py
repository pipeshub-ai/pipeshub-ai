"""The COMPLETED write of a run carries the identity of the parse, through the real ``_update_indexing_status``.

The identity is the one taken from the event's labels when the run began and carried on the transform context. It is written in the
same dict as the status, so a replacement copy's commit (``swap.commit``) moves it together with the record's ``virtualRecordId``.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.indexing import keep_old_copy
from app.modules.pipeline import parser_identity as pi
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.modules.transformers.transformer import TransformContext
from tests.support.parser_identity_world import file_record

if TYPE_CHECKING:
    from app.models.entities import Record


@pytest.fixture(autouse=True)
def _identity_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", "true")
    pi.end_run_identity()
    yield
    pi.end_run_identity()


def _orchestrator() -> SinkOrchestrator:
    graph = AsyncMock()
    graph.batch_update_nodes = AsyncMock(return_value=True)
    orch = SinkOrchestrator(
        graphdb=AsyncMock(), blob_storage=AsyncMock(), vector_store=AsyncMock(), graph_provider=graph,
        logger=logging.getLogger("test-sink"), config_service=MagicMock(),
    )
    orch.logger = MagicMock()
    return orch


def _ctx(**fields: object) -> SimpleNamespace:
    record = MagicMock()
    record.id, record.virtual_record_id, record.is_vlm_ocr_processed = "rec-1", "vr-9", False
    base = {"parser_identity": None, "parser_identity_source": pi.SOURCE_PARSED}
    return SimpleNamespace(record=record, settings={}, **{**base, **fields})


@pytest.mark.asyncio
async def test_the_indexed_write_carries_the_identity_with_its_own_timestamp_and_copy() -> None:
    orch = _orchestrator()
    ctx = _ctx(parser_identity="inproc:txt@1")

    await orch._update_indexing_status(ctx)

    (row,) = orch.graph_provider.batch_update_nodes.await_args.args[0]
    assert row[pi.IDENTITY_FIELD] == "inproc:txt@1"
    assert row[pi.IDENTITY_AT_FIELD] == row["lastIndexTimestamp"] == ctx.indexed_at_ms
    assert row[pi.IDENTITY_VRID_FIELD] == "vr-9" == row["virtualRecordId"]
    assert row[pi.IDENTITY_SOURCE_FIELD] == pi.SOURCE_PARSED
    assert pi.known_identity(row) == "inproc:txt@1"


@pytest.mark.asyncio
async def test_a_run_whose_parser_is_not_tracked_writes_no_identity() -> None:
    orch = _orchestrator()

    await orch._update_indexing_status(_ctx())

    (row,) = orch.graph_provider.batch_update_nodes.await_args.args[0]
    assert not set(pi.IDENTITY_FIELDS) & row.keys()


@pytest.mark.asyncio
async def test_a_replacement_copy_commit_receives_the_identity_in_its_fields() -> None:
    """Not a helper: the real write hands the same fields to the commit that moves the record to its new copy."""
    orch = _orchestrator()
    orch.swap = MagicMock()
    orch.swap.commit = AsyncMock(return_value=True)
    swap = keep_old_copy.SwapRun("rec-1", "o", "vr-old", "vr-9", False)
    ctx = _ctx(parser_identity="svc:md@1", parser_identity_source=pi.SOURCE_TWIN)

    with patch.object(keep_old_copy, "current_swap", return_value=swap):
        await orch._update_indexing_status(ctx)

    fields = orch.swap.commit.await_args.args[2]
    assert fields[pi.IDENTITY_FIELD] == "svc:md@1"
    assert fields[pi.IDENTITY_SOURCE_FIELD] == pi.SOURCE_TWIN
    assert fields[pi.IDENTITY_VRID_FIELD] == fields["virtualRecordId"] == "vr-9"
    orch.graph_provider.batch_update_nodes.assert_not_awaited()


def test_the_context_takes_the_identity_the_run_began_with() -> None:
    record: Record = file_record("a.txt", "txt", "text/plain")
    assert TransformContext(record=record).parser_identity is None
    pi.begin_run_identity("inproc:txt@1")
    assert TransformContext(record=record).parser_identity == "inproc:txt@1"
    assert TransformContext(record=record, parser_identity=None).parser_identity is None


@pytest.mark.asyncio
async def test_the_run_identity_does_not_outlive_its_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """A context built later in the same task for another record must not carry the identity of the run that just ended."""
    from tests.support.parser_identity_world import World

    monkeypatch.delenv("USE_PARSING_SERVICE", raising=False)
    world = World()
    world.identified("inproc:txt@1")
    world.renamed_to_markdown()
    await world.run(world.event(mime="text/markdown", ext="md", name="notes.md"))
    assert world.parses == 1

    other = file_record("report.pdf", "pdf", "application/pdf", record_id="rec-2")

    assert TransformContext(record=other).parser_identity is None
