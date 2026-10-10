"""Fixtures for the named-entity suite: the Labs flag on, and an indexed corpus.

The flag is org-wide, so the suite runs in the model-backed shard of its own
(see ``pytest.ini``) and puts the flag back when it ends.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator, Iterator
from dataclasses import dataclass

import pytest
import pytest_asyncio

from helper.clients.kb_client import KBClient
from helper.feature_flags import feature_flag
from helper.second_user import second_user  # noqa: F401 - fixture
from named_entities.corpus import corpus

logger = logging.getLogger("named-entity-fixtures")

FLAG = "ENABLE_NAMED_ENTITY_EXTRACTION"
EXTRACTION_TIMEOUT = 600
POLL = 5
_PENDING = (None, "NOT_STARTED", "QUEUED", "IN_PROGRESS")


@dataclass(frozen=True)
class NerCorpus:
    kb_id: str
    record_ids: dict[str, str]       # slug -> record id
    virtual_ids: dict[str, str]      # slug -> virtual record id


@pytest.fixture(scope="session")
def ner_enabled(pipeshub_client) -> Iterator[None]:
    with feature_flag(pipeshub_client, FLAG, True):
        yield


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def ner_corpus(pipeshub_client, ai_models_configured, ner_enabled, graph_provider) -> AsyncGenerator[NerCorpus, None]:
    kb_client = KBClient(pipeshub_client)
    nonce = uuid.uuid4().hex[:8]
    kb_id = kb_client.create_kb(f"named-entities-{nonce}")["id"]
    record_ids: dict[str, str] = {}
    virtual_ids: dict[str, str] = {}
    try:
        for document in corpus(nonce):
            upload = kb_client.upload_file(kb_id, document.filename, document.body.encode(), mimetype="text/markdown")
            assert upload["summary"]["failed"] == 0, f"upload of {document.filename} failed: {upload}"
            record_ids[document.slug] = upload["records"][0]["recordId"]
        for slug, record_id in record_ids.items():
            virtual_ids[slug] = await _wait_for_entities(kb_client, graph_provider, record_id, slug)
        yield NerCorpus(kb_id=kb_id, record_ids=record_ids, virtual_ids=virtual_ids)
    finally:
        try:
            kb_client.delete_kb(kb_id)
        except Exception as exc:  # noqa: BLE001 - teardown must not mask a failure
            logger.warning("Could not delete knowledge base %s: %s", kb_id, exc)


async def _wait_for_entities(kb_client: KBClient, graph_provider, record_id: str, slug: str) -> str:
    """Enrichment, and with it the entity write, ends after indexing. Waiting for the
    entities themselves keeps a slow model from reading as missing entities."""
    deadline = asyncio.get_event_loop().time() + EXTRACTION_TIMEOUT
    record: dict = {}
    while asyncio.get_event_loop().time() < deadline:
        record = kb_client.get_record(record_id).get("record") or {}
        if record.get("extractionStatus") not in _PENDING and record.get("virtualRecordId"):
            if await graph_provider.get_named_entities_for_record(record_id):
                return str(record["virtualRecordId"])
        await asyncio.sleep(POLL)
    raise AssertionError(
        f"{slug} ({record_id}) has no named entities within {EXTRACTION_TIMEOUT}s; "
        f"indexing={record.get('indexingStatus')} extraction={record.get('extractionStatus')}"
    )
