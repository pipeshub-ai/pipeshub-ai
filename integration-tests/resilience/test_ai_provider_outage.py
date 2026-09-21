"""Indexing tells people when the AI provider is down, and recovers when it is back.

Indexing calls the configured AI provider for every document. This test cuts
the app container off from the provider (its host names point at an address
that refuses connections), restarts the indexing process so no connection
opened earlier survives, and uploads documents to a fresh knowledge base:

  * the product must show a problem on the affected documents — a failed
    status or a reason — rather than leave them looking fine or silently stuck;
  * once the provider is reachable again, every document must end COMPLETED,
    by the product's own retries or by the re-index a person would click.

The outage runs once, in a module fixture; each test below checks one thing
it saw, so a failure names the part that broke.

    RESILIENCE_AI_OUTAGE_WINDOW_SEC  how long the product may take to show the problem (default 600)
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field

import pytest
import pytest_asyncio

from helper.clients.kb_client import KBClient
from helper.compose_control import ComposeStack
from helper.fault_switches import KILL_INDEXING, ai_provider_hosts, block_hosts_script, restore_hosts_script
from helper.indexing_progress import POLL, document, record_fields, wait_until_finished
from helper.plain_message import plain_language_problems

logger = logging.getLogger("resilience")

pytestmark = [pytest.mark.resilience, pytest.mark.asyncio(loop_scope="session")]

FILES = int(os.getenv("RESILIENCE_FILES", "8"))
OUTAGE_WINDOW = int(os.getenv("RESILIENCE_AI_OUTAGE_WINDOW_SEC", "600"))

def _state(kb_client: KBClient, record_id: str) -> dict[str, str]:
    fields = record_fields(kb_client.get_record(record_id))
    return {
        "indexing": fields.get("indexingStatus") or "UNKNOWN",
        "extraction": fields.get("extractionStatus") or "",
        "reason": (fields.get("reason") or "").strip(),
    }


def _shows_a_problem(state: dict[str, str]) -> bool:
    return state["indexing"] == "FAILED" or state["extraction"] == "FAILED" or bool(state["reason"])


def _cut_off(compose: ComposeStack) -> None:
    compose.exec("pipeshub-ai", ["sh", "-c", block_hosts_script(ai_provider_hosts())])
    # A process that already holds a provider connection could keep using it.
    compose.exec("pipeshub-ai", ["sh", "-c", KILL_INDEXING])


def _reconnect(compose: ComposeStack) -> None:
    compose.exec("pipeshub-ai", ["sh", "-c", restore_hosts_script()])


@dataclass
class AiOutage:
    """What one AI provider outage did: the reasons shown, and the statuses after recovery."""

    record_ids: list[str] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)
    clean_during_outage: list[str] = field(default_factory=list)
    final: dict[str, str] = field(default_factory=dict)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def ai_outage(compose: ComposeStack, kb_client: KBClient, pipeshub_client) -> AiOutage:
    """Run the outage once; every test in this module reads what it saw."""
    result = AiOutage()
    kb_id = kb_client.create_kb(f"resilience-ai-{uuid.uuid4().hex[:8]}")["id"]
    try:
        _cut_off(compose)
        for _ in range(FILES):
            token = uuid.uuid4().hex[:12]
            upload = kb_client.upload_file(kb_id, f"runbook-{token}.md", document(token), mimetype="text/markdown")
            assert upload["summary"]["failed"] == 0, f"upload failed before indexing began: {upload}"
            result.record_ids.append(upload["records"][0]["recordId"])

        deadline = asyncio.get_event_loop().time() + OUTAGE_WINDOW
        states = {rid: _state(kb_client, rid) for rid in result.record_ids}
        while asyncio.get_event_loop().time() < deadline:
            if all(_shows_a_problem(s) or s["indexing"] == "COMPLETED" for s in states.values()):
                break
            await asyncio.sleep(POLL)
            states = {rid: _state(kb_client, rid) for rid in result.record_ids}
        result.clean_during_outage = [
            rid for rid, s in states.items() if s["indexing"] == "COMPLETED" and not _shows_a_problem(s)
        ]
        affected = {rid: s for rid, s in states.items() if _shows_a_problem(s)}
        result.reasons = {rid: s["reason"] for rid, s in affected.items()}
        logger.info("Reasons shown during the AI outage: %s", result.reasons)

        _reconnect(compose)
        reindexed = [
            rid for rid, s in affected.items() if s["indexing"] == "FAILED" or s["extraction"] == "FAILED"
        ]
        for rid in reindexed:
            pipeshub_client.reindex_record(rid)
        result.final = await wait_until_finished(kb_client, result.record_ids, reindexed=reindexed)
        return result
    finally:
        # Idempotent, so it is safe after a successful reconnect, and it runs
        # even if the cut-off itself failed halfway.
        _reconnect(compose)
        try:
            kb_client.delete_kb(kb_id)
        except Exception as exc:  # noqa: BLE001 - teardown must not mask the result
            logger.warning("Could not delete knowledge base %s: %s", kb_id, exc)


async def test_an_ai_provider_outage_shows_on_the_affected_documents(ai_outage: AiOutage) -> None:
    assert not ai_outage.clean_during_outage, (
        f"{len(ai_outage.clean_during_outage)} document(s) finished cleanly with the AI provider unreachable, "
        f"so the cut-off did not take effect (blocked hosts: {ai_provider_hosts()}): {ai_outage.clean_during_outage}"
    )
    assert ai_outage.reasons, (
        f"with the AI provider unreachable for {OUTAGE_WINDOW}s, none of {len(ai_outage.record_ids)} documents "
        "showed a failed status or a reason"
    )


async def test_indexing_recovers_once_the_ai_provider_is_back(ai_outage: AiOutage) -> None:
    stuck = {rid: status for rid, status in ai_outage.final.items() if status != "COMPLETED"}
    assert not stuck, (
        f"after the AI provider came back and failed documents were re-indexed, "
        f"{len(stuck)} of {len(ai_outage.record_ids)} did not reach COMPLETED: {stuck}"
    )


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "A document hit by an AI provider outage shows the provider library's own error text as its "
        "reason: the indexing handler stores str(exception), e.g. \"Connection error.\" or "
        "\"Transient failure, retry scheduled: Connection error.\", with no mention of the AI model "
        "and no next step."
    ),
)
async def test_ai_outage_reasons_read_plainly(ai_outage: AiOutage) -> None:
    assert ai_outage.reasons, "the outage showed no reasons to check"
    unclear = {
        reason: why
        for reason in set(ai_outage.reasons.values())
        if (why := plain_language_problems(reason, about=("AI", "model", "provider")))
    }
    assert not unclear, f"reasons shown to users during an AI outage do not read plainly: {unclear}"
