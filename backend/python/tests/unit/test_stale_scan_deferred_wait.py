"""The stale-record scan leaves a record handed back for a provider's wait alone until the wait is over.

A record the handler handed back for a model provider's rate limit has a delayed message in the broker. Its
``processingStartedAt`` is old (the attempt that hit the limit began long ago), so without the explicit ``deferredUntilMs`` the
scan would publish a recovery event within a minute and call the provider again before it said it can serve.
"""

from __future__ import annotations

import time

from app.indexing_main import recover_in_progress_records
from app.modules.indexing.rate_limit_deferral import is_deferred_until_later
from tests.support.stale_scan_world import (
    enrichment_twin,
    make_container,
    stale_scan_graph,
)

NOW_MS = lambda: int(time.time() * 1000)  # noqa: E731


async def _scan(**fields: object) -> tuple[int, dict]:
    graph, rows = stale_scan_graph([{**enrichment_twin(1_000), **fields}])
    container = make_container()
    await recover_in_progress_records(container, graph)
    return container.kafka_consumers[0][2].send_event.await_count, rows["twin"]


async def test_the_scan_does_not_republish_a_record_before_its_wait_is_over() -> None:
    sent, row = await _scan(deferredUntilMs=NOW_MS() + 300_000)
    assert sent == 0
    assert row["processingStartedAt"] == 1_000, "the real start is kept, not moved"


async def test_the_scan_republishes_it_once_the_wait_is_over() -> None:
    sent, _row = await _scan(deferredUntilMs=NOW_MS() - 1_000)
    assert sent == 1


async def test_a_record_with_no_wait_is_recovered_as_before() -> None:
    sent, _row = await _scan()
    assert sent == 1


def test_a_wait_is_read_only_while_it_is_in_the_future() -> None:
    assert is_deferred_until_later({"deferredUntilMs": 2_000}, 1_000) is True
    assert is_deferred_until_later({"deferredUntilMs": 1_000}, 1_000) is False
    assert not any(is_deferred_until_later({"deferredUntilMs": v}, 1_000) for v in (None, "x", float("nan"), True))
    assert is_deferred_until_later({}, 1_000) is False


async def test_a_wait_set_while_the_scan_was_looking_is_seen_under_the_lease() -> None:
    """The page the scan read has no wait; when the record is read again under its lease it has been handed back for one."""
    graph, rows = stale_scan_graph([enrichment_twin(1_000)])
    previous = graph.get_document.side_effect

    async def get_document(doc_id, collection, **kwargs):  # noqa: ANN202
        row = await previous(doc_id, collection, **kwargs)
        if row and doc_id == "twin":
            row["deferredUntilMs"] = NOW_MS() + 300_000
        return row

    graph.get_document.side_effect = get_document
    container = make_container()
    await recover_in_progress_records(container, graph)
    assert container.kafka_consumers[0][2].send_event.await_count == 0
    assert rows["twin"]["processingStartedAt"] == 1_000


# ---- the scan's page is a snapshot: a wait written after it is seen under the lease, for an index record and a resume ---------

from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import pytest  # noqa: E402

from app.config.constants.arangodb import CollectionNames  # noqa: E402
from tests.support.stale_scan_world import FakeConcurrencyManager  # noqa: E402

_CONNECTOR = {"_key": "conn-1", "isActive": True, "name": "Slack"}


def _snapshot_and_fresh(kind: str) -> tuple[dict, dict]:
    base = {"_key": "r1", "orgId": "org-1", "connectorId": "conn-1", "origin": "CONNECTOR", "recordName": "a.pdf"}
    old = NOW_MS() - 900_000
    if kind == "resume":
        snap = {**base, "indexingStatus": "COMPLETED", "extractionStatus": "IN_PROGRESS", "processingStartedAt": old}
    else:
        snap = {**base, "indexingStatus": "IN_PROGRESS", "parsingStatus": "IN_PROGRESS", "processingStartedAt": old}
    return snap, dict(snap)


async def _scan_snapshot(monkeypatch: pytest.MonkeyPatch, snapshot: dict, fresh: dict, distributed: bool) -> int:
    import app.indexing_main as module

    graph = MagicMock()
    graph.ensure_pipeline_schema = AsyncMock()

    async def page(collection, skip=0, limit=100, filters=None, **_):  # noqa: ANN202
        ok = collection == CollectionNames.RECORDS.value and all(snapshot.get(k) == v for k, v in (filters or {}).items())
        return [dict(snapshot)] if ok and skip == 0 else []

    async def get_document(key, collection, **_):  # noqa: ANN202
        return _CONNECTOR if collection == CollectionNames.APPS.value else dict(fresh)

    graph.get_documents_paginated = AsyncMock(side_effect=page)
    graph.get_document = AsyncMock(side_effect=get_document)
    graph.update_node = AsyncMock(return_value=True)
    graph.batch_update_nodes = AsyncMock(return_value=True)
    graph.get_nodes_by_filters = AsyncMock(return_value=[])
    monkeypatch.setattr(module, "_all_org_ids", AsyncMock(return_value=["org-1"]))
    container = make_container()
    container.kafka_consumers[0][1].concurrency_manager = FakeConcurrencyManager() if distributed else None
    if not distributed:
        monkeypatch.setenv("STALE_INDEXING_RECOVERY_AFTER_SECONDS", "60")
    await module.recover_in_progress_records(container, graph)
    return container.kafka_consumers[0][2].send_event.await_count


@pytest.mark.parametrize("distributed", [True, False], ids=["leases", "no-leases"])
@pytest.mark.parametrize("kind", ["index", "resume"])
async def test_a_wait_written_after_the_snapshot_is_seen_under_the_lease(
    monkeypatch: pytest.MonkeyPatch, kind: str, distributed: bool
) -> None:
    snapshot, fresh = _snapshot_and_fresh(kind)
    fresh["deferredUntilMs"] = NOW_MS() + 600_000
    assert await _scan_snapshot(monkeypatch, snapshot, fresh, distributed) == 0


@pytest.mark.parametrize("kind", ["index", "resume"])
async def test_a_wait_that_is_over_is_recovered(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    snapshot, fresh = _snapshot_and_fresh(kind)
    fresh["deferredUntilMs"] = NOW_MS() - 1_000
    assert await _scan_snapshot(monkeypatch, snapshot, fresh, True) == 1
