"""The production lines that write the record processing diagnostics, tested where they are.

Each test drives the real function that holds the write (with the graph provider replaced) and pins what it writes: removing
the write from that function fails the test. The store statements are covered against real stores elsewhere; this is the
wiring between the pipeline and them.
"""

from __future__ import annotations

import ast
import inspect
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes.chatbot import attachment_record_doc
from app.config.constants.arangodb import ProgressStatus
from app.events import events as events_module
from app.events.processor import Processor
from app.modules.transformers.sink_orchestrator import sink_only_end_fields
from app.services.messaging.kafka.handlers.entity import EntityEventService
from app.services.messaging.kafka.handlers.record import requeue_updates
from tests.support.record_handler import make_record_handler
from tests.unit.events.test_events import _make_event_processor

IP, NS, Q = ProgressStatus.IN_PROGRESS.value, ProgressStatus.NOT_STARTED.value, ProgressStatus.QUEUED.value
APP = Path(events_module.__file__).resolve().parents[1]


class TestTheAttemptStart:
    @pytest.mark.asyncio
    async def test_the_legacy_in_process_and_the_twin_attach_attempt_start_goes_through_the_one_statement(self) -> None:
        ep, _, _, gp = _make_event_processor()

        await ep.mark_record_status({"_key": "r1"}, ProgressStatus.IN_PROGRESS, content_rev="rev-a", parsing_starts=True)

        gp.write_attempt_start.assert_awaited_once()
        fields = gp.write_attempt_start.await_args.args[1]
        assert fields["indexingStatus"] == IP and fields["contentRev"] == "rev-a"

    @pytest.mark.asyncio
    async def test_any_other_status_is_a_plain_write(self) -> None:
        ep, _, _, gp = _make_event_processor()

        await ep.mark_record_status({"_key": "r1"}, ProgressStatus.EMPTY)

        gp.write_attempt_start.assert_not_awaited()
        gp.update_node.assert_awaited()

    @pytest.mark.asyncio
    async def test_a_store_only_run_does_not_start_an_attempt(self) -> None:
        from app.modules.indexing.parse_now_marker import (
            ParseNowRun,
            begin_run,
            end_run,
        )

        ep, _, _, gp = _make_event_processor()
        begin_run(ParseNowRun(store_only=True))
        try:
            await ep.mark_record_status({"_key": "r1"}, ProgressStatus.IN_PROGRESS)
        finally:
            end_run()

        gp.write_attempt_start.assert_not_awaited()

    def test_the_service_path_attempt_start_in_on_event_passes_the_flag(self) -> None:
        """The in-process-pipeline-with-services attempt start is one ``update_record_fields`` call in ``on_event``: it must ask
        for an attempt start (a plain write would never count the attempt or start the run)."""
        tree = ast.parse(inspect.getsource(events_module))
        on_event = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "on_event")
        writers = [
            call for call in ast.walk(on_event)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "update_record_fields"
            and "IN_PROGRESS" in ast.unparse(call) and "parsingStatus" in ast.unparse(call)
        ]
        assert writers, "the service-path attempt start was not found"
        assert all(any(k.arg == "attempt_start" and ast.unparse(k.value) == "True" for k in call.keywords) for call in writers)


class TestTheTerminalAndRequeueWrites:
    @pytest.mark.asyncio
    async def test_the_handler_stamps_failed_at_on_a_failure_and_not_on_another_outcome(self) -> None:
        handler = make_record_handler()
        gp = handler.event_processor.graph_provider
        gp.get_document = AsyncMock(return_value={"_key": "r1", "extractionStatus": "NOT_STARTED"})
        gp.update_node = AsyncMock(return_value=True)

        await handler._RecordEventHandler__update_document_status("r1", ProgressStatus.FAILED.value, ProgressStatus.FAILED.value, "boom")
        failed = gp.update_node.await_args.args[2]
        await handler._RecordEventHandler__update_document_status("r1", ProgressStatus.COMPLETED.value, ProgressStatus.COMPLETED.value)
        done = gp.update_node.await_args.args[2]

        assert isinstance(failed["failedAt"], int) and "failedAt" not in done

    @pytest.mark.asyncio
    async def test_the_legacy_processor_stamps_failed_at_on_a_failure(self) -> None:
        processor = Processor.__new__(Processor)
        processor.logger = MagicMock()
        processor.graph_provider = AsyncMock()
        processor.graph_provider.get_document = AsyncMock(return_value={"_key": "r1"})
        processor.graph_provider.update_node = AsyncMock(return_value=True)

        await processor._mark_record("r1", ProgressStatus.FAILED, reason="x")
        failed = processor.graph_provider.update_node.await_args.args[2]
        await processor._mark_record("r1", ProgressStatus.EMPTY)
        empty = processor.graph_provider.update_node.await_args.args[2]

        assert isinstance(failed["failedAt"], int) and "failedAt" not in empty

    def test_a_retry_revert_keeps_the_run_and_a_record_from_before_the_fields_gains_its_start(self) -> None:
        inside = {"indexingStatus": IP, "firstQueuedAt": 5, "indexingAttempts": 2}
        before = {"indexingStatus": IP, "queuedAtTimestamp": 9}

        assert requeue_updates(inside, store_only=False)["firstQueuedAt"] == 5
        assert "indexingAttempts" not in requeue_updates(inside, store_only=False)
        assert requeue_updates(before, store_only=False)["firstQueuedAt"] == 9
        assert requeue_updates({"indexingStatus": "COMPLETED"}, store_only=False) == {}

    def test_the_handler_uses_that_function_for_the_revert(self) -> None:
        source = (APP / "services/messaging/kafka/handlers/record.py").read_text(encoding="utf-8")
        assert "updates = requeue_updates(current, store_only=store_only)" in source

    @pytest.mark.asyncio
    async def test_a_cut_hands_the_record_back_inside_its_run(self) -> None:
        ep, _, _, gp = _make_event_processor()
        gp.get_document = AsyncMock(return_value={"_key": "r1", "indexingStatus": IP, "firstQueuedAt": 7, "indexingAttempts": 1})
        gp.update_node_fields_if_match = AsyncMock(return_value=True)

        await ep._cut_writer("r1", "d1")(1.0, 0)

        updates = gp.update_node_fields_if_match.await_args.args[2]
        assert updates["firstQueuedAt"] == 7 and "indexingAttempts" not in updates

    @pytest.mark.asyncio
    async def test_a_cut_of_a_record_from_before_the_fields_fills_its_start(self) -> None:
        ep, _, _, gp = _make_event_processor()
        gp.get_document = AsyncMock(return_value={"_key": "r1", "indexingStatus": IP, "queuedAtTimestamp": 4})
        gp.update_node_fields_if_match = AsyncMock(return_value=True)

        await ep._cut_writer("r1", "d1")(1.0, 0)

        assert gp.update_node_fields_if_match.await_args.args[2]["firstQueuedAt"] == 4

    @pytest.mark.asyncio
    async def test_a_duplicate_parked_behind_its_twin_is_requeued_inside_its_run_from_a_fresh_read(self) -> None:
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = [{
            "_key": "dup-1", "virtualRecordId": "vr-1", "indexingStatus": IP, "extractionStatus": IP,
        }]
        stored = {"_key": "r1", "indexingStatus": IP, "firstQueuedAt": 11, "indexingAttempts": 3}
        gp.get_document = AsyncMock(return_value=stored)
        doc = {"_key": "r1", "md5Checksum": "abc", "recordType": "FILE", "sizeInBytes": 10}

        with patch.object(ep, "_attach_to_finished_twin", new=AsyncMock()):
            await ep._check_duplicate_by_md5(b"x", doc, before_reusing_status=None)

        queued = [c.args[2] for c in gp.update_node.await_args_list if c.args[2].get("indexingStatus") == Q]
        assert queued, "the record was not requeued behind its twin"
        assert queued[0]["firstQueuedAt"] == 11, "continued from the record as stored now, not from the event's copy"
        gp.get_document.assert_awaited()


class TestTheStartWrites:
    def test_a_sink_only_end_clears_the_start(self) -> None:
        fields = sink_only_end_fields("r1", "vr1")

        assert fields["indexingStatus"] == NS and "firstQueuedAt" in fields and fields["firstQueuedAt"] is None

    def test_the_sink_only_branch_writes_what_that_function_returns(self) -> None:
        source = (APP / "modules/transformers/sink_orchestrator.py").read_text(encoding="utf-8")
        assert "sink_only_end_fields(record_id, record.virtual_record_id)" in source

    def test_a_chat_attachment_record_starts_its_run(self) -> None:
        item = types.SimpleNamespace(fileName="a.pdf", mimeType="application/pdf", size=3)

        doc = attachment_record_doc(record_id="r1", org_id="o", item=item, external_record_id="x", virtual_record_id="v", now=1234)

        assert doc["firstQueuedAt"] == 1234 and doc["indexingAttempts"] == 0 and doc["indexingStatus"] == NS

    def test_the_upload_route_builds_its_record_with_that_function(self) -> None:
        source = (APP / "api/routes/chatbot.py").read_text(encoding="utf-8")
        assert "record_doc = attachment_record_doc(" in source

    def test_a_connector_turned_back_on_starts_a_new_run_for_a_record_it_parked(self) -> None:
        fields = EntityEventService._requeued_fields({"indexingStatus": "AUTO_INDEX_OFF", "firstQueuedAt": 3}, 99)

        assert fields["firstQueuedAt"] == 99 and fields["indexingAttempts"] == 0
