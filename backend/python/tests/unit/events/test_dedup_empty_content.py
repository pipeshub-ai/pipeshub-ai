"""Content with nothing in it gets no dedup hash, so blank pages are not attached to each other.

A blank Notion page streams as ``{"block_groups": [], "blocks": []}``. The normaliser fell back to the raw bytes when
no block carried data, so every blank page in an org had the same MD5 and dedup attached each new one to the virtual
record of the first: hundreds of unrelated pages under one virtual record, and a delete of any of them re-read all.
HTML with no text did the same (hashed by its markup).

Driven through the real ``EventProcessor`` dedup methods and ``on_event`` over an in-memory records store.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames, EventTypes, ProgressStatus
from app.events.events import EventProcessor
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
)
from tests.unit.attempt_start import attempt_start_via
from tests.unit.events.test_orchestrator_flow import _make_event_processor

BLOCKS_MIME = "application/blocks"
EMPTY_PAGE_MD5 = "851d2cf9e7a7deeca06368cc819aeed2"  # what every blank page hashed to before the fix
ORG = "org-1"


def _streamed(container: dict[str, Any]) -> bytes:
    """A blocks page as the Notion connector streams it (``model_dump_json(indent=2)``)."""
    return json.dumps(container, indent=2).encode("utf-8")


def _as_dedup_reads_a_dict(container: dict[str, Any]) -> bytes:
    """A blocks page that reached dedup already parsed: ``_check_duplicate_by_md5`` dumps it like this."""
    return json.dumps(container, sort_keys=True, ensure_ascii=False).encode("utf-8")


EMPTY_PAGE = {"block_groups": [], "blocks": []}
CONTENT_PAGE = {
    "block_groups": [{"index": 0, "data": {"table_summary": "Q3 numbers"}}],
    "blocks": [{"index": 0, "type": "text", "data": "Launch checklist"}, {"index": 1, "type": "text", "data": ""}],
}


@pytest.fixture
def processor() -> EventProcessor:
    return EventProcessor(logging.getLogger("test.dedup_empty"), MagicMock(), MagicMock(), MagicMock())


@pytest.mark.parametrize("content", [
    _streamed(EMPTY_PAGE),
    _as_dedup_reads_a_dict(EMPTY_PAGE),
    _streamed({"blocks": []}),
    _streamed({"block_groups": []}),
    _streamed({"block_groups": [{"index": 0, "data": None}], "blocks": [{"index": 0, "data": None}]}),
    _streamed({"blocks": [{"index": 0, "data": ""}, {"index": 1, "data": "  \n\t"}]}),
    _streamed({"blocks": [{"index": 0, "data": {}}, {"index": 1, "data": []}]}),
    _streamed({"blocks": [{"index": 0}]}),
], ids=["streamed", "dict", "no-groups", "no-blocks", "none-data", "blank-lines", "empty-containers", "no-data-key"])
def test_a_page_with_no_block_data_has_no_hash(processor: EventProcessor, content: bytes) -> None:
    assert processor._hash_for_dedup(content, "WEBPAGE", BLOCKS_MIME) is None


@pytest.mark.parametrize("html", [
    b"<html><body></body></html>",
    b"<p>  </p>\n<div>\t</div>",
    b"<script>track()</script><style>p{}</style><noscript>x</noscript>",
    b"   ",
])
def test_html_with_no_text_has_no_hash(processor: EventProcessor, html: bytes) -> None:
    assert processor._hash_for_dedup(html, "CONFLUENCE_PAGE", "text/html") is None


@pytest.mark.parametrize(("content", "record_type", "mime_type", "golden"), [
    (_streamed(CONTENT_PAGE), "WEBPAGE", BLOCKS_MIME, "99504300c9562adfcbf8a9be535b19bd"),
    (_as_dedup_reads_a_dict(CONTENT_PAGE), "WEBPAGE", BLOCKS_MIME, "99504300c9562adfcbf8a9be535b19bd"),
    (
        b"<html><body><h1 id='x'>Release notes</h1><p>Fixed the export.</p><script>x()</script></body></html>",
        "CONFLUENCE_PAGE", "text/html", "0a54f698e07baa31700dc02776261c55",
    ),
    (b"plain text body", "FILE", "text/plain", "fb5f99619efffccf263701d62484b87c"),
    (b'{"b": 1, "a": 2}', "FILE", "application/json", "5e4d14d82c320bafb2f1286fe486d1f8"),
], ids=["blocks-streamed", "blocks-dict", "html", "plain", "json"])
def test_content_hashes_as_it_did_before(
    processor: EventProcessor, content: bytes, record_type: str, mime_type: str, golden: str,
) -> None:
    """Golden values from main before the change: a changed hash would split every existing duplicate group on its next index."""
    assert processor._hash_for_dedup(content, record_type, mime_type) == golden


def _dedup_doc(**fields: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"_key": "rec-1", "orgId": ORG, "recordType": "WEBPAGE", "mimeType": BLOCKS_MIME, **fields}


BLANK_CONTENT = pytest.mark.parametrize(
    "content", [_streamed(EMPTY_PAGE), EMPTY_PAGE, b"<p></p>"], ids=["bytes", "dict", "html"],
)


def _dedup_graph() -> MagicMock:
    graph = MagicMock()
    graph.find_duplicate_records = AsyncMock(return_value=[{"_key": "twin", "virtualRecordId": "vr-shared"}])
    graph.update_node = AsyncMock(return_value=True)
    return graph


def _mime_of(content: Any) -> str:  # noqa: ANN401
    return "text/html" if content == b"<p></p>" else BLOCKS_MIME


@pytest.mark.asyncio
@BLANK_CONTENT
async def test_dedup_of_blank_content_looks_for_no_twin_and_writes_nothing(content: Any) -> None:  # noqa: ANN401
    graph = _dedup_graph()
    ep = EventProcessor(logging.getLogger("test.dedup_empty"), MagicMock(), graph, MagicMock())

    decision = await ep._check_duplicate_by_md5(content, _dedup_doc(mimeType=_mime_of(content)))

    graph.find_duplicate_records.assert_not_awaited()
    graph.update_node.assert_not_awaited()
    assert decision.virtual_record_id is None
    assert decision.skip_indexing is False


@pytest.mark.asyncio
@BLANK_CONTENT
@pytest.mark.parametrize("stale", [EMPTY_PAGE_MD5, "md5-of-the-text-it-had"], ids=["shared-blank-hash", "old-text"])
async def test_dedup_of_blank_content_clears_the_checksum_the_record_still_holds(content: Any, stale: str) -> None:  # noqa: ANN401
    graph = _dedup_graph()
    ep = EventProcessor(logging.getLogger("test.dedup_empty"), MagicMock(), graph, MagicMock())

    decision = await ep._check_duplicate_by_md5(content, _dedup_doc(md5Checksum=stale, mimeType=_mime_of(content)))

    graph.find_duplicate_records.assert_not_awaited()
    assert [c.args[2] for c in graph.update_node.await_args_list] == [{"md5Checksum": None}]
    assert decision.virtual_record_id is None
    assert decision.skip_indexing is False


@pytest.mark.asyncio
async def test_a_same_bytes_skip_writes_no_checksum_for_text_less_html() -> None:
    graph = MagicMock()
    graph.update_node_fields_if_match = AsyncMock(return_value=True)
    ep = EventProcessor(logging.getLogger("test.dedup_empty"), MagicMock(), graph, MagicMock())
    record = {"recordType": "CONFLUENCE_PAGE", "mimeType": "text/html", "md5Checksum": "source-md5",
              "parsingStatus": ProgressStatus.COMPLETED.value}

    await ep._restore_indexed_fields(record, "rec-1", b"<p><img src='a.png'></p>", "rev-1")

    graph.update_node_fields_if_match.assert_not_awaited()


class Records:
    """The records collection as dedup and the vrid choice read it."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.find_duplicate_calls = 0

    def add(self, key: str, **fields: Any) -> None:  # noqa: ANN401
        self.rows[key] = {
            "_key": key, "orgId": ORG, "recordName": f"page {key}", "recordType": "WEBPAGE", "mimeType": BLOCKS_MIME,
            "indexingStatus": ProgressStatus.NOT_STARTED.value, "connectorName": "NOTION", "origin": "CONNECTOR",
            "externalRecordId": f"ext-{key}", "connectorId": "conn-1", "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
            **fields,
        }

    async def get_document(self, key: str, collection: str, **_: object) -> dict[str, Any] | None:
        row = self.rows.get(key) if collection == CollectionNames.RECORDS.value else None
        return dict(row) if row is not None else None

    async def update_node(self, key: str, collection: str, fields: dict[str, Any], **_: object) -> bool:
        if collection == CollectionNames.RECORDS.value and key in self.rows:
            self.rows[key].update(fields)
        return True

    async def find_duplicate_records(self, record_key: str, md5_checksum: str, org_id: str, **_: object) -> list[dict]:
        self.find_duplicate_calls += 1
        return [
            dict(r) for k, r in self.rows.items()
            if k != record_key and r.get("md5Checksum") == md5_checksum and r.get("orgId") == org_id
        ]

    async def update_node_fields_if_match(
        self, key: str, collection: str, fields: dict[str, Any], match: dict[str, Any], **_: object,
    ) -> bool:
        row = self.rows.get(key) if collection == CollectionNames.RECORDS.value else None
        if row is None or any(row.get(k) != v for k, v in match.items()):
            return False
        row.update(fields)
        return True

    async def batch_update_nodes(self, rows: list[dict[str, Any]], collection: str, **_: object) -> bool:
        if collection == CollectionNames.RECORDS.value:
            for row in rows:
                self.rows[row["id"]].update({k: v for k, v in row.items() if k != "id"})
        return True

    async def get_records_by_virtual_record_id(self, vrid: str, **_: object) -> list[str]:
        return [k for k, r in self.rows.items() if r.get("virtualRecordId") == vrid]

    def graph(self) -> MagicMock:
        graph = MagicMock()
        graph.get_document = AsyncMock(side_effect=self.get_document)
        graph.update_node = AsyncMock(side_effect=self.update_node)
        graph.find_duplicate_records = AsyncMock(side_effect=self.find_duplicate_records)
        graph.get_records_by_virtual_record_id = AsyncMock(side_effect=self.get_records_by_virtual_record_id)
        graph.get_departments = AsyncMock(return_value=[])
        graph.batch_upsert_nodes = AsyncMock(return_value=True)
        graph.batch_update_nodes = AsyncMock(side_effect=self.batch_update_nodes)
        graph.update_node_fields_if_match = AsyncMock(side_effect=self.update_node_fields_if_match)
        graph.copy_document_relationships = AsyncMock(return_value=True)
        graph.copy_named_entity_mentions = AsyncMock(return_value=0)
        graph.write_attempt_start = attempt_start_via(graph)
        return graph


async def _index(records: Records, key: str, content: bytes, event_type: str = EventTypes.NEW_RECORD.value) -> str:
    """Index one page through ``on_event``; the vrid its blocks are written under."""
    written: list[str] = []

    async def process_blocks(**kwargs: Any):  # noqa: ANN202, ANN401
        written.append(kwargs["virtual_record_id"])
        # Stands in for the parse: the vrid is persisted on the record as indexing does.
        records.rows[kwargs["recordId"]]["virtualRecordId"] = kwargs["virtual_record_id"]
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=kwargs["recordId"]))
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=kwargs["recordId"]))

    graph = records.graph()
    processor = MagicMock()
    processor.process_blocks = process_blocks
    ep = _make_event_processor(graph_provider=graph, processor=processor)
    ep._spawn_abandoned_vrid_cleanup = MagicMock()
    row = records.rows[key]
    event = {"eventType": event_type, "payload": {
        "recordId": key, "orgId": ORG, "virtualRecordId": row.get("virtualRecordId"), "mimeType": BLOCKS_MIME,
        "recordName": row["recordName"], "buffer": content, "version": 1, "connectorName": "NOTION",
    }}
    with patch("app.events.events.keep_old_copy.swap_allowed", AsyncMock(return_value=False)):
        async for _ in ep.on_event(event):
            pass
    assert len(written) == 1, f"{key} was not indexed itself"
    return written[0]


@pytest.mark.asyncio
async def test_two_new_blank_pages_are_indexed_under_their_own_virtual_records() -> None:
    records = Records()
    records.add("blank-1")
    records.add("blank-2")

    first = await _index(records, "blank-1", _streamed(EMPTY_PAGE))
    second = await _index(records, "blank-2", _streamed(EMPTY_PAGE))

    assert first != second
    assert records.find_duplicate_calls == 0
    assert "md5Checksum" not in records.rows["blank-1"]
    assert "md5Checksum" not in records.rows["blank-2"]


@pytest.mark.asyncio
async def test_a_new_blank_page_is_not_attached_to_the_old_shared_group() -> None:
    records = Records()
    records.add("old-blank", md5Checksum=EMPTY_PAGE_MD5, virtualRecordId="vr-shared",
                indexingStatus=ProgressStatus.EMPTY.value)
    records.add("blank-new")

    vrid = await _index(records, "blank-new", _streamed(EMPTY_PAGE))

    assert vrid != "vr-shared"


@pytest.mark.asyncio
async def test_a_blank_page_in_a_shared_group_moves_out_when_updated() -> None:
    records = Records()
    for key in ("blank-a", "blank-b", "blank-c"):
        records.add(key, md5Checksum=EMPTY_PAGE_MD5, virtualRecordId="vr-shared", indexingStatus=ProgressStatus.EMPTY.value)

    vrid = await _index(records, "blank-a", _streamed(EMPTY_PAGE), EventTypes.UPDATE_RECORD.value)

    assert vrid != "vr-shared"
    assert records.find_duplicate_calls == 0


@pytest.mark.asyncio
async def test_two_pages_with_the_same_content_still_share_one_virtual_record() -> None:
    records = Records()
    records.add("copy-1")
    records.add("copy-2")

    first = await _index(records, "copy-1", _streamed(CONTENT_PAGE))
    records.rows["copy-1"]["indexingStatus"] = ProgressStatus.COMPLETED.value
    records.rows["copy-1"]["extractionStatus"] = ProgressStatus.COMPLETED.value
    await _index_expecting_reuse(records, "copy-2", _streamed(CONTENT_PAGE))

    assert records.rows["copy-2"]["virtualRecordId"] == first
    assert records.rows["copy-2"]["md5Checksum"] == records.rows["copy-1"]["md5Checksum"]


async def _index_expecting_reuse(records: Records, key: str, content: bytes) -> None:
    """Run a page that dedup settles on its finished twin: nothing is parsed."""
    graph = records.graph()
    processor = MagicMock()
    processor.process_blocks = MagicMock(side_effect=AssertionError("a duplicate of a finished page was parsed"))
    ep = _make_event_processor(graph_provider=graph, processor=processor)
    row = records.rows[key]
    event = {"eventType": EventTypes.NEW_RECORD.value, "payload": {
        "recordId": key, "orgId": ORG, "virtualRecordId": None, "mimeType": BLOCKS_MIME,
        "recordName": row["recordName"], "buffer": content, "version": 1, "connectorName": "NOTION",
    }}
    with patch("app.events.events.keep_old_copy.swap_allowed", AsyncMock(return_value=False)):
        async for _ in ep.on_event(event):
            pass


@pytest.mark.asyncio
async def test_a_page_whose_text_was_removed_is_not_a_twin_for_that_text() -> None:
    """Text, then blank, then another page with the first text: that page is indexed itself, not given EMPTY."""
    records = Records()
    records.add("emptied")
    records.add("new-copy")
    first = await _index(records, "emptied", _streamed(CONTENT_PAGE))
    records.rows["emptied"].update(indexingStatus=ProgressStatus.COMPLETED.value, extractionStatus=ProgressStatus.COMPLETED.value)
    text_md5 = records.rows["emptied"]["md5Checksum"]

    await _index(records, "emptied", _streamed(EMPTY_PAGE), EventTypes.UPDATE_RECORD.value)
    records.rows["emptied"]["indexingStatus"] = ProgressStatus.EMPTY.value
    vrid = await _index(records, "new-copy", _streamed(CONTENT_PAGE))

    assert records.rows["emptied"].get("md5Checksum") is None
    assert records.rows["new-copy"]["md5Checksum"] == text_md5
    assert vrid != records.rows["emptied"]["virtualRecordId"]
    assert records.rows["new-copy"]["indexingStatus"] != ProgressStatus.EMPTY.value
    assert first  # the text was indexed under a vrid of its own first
