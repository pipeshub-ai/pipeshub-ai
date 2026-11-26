"""The revision of the snapshot guards a plain document update on ArangoDB (``update_node_if_unchanged``), with concurrent
writers: whoever changed the record between the read and the write makes the conditional write refuse, and what that writer
wrote stays."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import pytest

from tests.integration.graph_db.record_diagnostics_support import (
    RECORDS,
    Store,
    new_org,
)
from tests.integration.real_graph import backend_unavailable, connect_arango

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
logger = logging.getLogger("record-diagnostics-if-match-it")


@pytest.fixture
async def store() -> AsyncIterator[Store]:
    try:
        provider = await connect_arango(logger, "record_diagnostics_if_match_it")
    except Exception as exc:
        backend_unavailable("arango", exc)
    made = Store("arango", provider, new_org())
    try:
        yield made
    finally:
        await made.cleanup()
        await provider.disconnect()


async def test_an_unchanged_record_takes_the_write(store: Store) -> None:
    key = store.key()
    await store.put(key, contentRev="rev-a", indexingStatus="IN_PROGRESS")
    snapshot = await store.doc(key)

    assert await store.p.update_node_if_unchanged(key, RECORDS, {"parsingStatus": "COMPLETED", "pageCount": 4}, snapshot, {"contentRev": "rev-a", "indexingStatus": "IN_PROGRESS"})

    assert (await store.doc(key))["pageCount"] == 4


async def test_a_record_written_by_someone_else_after_the_read_refuses_the_write_and_keeps_theirs(store: Store) -> None:
    key = store.key()
    await store.put(key, contentRev="rev-a", indexingStatus="IN_PROGRESS")
    snapshot = await store.doc(key)
    await store.p.update_node(key, RECORDS, {"contentRev": "rev-b", "pageCount": None, "indexingStatus": "QUEUED"})  # a newer revision arrives

    assert not await store.p.update_node_if_unchanged(key, RECORDS, {"pageCount": 4}, snapshot, {"contentRev": "rev-a", "indexingStatus": "IN_PROGRESS"})

    doc = await store.doc(key)
    assert doc["contentRev"] == "rev-b" and doc.get("pageCount") is None and doc["indexingStatus"] == "QUEUED"


async def test_of_many_concurrent_conditional_writers_exactly_one_wins(store: Store) -> None:
    key = store.key()
    await store.put(key, contentRev="rev-a", indexingStatus="IN_PROGRESS")
    snapshot = await store.doc(key)

    results = await asyncio.gather(*(
        store.p.update_node_if_unchanged(key, RECORDS, {"pageCount": n + 1}, snapshot, {"contentRev": "rev-a"}) for n in range(8)
    ))

    assert sum(1 for r in results if r) == 1
    assert (await store.doc(key))["pageCount"] in range(1, 9)


async def test_the_unconditional_control_lets_every_writer_through(store: Store) -> None:
    """The control: the same eight writes without the revision all apply (the last one stays)."""
    key = store.key()
    await store.put(key, contentRev="rev-a")

    await asyncio.gather(*(store.p.update_node(key, RECORDS, {"pageCount": n + 1}) for n in range(8)))

    assert (await store.doc(key))["pageCount"] in range(1, 9)
