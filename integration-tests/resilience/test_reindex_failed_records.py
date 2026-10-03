"""Reindex failed records, from the All Records page and from connector settings.

The test list names two ways a person retries records that failed to index:

  * All Records: "Retry indexing" on one failed record, which sends
    ``POST /api/v1/knowledgeBase/reindex/record/{id}`` with ``{"depth": 0}``;
  * connector settings (and a collection's "Re-index failed"): the
    "Reindex failed (N)" button, which sends
    ``POST /api/v1/connectors/{id}/reindex`` with ``{"statusFilters": ["FAILED"]}``.

A record has to really fail first, for a reason that can then be fixed, or the
retry proves nothing. So the fixture cuts the app off from its AI provider (the
same switch the outage test uses), uploads documents until they are FAILED,
then reconnects. One healthy document, indexed before the outage, sits beside
them: the connector-level retry must leave it alone.

It runs with the resilience tests because cutting off the AI provider breaks
indexing for anything else on the stack.

    RESILIENCE_AI_OUTAGE_WINDOW_SEC  how long the documents may take to reach FAILED (default 600)
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio

from helper.clients.kb_client import KBClient
from helper.compose_control import ComposeStack
from helper.fault_switches import KILL_INDEXING, ai_provider_hosts, block_hosts_script, restore_hosts_script
from helper.indexing_progress import POLL, document, record_fields, statuses, wait_until_finished

logger = logging.getLogger("reindex-failed")

pytestmark = [pytest.mark.resilience, pytest.mark.asyncio(loop_scope="session")]

OUTAGE_WINDOW = int(os.getenv("RESILIENCE_AI_OUTAGE_WINDOW_SEC", "600"))
FAILING_DOCUMENTS = 3


@dataclass
class FailedRecords:
    kb_id: str
    healthy: str
    failed: list[str] = field(default_factory=list)
    healthy_before: dict[str, Any] = field(default_factory=dict)


def _upload(kb_client: KBClient, kb_id: str) -> str:
    token = uuid.uuid4().hex[:12]
    upload = kb_client.upload_file(kb_id, f"runbook-{token}.md", document(token), mimetype="text/markdown")
    assert upload["summary"]["failed"] == 0, f"upload failed before indexing began: {upload}"
    return upload["records"][0]["recordId"]


def _virtual_id(kb_client: KBClient, record_id: str) -> str:
    virtual_id = record_fields(kb_client.get_record(record_id)).get("virtualRecordId")
    assert virtual_id, f"record {record_id} is COMPLETED but has no virtualRecordId"
    return str(virtual_id)


async def _index_marks(graph_provider, vector_store, kb_client: KBClient, record_id: str) -> dict[str, Any]:
    """What a re-queue or a re-index would change on a record that is already done."""
    node = await graph_provider.get_document(record_id, "records") or {}
    return {
        "indexingStatus": node.get("indexingStatus"),
        "queuedAtTimestamp": node.get("queuedAtTimestamp"),
        "lastIndexTimestamp": node.get("lastIndexTimestamp"),
        "contentChunks": await vector_store.count_content_chunks(_virtual_id(kb_client, record_id)),
    }


def _reconnect(compose: ComposeStack) -> None:
    compose.exec("pipeshub-ai", ["sh", "-c", restore_hosts_script()])


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def failed_records(
    compose: ComposeStack, kb_client: KBClient, vector_store, graph_provider
) -> FailedRecords:
    kb_id = kb_client.create_kb(f"reindex-failed-{uuid.uuid4().hex[:8]}")["id"]
    try:
        healthy = _upload(kb_client, kb_id)
        final = await wait_until_finished(kb_client, [healthy])
        assert final[healthy] == "COMPLETED", f"the healthy document did not index before the outage: {final}"
        state = FailedRecords(kb_id=kb_id, healthy=healthy)
        state.healthy_before = await _index_marks(graph_provider, vector_store, kb_client, healthy)
        assert state.healthy_before["lastIndexTimestamp"], (
            f"the healthy record has no lastIndexTimestamp in the graph ({state.healthy_before}), "
            "so a re-index of it could not be seen"
        )

        compose.exec("pipeshub-ai", ["sh", "-c", block_hosts_script(ai_provider_hosts())])
        # A process that already holds a provider connection could keep using it.
        compose.exec("pipeshub-ai", ["sh", "-c", KILL_INDEXING])
        uploaded = [_upload(kb_client, kb_id) for _ in range(FAILING_DOCUMENTS)]

        deadline = asyncio.get_event_loop().time() + OUTAGE_WINDOW
        seen = statuses(kb_client, uploaded)
        while asyncio.get_event_loop().time() < deadline and set(seen.values()) != {"FAILED"}:
            await asyncio.sleep(POLL)
            seen = statuses(kb_client, uploaded)
        _reconnect(compose)

        state.failed = [rid for rid, status in seen.items() if status == "FAILED"]
        assert len(state.failed) >= 2, (
            f"with the AI provider unreachable for {OUTAGE_WINDOW}s, only {len(state.failed)} of "
            f"{FAILING_DOCUMENTS} documents reached FAILED ({seen}); two are needed, one per retry path"
        )
        # Anything still retrying when the provider came back may finish on its own.
        leftovers = [rid for rid in uploaded if rid not in state.failed]
        if leftovers:
            await wait_until_finished(kb_client, leftovers)
        yield state
    finally:
        _reconnect(compose)
        try:
            kb_client.delete_kb(kb_id)
        except Exception as exc:  # noqa: BLE001 - teardown must not mask the result
            logger.warning("Could not delete knowledge base %s: %s", kb_id, exc)


@pytest.mark.order(1)
async def test_retry_indexing_on_one_failed_record_from_all_records(
    failed_records: FailedRecords, pipeshub_client, kb_client, vector_store
) -> None:
    target, others = failed_records.failed[0], failed_records.failed[1:]
    resp = pipeshub_client.request(
        "POST", f"/api/v1/knowledgeBase/reindex/record/{target}", json={"depth": 0}
    )
    assert resp.status_code == 200, f"Retry indexing answered HTTP {resp.status_code}: {resp.text[:400]}"

    final = await wait_until_finished(kb_client, [target], reindexed=[target])
    assert final[target] == "COMPLETED", (
        f"the failed record did not index once the AI provider was back and it was retried: {final}"
    )
    assert await vector_store.count_for_virtual_record(_virtual_id(kb_client, target)) > 0, (
        "the retried record is COMPLETED but has no embeddings, so search cannot find it"
    )
    untouched = statuses(kb_client, others)
    assert set(untouched.values()) == {"FAILED"}, (
        f"retrying one record changed other failed records in the same collection: {untouched}"
    )


@pytest.mark.order(2)
async def test_reindex_failed_from_connector_settings_retries_only_the_failed_records(
    failed_records: FailedRecords, pipeshub_client, kb_client, vector_store, graph_provider
) -> None:
    remaining = failed_records.failed[1:]
    assert set(statuses(kb_client, remaining).values()) == {"FAILED"}, "nothing left FAILED to retry"
    retried_first = failed_records.failed[0]
    retried_first_before = await _index_marks(graph_provider, vector_store, kb_client, retried_first)

    resp = pipeshub_client.request(
        "POST",
        f"/api/v1/connectors/{failed_records.kb_id}/reindex",
        json={"statusFilters": ["FAILED"]},
    )
    assert resp.status_code == 200, f"Reindex failed answered HTTP {resp.status_code}: {resp.text[:400]}"
    assert resp.json().get("eventPublished") is True, resp.json()

    final = await wait_until_finished(kb_client, remaining, reindexed=remaining)
    stuck = {rid: status for rid, status in final.items() if status != "COMPLETED"}
    assert not stuck, f"after Reindex failed, these records did not reach COMPLETED: {stuck}"
    for rid in remaining:
        assert await vector_store.count_for_virtual_record(_virtual_id(kb_client, rid)) > 0, (
            f"record {rid} is COMPLETED after Reindex failed but has no embeddings"
        )

    healthy_after = await _index_marks(graph_provider, vector_store, kb_client, failed_records.healthy)
    assert healthy_after == failed_records.healthy_before, (
        "Reindex failed re-queued or re-indexed a record that had not failed. "
        f"Before: {failed_records.healthy_before}, after: {healthy_after}"
    )
    retried_first_after = await _index_marks(graph_provider, vector_store, kb_client, retried_first)
    assert retried_first_after == retried_first_before, (
        "Reindex failed re-indexed a record that had already recovered. "
        f"Before: {retried_first_before}, after: {retried_first_after}"
    )
