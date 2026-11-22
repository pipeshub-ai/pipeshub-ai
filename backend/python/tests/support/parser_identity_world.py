"""Real file records as the event payload and the graph hold them, for the parser identity tests."""
from __future__ import annotations

import itertools
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from app.config.constants.arangodb import Connectors, EventTypes, OriginTypes
from app.core import org_context
from app.events.dedup import DedupDecision
from app.events.events import EventProcessor
from app.models.entities import FileRecord, RecordType
from app.modules.pipeline import parser_identity as pi
from app.modules.pipeline.fingerprint import content_revision
from app.services.graph_db.conditions import EQ, IS_NULL, Cond
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
)
from tests.unit.attempt_start import attempt_start_via

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

#: The mime a connector sends for an extension, and the generic ones blob stores send for anything.
REAL_MIME = {
    "txt": "text/plain", "md": "text/markdown", "mdx": "text/markdown", "json": "application/json",
    "yaml": "application/x-yaml", "yml": "application/x-yaml", "html": "text/html", "htm": "text/html",
    "csv": "text/csv", "tsv": "text/tab-separated-values", "log": "text/plain", "cfg": "text/plain",
    "xml": "application/xml", "ini": "text/plain", "toml": "text/plain", "rst": "text/plain",
}


def file_record(name: str, extension: str | None, mime_type: str, *, record_id: str = "rec-1") -> FileRecord:
    return FileRecord(
        id=record_id, org_id="org-1", record_name=name, record_type=RecordType.FILE, external_record_id=f"ext-{record_id}",
        version=1, origin=OriginTypes.CONNECTOR, connector_name=Connectors.GOOGLE_DRIVE, connector_id="conn-1",
        is_file=True, extension=extension, mime_type=mime_type,
    )


def payload_of(record: FileRecord) -> dict[str, Any]:
    """The event payload: what ``to_kafka_record`` puts on the wire (the extension is there)."""
    return record.to_kafka_record()


def graph_of(record: FileRecord) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(records node, files document)`` as the graph stores them: the extension is on the files document only."""
    return record.to_arango_base_record(), record.to_arango_record()


def corpus() -> list[tuple[str, str | None, str]]:
    """``(name, extension, mime)``: every real mime, text/plain and octet-stream for each extension, named with and without it."""
    rows = []
    for ext, mime in REAL_MIME.items():
        for m, name in itertools.product((mime, "text/plain", "application/octet-stream"), (f"notes.{ext}", "notes")):
            rows.append((name, ext, m))
    return rows


# ----------------------------------------------------------------------------------------------------- an event world
BYTES = b"some text of a note\n"
REV = content_revision(BYTES)
VRID = "vr-1"
UPDATE = EventTypes.UPDATE_RECORD.value
T0 = 1_700_000_000_000


def holds(doc: dict[str, Any], conditions: list[Cond]) -> bool:
    """The conditions of ``update_node_fields_if``, as the stores evaluate them."""
    for c in conditions:
        value = doc.get(c.field)
        if (c.op == EQ and value != c.value) or (c.op == IS_NULL and value is not None):
            return False
    return True


class World:
    """One COMPLETED record (and its files document) in a dict-backed graph, a parser that counts its runs and writes what the sink writes."""

    def __init__(self, *, node: dict[str, Any] | None = None, file_doc: dict[str, Any] | None = None) -> None:
        self.state: dict[str, Any] = {
            "_key": "rec-1", "recordType": "FILE", "orgId": "org-1", "origin": "UPLOAD", "mimeType": "text/plain",
            "recordName": "notes.txt", "indexingStatus": "COMPLETED", "extractionStatus": "COMPLETED",
            "hasIndexedCopy": True, "virtualRecordId": VRID, "contentRev": REV, "lastIndexTimestamp": T0,
        }
        self.state.update(node or {})
        self.files: dict[str, Any] | None = {"_key": "rec-1", "extension": "txt"} if file_doc is None else file_doc
        self.parses = 0
        self.conditional_writes: list[dict[str, Any]] = []
        graph = AsyncMock()
        graph.get_document = AsyncMock(side_effect=self._get)
        graph.update_node = AsyncMock(side_effect=self._write)
        graph.write_attempt_start = attempt_start_via(graph)
        graph.update_node_fields_if_match = AsyncMock(side_effect=self._write_if_match)
        graph.update_node_fields_if = AsyncMock(side_effect=self._write_if)
        graph.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])
        self.graph = graph
        processor = MagicMock()
        processor.indexing_pipeline = AsyncMock()
        processor.indexing_pipeline.orgs_holding_points = AsyncMock(return_value={"org-1"})
        processor.sink_orchestrator.blob_storage.get_actual_content_path = AsyncMock(return_value="stored/path")
        processor.process_txt_document = MagicMock(side_effect=self._parse)
        processor.process_md_document = MagicMock(side_effect=self._parse)
        self.processor = processor
        self.ingress = MagicMock()
        self.ingress.handoff_lost = AsyncMock(return_value=False)
        self.ingress.redrive_unchanged = AsyncMock(return_value=["j1"])
        self.ingress.redrive = AsyncMock()
        self.ep = EventProcessor(logging.getLogger("test"), processor, graph, MagicMock(), stage_ingress=self.ingress)
        self.ep._check_duplicate_by_md5 = AsyncMock(return_value=DedupDecision())
        self.ep._spawn_abandoned_vrid_cleanup = MagicMock()
        self.ep._rewrite_or_delete_vrid_vectors = AsyncMock()

    async def _get(self, key: str, collection: str, **_kwargs: object) -> dict[str, Any] | None:
        if collection == "files":
            return None if self.files is None else dict(self.files)
        return dict(self.state)

    async def _write(self, _key: str, _collection: str, fields: dict[str, Any]) -> bool:
        self.state.update(fields)
        return True

    async def _write_if_match(self, _key: str, _collection: str, fields: dict[str, Any], match: dict[str, Any]) -> bool:
        if any(self.state.get(name) != value for name, value in match.items()):
            return False
        self.state.update(fields)
        return True

    async def _write_if(
        self, _key: str, _collection: str, fields: dict[str, Any], conditions: list[Cond], *_a: object, **_k: object,
    ) -> bool:
        if not holds(self.state, conditions):
            return False
        self.conditional_writes.append(dict(fields))
        self.state.update(fields)
        return True

    async def _parse(self, *_args: Any, **_kwargs: Any) -> AsyncIterator[PipelineEvent]:  # noqa: ANN401
        """The parse, then what the sink's COMPLETED write leaves behind: the identity of the run, taken from the event."""
        self.parses += 1
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="rec-1"))
        at = self.state["lastIndexTimestamp"] + 1000
        self.state.update({"indexingStatus": "COMPLETED", "hasIndexedCopy": True, "lastIndexTimestamp": at})
        self.state.update(pi.indexed_parser_fields(pi.run_identity(), pi.SOURCE_PARSED, at=at, vrid=self.state["virtualRecordId"]))
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="rec-1"))

    def event(
        self, buffer: bytes = BYTES, *, event_type: str = UPDATE, mime: str = "text/plain", ext: str = "txt",
        name: str = "notes.txt", **extra: Any,  # noqa: ANN401
    ) -> dict[str, Any]:
        payload = {
            "recordId": "rec-1", "orgId": "org-1", "virtualRecordId": VRID, "version": 1, "connectorName": "",
            "extension": ext, "mimeType": mime, "recordName": name, "buffer": buffer, **extra,
        }
        return {"eventType": event_type, "payload": payload}

    async def run(self, event: dict[str, Any]) -> list[IndexingEvent]:
        with org_context.for_org("org-1", job="test"):
            return [item.event async for item in self.ep.on_event(event)]

    def identified(self, identity: str, *, source: str = pi.SOURCE_PARSED) -> None:
        """The record's copy carries ``identity``, written with this copy and this completion."""
        self.state.update({
            pi.IDENTITY_FIELD: identity, pi.IDENTITY_AT_FIELD: self.state["lastIndexTimestamp"],
            pi.IDENTITY_VRID_FIELD: self.state["virtualRecordId"], pi.IDENTITY_SOURCE_FIELD: source,
        })

    def renamed_to_markdown(self) -> None:
        self.state.update({"mimeType": "text/markdown", "recordName": "notes.md"})
        self.files = {"_key": "rec-1", "extension": "md"}
