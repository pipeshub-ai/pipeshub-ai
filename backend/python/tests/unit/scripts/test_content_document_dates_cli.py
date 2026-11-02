"""The operator command that gives already-indexed uploads the dates their content states."""

from __future__ import annotations

import copy
import io
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from app.models.blocks import Block, BlocksContainer, BlockType
from app.models.source_dates import SourceTimestampOrigin
from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.extractor import (
    NamedEntityExtractor,
    NamedEntityRequest,
)
from app.modules.named_entities.kept import ENTITIES_STAGE
from app.modules.pipeline.models import stage_state_key
from app.scripts.content_document_dates import backfill_content_dates

if TYPE_CHECKING:
    from app.modules.named_entities.domain.models import NamedEntityExtraction

UPLOADED = 1792090099000  # 2026-10-15 18:48:19 UTC
EMAIL_DAY = int(datetime(2025, 2, 11, tzinfo=UTC).timestamp() * 1000)
UPLOAD_ONLY = frozenset({SourceTimestampOrigin.UPLOAD})


def _doc(key: str, **overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    doc = {
        "_key": key, "id": key, "orgId": "o1", "connectorId": "kb-1", "connectorName": "KB", "origin": "UPLOAD",
        "virtualRecordId": f"v-{key}", "indexingStatus": "COMPLETED",
        "createdAtTimestamp": UPLOADED, "sourceCreatedAtTimestamp": UPLOADED - 40,
        "sourceLastModifiedTimestamp": UPLOADED - 40,
    }
    doc.update(overrides)
    return doc


async def _extraction(*texts: str) -> tuple[BlocksContainer, NamedEntityExtraction]:
    blocks = [Block(index=i, type=BlockType.TEXT, data=t) for i, t in enumerate(texts)]
    kinds = frozenset({EntityKind.DATE, EntityKind.DATE_TIME, EntityKind.DATE_RANGE, EntityKind.DURATION})
    return BlocksContainer(blocks=blocks), await NamedEntityExtractor().extract(
        NamedEntityRequest(blocks=blocks, enabled=kinds),
    )


async def _blob(*texts: str) -> dict[str, Any]:
    blocks, extraction = await _extraction(*texts)
    # As BlobStorage writes it: unset fields are left out.
    return {
        "block_containers": blocks.model_dump(mode="json", exclude_none=True),
        "semantic_metadata": {"named_entities": extraction.model_dump(mode="json")},
    }


APPS = [
    {"_key": "kb-1", "orgId": "o1", "type": "KB"},
    {"_key": "drive-1", "orgId": "o1", "type": "DRIVE"},
    {"_key": "kb-9", "orgId": "o2", "type": "KB"},
]


class _Graph:
    def __init__(self, docs: list[dict[str, Any]], apps: list[dict[str, Any]] = APPS) -> None:
        self.docs = {doc["_key"]: copy.deepcopy(doc) for doc in docs}
        self.apps = {app["_key"]: app for app in apps}
        self.batches: list[list[tuple[str, dict, dict]]] = []
        self.record_filters: list[dict[str, Any]] = []
        self.stage_states: dict[str, dict[str, Any]] = {}

    async def stage_state_get(self, key: str) -> dict[str, Any] | None:
        return self.stage_states.get(key)

    async def get_documents_paginated(
        self, collection: str, *, limit: int, filters: dict, sort_field: str, raise_on_error: bool, after_key: str | None,
    ) -> list[dict[str, Any]]:
        assert (sort_field, raise_on_error) == ("_key", True)
        if collection == "records":
            self.record_filters.append(dict(filters))
        docs = self.docs if collection == "records" else self.apps
        rows = [
            copy.deepcopy(doc) for key, doc in sorted(docs.items())
            if (after_key is None or key > after_key) and all(doc.get(f) == v for f, v in filters.items())
        ]
        return rows[:limit]

    async def update_nodes_fields_if_match(self, collection: str, rows: list[tuple[str, dict, dict]]) -> list[str]:
        self.batches.append(rows)
        applied = []
        for key, updates, expected in rows:
            doc = self.docs[key]
            if all(doc.get(f) == v for f, v in expected.items()):
                doc.update(updates)
                applied.append(key)
        return applied


class _Blobs:
    def __init__(self, blobs: dict[str, dict[str, Any] | None]) -> None:
        self.blobs = blobs
        self.reads = 0

    async def get_record_from_storage(
        self, virtual_record_id: str, org_id: str, lookup_result: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        self.reads += 1
        if virtual_record_id not in self.blobs:
            raise RuntimeError("storage down")
        return self.blobs[virtual_record_id]


EMAIL = ("Re: redlines", "From: Elaine <e@trident.com>\nDate: Tue, Feb 11, 2025 at 8:04 AM\nSubject: redlines", "Body.")


async def _setup() -> tuple[_Graph, _Blobs]:
    graph = _Graph([
        _doc("a", sourceTimestampOrigin="upload"),
        _doc("b"),  # stored before origins were kept, says no date
        _doc("c"),  # its blob cannot be read
        _doc("d", isDeleted=True),
        _doc("e", connectorId="drive-1", connectorName="DRIVE", origin="CONNECTOR"),
    ])
    blobs = _Blobs({"v-a": await _blob(*EMAIL), "v-b": await _blob("Runbook", "Restart it."), "v-d": await _blob(*EMAIL)})
    return graph, blobs


@pytest.mark.asyncio
async def test_a_dry_run_counts_and_writes_nothing() -> None:
    graph, blobs = await _setup()

    tally = await backfill_content_dates(graph, blobs, org_id="o1", apply=False, replace=UPLOAD_ONLY, page_size=2, out=io.StringIO())

    assert (tally.checked, tally.date, tally.stamp, tally.unreadable, tally.written) == (4, 1, 1, 1, 0)
    assert graph.batches == []


@pytest.mark.asyncio
async def test_apply_dates_and_stamps_in_one_write_per_page_and_is_idempotent() -> None:
    graph, blobs = await _setup()

    tally = await backfill_content_dates(graph, blobs, org_id="o1", apply=True, replace=UPLOAD_ONLY, out=io.StringIO())

    assert tally.written == 2
    assert len(graph.batches) == 1
    assert graph.docs["a"]["sourceCreatedAtTimestamp"] == EMAIL_DAY
    assert graph.docs["a"]["sourceTimestampOrigin"] == "content"
    assert graph.docs["b"]["sourceTimestampOrigin"] == "upload"
    assert "sourceTimestampOrigin" not in graph.docs["e"]

    again = await backfill_content_dates(graph, blobs, org_id="o1", apply=True, replace=UPLOAD_ONLY, out=io.StringIO())
    assert (again.date, again.stamp, again.written) == (0, 0, 0)


@pytest.mark.asyncio
async def test_revert_gives_the_upload_values_back_without_reading_blobs() -> None:
    graph, blobs = await _setup()
    await backfill_content_dates(graph, blobs, org_id="o1", apply=True, replace=UPLOAD_ONLY, out=io.StringIO())
    reads = blobs.reads

    tally = await backfill_content_dates(graph, blobs, org_id="o1", apply=True, revert=True, replace=frozenset(), out=io.StringIO())

    assert (tally.revert, tally.written) == (1, 1)
    assert blobs.reads == reads
    assert graph.docs["a"]["sourceCreatedAtTimestamp"] == UPLOADED - 40
    assert graph.docs["a"]["sourceTimestampOrigin"] == "upload"


@pytest.mark.asyncio
async def test_resuming_after_a_key_skips_what_was_done() -> None:
    graph, blobs = await _setup()

    tally = await backfill_content_dates(
        graph, blobs, org_id="o1", apply=False, replace=UPLOAD_ONLY, after_key="kb-1/b", out=io.StringIO(),
    )

    assert tally.checked == 2


@pytest.mark.asyncio
async def test_each_knowledge_base_is_walked_by_its_connector_index_and_resumed_across() -> None:
    # Filtering on connectorId alone and sorting by key is what the records'
    # (connectorId, key) index serves on both backends; the org's own filter is not.
    apps = [*APPS, {"_key": "kb-2", "orgId": "o1", "type": "KB"}]
    graph = _Graph([_doc("a"), _doc("b"), _doc("x", connectorId="kb-2"), _doc("y", connectorId="kb-9", orgId="o2")], apps)
    blobs = _Blobs({})

    tally = await backfill_content_dates(graph, blobs, org_id="o1", apply=False, replace=UPLOAD_ONLY, page_size=1, out=io.StringIO())

    assert tally.checked == 3 and tally.after_key == "kb-2/x"
    assert {tuple(filters) for filters in graph.record_filters} == {("connectorId",)}
    resumed = await backfill_content_dates(
        graph, blobs, org_id="o1", apply=False, replace=UPLOAD_ONLY, after_key="kb-1/a", out=io.StringIO(),
    )
    assert resumed.checked == 2


@pytest.mark.asyncio
async def test_an_extraction_the_entities_stage_kept_dates_the_upload() -> None:
    # Indexed through the entities stage: the stored record holds the blocks, and the
    # extraction is the stage's output for the record's revision.
    blocks, extraction = await _extraction(*EMAIL)
    graph = _Graph([_doc("a", contentRev="rev-a", sourceTimestampOrigin="upload")])
    graph.stage_states[stage_state_key("v-a", "rev-a", ENTITIES_STAGE)] = {
        "status": "COMPLETED", "output": extraction.model_dump_json(),
    }
    blobs = _Blobs({"v-a": {"block_containers": blocks.model_dump(mode="json", exclude_none=True)}})

    tally = await backfill_content_dates(graph, blobs, org_id="o1", apply=True, replace=UPLOAD_ONLY, out=io.StringIO())

    assert (tally.date, tally.written, tally.without_extraction) == (1, 1, 0)
    assert graph.docs["a"]["sourceCreatedAtTimestamp"] == EMAIL_DAY


@pytest.mark.asyncio
async def test_a_damaged_stored_record_is_skipped_and_the_run_goes_on() -> None:
    graph = _Graph([_doc("a", sourceTimestampOrigin="upload"), _doc("b", sourceTimestampOrigin="upload")])
    damaged = {**await _blob(*EMAIL), "block_containers": {"blocks": [{"index": "first"}], "block_groups": []}}
    blobs = _Blobs({"v-a": damaged, "v-b": await _blob(*EMAIL)})

    tally = await backfill_content_dates(graph, blobs, org_id="o1", apply=True, replace=UPLOAD_ONLY, out=io.StringIO())

    assert (tally.unreadable, tally.date, tally.written) == (1, 1, 1)
    assert graph.docs["b"]["sourceTimestampOrigin"] == "content"


@pytest.mark.asyncio
async def test_dating_needs_the_switch_on() -> None:
    graph, blobs = await _setup()
    with pytest.raises(ValueError, match="CONTENT_DOCUMENT_DATES"):
        await backfill_content_dates(graph, blobs, org_id="o1", apply=True, replace=frozenset(), out=io.StringIO())
