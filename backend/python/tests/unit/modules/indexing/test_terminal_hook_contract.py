"""Every writer of a terminal ``indexingStatus`` tells the lifecycle (``notify_terminal``) after its write.

A writer that skips the call leaves an installed lifecycle blind to that outcome. The scan fails when a new writer of a
terminal status appears without the call or an entry here, and the behaviour tests run the writers against a recording lifecycle.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.modules.indexing import extension
from app.modules.indexing.extension import DefaultIndexingLifecycle, IndexingExtension
from tests.unit.events.test_events import _make_event_processor

APP = Path(__file__).resolve().parents[4] / "app"

# file -> number of ``await notify_terminal(`` calls, and which writer each one follows
CALLERS = {
    "modules/transformers/sink_orchestrator.py": 1,  # _update_indexing_status: COMPLETED
    "modules/transformers/pipeline.py": 1,  # EMPTY of an empty parse
    "events/events.py": 2,  # mark_record_status, the in-process EMPTY write
    "events/processor.py": 1,  # _mark_record: EMPTY, FAILED, FILE_TYPE_NOT_SUPPORTED
    "services/messaging/kafka/handlers/record.py": 1,  # __update_document_status
}

STATUS_WRITE = re.compile(
    r'"indexingStatus"\s*:\s*ProgressStatus\.(?:COMPLETED|EMPTY|FAILED|PASSWORD_PROTECTED|FILE_TYPE_NOT_SUPPORTED)'
)

# modules that write a terminal status for a reason that is not the end of a record's own indexing
EXCLUDED = (
    "modules/pipeline", "modules/indexing/record_republish.py", "modules/indexing/parse_now_marker.py",
    "modules/indexing/connector_off_events.py", "modules/indexing/duplicate_reconcile.py",
    "modules/indexing/class_units_backfill.py", "modules/indexing/embed_resume.py", "events/dedup.py", "migrations/", "scripts/",
    # names COMPLETED as the status a write must still find (a compare-and-set expectation); it writes a note, not a status
    "modules/indexing/pdf_page_limit_audit.py",
)


@pytest.mark.parametrize("path", sorted(CALLERS))
def test_each_writer_file_calls_notify_terminal_where_it_writes(path: str) -> None:
    source = (APP / path).read_text()
    assert len(re.findall(r"await notify_terminal\(", source)) == CALLERS[path]


def test_no_other_module_writes_a_terminal_indexing_status_without_listing_itself() -> None:
    found = {
        str(p.relative_to(APP)) for p in APP.rglob("*.py")
        if "connectors/sources" not in str(p) and STATUS_WRITE.search(p.read_text())
    } - set(CALLERS)
    unexplained = {f for f in found if not any(f.startswith(e) for e in EXCLUDED)}
    assert not unexplained, f"writes a terminal indexingStatus without calling notify_terminal: {sorted(unexplained)}"


def test_the_scan_sees_a_writer_it_has_not_been_told_about() -> None:
    """Negative control: the predicate flags a fresh writer, so the test above can fail."""
    line = '    "indexingStatus": ProgressStatus.COMPLETED.value,'
    assert STATUS_WRITE.search(line)
    assert not any("some/new/writer.py".startswith(e) for e in EXCLUDED)


class Recording(DefaultIndexingLifecycle):
    def __init__(self) -> None:
        self.seen: list[tuple[object, object]] = []

    async def on_terminal(self, record: object, status: object) -> None:
        self.seen.append((record, status))


@pytest.fixture
def life() -> Recording:
    lifecycle = Recording()
    extension.install(IndexingExtension(lifecycle=lifecycle))
    return lifecycle


async def test_the_event_processors_status_writer_notifies_after_the_write(life: Recording) -> None:
    ep, _logger, _processor, gp = _make_event_processor()
    doc = {"_key": "rec-1"}

    await ep.mark_record_status(doc, ProgressStatus.FAILED)
    await ep.mark_record_status(doc, ProgressStatus.IN_PROGRESS)

    gp.update_node.assert_awaited()
    assert life.seen == [(doc, "FAILED"), (doc, "IN_PROGRESS")]


async def test_the_legacy_processors_status_writer_notifies_only_when_the_write_landed(life: Recording) -> None:
    from app.events.processor import Processor

    gp = AsyncMock()
    gp.get_document.return_value = {"_key": "rec-1"}
    with patch("app.events.processor.DoclingClient"), patch("app.events.processor.DoclingProcessor"):
        proc = Processor(
            logger=MagicMock(), config_service=MagicMock(), indexing_pipeline=MagicMock(), graph_provider=gp,
            parsers={}, document_extractor=MagicMock(), sink_orchestrator=MagicMock(),
        )

    gp.update_node.return_value = False
    await proc._mark_record("rec-1", ProgressStatus.EMPTY)
    assert life.seen == []

    gp.update_node.return_value = True
    await proc._mark_record("rec-1", ProgressStatus.EMPTY)
    assert [status for _record, status in life.seen] == ["EMPTY"]
