"""``EventProcessor.on_event`` in a parse-now run.

In store-only mode (a record with automatic indexing off) every status the event chain would write
becomes a marker write, deduplication is bypassed and nothing is cleaned up; in a run that indexes,
the statuses are written as usual with the marker beside them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import MimeTypes, ProgressStatus
from app.events.processor import Processor
from app.modules.indexing.parse_now_marker import (
    ParseNowRun,
    begin_run,
    end_run,
    store_only_active,
)
from app.modules.pipeline.fingerprint import content_revision
from app.modules.transformers.transformer import PARSE_NOW, STORE_ONLY
from tests.unit.events.test_events import (
    DedupDecision,
    _drain,
    _make_event_payload,
    _make_event_processor,
    _mock_processor_gen,
)
from tests.unit.modules.transformers.test_sink_orchestrator_artifacts import _record

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.events.events import EventProcessor

CONTENT = b"hello"
REV = content_revision(CONTENT)
STATUS_FIELDS = {"indexingStatus", "parsingStatus", "extractionStatus", "processingStartedAt", "reason", "hasIndexedCopy"}


@pytest.fixture(autouse=True)
def _no_run() -> Iterator[None]:
    end_run()
    yield
    end_run()


def _doc(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    doc = {
        "_key": "rec-1", "recordType": "FILE", "orgId": "org-1", "indexingStatus": "AUTO_INDEX_OFF",
        "reason": "Automatic indexing is off", "virtualRecordId": "vr-1", "extractionStatus": "NOT_STARTED",
    }
    doc.update(overrides)
    return doc


def _setup(
    *, doc: dict[str, Any] | None = None, sharing: list[str] | None = None
) -> tuple[EventProcessor, MagicMock, AsyncMock]:
    ep, _logger, processor, gp = _make_event_processor()
    gp.get_document.return_value = doc or _doc()
    gp.get_records_by_virtual_record_id = AsyncMock(return_value=sharing or ["rec-1"])
    processor.process_txt_document = MagicMock(side_effect=_mock_processor_gen)
    ep._drop_the_previous_vectors_of_an_emptied_record = AsyncMock()
    ep._spawn_abandoned_vrid_cleanup = MagicMock()
    ep._rewrite_or_delete_vrid_vectors = AsyncMock()
    ep.stage_ingress = None
    return ep, processor, gp


def _writes(gp: AsyncMock) -> list[dict[str, Any]]:
    return [call.args[2] for call in gp.update_node.await_args_list]


def _event(**kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
    return _make_event_payload(
        mime_type=MimeTypes.PLAIN_TEXT.value, event_type="reindexRecord", virtual_record_id="vr-1", buffer=CONTENT, **kwargs
    )


@pytest.mark.asyncio
class TestStoreOnly:
    @pytest.fixture(autouse=True)
    def _run(self) -> None:
        begin_run(ParseNowRun(store_only=True, content_rev="old"))

    async def test_no_write_touches_the_records_own_status(self) -> None:
        # B3, B7, B8
        ep, _processor, gp = _setup()

        await _drain(ep.on_event(_event()))

        writes = _writes(gp)
        assert writes
        assert all(not STATUS_FIELDS & set(write) for write in writes)
        assert {"contentRev": REV, "parseNowStatus": "IN_PROGRESS", "parseNowRev": REV, "parseNowReason": None, "parseNowAt": ANY} in writes

    async def test_a_finished_twin_is_not_attached_to(self) -> None:
        # B6
        ep, processor, _gp = _setup()
        check = AsyncMock(side_effect=AssertionError("a store-only run must not look for a twin"))

        with patch.object(ep, "_check_duplicate_by_md5", check):
            await _drain(ep.on_event(_event()))

        check.assert_not_awaited()
        processor.process_txt_document.assert_called_once()

    async def test_the_processor_builds_its_context_for_a_store_only_run(self) -> None:
        # The run reaches Processor._create_transform_context from inside the event chain.
        ep, processor, _gp = _setup()
        seen: list[dict[str, Any]] = []

        async def parse(*_args: object, **_kwargs: object):  # noqa: ANN202
            seen.append(Processor._create_transform_context(MagicMock(), _record()).settings)
            async for event in _mock_processor_gen():
                yield event

        processor.process_txt_document = MagicMock(side_effect=parse)

        await _drain(ep.on_event(_event()))

        assert seen == [{PARSE_NOW: True, STORE_ONLY: True}]
        assert store_only_active()  # still this task's run until the handler ends it

    async def test_the_records_vectors_and_virtual_record_are_not_touched(self) -> None:
        ep, _processor, gp = _setup()

        await _drain(ep.on_event(_event()))

        ep._spawn_abandoned_vrid_cleanup.assert_not_called()
        ep._rewrite_or_delete_vrid_vectors.assert_not_awaited()
        assert not any("virtualRecordId" in write for write in _writes(gp))  # kept: nobody else shares it

    async def test_a_virtual_record_shared_with_another_record_is_left_to_it(self) -> None:
        ep, _processor, gp = _setup(sharing=["rec-1", "rec-2"])

        await _drain(ep.on_event(_event()))

        (vrid_write,) = [write for write in _writes(gp) if "virtualRecordId" in write]
        assert vrid_write["virtualRecordId"] != "vr-1"
        ep._rewrite_or_delete_vrid_vectors.assert_not_awaited()

    async def test_an_empty_file_is_marked_empty_and_nothing_is_dropped(self) -> None:
        ep, processor, gp = _setup()

        await _drain(ep.on_event(_make_event_payload(
            mime_type=MimeTypes.PLAIN_TEXT.value, event_type="reindexRecord", virtual_record_id="vr-1", buffer=b"",
        )))

        assert _writes(gp)[-1]["parseNowStatus"] == "EMPTY"
        ep._drop_the_previous_vectors_of_an_emptied_record.assert_not_awaited()
        assert all(not STATUS_FIELDS & set(write) for write in _writes(gp))
        processor.process_txt_document.assert_not_called()


@pytest.mark.asyncio
class TestRunThatIndexes:
    @pytest.fixture(autouse=True)
    def _run(self) -> None:
        begin_run(ParseNowRun(store_only=False, content_rev="old"))

    async def test_the_statuses_are_written_as_usual_with_the_marker_beside_them(self) -> None:
        ep, processor, gp = _setup(doc=_doc(indexingStatus="QUEUED"))
        check = AsyncMock(return_value=DedupDecision(virtual_record_id=None, skip_indexing=False))

        with patch.object(ep, "_check_duplicate_by_md5", check):
            await _drain(ep.on_event(_event()))

        check.assert_awaited_once()  # a record that will be indexed anyway may still share its twin's content
        in_progress = next(w for w in _writes(gp) if w.get("indexingStatus") == ProgressStatus.IN_PROGRESS.value)
        assert in_progress["parseNowStatus"] == "IN_PROGRESS" and in_progress["parseNowRev"] == REV
        assert in_progress["contentRev"] == REV
        processor.process_txt_document.assert_called_once()


@pytest.mark.asyncio
class TestOutsideAParseNowRun:
    async def test_nothing_changes(self) -> None:
        ep, _processor, gp = _setup(doc=_doc(indexingStatus="QUEUED"))
        check = AsyncMock(return_value=DedupDecision(virtual_record_id=None, skip_indexing=False))

        with patch.object(ep, "_check_duplicate_by_md5", check):
            await _drain(ep.on_event(_event()))

        assert not any(key.startswith("parseNow") for write in _writes(gp) for key in write)
        assert store_only_active() is False


@pytest.mark.asyncio
class TestLegacyProcessorWrites:
    async def test_a_status_write_of_the_processor_becomes_the_marker(self) -> None:
        begin_run(ParseNowRun(store_only=True, content_rev=REV))
        processor = Processor.__new__(Processor)
        processor.logger = MagicMock()
        processor.graph_provider = AsyncMock()
        processor.graph_provider.get_document = AsyncMock(return_value={"_key": "rec-1", "parsingStatus": "IN_PROGRESS"})
        processor.graph_provider.update_node = AsyncMock(return_value=True)

        await processor._mark_record("rec-1", ProgressStatus.FILE_TYPE_NOT_SUPPORTED, reason="Too large")

        assert _writes(processor.graph_provider) == [
            {"parseNowStatus": "FILE_TYPE_NOT_SUPPORTED", "parseNowRev": REV, "parseNowReason": "Too large", "parseNowAt": ANY}
        ]
