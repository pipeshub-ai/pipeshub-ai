"""PASSWORD_PROTECTED on a real Neo4j and ArangoDB.

Requires the graph stack from
deployment/docker-compose/docker-compose.integration.graph-db.yml (NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD; the defaults match it).

The strict ArangoDB `records` schema is applied by `ensure_schema`, so a status
it does not declare fails the write here as it would in production.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from tests.integration.graph_db.test_full_sync_unlink_real_backends import (
    _cleanup,
    _seed,
    _Sync,
)
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
logger = logging.getLogger("password-protected-it")

RECORDS = CollectionNames.RECORDS.value
LOCKED = ProgressStatus.PASSWORD_PROTECTED.value
REASON = "This file is password-protected, so we can't read it."
DOC = Path(__file__).resolve().parents[5] / "docs" / "indexing-service.md"


async def _synced(backend: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Sync]:
    try:
        graph = await (
            connect_neo4j(logger, monkeypatch) if backend == "neo4j"
            else connect_arango(logger, "password_protected_it")
        )
    except Exception as exc:
        backend_unavailable(backend, exc)
    seeded = await _seed(graph)
    try:
        yield seeded
    finally:
        await _cleanup(seeded)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            await disconnect()


@pytest.fixture(params=["neo4j", "arango"])
async def sync(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Sync]:
    async for seeded in _synced(request.param, monkeypatch):
        yield seeded


@pytest.fixture(params=["arango"])
async def arango_sync(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Sync]:
    """For tests that only make sense on ArangoDB; parametrized rather than skipped at run time."""
    async for seeded in _synced(request.param, monkeypatch):
        yield seeded


async def _records(sync: _Sync, *names: str, md5: str | None = None) -> dict[str, str]:
    """Message records in the channel, each QUEUED as a new upload would be; their ids by name."""
    await sync.processor.on_new_record_groups([sync.channel(readers=(sync.alice,))])
    batch = []
    for name in names:
        record, grants = sync.message(name, readers=(sync.alice,))
        record.indexing_status = ProgressStatus.NOT_STARTED.value
        if md5:
            record.md5_hash = md5
        batch.append((record, grants))
    await sync.processor.on_new_records(batch)
    return {name: await sync.record_id(name) for name in names}


async def _status(sync: _Sync, record_id: str) -> dict:
    doc = await sync.graph.get_document(record_id, RECORDS)
    assert doc is not None
    return doc


async def _lock(sync: _Sync, record_id: str) -> None:
    assert await sync.graph.update_node(
        record_id, RECORDS,
        # The three fields as the handler leaves a locked record.
        {
            "indexingStatus": LOCKED, "parsingStatus": LOCKED,
            "extractionStatus": ProgressStatus.SKIPPED.value, "reason": REASON,
        },
    )


async def test_the_status_is_written_and_read_back(sync: _Sync) -> None:
    ids = await _records(sync, "locked")

    await _lock(sync, ids["locked"])

    doc = await _status(sync, ids["locked"])
    assert doc["indexingStatus"] == LOCKED
    assert doc["reason"] == REASON


async def test_stats_count_it_in_its_own_bucket_not_as_failed(sync: _Sync) -> None:
    ids = await _records(sync, "locked1", "locked2", "broken", "fine")
    await _lock(sync, ids["locked1"])
    await _lock(sync, ids["locked2"])
    assert await sync.graph.update_node(ids["broken"], RECORDS, {"indexingStatus": "FAILED"})
    assert await sync.graph.update_node(ids["fine"], RECORDS, {"indexingStatus": "COMPLETED"})

    result = await sync.graph.get_connector_stats(sync.org_id, sync.connector_id)

    assert result["success"], result
    counts = result["data"]["stats"]["indexingStatus"]
    assert counts[LOCKED] == 2
    assert counts["FAILED"] == 1
    extraction = result["data"]["stats"]["extractionStatus"]
    assert extraction["SKIPPED"] == 2, "a locked file is not a classification failure"
    assert extraction["FAILED"] == 0
    assert counts["COMPLETED"] == 1
    assert result["data"]["stats"]["total"] == 4


async def test_reindex_failed_leaves_it_out_and_an_explicit_filter_finds_it(sync: _Sync) -> None:
    ids = await _records(sync, "locked", "broken")
    await _lock(sync, ids["locked"])
    assert await sync.graph.update_node(ids["broken"], RECORDS, {"indexingStatus": "FAILED"})

    async def selected(filters: list[str]) -> set[str]:
        found = await sync.graph.get_records_by_status(
            sync.org_id, sync.connector_id, filters, exclude_statuses=[ProgressStatus.IN_PROGRESS.value],
            is_placeholder=False,
        )
        return {r.id for r in found}

    assert await selected(["FAILED"]) == {ids["broken"]}
    assert await selected([LOCKED]) == {ids["locked"]}


async def test_queued_copies_of_a_locked_file_get_the_status_not_failed(sync: _Sync) -> None:
    ids = await _records(sync, "primary", "copy1", "copy2", md5="same-bytes-md5")
    primary, copies = ids["primary"], (ids["copy1"], ids["copy2"])
    for key in (primary, *copies):
        assert await sync.graph.update_node(key, RECORDS, {"indexingStatus": "QUEUED", "sizeInBytes": 10})
    await _lock(sync, primary)

    updated = await sync.graph.update_queued_duplicates_status(primary, LOCKED, "vr-primary", reason=REASON)

    assert updated == 2
    for key in copies:
        doc = await _status(sync, key)
        assert doc["indexingStatus"] == LOCKED
        assert doc["extractionStatus"] == ProgressStatus.SKIPPED.value
        assert doc["reason"] == REASON


async def test_a_resync_of_an_unchanged_revision_does_not_queue_it_but_a_changed_one_does(sync: _Sync) -> None:
    ids = await _records(sync, "locked")
    await _lock(sync, ids["locked"])
    sent: list[object] = []
    sync.processor.messaging_producer.send_messages.side_effect = (
        lambda topic, messages: sent.extend(messages) or [True] * len(messages)
    )

    def resync(revision: str) -> list[tuple]:
        record, grants = sync.message("locked", readers=(sync.alice,))
        record.indexing_status = ProgressStatus.NOT_STARTED.value
        record.external_revision_id = revision
        return [(record, grants)]

    await sync.processor.on_new_records(resync("1"))

    assert sent == []
    doc = await _status(sync, ids["locked"])
    assert doc["indexingStatus"] == LOCKED
    assert doc["reason"] == REASON

    await sync.processor.on_new_records(resync("2"))

    assert len(sent) == 1
    assert (await _status(sync, ids["locked"]))["indexingStatus"] != LOCKED


def _rollback_aql() -> str:
    section = DOC.read_text().split("**Rolling back to 0.10.0 on ArangoDB.**", 1)[1]
    match = re.search(r"```aql\n(.*?)```", section, re.S)
    assert match, "the rollback AQL is missing from docs/indexing-service.md"
    return match.group(1)


async def test_the_documented_rollback_rewrites_it_to_failed_and_touches_nothing_else(arango_sync: _Sync) -> None:
    sync = arango_sync
    assert isinstance(sync.graph, ArangoHTTPProvider)
    ids = await _records(sync, "locked", "fine", "broken")
    await _lock(sync, ids["locked"])
    assert await sync.graph.update_node(ids["fine"], RECORDS, {"indexingStatus": "COMPLETED"})
    assert await sync.graph.update_node(ids["broken"], RECORDS, {"indexingStatus": "FAILED"})
    before = {name: await _status(sync, key) for name, key in ids.items()}

    # The statement updates every matching record of the database; this one is
    # private to the test, and the seeded records are all it holds.
    await sync.graph.http_client.execute_aql(_rollback_aql())

    after = {name: await _status(sync, key) for name, key in ids.items()}
    assert after["locked"]["indexingStatus"] == "FAILED"
    assert after["locked"]["reason"] == REASON
    assert after["fine"]["indexingStatus"] == "COMPLETED"
    assert after["broken"]["indexingStatus"] == "FAILED"
    for name in ("fine", "broken"):
        assert {k: v for k, v in after[name].items() if k != "_rev"} == {
            k: v for k, v in before[name].items() if k not in ("_rev", "contentRev")
        }
    assert after["locked"]["parsingStatus"] == "FAILED"
    assert after["locked"]["extractionStatus"] == "FAILED"
    assert after["locked"].get("contentRev") is None


async def test_classification_counts_and_its_retry_do_not_select_a_locked_record(sync: _Sync) -> None:
    ids = await _records(sync, "locked", "fine", "broken")
    await _lock(sync, ids["locked"])
    assert await sync.graph.update_node(
        ids["fine"], RECORDS, {"indexingStatus": "COMPLETED", "extractionStatus": "COMPLETED"}
    )
    assert await sync.graph.update_node(
        ids["broken"], RECORDS, {"indexingStatus": "COMPLETED", "extractionStatus": "FAILED"}
    )

    stats = (await sync.graph.get_connector_stats(sync.org_id, sync.connector_id))["data"]["stats"]
    # The panel's "failed classification" and its Retry button use the FAILED bucket and filter.
    assert stats["extractionStatus"]["FAILED"] == 1
    assert stats["extractionStatus"]["SKIPPED"] == 1
    retried = await sync.graph.get_records_by_status(
        sync.org_id, sync.connector_id, ["FAILED"], status_field="extractionStatus", is_placeholder=False,
    )
    assert {r.id for r in retried} == {ids["broken"]}
