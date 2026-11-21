"""A file with nothing to index ends EMPTY, and an update that empties it leaves search.

JSON and YAML parsers raise ``ParseError(EMPTY_CONTENT)`` for whitespace or a YAML file of
comments only; it was wrapped as a DocumentProcessingError, so the record ended FAILED for good
while an empty text file ended EMPTY. A top-level ``{}`` or ``[]`` parsed to a group with no
blocks and was indexed.

Types reconciled in place (text, Markdown, HTML) keep their VRID on an update. Their EMPTY
exits wrote the status only, so the old vectors stayed in the store under a record marked
EMPTY. They are dropped first now, as the empty-bytes branch of ``on_event`` already did.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import EventTypes
from app.events.processor import Processor
from app.exceptions.indexing_exceptions import DocumentProcessingError, IndexingError
from app.modules.parsers.json.json_parser import JSONParser
from app.modules.parsers.yaml.yaml_parser import YAMLParser
from app.services.parsing.interface import ParseError, ParseErrorCode
from tests.unit.attempt_start import attempt_start_via
from tests.unit.events.test_orchestrator_flow import _make_event_processor


def _graph(name: str, ext: str, mime: str) -> MagicMock:
    doc = {
        "_key": "rec-1", "orgId": "org-1", "recordName": name, "recordType": "FILE",
        "indexingStatus": "COMPLETED", "mimeType": mime, "extension": ext, "virtualRecordId": "vr-1",
        "connectorName": None, "origin": "UPLOAD", "externalRecordId": "ext-1", "connectorId": "c1",
        "createdAtTimestamp": 1, "updatedAtTimestamp": 1, "md5Checksum": "old",
    }
    graph = MagicMock()
    graph.get_document = AsyncMock(return_value=dict(doc))
    graph.get_departments = AsyncMock(return_value=[])
    graph.batch_upsert_nodes = AsyncMock(return_value=True)
    graph.batch_update_nodes = AsyncMock(return_value=True)
    graph.update_node = AsyncMock(return_value=True)
    graph.write_attempt_start = attempt_start_via(graph)
    graph.find_duplicate_records = AsyncMock(return_value=[])
    graph.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])
    return graph


def _processor(graph: MagicMock) -> Processor:
    with patch("app.events.processor.DoclingClient"), patch("app.events.processor.DoclingProcessor"):
        proc = Processor(
            logger=logging.getLogger("test.empty"), config_service=MagicMock(), indexing_pipeline=AsyncMock(),
            graph_provider=graph, parsers={"json": JSONParser(), "yaml": YAMLParser()},
            document_extractor=MagicMock(), sink_orchestrator=MagicMock(),
        )
    blob = proc.sink_orchestrator.blob_storage
    blob.save_reconciliation_metadata = AsyncMock()
    blob.get_actual_content_path = AsyncMock(return_value="path/vr-1")
    blob.get_reconciliation_metadata = AsyncMock(return_value={"blocks": ["stored"]})
    return proc


def _statuses(graph: MagicMock) -> list[str]:
    return [
        c.args[2]["indexingStatus"] for c in graph.update_node.await_args_list
        if len(c.args) > 2 and isinstance(c.args[2], dict) and "indexingStatus" in c.args[2]
    ]


async def _on_event(name: str, ext: str, mime: str, buffer: bytes, *, event_type: str = EventTypes.UPDATE_RECORD.value,
                    holders: list[str] | None = None, delete_fails: bool = False) -> tuple[Processor, MagicMock]:
    graph = _graph(name, ext, mime)
    if holders is not None:
        graph.get_records_by_virtual_record_id = AsyncMock(return_value=holders)
    proc = _processor(graph)
    if delete_fails:
        proc.indexing_pipeline.delete_points_for_virtual_record = AsyncMock(side_effect=ConnectionError("vector store"))
    ep = _make_event_processor(graph_provider=graph, processor=proc)
    ep._spawn_abandoned_vrid_cleanup = MagicMock()
    event: dict[str, Any] = {"eventType": event_type, "payload": {
        "recordId": "rec-1", "orgId": "org-1", "virtualRecordId": "vr-1", "extension": ext, "mimeType": mime,
        "recordName": name, "buffer": buffer, "version": 2, "connectorName": "",
    }}
    with patch("app.events.events.keep_old_copy.swap_allowed", AsyncMock(return_value=False)):
        async for _ in ep.on_event(event):
            pass
    return proc, graph


EMPTY_STRUCTURED = [
    ("json", b"  \n"),
    ("json", b"{}"),
    ("json", b"[]"),
    ("yaml", b"  \n"),
    ("yaml", b"# only a comment\n"),
    ("yaml", b"---\n"),
    ("yaml", b"{}\n"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("ext", "content"), EMPTY_STRUCTURED)
async def test_a_structured_file_with_nothing_to_index_ends_empty(ext: str, content: bytes) -> None:
    graph = _graph(f"a.{ext}", ext, "application/json")
    proc = _processor(graph)
    proc._pipeline = MagicMock()

    events = [e.event.value async for e in proc.process_structured_document(
        recordName=f"a.{ext}", recordId="rec-1", file_content=content, virtual_record_id="vr-1", extension=ext,
    )]

    assert _statuses(graph) == ["EMPTY"]
    assert events == ["parsing_complete", "indexing_complete"]
    proc._pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_a_structured_file_with_one_value_is_still_indexed() -> None:
    graph = _graph("a.json", "json", "application/json")
    proc = _processor(graph)
    proc._pipeline = MagicMock(return_value=MagicMock(apply=AsyncMock()))

    async for _ in proc.process_structured_document(
        recordName="a.json", recordId="rec-1", file_content=b'{"a": 1}', virtual_record_id="vr-1", extension="json",
    ):
        pass

    proc._pipeline.return_value.apply.assert_awaited_once()
    assert "EMPTY" not in _statuses(graph)


@pytest.mark.asyncio
async def test_any_other_parse_error_still_fails_the_file() -> None:
    graph = _graph("a.json", "json", "application/json")
    proc = _processor(graph)
    proc.parsers["json"] = MagicMock(parse=AsyncMock(side_effect=ParseError(ParseErrorCode.PARSE_FAILED, "bad")))

    with pytest.raises(DocumentProcessingError):
        async for _ in proc.process_structured_document(
            recordName="a.json", recordId="rec-1", file_content=b"{", virtual_record_id="vr-1", extension="json",
        ):
            pass

    assert "EMPTY" not in _statuses(graph)


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "ext", "mime", "content"), [
    ("a.json", "json", "application/json", b"  \n"),
    ("a.yaml", "yaml", "application/x-yaml", b"# only a comment\n"),
])
async def test_an_update_emptying_a_structured_file_ends_empty_and_releases_its_old_vrid(
    name: str, ext: str, mime: str, content: bytes,
) -> None:
    proc, graph = await _on_event(name, ext, mime, content)

    assert _statuses(graph)[-1] == "EMPTY"
    # Not reconciled in place: the update moved to a new VRID and the old one was cleaned up before the parse.
    proc.indexing_pipeline.rewrite_or_delete_vector_membership.assert_awaited_once_with("vr-1")
    proc.indexing_pipeline.delete_points_for_virtual_record.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "ext", "mime"), [
    ("a.txt", "txt", "text/plain"),
    ("a.md", "md", "text/markdown"),
])
async def test_an_update_emptying_a_file_reconciled_in_place_drops_its_old_vectors(name: str, ext: str, mime: str) -> None:
    proc, graph = await _on_event(name, ext, mime, b"  \n\n ")

    proc.indexing_pipeline.delete_points_for_virtual_record.assert_awaited_once()
    assert proc.indexing_pipeline.delete_points_for_virtual_record.await_args.args[0] == "vr-1"
    # The VRID is kept, so its stored block list is emptied with the vectors.
    saved = proc.sink_orchestrator.blob_storage.save_reconciliation_metadata.await_args
    assert saved.args[2] == "vr-1" and not any(saved.args[3].values())
    assert _statuses(graph)[-1] == "EMPTY"


@pytest.mark.asyncio
async def test_a_new_empty_file_has_no_old_vectors_to_drop() -> None:
    proc, graph = await _on_event("a.txt", "txt", "text/plain", b"  \n", event_type=EventTypes.NEW_RECORD.value)

    proc.indexing_pipeline.delete_points_for_virtual_record.assert_not_awaited()
    assert _statuses(graph)[-1] == "EMPTY"


@pytest.mark.asyncio
async def test_vectors_another_record_still_holds_are_kept() -> None:
    proc, graph = await _on_event("a.txt", "txt", "text/plain", b"  \n", holders=["rec-1", "rec-2"])

    proc.indexing_pipeline.delete_points_for_virtual_record.assert_not_awaited()
    assert _statuses(graph)[-1] == "EMPTY"


@pytest.mark.asyncio
async def test_a_failed_vector_delete_retries_the_event_instead_of_ending_it() -> None:
    with pytest.raises(IndexingError) as raised:
        await _on_event("a.txt", "txt", "text/plain", b"  \n", delete_fails=True)

    # Not a DocumentProcessingError: that is final, and the old vectors would stay for good.
    assert not isinstance(raised.value, DocumentProcessingError)
