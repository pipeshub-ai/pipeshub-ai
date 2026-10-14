"""The operator command that queues an org's records for named-entity re-extraction."""

from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.scripts.named_entity_reextract import queue_reextractions


def _extraction(status: str, total: int, run: int) -> dict[str, Any]:
    return {"status": status, "strategy": "single_call", "termination_reason": "single_call",
            "entities": [], "stats": {"windows_total": total, "windows_run": run}}


BLOBS: dict[str, dict[str, Any] | None] = {
    "v-partial": {"semantic_metadata": {"named_entities": _extraction("PARTIAL", 9, 3)}},
    "v-complete": {"semantic_metadata": {"named_entities": _extraction("COMPLETED", 2, 2)}},
    "v-none": {"semantic_metadata": {}},
}


def _record(key: str, vrid: str, **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"_key": key, "orgId": "o1", "virtualRecordId": vrid, **extra}


RECORDS = [
    _record("a", "v-partial"), _record("b", "v-complete"), _record("c", "v-none"),
    _record("d", "v-partial", isDeleted=True), _record("e", "v-broken"),
]


def _graph(records: list[dict[str, Any]]) -> MagicMock:
    async def page(collection: str, *, limit: int, filters: dict, sort_field: str, raise_on_error: bool, after_key: str | None) -> list:
        del collection, filters, sort_field, raise_on_error
        rest = [r for r in records if after_key is None or r["_key"] > after_key]
        return rest[:limit]

    graph = MagicMock()
    graph.get_documents_paginated = AsyncMock(side_effect=page)
    graph.schedule_named_entity_reextract = AsyncMock()
    return graph


def _blobs() -> MagicMock:
    async def read(vrid: str, org_id: str) -> dict[str, Any] | None:
        del org_id
        if vrid not in BLOBS:
            raise RuntimeError("storage down")
        return BLOBS[vrid]

    blobs = MagicMock()
    blobs.get_record_from_storage = AsyncMock(side_effect=read)
    return blobs


async def test_a_dry_run_counts_and_queues_nothing() -> None:
    graph = _graph(RECORDS)

    tally = await queue_reextractions(graph, _blobs(), org_id="o1", everything=False, apply=False, page_size=2, out=io.StringIO())

    assert (tally.checked, tally.queued, tally.without_extraction, tally.unreadable) == (5, 1, 1, 1)
    graph.schedule_named_entity_reextract.assert_not_awaited()


async def test_apply_queues_only_records_that_left_text_unread() -> None:
    graph = _graph(RECORDS)

    await queue_reextractions(graph, _blobs(), org_id="o1", everything=False, apply=True, out=io.StringIO())

    graph.schedule_named_entity_reextract.assert_awaited_once_with("o1", "a")


async def test_all_queues_every_live_record_with_an_extraction() -> None:
    graph = _graph(RECORDS)

    await queue_reextractions(graph, _blobs(), org_id="o1", everything=True, apply=True, out=io.StringIO())

    assert [c.args for c in graph.schedule_named_entity_reextract.await_args_list] == [("o1", "a"), ("o1", "b")]


async def test_a_run_resumes_after_a_key_and_stops_at_its_limit() -> None:
    graph = _graph(RECORDS)
    out = io.StringIO()

    tally = await queue_reextractions(graph, _blobs(), org_id="o1", everything=True, apply=False, after_key="a", limit=2, out=out)

    assert (tally.checked, tally.after_key) == (2, "c")
    assert json.loads(out.getvalue().splitlines()[-1])["after_key"] == "c"
