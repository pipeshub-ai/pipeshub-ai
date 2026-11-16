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

    async def get_document(doc_id, collection, **kwargs):  # noqa: ANN001, ANN202
        row = await previous(doc_id, collection, **kwargs)
        if row and doc_id == "twin":
            row["deferredUntilMs"] = NOW_MS() + 300_000
        return row

    graph.get_document.side_effect = get_document
    container = make_container()
    await recover_in_progress_records(container, graph)
    assert container.kafka_consumers[0][2].send_event.await_count == 0
    assert rows["twin"]["processingStartedAt"] == 1_000
