"""A capped .log/.cfg carries its "partly indexed" note in `indexingNote`, apart from `reason`: the
stages that run after indexing (classification) write `reason`, and must neither replace the note
nor turn their own text into one."""
from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.modules.pipeline.models import StageOutcome, StageResult
from app.modules.pipeline.stages.classify import ClassifyStage
from app.modules.pipeline.stages.classify_io import GraphClassifyIO
from app.modules.pipeline.state import GraphHeadlineStatusWriter
from app.modules.pipeline.worker import StageJobHandler
from app.services.graph_db.common.utils import api_record
from app.utils.user_errors import partly_indexed_text

from .new_type_harness import legacy
from .samples import make_pdf

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

pytestmark = pytest.mark.timeout(300)
KIB = 1024
NOTE_FIELD = "indexingNote"


def _big_log() -> bytes:
    return "".join(f"2026-11-14 12:00:{i % 60:02d} worker started job {i}\n" for i in range(200_000)).encode()


class _Graph:
    """The record store the headline writer writes to."""

    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record

    async def compare_and_set_record_fields(
        self, record_ids: Sequence[str], _rev: str, fields: dict[str, Any],
    ) -> list[str]:
        self.record.update(fields)
        return list(record_ids)


async def _finish_classify(record: dict[str, Any], result: StageResult) -> None:
    handler = StageJobHandler.__new__(StageJobHandler)
    handler._headlines = GraphHeadlineStatusWriter(_Graph(record))  # type: ignore[arg-type]
    handler._logger = MagicMock()
    handler._on_headline_settled = None
    handler._coordinator = MagicMock()
    handler._coordinator.on_stage_done = AsyncMock()
    await handler._finish(SimpleNamespace(record_ids=["r1"], rev="rev-1", job_id="job-1"), ClassifyStage(), result)  # type: ignore[arg-type]


async def _classify_skip(name: str) -> StageResult:
    io = GraphClassifyIO.__new__(GraphClassifyIO)
    io._classifier = AsyncMock(return_value=None)  # type: ignore[attr-defined]
    io._job = SimpleNamespace(record_ids=("r1",))  # type: ignore[attr-defined]
    io._record_header = AsyncMock(return_value=(name, "FILE"))  # type: ignore[method-assign]
    io.current_record_ids = AsyncMock(return_value=["r1"])  # type: ignore[method-assign]
    io.load_blocks = AsyncMock(return_value=MagicMock())  # type: ignore[method-assign]
    io.departments = AsyncMock(return_value=[])  # type: ignore[method-assign]
    return await ClassifyStage().run(SimpleNamespace(org_id="org-1", view=None), io)  # type: ignore[arg-type]


async def test_a_capped_log_keeps_its_note_after_the_classify_stage_skips_it() -> None:
    _, _, record = await legacy("log", "text/plain", _big_log(), name="server.log")
    assert record[NOTE_FIELD] == partly_indexed_text()
    result = await _classify_skip("server.log")
    assert result.outcome is StageOutcome.SKIPPED
    await _finish_classify(record, result)
    assert record["extractionStatus"] == ProgressStatus.SKIPPED.value
    assert api_record(record)[NOTE_FIELD] == partly_indexed_text()
    assert record.get("reason") != partly_indexed_text()


async def test_a_capped_log_keeps_its_note_after_the_classify_stage_completes() -> None:
    _, _, record = await legacy("log", "text/plain", _big_log(), name="server.log")
    await _finish_classify(record, StageResult(outcome=StageOutcome.COMPLETED))
    assert api_record(record)[NOTE_FIELD] == partly_indexed_text()


async def test_a_completed_pdf_whose_extraction_was_skipped_has_no_note() -> None:
    from app.events.events import EventProcessor

    async def index_pdf(self: EventProcessor, *_a: object, **_k: object) -> AsyncIterator[None]:
        # What the PDF path does at its end: the record is indexed.
        await self.graph_provider.update_node("r1", "records", {"indexingStatus": ProgressStatus.COMPLETED.value})
        return
        yield

    with patch.object(EventProcessor, "_dispatch_pdf_binary", index_pdf):
        _, _, record = await legacy("pdf", "application/pdf", make_pdf([["Quarterly summary", "All good."]]))
    assert record["indexingStatus"] == ProgressStatus.COMPLETED.value
    await _finish_classify(
        record, StageResult(outcome=StageOutcome.SKIPPED, reason="Extraction skipped: LLM not configured"),
    )
    assert record["reason"] == "Extraction skipped: LLM not configured"
    assert api_record(record).get(NOTE_FIELD) is None


async def test_a_log_no_longer_capped_drops_the_note_of_an_earlier_run() -> None:
    from . import new_type_harness

    real_record = new_type_harness._record

    def with_note(*a: object, **k: object) -> dict[str, Any]:
        return {**real_record(*a, **k), NOTE_FIELD: partly_indexed_text()}  # type: ignore[arg-type]

    new_type_harness._record = with_note  # type: ignore[assignment]
    try:
        _, _, record = await legacy("log", "text/plain", b"one short line\n", name="server.log")
    finally:
        new_type_harness._record = real_record  # type: ignore[assignment]
    assert record["indexingStatus"] == ProgressStatus.COMPLETED.value
    assert api_record(record).get(NOTE_FIELD) is None
