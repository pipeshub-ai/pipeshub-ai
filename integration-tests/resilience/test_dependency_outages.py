"""Indexing survives its dependencies going away mid-run.

Each test uploads a batch of documents to a fresh knowledge base and, while
they are being indexed, takes one dependency away: the message broker (Redis),
the vector database (Qdrant), the graph database (Neo4j or ArangoDB) or
MongoDB is restarted, or the indexing process is killed outright. Then every
document must still reach COMPLETED, exactly once:

  * the knowledge base lists each upload once, and nothing else;
  * the graph holds one record per upload for the knowledge base, no more;
  * each document has vectors, and the same number as its siblings. The
    documents share one shape -- identical in length, and segmenting into the
    same sentences -- so any difference in count means the outage changed what
    was written: a document short of vectors has content that cannot be found,
    and one carrying extra has a passage indexed more than once.

A document left QUEUED, IN_PROGRESS or FAILED after the recovery window is a
real failure: the product lost work when a dependency blinked.

The fault must meet work in flight, or the test proves nothing. On a warm
stack a small batch can finish before the fault lands, so uploading continues
batch by batch until the newest batch is still being indexed, and the test
fails if it never catches one.

    RESILIENCE_FILES                 documents per batch (default 8)
    RESILIENCE_MAX_BATCHES           batches to try before failing (default 4)
    RESILIENCE_RECOVERY_TIMEOUT_SEC  how long indexing may take to recover (default 900)
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from helper.clients.kb_client import KBClient
from helper.compose_control import ComposeStack, graph_service
from helper.fault_switches import KILL_INDEXING
from helper.stored_names import stored_name
from helper.vector_counts import describe_divergence
from helper.indexing_progress import (
    POLL,
    RECOVERY_TIMEOUT,
    UNFINISHED,
    document,
    record_fields,
    statuses,
    wait_until_finished,
)

logger = logging.getLogger("resilience")

FILES = int(os.getenv("RESILIENCE_FILES", "8"))
MAX_BATCHES = int(os.getenv("RESILIENCE_MAX_BATCHES", "4"))

# Redis in the stack has no health check, so readiness is an answered PING
# (a Redis still loading its data answers LOADING, not PONG).
REDIS_PING = [
    "sh", "-c",
    'redis-cli ${REDIS_PASSWORD:+-a "$REDIS_PASSWORD"} --no-auth-warning ping | grep -q PONG',
]

pytestmark = [pytest.mark.resilience, pytest.mark.asyncio(loop_scope="session")]


def _restart(service: str, probe: list[str] | None = None) -> Callable[[ComposeStack], None]:
    def inject(compose: ComposeStack) -> None:
        compose.restart(service)
        compose.wait_ready(service, probe=probe)

    return inject


def _kill_indexing(compose: ComposeStack) -> None:
    killed = compose.exec("pipeshub-ai", ["sh", "-c", KILL_INDEXING]).stdout.split()
    assert killed, "no indexing process was running in the pipeshub-ai container to kill"
    logger.info("Killed indexing process(es) %s; the container's monitor restarts it", killed)


@dataclass(frozen=True)
class Outage:
    name: str
    inject: Callable[[ComposeStack], None]


OUTAGES = [
    Outage("broker restart", _restart("redis", REDIS_PING)),
    Outage("vector database restart", _restart("qdrant")),
    Outage("graph database restart", _restart(graph_service())),
    Outage("main database restart", _restart("mongodb")),
    Outage("indexing process killed", _kill_indexing),
]


def _listed_names(kb_client: KBClient, kb_id: str, expected: int) -> list[str]:
    # Room past the expected count, so a duplicate is listed rather than paged off.
    payload = kb_client.list_records(kb_id, limit=min(expected + 10, 200))
    return [item["name"] for item in payload.get("items") or []]


async def _graph_record_names(graph_provider, kb_id: str) -> list[str]:
    # The graph database may just have come back; its first query can meet a stale connection.
    for attempt in range(3):
        try:
            return await graph_provider.fetch_record_names(kb_id)
        except Exception:  # noqa: BLE001 - retried, then raised
            if attempt == 2:
                raise
            await asyncio.sleep(POLL)
    return []


@pytest.mark.parametrize("outage", OUTAGES, ids=[o.name for o in OUTAGES])
async def test_indexing_recovers_from_outage_without_losing_or_duplicating(
    outage: Outage,
    compose: ComposeStack,
    kb_client: KBClient,
    vector_store,
    graph_provider,
) -> None:
    kb_id = kb_client.create_kb(f"resilience-{uuid.uuid4().hex[:8]}")["id"]
    try:
        names: list[str] = []
        record_ids: list[str] = []
        in_flight: dict[str, str] = {}
        for _ in range(MAX_BATCHES):
            batch = []
            for _ in range(FILES):
                token = uuid.uuid4().hex[:12]
                name = f"runbook-{token}.md"
                upload = kb_client.upload_file(kb_id, name, document(token), mimetype="text/markdown")
                assert upload["summary"]["failed"] == 0, f"upload of {name} failed before any fault: {upload}"
                names.append(name)
                batch.append(upload["records"][0]["recordId"])
            record_ids += batch
            # The newest batch is the one most likely still in the pipeline.
            in_flight = {rid: st for rid, st in statuses(kb_client, batch).items() if st in UNFINISHED}
            if in_flight:
                break
        assert in_flight, (
            f"all {len(record_ids)} documents were indexed before the {outage.name} could land, "
            "so it would meet no work in flight; raise RESILIENCE_FILES or RESILIENCE_MAX_BATCHES"
        )
        logger.info("Injecting %s with %d document(s) in flight: %s", outage.name, len(in_flight), in_flight)
        outage.inject(compose)

        final = await wait_until_finished(kb_client, record_ids)
        stuck = {rid: status for rid, status in final.items() if status != "COMPLETED"}
        assert not stuck, (
            f"after a {outage.name}, {len(stuck)} of {len(record_ids)} documents did not reach COMPLETED "
            f"within {RECOVERY_TIMEOUT}s: {stuck}"
        )

        # A record is stored under the file name without its extension, so
        # comparing against the uploaded names would fail on files that were
        # indexed perfectly well — and would read as a lost or duplicated
        # record, which is what this test is actually watching for.
        expected = sorted(stored_name(name) for name in names)

        listed = _listed_names(kb_client, kb_id, len(names))
        assert sorted(listed) == expected, (
            f"after a {outage.name}, the knowledge base lists {sorted(listed)}; "
            f"expected each of the {len(names)} uploads exactly once"
        )
        in_graph = await _graph_record_names(graph_provider, kb_id)
        assert sorted(in_graph) == expected, (
            f"after a {outage.name}, the graph holds records {sorted(in_graph)} for the knowledge base; "
            f"expected one per upload ({len(names)}), so a record was lost or written twice"
        )

        counts = {}
        for record_id in record_ids:
            virtual_id = record_fields(kb_client.get_record(record_id)).get("virtualRecordId")
            assert virtual_id, f"record {record_id} is COMPLETED but has no virtual record id"
            counts[record_id] = await vector_store.count_content_chunks(str(virtual_id))
        assert all(counts.values()), f"after a {outage.name}, some COMPLETED documents have no vectors: {counts}"
        # Content chunks only. The record summary is a further vector written by
        # the enrichment step, which runs once the document is already
        # searchable and is allowed to fail, so counting it makes two copies of
        # the same file legitimately differ by one -- which is what this check
        # was reporting as lost or duplicated content.
        #
        # Every document here is byte-identical in length and segments into the
        # same number of sentences, and chunking is character-based over those
        # sentences -- so an equal count is arithmetic, not an assumption about
        # the product. What a difference means is worked out in
        # `describe_divergence`, which has its own tests: the two directions are
        # different bugs, and on a tie it declines to pick one rather than
        # naming whichever count was uploaded first.
        divergence = describe_divergence(counts)
        assert divergence is None, (
            f"after a {outage.name}, documents of one shape hold different vector "
            f"counts -- {divergence}. All counts: {counts}"
        )

    finally:
        try:
            kb_client.delete_kb(kb_id)
        except Exception as exc:  # noqa: BLE001 - teardown must not mask the result
            logger.warning("Could not delete knowledge base %s: %s", kb_id, exc)
