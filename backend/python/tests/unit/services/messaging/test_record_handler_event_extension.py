"""The record handler takes a FILE's extension from its FILES document when the event names none.

Stale recovery, the stranded-record sweep and the appEnabled re-queue rebuild a record's event from its RECORDS
document, which has no ``extension`` on any graph backend (it is on the FILES document), so they send ``None``. The
handler reads FILES for such an event, as for parse-now, before anything is purged and before the budget and the gate
use the extension. An event that carries one is used as is.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import EventTypes, ProgressStatus
from app.services.messaging import record_budget
from app.services.messaging.config import messaging_env
from app.services.messaging.kafka.handlers import record as record_module
from tests.unit.services.messaging.test_record_handler_parse_now import (
    FILES,
    Harness,
    _file,
    _record,
)
from tests.unit.test_indexing_main import (  # the autouse fixtures that module runs recovery under
    _make_container,
    _make_graph_provider,
    _no_fleet_redis,  # noqa: F401
    disable_distributed_concurrency_by_default,  # noqa: F401
)
from tests.unit.test_stranded_sweep_parked_while_off import (
    ON,
    FakeGraph,
    Producer,
    _parked,
    _resume,
    _sweep,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

OCTET = "application/octet-stream"
NEW, REINDEX = EventTypes.NEW_RECORD.value, EventTypes.REINDEX_RECORD.value


def _queued(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    """A RECORDS document waiting to be indexed, as a re-sent event finds it: no extension."""
    fields = {"mimeType": OCTET, "recordName": "Report.pdf", "indexingStatus": ProgressStatus.QUEUED.value,
              "virtualRecordId": None, "version": 0, **overrides}
    return _record(**fields)


def _event(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    """The payload recovery and `record_event` build from that RECORDS document."""
    payload = {"recordId": "rec-1", "orgId": "org-1", "connectorId": "conn-1", "connectorName": "SLACK",
               "recordName": "Report.pdf", "version": 0, "extension": None, "mimeType": OCTET, "origin": "CONNECTOR",
               "recordType": "FILE", "virtualRecordId": None}
    payload.update(overrides)
    return payload


def _pdf() -> dict[str, Any]:
    return _file(extension="pdf", name="Report.pdf")


def _refused(harness: Harness) -> bool:
    return any(w.get("indexingStatus") == ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value for w in harness.writes())


def _files_reads(harness: Harness) -> list[Any]:
    return [c for c in harness.graph.get_document.await_args_list if c.args[1] == FILES]


@pytest.mark.asyncio
class TestAnEventWithoutAnExtension:
    @pytest.mark.parametrize(
        ("event_type", "record", "payload"),
        [
            (NEW, _queued(), _event()),
            # A recovered record that was indexed before is re-sent as a reindex.
            (REINDEX, _queued(virtualRecordId="vr-1", version=3), _event(virtualRecordId="vr-1", version=3)),
            (NEW, _queued(), _event(extension="")),
        ],
        ids=["new-record", "reindex", "empty"],
    )
    async def test_an_octet_stream_file_is_read_by_its_files_extension(
        self, event_type: str, record: dict[str, Any], payload: dict[str, Any],
    ) -> None:
        harness = Harness(record, file=_pdf())

        with patch.object(record_module.file_types, "gate_decision", wraps=record_module.file_types.gate_decision) as gate:
            await harness.run(payload, event_type)

        assert gate.call_args.kwargs["extension"] == "pdf"
        assert not _refused(harness)
        assert [run["payload"]["extension"] for run in harness.runs] == ["pdf"]

    async def test_a_source_file_with_a_parse_only_mime_is_read(self) -> None:
        harness = Harness(_queued(mimeType="text/x-csrc", recordName="main.c"), file=_file(extension="c", name="main.c"))

        await harness.run(_event(mimeType="text/x-csrc", recordName="main.c"), NEW)

        assert not _refused(harness)
        assert [run["payload"]["extension"] for run in harness.runs] == ["c"]

    async def test_a_type_with_no_reader_is_refused_under_its_own_extension(self) -> None:
        harness = Harness(_queued(recordName="Report.zip"), file=_file(extension="zip", name="Report.zip"))

        await harness.run(_event(recordName="Report.zip"), NEW)

        assert _refused(harness)
        harness.download.assert_not_awaited()
        assert ".zip files" in harness.writes()[-1]["reason"]

    @pytest.mark.parametrize("file", [{}, _file(extension=None), _file(extension="")], ids=["no-files-doc", "none", "empty"])
    async def test_a_files_document_without_an_extension_leaves_the_event_as_it_was(self, file: dict[str, Any]) -> None:
        harness = Harness(_queued(), file=file)

        await harness.run(_event(), NEW)

        assert _refused(harness)  # as on main: an octet-stream file with no extension anywhere names no type
        assert "this type of file" in harness.writes()[-1]["reason"]

    async def test_the_budget_is_sized_from_the_files_extension(self) -> None:
        harness = Harness(_queued(), file=_pdf())
        general = messaging_env.record_processing_timeout

        async with asyncio.timeout(general) as timeout, record_budget.armed(timeout, general) as budget:
            await harness.run(_event(), NEW)

        assert budget.seconds == messaging_env.pdf_record_processing_timeout

    async def test_a_files_read_the_graph_cannot_answer_fails_the_event_before_anything_is_purged(self) -> None:
        harness = Harness(_queued(virtualRecordId="vr-1", version=3), file=_pdf())
        harness.files_error = ConnectionError("graph down")

        with (
            patch.object(record_module.paid_cache_access, "paid_cache_for", MagicMock()) as paid_cache_for,
            pytest.raises(ConnectionError),
        ):
            await harness.run(_event(virtualRecordId="vr-1", version=3, refreshPaidOutputs=True), REINDEX)

        paid_cache_for.assert_not_called()
        assert harness.runs == []
        harness.download.assert_not_awaited()
        assert not _refused(harness)
        assert [c.kwargs.get("raise_on_error") for c in _files_reads(harness)] == [True]

    async def test_an_extension_the_event_carries_is_used_without_reading_files(self) -> None:
        harness = Harness(_queued(), file=_file(extension="zip"))

        await harness.run(_event(extension="pdf"), NEW)

        assert _files_reads(harness) == []
        assert [run["payload"]["extension"] for run in harness.runs] == ["pdf"]

    async def test_a_code_file_is_judged_by_its_name_without_reading_files(self) -> None:
        harness = Harness(_queued(recordType="CODE_FILE", recordName="main.py", mimeType="text/plain"), file=_pdf())

        await harness.run(_event(recordType="CODE_FILE", recordName="main.py", mimeType="text/plain"), NEW)

        assert _files_reads(harness) == []


# --- Producer to handler: each re-send path's own payload, read as the record's sync event is ---------------------


class _FilesGraph(FakeGraph):
    """The stranded-sweep fake with a FILES collection of its own (FakeGraph answers it from RECORDS)."""

    def __init__(self) -> None:
        super().__init__()
        self.files: dict[str, dict[str, Any]] = {}

    async def get_document(
        self, key: str, collection: str, transaction: object = None, *, raise_on_error: bool = False,
    ) -> dict[str, Any] | None:
        if collection == FILES:
            return dict(self.files[key]) if key in self.files else None
        return await super().get_document(key, collection, transaction, raise_on_error=raise_on_error)


class _PayloadProducer(Producer):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def send_messages(self, topic: str, messages: list[tuple[str, dict[str, Any]]]) -> list[bool]:
        self.events += [(m["eventType"], m["payload"]) for _key, m in messages]
        return await super().send_messages(topic, messages)

    async def send_event(self, topic: str, event_type: str, payload: dict[str, Any], key: str | None = None) -> bool:
        self.events.append((event_type, payload))
        return await super().send_event(topic, event_type, payload, key)


def _parked_pdf() -> dict[str, Any]:
    doc = _parked("rec-1", recordName="Report.pdf", mimeType=OCTET)
    assert "extension" not in doc  # the RECORDS shape
    return doc


async def _recovered() -> tuple[str, dict[str, Any]]:
    from app.indexing_main import recover_in_progress_records

    container, gp = _make_container(), _make_graph_provider()
    gp.get_nodes_by_filters = AsyncMock(return_value=[{
        "_key": "rec-1", "recordName": "Report.pdf", "mimeType": OCTET, "recordType": "FILE", "orgId": "org-1",
        "indexingStatus": ProgressStatus.IN_PROGRESS.value, "processingStartedAt": int((time.time() - 86400) * 1000),
    }])
    await recover_in_progress_records(container, gp)
    send = container.kafka_consumers[0][2].send_event
    send.assert_awaited_once()
    return send.await_args.kwargs["event_type"], send.await_args.kwargs["payload"]


async def _swept() -> tuple[str, dict[str, Any]]:
    graph, producer = _FilesGraph(), _PayloadProducer()
    graph.records["rec-1"], graph.files["rec-1"] = _parked_pdf(), _pdf()
    assert await _sweep(graph, producer) == 1
    return producer.events[0]


async def _requeued() -> tuple[str, dict[str, Any]]:
    graph, producer = _FilesGraph(), _PayloadProducer()
    graph.records["rec-1"], graph.files["rec-1"] = _parked_pdf(), _pdf()
    assert await _resume(graph, producer)._requeue_records_parked_while_off(ON) == 1
    return producer.events[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("resend", [_recovered, _swept, _requeued], ids=["recovery", "stranded-sweep", "app-enabled"])
async def test_a_re_sent_octet_stream_pdf_is_read_as_its_sync_event_is(
    resend: Callable[[], Awaitable[tuple[str, dict[str, Any]]]],
) -> None:
    event_type, payload = await resend()
    assert payload["extension"] is None  # the producer sends what RECORDS has; the handler fills it
    harness = Harness(_queued(), file=_pdf())

    await harness.run({**payload, "recordId": "rec-1", "connectorId": "conn-1"}, event_type)

    assert not _refused(harness), harness.writes()
    assert [run["payload"]["extension"] for run in harness.runs] == ["pdf"]
