"""Where the edge builder gets a file's full cross-file facts.

Block nodes carry only what every build reads for every file: heritage and
re-export facts, and the names the file references (see ``RESIDENT_RELATIONS``
in ``block_projection``). The bulk -- calls and imports, most of the bytes -- is
read only for the files a build re-resolves, and comes from the record's blob:
the indexing pipeline uploads the whole parsed record there, from the same parse
that produced the block nodes, before the record is marked COMPLETED.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from app.modules.code_graph.block_projection import (
    FILE_SUMMARY_QUALIFIED_NAME,
    block_key_for,
)

if TYPE_CHECKING:
    from app.modules.transformers.blob_storage import BlobStorage
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = [
    "BlobCodeFactsSource",
    "CodeFactsSource",
    "RecordFacts",
    "facts_from_blob_record",
]

_FETCH_CONCURRENCY = 16

logger = logging.getLogger(__name__)


@dataclass
class RecordFacts:
    """One file's facts, keyed the way its block nodes are."""

    pending_edges: dict[str, list[dict]] = field(default_factory=dict)
    type_table: dict[str, str] = field(default_factory=dict)


class CodeFactsSource(Protocol):
    async def load(self, org_id: str, record_ids: set[str]) -> dict[str, RecordFacts]:
        """Facts for every record that could be read; a record absent from the
        result has no readable blob."""


def facts_from_blob_record(record_id: str, record: dict[str, Any]) -> RecordFacts:
    """Facts out of a stored record, keyed by block node.

    Mirrors the key derivation in ``write_code_file_blocks_to_graph`` -- groups
    without a qualified name are skipped, a block without one is the file
    summary -- so each fact lands on the node the projection wrote for it.
    """
    out = RecordFacts()
    container = record.get("block_containers")
    if not isinstance(container, dict):
        return out
    for group in container.get("block_groups") or []:
        meta = _code_metadata(group)
        qualified_name = meta.get("qualified_name")
        if not qualified_name:
            continue
        _take(out, block_key_for(record_id, qualified_name), meta)
    for block in container.get("blocks") or []:
        meta = _code_metadata(block)
        qualified_name = meta.get("qualified_name") or FILE_SUMMARY_QUALIFIED_NAME
        _take(out, block_key_for(record_id, qualified_name), meta)
    return out


def _code_metadata(item: Any) -> dict[str, Any]:
    meta = item.get("code_metadata") if isinstance(item, dict) else None
    return meta if isinstance(meta, dict) else {}


def _take(out: RecordFacts, block_key: str, meta: dict[str, Any]) -> None:
    facts = meta.get("pending_edges")
    if isinstance(facts, list) and facts:
        out.pending_edges.setdefault(block_key, []).extend(
            fact for fact in facts if isinstance(fact, dict)
        )
    table = meta.get("type_table")
    if isinstance(table, dict):
        out.type_table.update(table)


class BlobCodeFactsSource:
    """``CodeFactsSource`` over the records the indexing pipeline stored."""

    def __init__(
        self,
        blob_storage: BlobStorage,
        graph_provider: IGraphDBProvider,
        log: logging.Logger | None = None,
        concurrency: int = _FETCH_CONCURRENCY,
    ) -> None:
        self._blob_storage = blob_storage
        self._graph_provider = graph_provider
        self._log = log or logger
        self._concurrency = concurrency

    async def load(self, org_id: str, record_ids: set[str]) -> dict[str, RecordFacts]:
        ids = sorted(record_ids)
        if not ids:
            return {}
        virtual_ids = await self._graph_provider.get_virtual_record_ids_for_record_ids(ids)
        # One mapping query for the batch; the per-record lookup inside
        # get_record_from_storage would otherwise cost a graph round trip each.
        lookups = await self._blob_storage.get_document_ids_by_virtual_record_ids(
            sorted(set(virtual_ids.values()))
        )
        semaphore = asyncio.Semaphore(self._concurrency)

        async def one(record_id: str) -> tuple[str, RecordFacts | None]:
            virtual_id = virtual_ids.get(record_id)
            if not virtual_id:
                return record_id, None
            async with semaphore:
                try:
                    record = await self._blob_storage.get_record_from_storage(
                        virtual_id, org_id, lookup_result=lookups.get(virtual_id)
                    )
                except Exception:
                    self._log.exception("Failed to read stored facts for record %s", record_id)
                    return record_id, None
            if not record:
                return record_id, None
            return record_id, facts_from_blob_record(record_id, record)

        results = await asyncio.gather(*(one(record_id) for record_id in ids))
        return {record_id: facts for record_id, facts in results if facts is not None}
