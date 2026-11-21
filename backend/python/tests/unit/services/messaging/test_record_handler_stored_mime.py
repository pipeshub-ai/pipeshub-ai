"""An event that names no mime is read with the record's stored mime by the gate and the dispatch alike (F23e).

The record handler resolves the mime once (the event's, else the stored record's, else ``unknown``) and hands that
answer to the dispatch on the payload, so the gate's verdict, the off switch and the reader are judged on one mime.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import file_types
from app.config.constants.arangodb import EventTypes
from app.events.events import DedupDecision, EventProcessor
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    StreamMessage,
    messaging_env,
)
from app.services.messaging.kafka.utils.utils import KafkaUtils
from app.services.resource_governor.models import ParseTier
from tests.unit.config.file_types import gen_pinned_decisions as gen

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.messaging.kafka.handlers.record import RecordEventHandler

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
OCTET = "application/octet-stream"
ZIP = "application/zip"
ABSENT = object()  # the event (or the record) has no "mimeType" key
_PLACEHOLDERS = (ABSENT, None, "", "unknown")
_MODULE = "app.services.messaging.kafka.handlers.record"


def _record(name: str, stored: object, record_type: str = "FILE") -> dict[str, Any]:
    record = {
        "_key": "r1", "orgId": "org-1", "virtualRecordId": "vr1", "recordType": record_type, "recordName": name,
        "connectorId": "conn-1", "indexingStatus": "QUEUED",
    }
    if stored is not ABSENT:
        record["mimeType"] = stored
    return record


def _payload(name: str, ext: str | None, mime: object, *, signed_url: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "recordId": "r1", "orgId": "org-1", "virtualRecordId": "vr1", "connectorId": "conn-1",
        "recordName": name, "extension": ext,
        **({"signedUrl": "https://example.invalid/f"} if signed_url else {}),
    }
    if mime is not ABSENT:
        payload["mimeType"] = mime
    return payload


def _handler(record: dict[str, Any], disabled: tuple[str, ...] = ()) -> RecordEventHandler:
    handler = gen._gate_handler(record)

    async def config(key: str, *_a: object, **_k: object) -> object:
        if key == file_types.FILE_TYPES_CONFIG_KEY:
            return {"disabled": list(disabled)}
        return {"connectors": {"endpoint": "http://connector.invalid"}}

    handler.config_service.get_config = AsyncMock(side_effect=config)
    return handler


def _downloads(handler: RecordEventHandler) -> Any:  # noqa: ANN401
    """Both download paths answer with the same fixed bytes."""
    return (
        patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock, return_value=b"%PDF-1.7 bytes"),
        patch(f"{_MODULE}.generate_jwt", new_callable=AsyncMock, return_value="token"),
        patch(f"{_MODULE}.make_api_call", new_callable=AsyncMock, return_value={"data": b"%PDF-1.7 bytes"}),
    )


class _Judged:
    """Wraps the gate to record the mime it judged."""

    def __init__(self) -> None:
        self.mimes: list[object] = []
        self._real = file_types.gate_decision

    def __call__(self, **kw: Any) -> file_types.GateDecision:  # noqa: ANN401
        self.mimes.append(kw["mime"])
        return self._real(**kw)


async def _envelope(record: dict[str, Any], payload: dict[str, Any]) -> tuple[dict[str, Any], _Judged]:
    """The envelope the handler hands the dispatch (a stub), and the mime its gate judged."""
    handler = _handler(record)
    envelopes: list[dict[str, Any]] = []

    def on_event(envelope: dict[str, Any]) -> AsyncIterator[PipelineEvent]:
        envelopes.append(envelope)

        async def events() -> AsyncIterator[PipelineEvent]:
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        return events()

    handler.event_processor.on_event = MagicMock(side_effect=on_event)
    judged = _Judged()
    signed, jwt, stream = _downloads(handler)
    with signed, jwt, stream, patch.object(file_types, "gate_decision", side_effect=judged):
        async for _ in handler.process_event("newRecord", payload):
            pass
    assert len(envelopes) == 1, "the gate accepts every case of this matrix"
    return envelopes[0], judged


def _expected(event_mime: object, stored: object) -> object:
    if event_mime not in _PLACEHOLDERS:
        return event_mime
    return (None if stored is ABSENT else stored) or "unknown"


_EVENT_MIMES = [*_PLACEHOLDERS, PDF, OCTET, DOCX]
_STORED = [ABSENT, None, "", "unknown", PDF]


def _id(value: object) -> str:
    return "absent" if value is ABSENT else repr(value)


# T1 + T2: every mime source, both download paths, both record types.
@pytest.mark.parametrize("record_type", ["FILE", "CODE_FILE"])
@pytest.mark.parametrize("signed_url", [True, False], ids=["signed-url", "connector-stream"])
@pytest.mark.parametrize("stored", _STORED, ids=_id)
@pytest.mark.parametrize("event_mime", _EVENT_MIMES, ids=_id)
async def test_the_dispatch_reads_the_mime_the_gate_judged(
    event_mime: object, stored: object, signed_url: bool, record_type: str,
) -> None:
    name = "Report.pdf"  # an extension, so a CODE_FILE is accepted by its name too
    envelope, judged = await _envelope(
        _record(name, stored, record_type), _payload(name, "pdf", event_mime, signed_url=signed_url)
    )
    assert envelope["payload"]["mimeType"] == judged.mimes[-1]
    assert envelope["payload"]["mimeType"] == _expected(event_mime, stored)


async def _read(
    *, name: str, ext: str | None, event_mime: object, stored: object, disabled: tuple[str, ...] = (),
    service: bool = False,
) -> dict[str, Any]:
    """One record end to end: the real handler hands its envelope to a real ``on_event``, parsers stubbed."""
    record = _record(name, stored)
    handler = _handler(record, disabled)
    processor = gen._processor()
    graph = AsyncMock()
    graph.update_node = AsyncMock(return_value=True)
    graph.get_document.return_value = dict(record)
    ep = EventProcessor(gen._Silent(), processor, graph, MagicMock())
    ep.parsing_client = MagicMock(circuit_open=False)
    handler.event_processor.on_event = MagicMock(side_effect=ep.on_event)
    sent: list[dict[str, Any]] = []

    async def pdf(*_a: object, **_k: object) -> AsyncIterator[object]:
        processor.routed.append("_dispatch_pdf_binary")
        return
        yield

    async def services(**kw: Any) -> AsyncIterator[PipelineEvent]:  # noqa: ANN401
        sent.append(kw)
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

    outcome: dict[str, Any] = {"downloaded": False, "error": None}
    signed, jwt, stream = _downloads(handler)
    with gen._quiet_env(), signed, jwt, stream, \
         patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
                      return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
         patch.object(ep, "_dispatch_pdf_binary", side_effect=pdf), \
         patch.object(ep, "_use_service_pipeline", return_value=service), \
         patch.object(ep, "_orchestrate_via_services", side_effect=services):
        try:
            async for event in handler.process_event("newRecord", _payload(name, ext, event_mime)):
                if event.event == IndexingEvent.START_DOWNLOAD:
                    outcome["downloaded"] = True
                elif event.event == IndexingEvent.START_PARSING:
                    outcome["tier"] = event.data.tier
        except Exception as exc:  # the handler re-raises what the dispatch raised
            outcome["error"] = type(exc).__name__
    outcome.update(reader=processor.routed[0] if processor.routed else None, status=record.get("indexingStatus"),
                   service=sent[0] if sent else None)
    return outcome


_NO_MIME = [pytest.param(m, id=_id(m)) for m in _PLACEHOLDERS]


# T3: accepted as a PDF and read as one, never downloaded and then failed as unreadable.
@pytest.mark.parametrize("event_mime", _NO_MIME)
async def test_a_stored_pdf_sent_without_a_mime_is_read_as_a_pdf(event_mime: object) -> None:
    out = await _read(name="Report", ext="unknown", event_mime=event_mime, stored=PDF)
    assert out["downloaded"]
    assert out["error"] is None and out["reader"] == "_dispatch_pdf_binary", out
    assert out["status"] != "FAILED"


# T4: the name does not overrule the stored mime the gate judged.
@pytest.mark.parametrize("event_mime", _NO_MIME)
async def test_its_name_does_not_send_a_stored_pdf_to_the_text_reader(event_mime: object) -> None:
    out = await _read(name="Report.txt", ext="unknown", event_mime=event_mime, stored=PDF)
    assert out["reader"] == "_dispatch_pdf_binary", out


# T5: the off switch names the type that reads the file.
async def test_turning_off_the_reader_refuses_the_record_before_its_download() -> None:
    out = await _read(name="Report.docx", ext="docx", event_mime=ABSENT, stored=PDF, disabled=("pdf",))
    assert not out["downloaded"] and out["status"] == "FILE_TYPE_NOT_SUPPORTED"


async def test_turning_off_another_type_leaves_the_record_to_its_reader() -> None:
    out = await _read(name="Report.docx", ext="docx", event_mime=ABSENT, stored=PDF, disabled=("docx",))
    assert out["downloaded"] and out["reader"] == "_dispatch_pdf_binary", out


# T6: controls.
async def test_the_event_mime_wins_over_the_stored_one() -> None:
    out = await _read(name="Report", ext="unknown", event_mime=DOCX, stored=PDF)
    assert out["reader"] == "process_docx_document", out


async def test_a_record_with_no_mime_anywhere_is_refused_before_its_download() -> None:
    out = await _read(name="Report", ext="unknown", event_mime=ABSENT, stored=ABSENT)
    assert not out["downloaded"] and out["status"] == "FILE_TYPE_NOT_SUPPORTED"


# T7: a stored mime with no in-process reader ends as the same event carrying it does (F23c limitation 2).
async def test_a_stored_mime_without_a_reader_ends_as_when_the_event_carries_it() -> None:
    without = await _read(name="Report.docx", ext="unknown", event_mime=ABSENT, stored=ZIP)
    carried = await _read(name="Report.docx", ext="unknown", event_mime=ZIP, stored=ZIP)
    assert without["downloaded"] and without["error"] == "UnsupportedFormatError" and without["status"] == "FAILED"
    assert {k: without[k] for k in ("error", "reader", "status")} == {k: carried[k] for k in ("error", "reader", "status")}


# MED-2: the parsing service is told the stored mime. Its budget is then the PDF one, while the consumer sized the
# record's own budget from the event's labels before the handler ran (known limitation, follow-up F23g).
async def test_the_parsing_service_is_sent_the_stored_mime() -> None:
    out = await _read(name="Report", ext="unknown", event_mime=ABSENT, stored=PDF, service=True)
    assert out["service"] is not None and out["service"]["mime_type"] == PDF, out
    service_budget = messaging_env.record_processing_timeout_for("unknown", out["service"]["mime_type"])
    consumer_budget = messaging_env.record_processing_timeout_for("unknown", None)
    assert service_budget == messaging_env.record_processing_timeout_for("pdf", PDF)
    assert consumer_budget == messaging_env.record_processing_timeout_for("unknown", "unknown")


# F23g (was test_the_parse_tier_still_follows_the_event_extension, which pinned LIGHT): the parse tier follows a
# heavy reader, so an event extension `txt` on a stored PDF is parsed by the PDF reader under the heavy tier, as when the
# event carries both labels.
@pytest.mark.parametrize("event_mime", [ABSENT, PDF], ids=["no-mime", "carried"])
async def test_the_parse_tier_follows_a_heavy_reader(event_mime: object) -> None:
    out = await _read(name="Report.txt", ext="txt", event_mime=event_mime, stored=PDF)
    assert out["reader"] == "_dispatch_pdf_binary"
    assert out["tier"] == ParseTier.HEAVY


@pytest.mark.parametrize("mime", [
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.presentation",
    "application/vnd.google-apps.spreadsheet",
])
async def test_a_google_export_labelled_txt_is_parsed_under_the_heavy_tier(mime: str) -> None:
    out = await _read(name="Notes.txt", ext="txt", event_mime=mime, stored=mime)
    assert out["reader"] is not None and out["tier"] == ParseTier.HEAVY, out


@pytest.mark.parametrize(("service", "tier"), [(False, ParseTier.LIGHT), (True, ParseTier.HEAVY)], ids=["in-process", "service"])
async def test_the_tier_follows_the_reader_of_the_pipeline_that_parses(service: bool, tier: ParseTier) -> None:
    """`csv` + image/png: the in-process dispatch reads it as CSV (light); the parsing service resolves png (heavy)."""
    out = await _read(name="Chart.csv", ext="csv", event_mime="image/png", stored="image/png", service=service)
    assert out["tier"] == tier, out


async def test_a_light_reader_keeps_the_light_tier() -> None:
    out = await _read(name="Notes.md", ext="md", event_mime="text/markdown", stored="text/markdown")
    assert out["tier"] == ParseTier.LIGHT, out


# LOW-2: the broker's message is not changed; a redelivery resolves the mime again from the record.
async def test_the_broker_message_keeps_its_own_payload() -> None:
    handler = _handler(_record("Report", PDF))
    envelopes: list[dict[str, Any]] = []

    def on_event(envelope: dict[str, Any]) -> AsyncIterator[PipelineEvent]:
        envelopes.append(envelope)

        async def events() -> AsyncIterator[PipelineEvent]:
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        return events()

    handler.event_processor.on_event = MagicMock(side_effect=on_event)
    message = StreamMessage(eventType=EventTypes.NEW_RECORD.value, payload=_payload("Report", "unknown", ABSENT))
    handle = await KafkaUtils.create_record_message_handler(MagicMock(), record_event_service=handler)
    signed, jwt, stream = _downloads(handler)
    with signed, jwt, stream:
        async for _ in handle(message):
            pass
    assert "mimeType" not in message.payload
    assert envelopes[0]["payload"]["mimeType"] == PDF
