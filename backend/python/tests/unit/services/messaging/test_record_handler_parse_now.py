"""The record handler on a parse-now event (``reindexRecord`` with ``parseNow``, from the priority topic).

It re-checks everything the producer checked before it downloads or parses anything; a record with
automatic indexing off is parsed in store-only mode and keeps its statuses; failures and retries end
in the parse-now marker rather than in the record's status.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames, EventTypes
from app.exceptions.indexing_exceptions import (
    DocumentProcessingError,
    PasswordProtectedError,
)
from app.modules.indexing.parse_now_marker import current_run, store_only_active
from app.modules.parsers.parse_pool import ParseWorkerError
from app.services.messaging import record_budget
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    StreamMessage,
    messaging_env,
)
from app.services.messaging.kafka.handlers import record as record_module
from app.services.messaging.kafka.handlers.record import RecordEventHandler
from app.utils.user_errors import (
    PARSE_WORKER_KILLED_RETRYING,
    PARSE_WORKER_OUT_OF_MEMORY,
    RETRY_SCHEDULED,
)
from tests.support.record_access import InMemoryRecordAccess
from tests.unit.attempt_start import attempt_start_via

RECORDS = CollectionNames.RECORDS.value
FILES = CollectionNames.FILES.value
APPS = CollectionNames.APPS.value
REV = "rev-1"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _record(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    """The RECORDS document. It has no `extension`: that is on the FILES document (`_file`)."""
    record = {
        "_key": "rec-1", "orgId": "org-1", "connectorId": "conn-1", "origin": "CONNECTOR", "recordName": "deck.pptx",
        "recordType": "FILE", "mimeType": PPTX, "indexingStatus": "AUTO_INDEX_OFF", "isFile": True,
        "virtualRecordId": "vr-1", "contentRev": REV, "version": 3,
    }
    record.update(overrides)
    return record


def _file(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    """The FILES document of the same key, as `FileRecord.to_arango_record` writes it."""
    file = {"_key": "rec-1", "orgId": "org-1", "name": "deck.pptx", "isFile": True, "extension": "pptx", "path": None}
    file.update(overrides)
    return file


def _payload(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    payload = {
        "recordId": "rec-1", "orgId": "org-1", "connectorId": "conn-1", "connectorName": "SLACK", "mimeType": PPTX,
        "virtualRecordId": "vr-1", "parseNow": True, "requestedByUserId": "user-1", "contentRev": REV,
    }
    payload.update(overrides)
    return payload


class Harness:
    """A handler over a fake graph, with the event processor's run recorded."""

    def __init__(
        self, record: dict[str, Any] | None, *, connector: dict[str, Any] | None = None, access: bool = True,
        file: dict[str, Any] | None = None,
    ) -> None:
        self.record = record
        self.file = _file() if file is None else file
        self.files_error: Exception | None = None
        self.connector = {"_key": "conn-1", "isActive": True, "name": "Slack"} if connector is None else connector
        self.runs: list[dict[str, Any]] = []
        self.fail_with: Exception | None = None
        event_processor = MagicMock()
        graph = AsyncMock()
        graph.get_document = AsyncMock(side_effect=self._get)
        graph.update_node = AsyncMock(return_value=True)
        graph.write_attempt_start = attempt_start_via(graph)
        self.access = InMemoryRecordAccess().user("user-1", "org-1").record("rec-1", "org-1")
        if access:
            self.access.grant("rec-1", user="user-1")
        self.access.serve(graph)
        event_processor.graph_provider = graph
        event_processor.stage_ingress = None
        event_processor.sink_orchestrator = None  # no vector store to ask about the weight precision
        event_processor.on_event = self._on_event
        pipeline = AsyncMock()
        pipeline.bulk_delete_embeddings = AsyncMock(return_value={"success": True})
        event_processor.processor.indexing_pipeline = pipeline
        config = AsyncMock()
        config.get_config = AsyncMock(return_value={"connectors": {"endpoint": "http://connectors"}})
        self.handler = RecordEventHandler(logger=MagicMock(), config_service=config, event_processor=event_processor, producer=AsyncMock())
        for name in ("_release_queued_copies", "_trigger_next_queued_duplicate", "_propagate_primary_failure_to_queued_duplicates",
                     "_request_code_edge_build_if_repo_drained"):
            setattr(self.handler, name, AsyncMock())
        self.graph = graph
        self.pipeline = pipeline

    async def _get(self, key: str, collection: str, *, raise_on_error: bool = False, **_: object) -> dict[str, Any] | None:
        if collection == APPS:
            return self.connector
        if collection == FILES:
            if self.files_error is not None:
                # As every graph backend does: a failed read is logged and returns None unless asked to raise.
                if raise_on_error:
                    raise self.files_error
                return None
            return dict(self.file) if self.file else None
        return None if self.record is None else dict(self.record)

    async def _on_event(self, event_data: dict[str, Any]):  # noqa: ANN202
        run = current_run()
        self.runs.append({"payload": dict(event_data["payload"]), "store_only": store_only_active(), "run": run})
        if self.fail_with is not None:
            raise self.fail_with
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="rec-1"))
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="rec-1"))

    async def run(
        self, payload: dict[str, Any] | None = None, event_type: str = EventTypes.REINDEX_RECORD.value,
        *, download_error: Exception | None = None,
    ) -> list[str]:
        download = AsyncMock(return_value={"data": b"hello"}, side_effect=download_error)
        with (
            patch.object(record_module, "generate_jwt", AsyncMock(return_value="token")),
            patch.object(record_module, "make_api_call", download),
        ):
            self.download = download
            return [e.event async for e in self.handler.process_event(event_type, payload or _payload())]

    def writes(self) -> list[dict[str, Any]]:
        return [call.args[2] for call in self.graph.update_node.await_args_list]


DRAINED = [IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE]


@pytest.mark.asyncio
class TestRechecksBeforeParsing:
    """L2: dropped and logged, with no download, no parse and no status write."""

    async def _assert_dropped(
        self, harness: Harness, payload: dict[str, Any] | None = None, *, marked: bool = False,
    ) -> None:
        events = await harness.run(payload)

        assert events == DRAINED
        harness.download.assert_not_awaited()
        assert harness.runs == []
        # A final refusal leaves FAILED for a reader that is waiting; a redundant one writes nothing.
        writes = harness.writes()
        assert [w["parseNowStatus"] for w in writes] == (["FAILED"] if marked else [])
        assert all(w["parseNowReason"] for w in writes)
        assert current_run() is None

    async def test_an_event_for_another_org_is_dropped(self) -> None:
        await self._assert_dropped(Harness(_record()), _payload(orgId="org-2"))  # nothing is written for a foreign org

    async def test_an_event_without_an_org_is_dropped(self) -> None:
        await self._assert_dropped(Harness(_record()), _payload(orgId=None))

    async def test_a_missing_record_is_dropped(self) -> None:
        await self._assert_dropped(Harness(None))

    async def test_a_record_in_the_trash_is_dropped(self) -> None:
        await self._assert_dropped(Harness(_record(isDeleted=True)))

    async def test_a_user_who_lost_access_is_dropped(self) -> None:
        harness = Harness(_record(), access=False)

        await self._assert_dropped(harness)  # a user without access marks nothing

        assert harness.access.asked_about == [("user-1", "org-1", "rec-1")]

    async def test_an_event_naming_no_user_is_dropped(self) -> None:
        await self._assert_dropped(Harness(_record()), _payload(requestedByUserId=None))

    async def test_a_folder_is_dropped_not_marked_completed(self) -> None:
        await self._assert_dropped(Harness(_record(isFile=False)), marked=True)

    async def test_content_already_stored_at_its_revision_is_acknowledged(self) -> None:
        # P8, P9: a redelivery of a finished event, or a normal event that stored it first.
        await self._assert_dropped(Harness(_record(parseNowStatus="STORED", parseNowRev=REV)))

    async def test_a_stored_marker_of_an_older_revision_does_not_count(self) -> None:
        harness = Harness(_record(parseNowStatus="STORED", parseNowRev="old"))

        await harness.run()

        assert len(harness.runs) == 1

    async def test_a_record_that_is_already_readable_is_acknowledged(self) -> None:
        await self._assert_dropped(Harness(_record(indexingStatus="QUEUED", hasIndexedCopy=True)))

    async def test_an_indexed_record_is_acknowledged_without_a_reparse(self) -> None:
        await self._assert_dropped(Harness(_record(indexingStatus="COMPLETED")))

    async def test_a_turned_off_connector_settles_as_it_does_today(self) -> None:
        harness = Harness(_record(), connector={"_key": "conn-1", "isActive": False})

        events = await harness.run()

        assert events == DRAINED
        harness.download.assert_not_awaited()
        assert harness.runs == []
        marker, write = harness.writes()
        # The reader is told at once, and the record settles as it does for any event.
        assert marker["parseNowStatus"] == "FAILED" and marker["parseNowReason"] == record_module.CONNECTOR_OFF
        assert write["indexingStatus"] == "AUTO_INDEX_OFF" and write["reason"] == record_module.CONNECTOR_OFF
        assert not any(key.startswith("parseNow") for key in write)

    @pytest.mark.parametrize("status", ["ENABLE_MULTIMODAL_MODELS", "SKIPPED", "FILE_TYPE_NOT_SUPPORTED"])
    async def test_a_status_that_is_not_parsed_on_demand_is_dropped(self, status: str) -> None:
        await self._assert_dropped(Harness(_record(indexingStatus=status)), marked=True)

    async def test_a_removed_connector_writes_nothing(self) -> None:
        await self._assert_dropped(Harness(_record(), connector={}))


@pytest.mark.asyncio
class TestStoreOnlyRun:
    async def test_a_record_with_automatic_indexing_off_is_parsed_without_changing_its_statuses(self) -> None:
        # B3
        harness = Harness(_record(reason="Automatic indexing is off for this connector"))

        events = await harness.run()

        assert events[-1] == IndexingEvent.INDEXING_COMPLETE
        (run,) = harness.runs
        assert run["store_only"] is True
        harness.download.assert_awaited_once()
        # Only the marker is written by the handler, and before the download.
        assert harness.writes() == [{"parseNowStatus": "IN_PROGRESS", "parseNowRev": REV, "parseNowReason": None, "parseNowAt": ANY}]
        harness.handler._release_queued_copies.assert_not_awaited()
        harness.handler._request_code_edge_build_if_repo_drained.assert_not_awaited()
        assert current_run() is None

    async def test_nothing_is_taken_out_of_the_vector_store_and_the_reindex_is_not_forced(self) -> None:
        harness = Harness(_record())

        await harness.run()

        harness.pipeline.bulk_delete_embeddings.assert_not_awaited()
        assert "forceReindex" not in harness.runs[0]["payload"]

    async def test_a_normal_reindex_runs_in_full_without_taking_the_embeddings_out_first(self) -> None:
        harness = Harness(_record(indexingStatus="QUEUED"))

        await harness.run(_payload(parseNow=False, forceReindex=True))

        harness.pipeline.bulk_delete_embeddings.assert_not_awaited()
        assert harness.runs[0]["run"] is None

    async def test_the_event_is_completed_from_the_record(self) -> None:
        harness = Harness(_record())

        await harness.run()

        sent = harness.runs[0]["payload"]
        assert sent["recordName"] == "deck.pptx" and sent["origin"] == "CONNECTOR" and sent["extension"] == "pptx"

    async def test_an_octet_stream_file_is_judged_by_the_extension_on_its_files_document(self) -> None:
        harness = Harness(
            _record(mimeType="application/octet-stream", recordName="Report.pdf"),
            file=_file(extension="pdf", name="Report.pdf"),
        )

        with patch.object(record_module.file_types, "gate_decision", wraps=record_module.file_types.gate_decision) as gate:
            await harness.run(_payload(mimeType="application/octet-stream"))

        assert gate.call_args.kwargs["extension"] == "pdf"
        assert harness.runs[0]["payload"]["extension"] == "pdf"

    async def test_a_files_document_that_names_another_type_decides_before_the_download(self) -> None:
        # FILES `zip`, name `Report.pdf`: refused as its sync event is, not accepted by the name and downloaded.
        harness = Harness(
            _record(mimeType="application/zip", recordName="Report.pdf"), file=_file(extension="zip", name="Report.pdf")
        )

        events = await harness.run(_payload(mimeType="application/zip"))

        assert events == DRAINED
        harness.download.assert_not_awaited()
        assert harness.runs == []
        assert [w["parseNowStatus"] for w in harness.writes()] == ["IN_PROGRESS", "FILE_TYPE_NOT_SUPPORTED"]
        assert ".zip" in harness.writes()[-1]["parseNowReason"]

    @pytest.mark.parametrize("file", [{}, _file(extension=None), _file(extension="")], ids=["no-files-doc", "none", "empty"])
    async def test_a_file_with_no_extension_on_its_files_document_is_sent_as_before(self, file: dict[str, Any]) -> None:
        harness = Harness(_record(), file=file)

        await harness.run()

        assert harness.runs[0]["payload"].get("extension", "unknown") == "unknown"

    async def test_a_files_document_the_graph_cannot_read_fails_the_event_instead_of_dropping_the_extension(self) -> None:
        harness = Harness(_record())
        harness.files_error = ConnectionError("graph down")

        with pytest.raises(ConnectionError):
            await harness.run()

        assert harness.runs == []
        harness.download.assert_not_awaited()
        files_reads = [c for c in harness.graph.get_document.await_args_list if c.args[1] == FILES]
        assert [c.kwargs.get("raise_on_error") for c in files_reads] == [True]

    async def test_a_code_file_is_judged_by_its_name_without_reading_files(self) -> None:
        harness = Harness(_record(recordType="CODE_FILE", recordName="main.py", mimeType="text/plain"))

        await harness.run(_payload(mimeType="text/plain"))

        assert all(c.args[1] != FILES for c in harness.graph.get_document.await_args_list)

    async def test_an_extension_the_event_carries_is_kept(self) -> None:
        harness = Harness(_record(), file=_file(extension="ppt"))

        await harness.run(_payload(extension="pptx"))

        assert harness.runs[0]["payload"]["extension"] == "pptx"
        assert all(c.args[1] != FILES for c in harness.graph.get_document.await_args_list)

    @pytest.mark.parametrize("mime", [None, "", "unknown"])
    async def test_an_event_without_a_mime_is_read_with_the_stored_one(self, mime: str | None) -> None:
        harness = Harness(_record())

        await harness.run(_payload(mimeType=mime))

        assert harness.runs[0]["payload"]["mimeType"] == PPTX

    async def test_an_unsupported_type_is_marked_and_its_status_is_left_alone(self) -> None:
        harness = Harness(
            _record(mimeType="application/x-unknown", recordName="data.xyz"), file=_file(extension="xyz", name="data.xyz")
        )

        events = await harness.run(_payload(mimeType="application/x-unknown"))

        assert events == DRAINED
        harness.download.assert_not_awaited()
        assert [w["parseNowStatus"] for w in harness.writes()] == ["IN_PROGRESS", "FILE_TYPE_NOT_SUPPORTED"]
        assert harness.writes()[-1]["parseNowReason"]
        assert all("indexingStatus" not in write for write in harness.writes())

    async def test_a_record_that_will_be_indexed_anyway_is_not_store_only(self) -> None:
        # Any other status runs the normal pipeline, which also marks the record once stored.
        harness = Harness(_record(indexingStatus="QUEUED"))

        await harness.run()

        assert harness.runs[0]["store_only"] is False
        assert harness.runs[0]["run"] is not None
        harness.pipeline.bulk_delete_embeddings.assert_not_awaited()
        assert harness.writes()[0]["parseNowStatus"] == "IN_PROGRESS"

    async def test_a_source_rate_limit_is_an_ordinary_retry_not_a_deferral(self) -> None:
        # L14: a deferral of hours is no answer to a reader.
        harness = Harness(_record())
        harness.handler._defer_rate_limited_record = AsyncMock(return_value=True)

        with pytest.raises(Exception, match="429"):
            await harness.run(download_error=RuntimeError("429"))

        harness.handler._defer_rate_limited_record.assert_not_awaited()


@pytest.mark.asyncio
class TestFailuresEndInTheMarker:
    async def _fail(self, error: Exception, *, final: bool, status: str = "AUTO_INDEX_OFF") -> Harness:
        harness = Harness(_record(indexingStatus=status))
        harness.fail_with = error
        with pytest.raises(Exception):  # noqa: B017 (an unknown error is re-raised as a plain Exception)
            await harness.run(_payload(is_final_failure=final))
        return harness

    async def test_a_transient_failure_puts_the_marker_back_in_line_and_leaves_the_status(self) -> None:
        # P12
        harness = await self._fail(RuntimeError("parser busy"), final=False)

        assert harness.writes()[-1] == {
            "parseNowStatus": "QUEUED", "parseNowRev": REV, "parseNowReason": RETRY_SCHEDULED, "parseNowAt": ANY,
        }
        assert all("indexingStatus" not in write for write in harness.writes())

    async def test_exhausted_retries_end_failed_and_the_status_is_left_alone(self) -> None:
        harness = await self._fail(RuntimeError("parser down"), final=True)

        final = harness.writes()[-1]
        assert final["parseNowStatus"] == "FAILED" and final["parseNowRev"] == REV and final["parseNowReason"]
        assert all("indexingStatus" not in write and "extractionStatus" not in write for write in harness.writes())
        harness.handler._trigger_next_queued_duplicate.assert_not_awaited()
        harness.handler._propagate_primary_failure_to_queued_duplicates.assert_not_awaited()

    @pytest.mark.parametrize(("final", "marker"), [(True, "FAILED"), (False, "QUEUED")])
    async def test_a_failure_before_the_run_begins_still_leaves_a_store_only_status(self, final: bool, marker: str) -> None:
        harness = Harness(_record())
        with (
            patch.object(harness.handler, "_parse_now_refusal", AsyncMock(side_effect=RuntimeError("graph read failed"))),
            pytest.raises(Exception),  # noqa: B017
        ):
            await harness.run(_payload(is_final_failure=final))

        assert harness.writes()[-1]["parseNowStatus"] == marker
        assert all("indexingStatus" not in w and "extractionStatus" not in w for w in harness.writes() if "parseNowStatus" in w)
        assert harness.runs == []  # the pipeline was never reached

    # (status, final) -> (parseNowStatus written last, or None for no write; the record's status written, or None)
    _UNANSWERED_CHECK = {
        ("AUTO_INDEX_OFF", False): ("QUEUED", None),
        ("AUTO_INDEX_OFF", True): ("FAILED", None),
        ("QUEUED", False): (None, None),
        ("QUEUED", True): ("FAILED", "FAILED"),
    }

    @pytest.mark.parametrize(("status", "final"), _UNANSWERED_CHECK, ids=["store_only-retry", "store_only-final", "indexed-retry", "indexed-final"])
    async def test_an_access_check_the_graph_could_not_answer_fails_the_event_not_the_reader(
        self, status: str, final: bool,
    ) -> None:
        """Not "no access" (which writes nothing): the event fails as any failure before the run does, with nothing parsed.

        On the final attempt a record that is indexed as usual is marked FAILED with a reason, as for any failure before
        the run; a store-only record keeps its status and only its marker ends.
        """
        marker, record_status = self._UNANSWERED_CHECK[(status, final)]
        harness = Harness(_record(indexingStatus=status))
        harness.access.fail_with(ConnectionError("graph down"))
        with pytest.raises(ConnectionError):
            await harness.run(_payload(is_final_failure=final))

        harness.download.assert_not_awaited()
        assert harness.runs == []
        writes = harness.writes()
        assert (writes[-1]["parseNowStatus"] if writes else None) == marker
        status_writes = [(w["indexingStatus"], w["extractionStatus"], bool(w["reason"])) for w in writes if "indexingStatus" in w]
        assert status_writes == ([(record_status, record_status, True)] if record_status else [])

    async def test_a_password_protected_file_ends_at_once_without_a_retry(self) -> None:
        harness = await self._fail(PasswordProtectedError("needs a password"), final=False)

        assert harness.writes()[-1]["parseNowStatus"] == "PASSWORD_PROTECTED"

    async def test_an_indexing_run_that_fails_marks_the_record_and_its_status(self) -> None:
        harness = await self._fail(RuntimeError("parser down"), final=True, status="QUEUED")

        final = harness.writes()[-1]
        assert final["indexingStatus"] == "FAILED" and final["parseNowStatus"] == "FAILED"

    async def test_an_abandoned_message_ends_the_marker_failed(self) -> None:
        # P10: MAX_UNFINISHED_ATTEMPTS, or retries run out, and the consumer gives the message up.
        harness = Harness(_record())
        message = StreamMessage(eventType=EventTypes.REINDEX_RECORD.value, payload=_payload())

        await harness.handler.on_message_abandoned(message, reason="2 earlier attempts never finished", attempts=2)

        assert harness.writes()[0]["parseNowStatus"] == "FAILED"
        harness.graph.compare_and_set_indexing_status.assert_not_awaited()  # AUTO_INDEX_OFF is left as it is

    async def test_an_abandoned_message_of_another_org_does_not_touch_the_record(self) -> None:
        harness = Harness(_record())
        message = StreamMessage(eventType=EventTypes.REINDEX_RECORD.value, payload=_payload(orgId="org-2"))

        await harness.handler.on_message_abandoned(message, reason="x", attempts=3)

        assert not any("parseNowStatus" in write for write in harness.writes())

    @pytest.mark.parametrize("marker", ["PASSWORD_PROTECTED", "FILE_TYPE_NOT_SUPPORTED", "EMPTY", "NEEDS_MULTIMODAL"])
    async def test_a_permanent_marker_is_not_replaced_by_the_abandon_failure(self, marker) -> None:
        harness = Harness(_record(parseNowStatus=marker, parseNowRev=REV))
        message = StreamMessage(eventType=EventTypes.REINDEX_RECORD.value, payload=_payload())

        await harness.handler.on_message_abandoned(message, reason="terminal error: PasswordProtectedError", attempts=1)

        assert not any("parseNowStatus" in write for write in harness.writes())

    async def test_a_stored_marker_is_not_replaced_by_a_late_failure(self) -> None:
        harness = Harness(_record(parseNowStatus="STORED", parseNowRev=REV))
        message = StreamMessage(eventType=EventTypes.REINDEX_RECORD.value, payload=_payload())

        await harness.handler.on_message_abandoned(message, reason="x", attempts=3)

        assert not any("parseNowStatus" in write for write in harness.writes())


class TestRecordWhoseParseWorkerWasKilled:
    """The first kill of a record's parse worker is retried; what the record shows must match what happens."""

    @staticmethod
    def _killed_first() -> DocumentProcessingError:
        killed = ParseWorkerError(PARSE_WORKER_KILLED_RETRYING, "crashed", retrying=True)
        try:
            raise DocumentProcessingError("Failed to process document") from killed
        except DocumentProcessingError as wrapped:
            return wrapped

    async def test_a_record_that_will_retry_shows_the_memory_pressure_reason(self) -> None:
        harness = Harness(_record(indexingStatus="IN_PROGRESS"))
        harness.fail_with = self._killed_first()

        with pytest.raises(DocumentProcessingError):
            await harness.run(_payload(is_final_failure=False, parseNow=False))

        final = harness.writes()[-1]
        assert PARSE_WORKER_KILLED_RETRYING in (final.get("reason"), final.get("parseNowReason"))

    async def test_a_record_that_is_failed_for_it_does_not_promise_a_retry(self) -> None:
        # The last attempt (or a process that has no retry tracker): nothing comes after it.
        harness = Harness(_record(indexingStatus="QUEUED"))
        harness.fail_with = self._killed_first()

        with pytest.raises(DocumentProcessingError):
            await harness.run(_payload(is_final_failure=True))

        final = harness.writes()[-1]
        assert final.get("indexingStatus") == "FAILED"
        reasons = [final.get("reason"), final.get("parseNowReason")]
        assert PARSE_WORKER_KILLED_RETRYING not in reasons
        assert PARSE_WORKER_OUT_OF_MEMORY in reasons

    async def test_a_failed_status_write_does_not_hide_the_retry_from_the_consumer(self) -> None:
        from app.services.messaging.error_classifier import (
            MessageErrorClassifier,
            MessageErrorType,
        )

        harness = Harness(_record(indexingStatus="IN_PROGRESS"))
        harness.fail_with = self._killed_first()
        write_failure = ConnectionError("graph down")

        async def failing_requeue_write(key: str, collection: str, updates: dict[str, Any]) -> bool:
            if "reason" in updates:
                raise write_failure
            return True

        harness.graph.update_node = AsyncMock(side_effect=failing_requeue_write)

        with pytest.raises(DocumentProcessingError) as raised:
            await harness.run(_payload(is_final_failure=False, parseNow=False))

        # The consumer reads explicit causes: the worker death must still be the cause, with the write as context.
        assert isinstance(raised.value.__cause__, ParseWorkerError)
        assert raised.value.__context__ is write_failure
        assert MessageErrorClassifier.classify_by_exception(raised.value) == MessageErrorType.TRANSIENT


@pytest.mark.parametrize("stored", ["application/pdf", "application/octet-stream"])
async def test_a_parse_now_event_gets_the_budget_its_client_sends(stored: str) -> None:
    """RECORDS and FILES documents as production stores them (the extension only on FILES) and the payload
    `parse_now_event` builds: the consumer's budget after the handler equals what the parsing client sends (F23g), and
    an octet-stream PDF gets the PDF budget on both sides from its FILES extension."""
    harness = Harness(_record(mimeType=stored, recordName="report.pdf"), file=_file(extension="pdf", name="report.pdf"))
    general = messaging_env.record_processing_timeout
    async with asyncio.timeout(general) as timeout, record_budget.armed(timeout, general) as budget:
        await harness.run(_payload(mimeType=stored))
    sent = harness.runs[0]["payload"]
    client = messaging_env.record_processing_timeout_for(sent.get("extension"), sent.get("mimeType"))
    assert budget.seconds == client == messaging_env.pdf_record_processing_timeout
